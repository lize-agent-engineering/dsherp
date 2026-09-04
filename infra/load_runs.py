"""Run the bounded G5 load against alpha and daily with a local SSE model."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from datetime import datetime
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsherp.runtime_host import IMAGE, container_base, load_settings
from dsherp.runtime_revision import configuration_revision


ALPHA_SITE = "dsherp-validation.localhost"
DAILY_SITE = "dsherp-daily.localhost"
TERMINAL = frozenset({"Succeeded", "Failed", "Cancelled", "NeedsInput"})
REQUIRED_USER_KEYS = ("user", "api_key", "api_secret", "base_url", "site")
BACKEND = "dsherp-validation-backend-1"
SITE_PYTHON = "/home/frappe/frappe-bench/env/bin/python"
CONTEXT_API = "/api/method/dsherp_bridge.context_api."
EXECUTION_API = "/api/method/dsherp_bridge.context_execution."


def _fail(message):
    raise RuntimeError(message)


def _read_json(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"missing json: {path}") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid json: {path}") from error
    if not isinstance(payload, dict):
        _fail(f"json must be an object: {path}")
    return payload


def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail(f"invalid {label}")
    return value


def _parse_time(value, label):
    if not isinstance(value, str):
        _fail(f"invalid {label}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"invalid {label}") from error


def require_worker_stopped(pid_path):
    path = Path(pid_path)
    if not path.exists():
        return
    try:
        pid = int(path.read_text(encoding="utf-8"))
        os.kill(pid, 0)
    except ProcessLookupError:
        _fail("resident worker pid file is stale")
    except (OSError, ValueError) as error:
        raise RuntimeError("resident worker pid state is invalid") from error
    _fail("resident worker is running")


def percentile95(samples):
    if not isinstance(samples, list) or len(samples) != 20:
        _fail("run_status requires exactly twenty samples")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in samples):
        _fail("invalid run_status sample")
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _profile(payload, role):
    profile = payload.get(role)
    if not isinstance(profile, dict):
        _fail(f"missing {role}")
    for key in REQUIRED_USER_KEYS:
        _require_text(profile.get(key), f"{role}.{key}")
    if profile["site"] != ALPHA_SITE:
        _fail(f"{role} must use {ALPHA_SITE}")
    return profile


def load_alpha_actors(users_path, writer_path):
    users = _read_json(users_path)
    writer = _read_json(writer_path)
    reader = _profile(users, "reader")
    denied = _profile(users, "denied")
    writer_user = _require_text(writer.get("user"), "writer.user")
    actors = [reader, denied, writer]
    names = [reader["user"], denied["user"], writer_user]
    if len(set(names)) != 3:
        _fail("alpha actors must be unique")
    return actors


def summarize_run(run_id, site, owner, status, events):
    if not isinstance(events, list) or not events:
        _fail("events required")
    previous_seq = None
    kinds = []
    by_kind = {}
    for event in events:
        if not isinstance(event, dict):
            _fail("invalid event")
        seq = event.get("seq")
        kind = event.get("kind")
        recorded_at = event.get("recorded_at")
        if not isinstance(seq, int) or isinstance(seq, bool):
            _fail("invalid event seq")
        if previous_seq is not None and seq <= previous_seq:
            _fail("event seq is disordered")
        previous_seq = seq
        _require_text(kind, "event kind")
        _parse_time(recorded_at, "event recorded_at")
        kinds.append(kind)
        if kind in by_kind and kind in {"queued", "claimed", "finished"}:
            _fail(f"duplicate {kind} event")
        by_kind.setdefault(kind, event)
    for required in ("queued", "claimed", "finished"):
        if required not in by_kind:
            _fail(f"missing {required} event")
    queued_at = _parse_time(by_kind["queued"]["recorded_at"], "queued recorded_at")
    claimed_at = _parse_time(by_kind["claimed"]["recorded_at"], "claimed recorded_at")
    finished_at = _parse_time(by_kind["finished"]["recorded_at"], "finished recorded_at")
    if not queued_at <= claimed_at <= finished_at:
        _fail("run event times are disordered")
    return {
        "run_id": run_id,
        "site": site,
        "owner": owner,
        "status": status,
        "queued_to_claimed_seconds": (claimed_at - queued_at).total_seconds(),
        "total_seconds": (finished_at - queued_at).total_seconds(),
        "kinds": kinds,
        "queued_at": by_kind["queued"]["recorded_at"],
        "claimed_at": by_kind["claimed"]["recorded_at"],
        "finished_at": by_kind["finished"]["recorded_at"],
    }


def validate_load(rows, alpha_order, daily_id, run_status_p95):
    if not isinstance(rows, list) or len(rows) != 4:
        _fail("load requires exactly four runs")
    if not isinstance(alpha_order, list) or len(alpha_order) != 3:
        _fail("alpha_order must have three run ids")
    daily_id = _require_text(daily_id, "daily_id")
    expected = list(alpha_order) + [daily_id]
    if any(not isinstance(run_id, str) or not run_id.strip() for run_id in alpha_order):
        _fail("invalid alpha run id")
    if len(set(expected)) != 4:
        _fail("run ids must be unique")
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail("invalid load row")
        run_id = _require_text(row.get("run_id"), "run_id")
        if run_id in by_id:
            _fail("duplicate run_id")
        status = row.get("status")
        if status not in TERMINAL:
            _fail("run is not terminal")
        if status == "Failed":
            _fail("load run failed")
        _parse_time(row.get("claimed_at"), "claimed_at")
        by_id[run_id] = row
    if set(by_id) != set(expected):
        _fail("run ids do not match load set")
    claimed = [
        (run_id, _parse_time(by_id[run_id]["claimed_at"], "claimed_at"))
        for run_id in alpha_order
    ]
    actual_order = [run_id for run_id, _ in sorted(claimed, key=lambda item: item[1])]
    if actual_order != list(alpha_order):
        _fail("alpha claim order mismatch")
    daily_claimed = _parse_time(by_id[daily_id]["claimed_at"], "daily claimed_at")
    if daily_claimed >= claimed[1][1]:
        _fail("daily is not parallel with alpha")
    if isinstance(run_status_p95, bool) or not isinstance(run_status_p95, (int, float)):
        _fail("invalid run_status_p95")
    if not run_status_p95 < 1:
        _fail("run_status p95 must be under 1")


def _site_json(site, body, payload=None, timeout=60):
    encoded = json.dumps(payload, ensure_ascii=False)
    script = (
        "import os,json,frappe\n"
        "os.chdir('/home/frappe/frappe-bench/sites')\n"
        f"frappe.init(site={site!r});frappe.connect()\n"
        f"PAYLOAD=json.loads({encoded!r})\n"
        + body
        + "\nfrappe.destroy()\n"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", BACKEND, SITE_PYTHON, "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        tail = result.stderr.strip().splitlines()
        kind = tail[-1].split(":", 1)[0] if tail else "unknown"
        raise RuntimeError(f"site command failed for {site}: {kind}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        _fail(f"site command returned no result for {site}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"site command returned invalid result for {site}") from error


def _service_client(profile):
    return httpx.Client(
        base_url=profile["base_url"],
        headers={
            "X-Frappe-Site-Name": profile["site"],
            "Authorization": "token " + profile["api_key"] + ":" + profile["api_secret"],
        },
        timeout=25,
        trust_env=False,
        follow_redirects=False,
    )


def _call(client, prefix, method, payload):
    response = client.post(prefix + method, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"{method} failed with HTTP {response.status_code}")
    body = response.json()
    if "message" not in body:
        _fail(f"{method} returned no message")
    return body["message"]


def _active_counts():
    body = """
statuses=['Queued','Running','Cancelling']
print(json.dumps({'active':frappe.db.count('DS Model Run',{'status':['in',statuses]})}))
"""
    return {
        site: _site_json(site, body)["active"]
        for site in (ALPHA_SITE, DAILY_SITE)
    }


def _worker_processes():
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="], text=True, capture_output=True, check=True, timeout=10
    )
    return [line.strip() for line in result.stdout.splitlines() if "-m dsherp.context_worker" in line]


def _preflight():
    require_worker_stopped(ROOT / ".runtime" / "agent-worker.pid")
    processes = _worker_processes()
    if processes:
        _fail("resident worker process exists without an accepted pid state")
    active = _active_counts()
    if any(active.values()):
        _fail(f"active runs must be zero before G5 load: {active}")


def _enqueue_http(profile, question, request_id):
    with _service_client(profile) as client:
        message = _call(
            client,
            CONTEXT_API,
            "send_message",
            {
                "question": question,
                "context": {"schema_version": 1, "page_type": "unknown", "route": []},
                "request_id": request_id,
                "domain": "query",
            },
        )
    return {"run_id": message["active_run"], "conversation": message["id"]}


def _enqueue_site(site, owner, question, request_id):
    body = """
from dsherp_bridge import context_api
frappe.set_user(PAYLOAD['owner'])
message=context_api.send_message(PAYLOAD['question'],{'schema_version':1,'page_type':'unknown','route':[]},PAYLOAD['request_id'],domain='query')
frappe.db.commit()
print(json.dumps({'run_id':message['active_run'],'conversation':message['id']}))
"""
    return _site_json(
        site,
        body,
        {"owner": owner, "question": question, "request_id": request_id},
    )


def _seed_heartbeat(clients):
    for client in clients.values():
        result = _call(client, EXECUTION_API, "worker_heartbeat", {})
        if not isinstance(result.get("heartbeat"), str):
            _fail("worker heartbeat was not recorded")


def _status_probe(alpha_client, reader_profile, settings):
    fixture = _enqueue_http(
        reader_profile,
        "S7 G5 run_status 并发探针",
        "s7-status-" + uuid.uuid4().hex,
    )
    try:
        claim = _call(
            alpha_client,
            EXECUTION_API,
            "claim_run",
            {"runtime_revision": configuration_revision(settings)},
        )
        if not claim or claim.get("run_id") != fixture["run_id"]:
            _fail("run_status probe did not claim its exact run")
        cap = {"run_id": claim["run_id"], "capability": claim["capability"]}

        def timed_call(_index):
            started = time.perf_counter()
            value = _call(alpha_client, EXECUTION_API, "run_status", cap)
            elapsed = time.perf_counter() - started
            if value.get("status") != "Running":
                _fail("run_status probe stopped being Running")
            return elapsed

        with ThreadPoolExecutor(max_workers=20, thread_name_prefix="g5-status") as pool:
            samples = list(pool.map(timed_call, range(20)))
        return percentile95(samples), samples
    finally:
        _cleanup_site(ALPHA_SITE, [fixture])


def _read_runs(site, descriptors):
    body = """
from dsherp_bridge import context_events
result=[]
for item in PAYLOAD:
    row=frappe.db.get_value('DS Model Run',item['run_id'],['name','status','owner'],as_dict=True)
    if not row:raise RuntimeError('load run disappeared')
    result.append({'run_id':row.name,'status':row.status,'owner':row.owner,'events':context_events.list_events(row.name,page_length=500)})
print(json.dumps(result,ensure_ascii=False,default=str))
"""
    return _site_json(site, body, descriptors)


def _wait_terminal(created, timeout=360):
    deadline = time.monotonic() + timeout
    while True:
        state = {
            site: _read_runs(site, descriptors)
            for site, descriptors in created.items()
            if descriptors
        }
        rows = [item for items in state.values() for item in items]
        if len(rows) == sum(len(items) for items in created.values()) and all(
            item["status"] in TERMINAL for item in rows
        ):
            return state
        if time.monotonic() >= deadline:
            summary = {item["run_id"]: item["status"] for item in rows}
            raise RuntimeError(f"G5 load did not finish before timeout: {summary}")
        time.sleep(2)


def _cleanup_site(site, descriptors):
    if not descriptors:
        return {"runs": 0, "conversations": 0}
    body = """
frappe.set_user('Administrator')
for item in PAYLOAD:
    run=item['run_id'];conversation=item['conversation']
    assert frappe.db.count('DS Operation Proposal',{'model_run':run})==0
    frappe.db.delete('DS Run Event',{'run':run})
    if frappe.db.exists('DS Model Run',run):frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    if frappe.db.exists('DS Conversation',conversation):frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
frappe.db.commit()
remaining_runs=sum(frappe.db.exists('DS Model Run',item['run_id']) is not None for item in PAYLOAD)
remaining_conversations=sum(frappe.db.exists('DS Conversation',item['conversation']) is not None for item in PAYLOAD)
print(json.dumps({'runs':remaining_runs,'conversations':remaining_conversations}))
"""
    result = _site_json(site, body, descriptors)
    if result != {"runs": 0, "conversations": 0}:
        _fail(f"G5 cleanup incomplete for {site}: {result}")
    return result


def _session_directory(site, owner, conversation, native_session_id):
    value = json.dumps(
        [site, owner, conversation, "query", native_session_id], separators=(",", ":")
    )
    scope = hashlib.sha256(value.encode()).hexdigest()
    path = ROOT / ".runtime" / "business-sessions" / scope
    if path.parent != ROOT / ".runtime" / "business-sessions":
        _fail("invalid business session cleanup path")
    return path


def _session_paths(site, descriptors, run_rows):
    by_id = {item["run_id"]: item for item in descriptors}
    paths = []
    for row in run_rows:
        claimed = [event for event in row["events"] if event["kind"] == "claimed"]
        if len(claimed) != 1:
            _fail("load run must contain exactly one claimed event")
        native = claimed[0]["payload"].get("native_session_id")
        if not isinstance(native, str) or not native:
            _fail("claimed event lacks native session id")
        item = by_id[row["run_id"]]
        paths.append(_session_directory(site, row["owner"], item["conversation"], native))
    return paths


class _SyntheticModel:
    def __init__(self, log_path=None):
        self.requests = 0
        self.summaries = []
        self.log_path = Path(log_path) if log_path else None
        self._lock = threading.Lock()

    def handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")

            def do_POST(self):
                try:
                    size = int(self.headers["Content-Length"])
                    request = json.loads(self.rfile.read(size))
                    messages = request.get("messages")
                    if not isinstance(messages, list):
                        raise ValueError("messages")
                    with model._lock:
                        model.requests += 1
                        call_id = model.requests
                    text = json.dumps(messages, ensure_ascii=False)
                    has_tool_result = any(item.get("role") == "tool" for item in messages)
                    marker = next(
                        (
                            value
                            for value in ("[alpha:reader]", "[alpha:denied]", "[alpha:writer]", "[daily]")
                            if value in text
                        ),
                        "unknown",
                    )
                    if not has_tool_result:
                        if "[alpha:denied]" in text:
                            function = {
                                "name": "mcp__erp__erp_request_input",
                                "arguments": json.dumps({"question": "该合成身份没有物料读取权限"}, ensure_ascii=False),
                            }
                        else:
                            item = "DAILY-AGENT-ITEM" if "[daily]" in text else "DSHERP-TEST-ITEM"
                            function = {
                                "name": "mcp__erp__erp_read_record",
                                "arguments": json.dumps({"doctype": "Item", "name": item}),
                            }
                        deltas = [
                            (
                                {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": f"call-g5-{call_id}",
                                            "type": "function",
                                            "function": function,
                                        }
                                    ],
                                },
                                None,
                            ),
                            ({}, "tool_calls"),
                        ]
                    else:
                        deltas = [
                            ({"role": "assistant", "content": "DSHERP_G5_LOCAL_OK"}, None),
                            ({}, "stop"),
                        ]
                        function = None
                    with model._lock:
                        summary = {
                            "request": call_id,
                            "marker": marker,
                            "roles": [item.get("role") for item in messages],
                            "offered_tools": [
                                item.get("function", {}).get("name")
                                for item in request.get("tools", [])
                            ],
                            "has_tool_result": has_tool_result,
                            "selected_tool": None if function is None else function["name"],
                        }
                        model.summaries.append(summary)
                        if model.log_path:
                            with model.log_path.open("a", encoding="utf-8") as stream:
                                stream.write(json.dumps(summary, ensure_ascii=False) + "\n")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for delta, reason in deltas:
                        chunk = {
                            "id": "synthetic-g5",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "deepseek-v4-flash",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                        }
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception:
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, *_args):
                pass

        return Handler


def _serve_model(port, log_path):
    model = _SyntheticModel(log_path)
    server = ThreadingHTTPServer(("0.0.0.0", port), model.handler())
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _model_summaries(state):
    path = state["log"]
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise RuntimeError("synthetic model wrote an invalid request summary") from error
    return rows


@contextmanager
def _synthetic_model_container(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    log_path = directory / "requests.jsonl"
    name = "dsherp-g5-model-" + uuid.uuid4().hex[:12]
    port = 38127
    command = container_base(name)
    command.extend(
        [
        "-v",
        "dsherp-v16-agent-runtime:/opt/runtime:ro",
        "-v",
        f"{ROOT / 'dsherp'}:/opt/dsherp/dsherp:ro",
        "-v",
        f"{ROOT / 'config'}:/opt/dsherp/config:ro",
        "-v",
        f"{ROOT / 'business-skills'}:/opt/dsherp/business-skills:ro",
        "-v",
        f"{ROOT / 'infra'}:/opt/dsherp/infra:ro",
        "-v",
        f"{directory}:/evidence:rw",
        "-e",
        "PYTHONPATH=/opt/dsherp",
        "--entrypoint",
        "/opt/runtime/bin/python",
        IMAGE,
            "/opt/dsherp/infra/load_runs.py",
            "--serve-model",
            str(port),
            "/evidence/requests.jsonl",
        ]
    )
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + 20
    ready = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        probe = subprocess.run(
            [
                "docker",
                "exec",
                BACKEND,
                SITE_PYTHON,
                "-c",
                (
                    "from urllib.request import build_opener,ProxyHandler;"
                    f"print(build_opener(ProxyHandler({{}})).open('http://{name}:{port}/health',timeout=2).read().decode())"
                ),
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "OK":
            ready = True
            break
        time.sleep(0.2)
    if not ready:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        stdout, stderr = process.communicate(timeout=10)
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit {process.returncode}"
        raise RuntimeError("synthetic model container did not become ready: " + detail)
    state = {"name": name, "port": port, "log": log_path, "process": process}
    try:
        yield state
    finally:
        subprocess.run(["docker", "stop", "--time", "3", name], capture_output=True, timeout=10)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            stdout, stderr = process.communicate(timeout=10)
        if stdout.strip():
            _fail("synthetic model container wrote unexpected stdout")
        if process.returncode not in (0, 137, 143):
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else "unknown"
            raise RuntimeError("synthetic model container exited unexpectedly: " + detail)


def _write_provider_env(path, base_url):
    settings = {
        "DEEPSEEK_API_KEY": "synthetic-not-a-credential",
        "DSH_MODEL": "deepseek-v4-flash",
        "DEEPSEEK_BASE_URL": base_url,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        for key, value in settings.items():
            stream.write(f"{key}={value}\n")
    return load_settings(path)


def _start_worker(provider_env):
    process = subprocess.Popen(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "-m",
            "dsherp.context_worker",
            "--profile",
            str(ROOT / ".runtime" / "context-worker-sites.json"),
            "--provider-env",
            str(provider_env),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20
    pid_path = ROOT / ".runtime" / "agent-worker.pid"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail(f"G5 worker exited during start with code {process.returncode}")
        if pid_path.exists() and pid_path.read_text().strip() == str(process.pid):
            return process
        time.sleep(0.2)
    process.terminate()
    process.wait(timeout=10)
    _fail("G5 worker pid did not become ready")


def _stop_worker(process):
    if process is None:
        return {"pid": None, "returncode": None, "events": []}
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError("G5 worker did not stop on SIGTERM")
    if process.returncode != 0:
        raise RuntimeError(f"G5 worker exited with code {process.returncode}")
    if stdout.strip():
        _fail("G5 worker wrote unexpected stdout")
    records = []
    for line in stderr.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise RuntimeError("G5 worker wrote a non-JSON diagnostic") from error
    return {"pid": process.pid, "returncode": process.returncode, "events": records}


def execute_load():
    _preflight()
    actors = load_alpha_actors(
        ROOT / ".runtime" / "erp-users.json", ROOT / ".runtime" / "context-writer.json"
    )
    daily_owner = "daily-operator@example.invalid"
    worker_profile = _read_json(ROOT / ".runtime" / "context-worker-sites.json")
    site_profiles = {item["site"]: item for item in worker_profile.get("sites", [])}
    if set(site_profiles) != {ALPHA_SITE, DAILY_SITE}:
        _fail("worker profile must contain exact alpha and daily sites")
    created = {ALPHA_SITE: [], DAILY_SITE: []}
    session_paths = []
    worker = None
    result = None
    cleanup = {}
    (ROOT / "work").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="g5-load-", dir=ROOT / "work") as temporary:
        temporary = Path(temporary)
        with _synthetic_model_container(temporary / "model") as model:
            env_path = temporary / "provider.env"
            settings = _write_provider_env(
                env_path, f"http://{model['name']}:{model['port']}/v1"
            )
            clients = {site: _service_client(profile) for site, profile in site_profiles.items()}
            try:
                _seed_heartbeat(clients)
                p95, status_samples = _status_probe(clients[ALPHA_SITE], actors[0], settings)
                if _active_counts() != {ALPHA_SITE: 0, DAILY_SITE: 0}:
                    _fail("run_status probe cleanup did not restore idle sites")
                worker = _start_worker(env_path)
                questions = [
                    "[alpha:reader] 只读检查合成物料",
                    "[alpha:denied] 只读检查合成物料",
                    "[alpha:writer] 只读检查合成物料",
                ]
                for index, (actor, question) in enumerate(zip(actors, questions)):
                    request_id = "s7-alpha-" + uuid.uuid4().hex
                    if "api_key" in actor:
                        item = _enqueue_http(actor, question, request_id)
                    else:
                        item = _enqueue_site(ALPHA_SITE, actor["user"], question, request_id)
                    item.update(owner=actor["user"], ordinal=index)
                    created[ALPHA_SITE].append(item)
                daily = _enqueue_site(
                    DAILY_SITE,
                    daily_owner,
                    "[daily] 只读检查日常合成物料",
                    "s7-daily-" + uuid.uuid4().hex,
                )
                daily.update(owner=daily_owner, ordinal=0)
                created[DAILY_SITE].append(daily)
                state = _wait_terminal(created)
                rows = []
                for site, run_rows in state.items():
                    session_paths.extend(_session_paths(site, created[site], run_rows))
                    for row in run_rows:
                        rows.append(
                            summarize_run(
                                row["run_id"], site, row["owner"], row["status"], row["events"]
                            )
                        )
                alpha_order = [item["run_id"] for item in created[ALPHA_SITE]]
                daily_id = created[DAILY_SITE][0]["run_id"]
                try:
                    validate_load(rows, alpha_order, daily_id, p95)
                except RuntimeError:
                    print(
                        "G5_LOAD_FAILURE="
                        + json.dumps(
                            {
                                "runs": rows,
                                "run_status_p95_seconds": round(p95, 6),
                                "provider_request_summaries": _model_summaries(model),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    raise
                summaries = _model_summaries(model)
                result = {
                    "runs": rows,
                    "run_status_p95_seconds": round(p95, 6),
                    "run_status_samples": len(status_samples),
                    "provider_requests": len(summaries),
                }
            finally:
                try:
                    worker_state = _stop_worker(worker)
                finally:
                    for site, descriptors in created.items():
                        cleanup[site] = _cleanup_site(site, descriptors)
                    for path in session_paths:
                        if path.exists():
                            shutil.rmtree(path)
                    for client in clients.values():
                        client.close()
        if result is None:
            _fail("G5 load produced no result")
        result["worker"] = worker_state
        result["cleanup"] = cleanup
        result["business_session_paths_removed"] = len(session_paths)
        result["final_active"] = _active_counts()
        if result["final_active"] != {ALPHA_SITE: 0, DAILY_SITE: 0}:
            _fail("G5 cleanup left active runs")
        return result


def main():
    result = execute_load()
    print("G5_LOAD_RESULT=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve-model":
        _serve_model(int(sys.argv[2]), Path(sys.argv[3]))
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("usage: load_runs.py [--serve-model PORT LOG_PATH]")
