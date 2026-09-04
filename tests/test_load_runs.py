import json
import threading
import time

import httpx
import pytest

from infra.load_runs import (
    _cleanup_site,
    _plan_status_history,
    _write_provider_env,
    capability_client,
    load_alpha_actors,
    percentile95,
    require_worker_stopped,
    summarize_run,
    validate_load,
    validate_status_probe,
)

READER = "dsherp-reader@example.invalid"
DENIED = "dsherp-denied@example.invalid"
WRITER = "dsherp-writer@example.invalid"
ALPHA = "dsherp-validation.localhost"
DAILY = "dsherp-daily.localhost"


def test_require_worker_stopped_fastfails_on_live_pid(tmp_path):
    pid_path = tmp_path / "agent-worker.pid"
    pid_path.write_text(str(__import__("os").getpid()))
    with pytest.raises(RuntimeError, match="resident worker"):
        require_worker_stopped(pid_path)
    require_worker_stopped(tmp_path / "missing.pid")


def test_write_provider_env_returns_and_writes_only_api_key_and_base_url(tmp_path):
    path = tmp_path / "provider.env"
    url = "http://127.0.0.1:9/v1"
    settings = _write_provider_env(path, url)

    assert set(settings) == {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}
    assert settings["DEEPSEEK_BASE_URL"] == url
    assert path.stat().st_mode & 0o777 == 0o600

    text = path.read_text(encoding="utf-8")
    written = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, value = line.split("=", 1)
        written[key] = value
    assert set(written) == {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}
    assert "DSH_MODEL" not in text
    assert written["DEEPSEEK_BASE_URL"] == url
    assert written["DEEPSEEK_API_KEY"] == settings["DEEPSEEK_API_KEY"]


def test_percentile95_uses_nearest_rank_and_requires_twenty_samples():
    assert percentile95([index / 100 for index in range(1, 21)]) == 0.19
    with pytest.raises(RuntimeError):
        percentile95([0.1] * 19)


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _erp_users(reader=READER, denied=DENIED):
    return {
        "reader": {
            "user": reader,
            "api_key": "reader-key",
            "api_secret": "reader-secret",
            "base_url": "http://127.0.0.1:18081",
            "site": ALPHA,
        },
        "denied": {
            "user": denied,
            "api_key": "denied-key",
            "api_secret": "denied-secret",
            "base_url": "http://127.0.0.1:18081",
            "site": ALPHA,
        },
    }


def _writer_profile(user=WRITER):
    return {
        "user": user,
        "password": "synthetic-writer-password",
        "item": "DSHERP-HITL-ITEM",
        "baseline": "2026-09-04 00:00:00.000000",
    }


def _event(seq, kind, recorded_at):
    return {
        "name": f"evt-{seq:06d}",
        "seq": seq,
        "kind": kind,
        "source": "server",
        "error_class": "",
        "payload": {},
        "recorded_at": recorded_at,
    }


def _events(queued_at, claimed_at, finished_at):
    return [
        _event(1, "queued", queued_at),
        _event(2, "claimed", claimed_at),
        _event(3, "finished", finished_at),
    ]


def test_load_alpha_actors_returns_reader_denied_and_writer(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    actors = load_alpha_actors(users_path, writer_path)
    users = [actor["user"] for actor in actors]

    assert users == [READER, DENIED, WRITER]
    assert len(users) == 3
    assert len(set(users)) == 3


def test_load_alpha_actors_fastfails_when_reader_is_missing(tmp_path):
    payload = _erp_users()
    del payload["reader"]
    users_path = _write_json(tmp_path / "erp-users.json", payload)
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_load_alpha_actors_fastfails_when_denied_is_missing(tmp_path):
    payload = _erp_users()
    del payload["denied"]
    users_path = _write_json(tmp_path / "erp-users.json", payload)
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_load_alpha_actors_fastfails_when_writer_profile_is_missing(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, tmp_path / "context-writer.json")


def test_load_alpha_actors_fastfails_when_users_are_duplicated(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile(user=READER))

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_summarize_run_uses_recorded_at_and_keeps_strict_seq_kinds():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.500000"),
        _event(3, "runtime_started", "2026-09-04 10:00:03.000000"),
        _event(4, "finished", "2026-09-04 10:00:10.000000"),
    ]

    row = summarize_run("run-alpha-1", ALPHA, READER, "Succeeded", events)

    assert row["run_id"] == "run-alpha-1"
    assert row["site"] == ALPHA
    assert row["owner"] == READER
    assert row["status"] == "Succeeded"
    assert row["queued_to_claimed_seconds"] == 2.5
    assert row["total_seconds"] == 10.0
    assert row["kinds"] == ["queued", "claimed", "runtime_started", "finished"]
    assert row["queued_at"] == "2026-09-04 10:00:00.000000"
    assert row["claimed_at"] == "2026-09-04 10:00:02.500000"
    assert row["finished_at"] == "2026-09-04 10:00:10.000000"


def test_summarize_run_fastfails_when_queued_is_missing():
    events = [
        _event(1, "claimed", "2026-09-04 10:00:02.000000"),
        _event(2, "finished", "2026-09-04 10:00:10.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_claimed_is_missing():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "finished", "2026-09-04 10:00:10.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_terminal_event_is_missing():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_seq_is_disordered():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(3, "finished", "2026-09-04 10:00:10.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def _row(run_id, site, owner, status, queued_at, claimed_at, finished_at):
    return summarize_run(
        run_id,
        site,
        owner,
        status,
        _events(queued_at, claimed_at, finished_at),
    )


def _valid_load_rows():
    return [
        _row(
            "alpha-1",
            ALPHA,
            READER,
            "Succeeded",
            "2026-09-04 10:00:00.000000",
            "2026-09-04 10:00:01.000000",
            "2026-09-04 10:00:08.000000",
        ),
        _row(
            "alpha-2",
            ALPHA,
            DENIED,
            "Succeeded",
            "2026-09-04 10:00:00.100000",
            "2026-09-04 10:00:09.000000",
            "2026-09-04 10:00:16.000000",
        ),
        _row(
            "alpha-3",
            ALPHA,
            WRITER,
            "Succeeded",
            "2026-09-04 10:00:00.200000",
            "2026-09-04 10:00:17.000000",
            "2026-09-04 10:00:24.000000",
        ),
        _row(
            "daily-1",
            DAILY,
            READER,
            "Succeeded",
            "2026-09-04 10:00:00.000000",
            "2026-09-04 10:00:01.400000",
            "2026-09-04 10:00:07.000000",
        ),
    ]


def test_validate_load_accepts_terminal_alpha_order_and_parallel_daily():
    validate_load(_valid_load_rows(), ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_a_run_is_not_terminal():
    rows = _valid_load_rows()
    rows[0] = _row(
        "alpha-1",
        ALPHA,
        READER,
        "Running",
        "2026-09-04 10:00:00.000000",
        "2026-09-04 10:00:01.000000",
        "2026-09-04 10:00:08.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_any_run_failed():
    rows = _valid_load_rows()
    rows[2] = _row(
        "alpha-3",
        ALPHA,
        WRITER,
        "Failed",
        "2026-09-04 10:00:00.200000",
        "2026-09-04 10:00:17.000000",
        "2026-09-04 10:00:24.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_alpha_claimed_order_differs():
    with pytest.raises(RuntimeError):
        validate_load(_valid_load_rows(), ["alpha-2", "alpha-1", "alpha-3"], "daily-1", 0.4)


def test_validate_load_accepts_next_claim_after_needs_input_terminal_before_finished_event():
    rows = _valid_load_rows()
    rows[1] = _row(
        "alpha-2",
        ALPHA,
        DENIED,
        "NeedsInput",
        "2026-09-04 10:00:00.100000",
        "2026-09-04 10:00:02.000000",
        "2026-09-04 10:00:09.000000",
    )
    validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_daily_claimed_after_second_alpha():
    rows = _valid_load_rows()
    rows[3] = _row(
        "daily-1",
        DAILY,
        READER,
        "Succeeded",
        "2026-09-04 10:00:00.000000",
        "2026-09-04 10:00:09.500000",
        "2026-09-04 10:00:12.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_run_status_p95_is_not_under_one():
    with pytest.raises(RuntimeError):
        validate_load(_valid_load_rows(), ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 1.0)


def _frappe_run_status(status, run_id="probe-run"):
    return httpx.Response(
        200,
        json={
            "message": {
                "run_id": run_id,
                "status": status,
                "needs_input": "",
                "lease_remaining_seconds": 180,
            }
        },
    )


def _frappe_ping(status_code=200):
    if status_code == 200:
        return httpx.Response(200, json={"message": "pong"})
    return httpx.Response(
        status_code,
        json={
            "exc_type": "Exception",
            "exception": "frappe.exceptions.ValidationError: ping failed",
            "_server_messages": "[]",
        },
    )


def _samples_with_p95(p95, tail=None):
    if p95 == 0:
        return [0.0] * 20
    return [p95 / 2] * 18 + [p95, p95 + 0.01 if tail is None else tail]


def _stderr_json(capsys):
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    line = lines[0]
    start = line.find("{")
    assert start >= 0, line
    return json.loads(line[start:])


def test_validate_status_probe_accepts_hundred_history_turns_twenty_samples_and_p95_under_one():
    """抓住仍按旧三参签名放行、漏掉槽位并发与 0 轮对照的验收。"""
    validate_status_probe(100, 20, 0.4, 3, 1.2)


def test_validate_status_probe_fastfails_when_history_turns_are_below_one_hundred():
    """抓住历史不足 100 轮却声称覆盖了 G5 长会话延迟。"""
    with pytest.raises(RuntimeError):
        validate_status_probe(99, 20, 0.4, 3, 1.2)


def test_validate_status_probe_fastfails_when_sample_count_is_below_twenty():
    """抓住用不足 20 个样本把 nearest-rank P95 算成看起来很快。"""
    with pytest.raises(RuntimeError):
        validate_status_probe(100, 19, 0.4, 3, 1.2)


def test_validate_status_probe_fastfails_when_p95_is_not_under_one():
    """抓住槽位并发下 run_status P95 已到 1s 仍放行。"""
    with pytest.raises(RuntimeError):
        validate_status_probe(100, 20, 1.0, 3, 1.2)


def test_validate_status_probe_fastfails_when_ratio_exceeds_one_point_five():
    """抓住 100 轮比 0 轮慢超过 1.5 倍（历史长度泄漏到状态查询）却被判通过。"""
    with pytest.raises(RuntimeError):
        validate_status_probe(100, 20, 0.4, 3, 1.6)


def test_capability_client_sets_site_header_without_authorization():
    profile = {
        "base_url": "http://127.0.0.1:9",
        "site": ALPHA,
        "api_key": "unused-api-key",
        "api_secret": "unused-api-secret",
    }
    client = capability_client(profile)
    try:
        assert isinstance(client, httpx.Client)
        assert client.headers["X-Frappe-Site-Name"] == ALPHA
        assert "Authorization" not in client.headers
    finally:
        client.close()


def test_measure_run_status_prechecks_once_then_returns_twenty_timed_samples():
    """抓住仍按 20 路打满验证栈 CPU，或把预检/连接建立时间算进 20 个计时样本。"""
    from infra.load_runs import measure_run_status

    cap = {"run_id": "probe-run", "capability": "probe-cap"}
    calls = []
    lock = threading.Lock()
    precheck_done = threading.Event()
    started_during_precheck = []
    in_flight = 0
    peak = 0
    timed_started = 0
    first_wave = threading.Barrier(3, timeout=2)

    def handler(request):
        nonlocal in_flight, peak, timed_started
        payload = json.loads(request.content)
        method = request.url.path.rsplit(".", 1)[-1]
        with lock:
            seq = len(calls)
            calls.append((method, payload))
        if seq == 0:
            time.sleep(0.02)
            precheck_done.set()
            return _frappe_run_status("Running", cap["run_id"])
        if not precheck_done.is_set():
            started_during_precheck.append(seq)
        with lock:
            timed_started += 1
            mine = timed_started
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            if mine <= 3:
                first_wave.wait()
            return _frappe_run_status("Running", cap["run_id"])
        finally:
            with lock:
                in_flight -= 1

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        samples = measure_run_status(client, cap, concurrency=3, sample_count=20)

    assert len(calls) == 21
    assert all(method == "run_status" and payload == cap for method, payload in calls)
    assert started_during_precheck == []
    assert peak == 3
    assert len(samples) == 20
    assert all(isinstance(item, float) and item >= 0 for item in samples)


def test_measure_run_status_fastfails_when_precheck_is_not_running():
    """抓住预检已不是 Running 仍继续打 20 个计时请求，把已结束运行的错误当延迟。"""
    from infra.load_runs import measure_run_status

    calls = []

    def handler(request):
        calls.append(request.url.path.rsplit(".", 1)[-1])
        return _frappe_run_status("Succeeded")

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):
            measure_run_status(
                client,
                {"run_id": "probe-run", "capability": "probe-cap"},
                concurrency=3,
                sample_count=20,
            )
    assert calls == ["run_status"]


def test_measure_run_status_can_collect_capacity_baseline_at_concurrency_twenty():
    """同一 helper 必须能以 20 并发采满 20 个 status 样本；该结果只入档，不能当成 P95 门。"""
    from infra.load_runs import measure_run_status

    cap = {"run_id": "probe-run", "capability": "probe-cap"}
    calls = []
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    capacity_wave = threading.Barrier(20, timeout=2)

    def handler(request):
        nonlocal in_flight, peak
        payload = json.loads(request.content)
        method = request.url.path.rsplit(".", 1)[-1]
        with lock:
            seq = len(calls)
            calls.append((method, payload))
        if seq == 0:
            return _frappe_run_status("Running", cap["run_id"])
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            capacity_wave.wait()
            return _frappe_run_status("Running", cap["run_id"])
        finally:
            with lock:
                in_flight -= 1

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        samples = measure_run_status(client, cap, concurrency=20, sample_count=20)

    assert len(calls) == 21
    assert all(method == "run_status" and payload == cap for method, payload in calls)
    assert peak == 20
    assert len(samples) == 20
    assert all(isinstance(item, float) and item >= 0 for item in samples)


def test_measure_http_ping_collects_capacity_baseline_and_requires_http_200():
    """抓住漏测空操作 ping 容量基线，或把非 200 响应计进网页层延迟。"""
    from infra.load_runs import measure_http_ping

    calls = []
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    capacity_wave = threading.Barrier(20, timeout=2)

    def handler(request):
        nonlocal in_flight, peak
        assert request.url.path == "/api/method/ping"
        with lock:
            calls.append((request.method, request.url.path))
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            capacity_wave.wait()
            return _frappe_ping()
        finally:
            with lock:
                in_flight -= 1

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        samples = measure_http_ping(client, concurrency=20, sample_count=20)

    assert len(calls) == 20
    assert all(path == "/api/method/ping" for _method, path in calls)
    assert peak == 20
    assert len(samples) == 20
    assert all(isinstance(item, float) and item >= 0 for item in samples)


def test_measure_http_ping_fastfails_when_a_sample_is_not_http_200():
    """抓住 backend 已 503 仍把失败 ping 当成容量样本。"""
    from infra.load_runs import measure_http_ping

    def handler(request):
        assert request.url.path == "/api/method/ping"
        return _frappe_ping(503)

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):
            measure_http_ping(client, concurrency=20, sample_count=20)


def test_report_status_probe_prints_diagnostics_then_fastfails_on_ratio(capsys):
    """失败现场若只抛一句 ratio 错误，就无法判断是历史变慢、0 轮抖动还是 20 并发容量。"""
    from infra.load_runs import report_status_probe

    history = _samples_with_p95(0.8, tail=0.99)
    zero = _samples_with_p95(0.5, tail=0.9)
    status_capacity = _samples_with_p95(1.2)
    ping_capacity = _samples_with_p95(0.4)

    with pytest.raises(RuntimeError):
        report_status_probe(
            history_samples=history,
            zero_samples=zero,
            concurrency=3,
            history_turns=100,
            status_capacity_samples=status_capacity,
            ping_capacity_samples=ping_capacity,
        )

    payload = _stderr_json(capsys)
    assert payload["history_samples"] == history
    assert payload["zero_samples"] == zero
    assert payload["status_capacity_samples"] == status_capacity
    assert payload["ping_capacity_samples"] == ping_capacity
    assert payload["history_p95"] == 0.8
    assert payload["zero_p95"] == 0.5
    assert payload["status_capacity_p95"] == 1.2
    assert payload["ping_capacity_p95"] == 0.4
    assert payload["concurrency"] == 3
    assert payload["history_turns"] == 100
    assert payload["ratio"] == pytest.approx(0.8 / 0.5)


def test_report_status_probe_ratio_uses_history_p95_over_zero_turn_p95(capsys):
    """抓住用均值、最大值或颠倒除法输出 ratio，把历史泄漏或基线抖动判错。"""
    from infra.load_runs import report_status_probe

    history = _samples_with_p95(0.8, tail=0.99)
    zero = _samples_with_p95(0.5, tail=0.9)
    assert percentile95(history) == 0.8
    assert percentile95(zero) == 0.5
    assert max(history) != percentile95(history)
    assert max(zero) != percentile95(zero)

    with pytest.raises(RuntimeError):
        report_status_probe(
            history_samples=history,
            zero_samples=zero,
            concurrency=3,
            history_turns=100,
            status_capacity_samples=_samples_with_p95(1.2),
            ping_capacity_samples=_samples_with_p95(0.4),
        )

    payload = _stderr_json(capsys)
    assert payload["ratio"] == percentile95(history) / percentile95(zero)
    assert payload["ratio"] == pytest.approx(1.6)


def test_report_status_probe_rejects_zero_turn_p95_of_zero_after_printing_diagnostics(capsys):
    """抓住 0 轮 P95=0 时除零得到 inf，把任意历史延迟都判成通过。"""
    from infra.load_runs import report_status_probe

    with pytest.raises(RuntimeError):
        report_status_probe(
            history_samples=_samples_with_p95(0.4),
            zero_samples=_samples_with_p95(0.0),
            concurrency=3,
            history_turns=100,
            status_capacity_samples=_samples_with_p95(0.4),
            ping_capacity_samples=_samples_with_p95(0.4),
        )

    payload = _stderr_json(capsys)
    assert payload["zero_p95"] == 0.0
    assert payload["ratio"] is None


def test_report_status_probe_does_not_gate_on_capacity_baseline_p95():
    """20 并发 status/ping 的 P95 即使超过 1s 也不能单独失败，那是验证栈容量而不是 run_status 门。"""
    from infra.load_runs import report_status_probe

    report_status_probe(
        history_samples=_samples_with_p95(0.48),
        zero_samples=_samples_with_p95(0.40),
        concurrency=3,
        history_turns=100,
        status_capacity_samples=_samples_with_p95(1.2),
        ping_capacity_samples=_samples_with_p95(1.2),
    )


class _QueryTimeoutError(Exception):
    pass


class _FakeCleanupSite:
    def __init__(self):
        self.runs = {}
        self.conversations = {}
        self.events = []
        self.proposals = []
        self.commits = 0

    def seed(self, descriptors):
        for item in descriptors:
            self.runs[item["run_id"]] = {
                "name": item["run_id"],
                "conversation": item["conversation"],
            }
            self.events.append({"name": f"evt-{item['run_id']}", "run": item["run_id"]})
            self.conversations.setdefault(
                item["conversation"], {"name": item["conversation"]}
            )

    def leftover_for(self, descriptors):
        run_ids = {item["run_id"] for item in descriptors}
        conversations = {item["conversation"] for item in descriptors}
        return {
            "runs": sum(name in self.runs for name in run_ids),
            "conversations": sum(name in self.conversations for name in conversations),
            "events": sum(1 for event in self.events if event["run"] in run_ids),
            "proposals": sum(
                1 for row in self.proposals if row["model_run"] in run_ids
            ),
        }

    def execute(self, site, body, payload):
        printed = []
        namespace = {
            "frappe": self._frappe(),
            "PAYLOAD": payload,
            "json": json,
            "print": printed.append,
        }
        try:
            exec(body, namespace, namespace)
        except _QueryTimeoutError as error:
            raise RuntimeError(f"site command failed for {site}: QueryTimeoutError") from error
        if not printed:
            raise RuntimeError(f"site command returned no result for {site}")
        return json.loads(printed[-1])

    def _frappe(self):
        site = self

        class DB:
            def count(self, doctype, filters=None):
                return len(site._rows(doctype, filters or {}))

            def exists(self, doctype, name):
                table = site._table(doctype)
                if isinstance(table, dict):
                    return name if name in table else None
                return name if any(row["name"] == name for row in table) else None

            def delete(self, doctype, filters):
                site._remove(doctype, filters)

            def commit(self):
                site.commits += 1

        class Frappe:
            db = DB()

            def set_user(self, user):
                return None

            def delete_doc(self, doctype, name, ignore_permissions=False):
                raise _QueryTimeoutError("QueryTimeoutError")

        return Frappe()

    def _table(self, doctype):
        tables = {
            "DS Model Run": self.runs,
            "DS Conversation": self.conversations,
            "DS Run Event": self.events,
            "DS Operation Proposal": self.proposals,
        }
        if doctype not in tables:
            raise RuntimeError(f"unknown doctype {doctype}")
        return tables[doctype]

    def _as_rows(self, doctype):
        table = self._table(doctype)
        return list(table.values()) if isinstance(table, dict) else list(table)

    def _match(self, row, filters):
        for key, expected in filters.items():
            value = row.get(key)
            if isinstance(expected, (list, tuple)) and len(expected) == 2 and expected[0] == "in":
                if value not in expected[1]:
                    return False
            elif value != expected:
                return False
        return True

    def _rows(self, doctype, filters):
        return [row for row in self._as_rows(doctype) if self._match(row, filters)]

    def _remove(self, doctype, filters):
        matched = self._rows(doctype, filters)
        if doctype == "DS Model Run":
            for row in matched:
                self.runs.pop(row["name"], None)
        elif doctype == "DS Conversation":
            for row in matched:
                self.conversations.pop(row["name"], None)
        elif doctype == "DS Run Event":
            self.events = [row for row in self.events if row not in matched]
        else:
            self.proposals = [row for row in self.proposals if row not in matched]


def test_cleanup_site_uses_one_transaction_to_batch_delete_hundred_exact_hist_runs(
    monkeypatch,
):
    conversation = "s7-hist-conv-tdd"
    descriptors = [
        {"run_id": f"s7-hist-run-tdd-{index:03d}", "conversation": conversation}
        for index in range(100)
    ]
    store = _FakeCleanupSite()
    store.seed(descriptors)
    store.seed([{"run_id": "keeper-run", "conversation": "keeper-conv"}])
    captured = []

    def fake_site_json(site, body, payload=None, timeout=60):
        captured.append({"site": site, "body": body, "payload": payload})
        return store.execute(site, body, payload)

    monkeypatch.setattr("infra.load_runs._site_json", fake_site_json)

    result = _cleanup_site(ALPHA, descriptors)

    assert len(descriptors) == 100
    assert captured[0]["site"] == ALPHA
    assert captured[0]["payload"] == descriptors
    leftover = store.leftover_for(descriptors)
    assert leftover["proposals"] == 0
    assert leftover == {"runs": 0, "conversations": 0, "events": 0, "proposals": 0}
    assert result == {"runs": 0, "conversations": 0}
    assert store.commits == 1
    assert "keeper-run" in store.runs
    assert "keeper-conv" in store.conversations


def test_plan_status_history_builds_independent_zero_and_hundred_turn_sessions():
    """抓住没有真正建立独立 0 轮对照或复用 100 轮会话的错误。"""
    zero = _plan_status_history(0)
    hundred = _plan_status_history(100)
    bound = {
        zero["conversation"]: zero["runtime_session"],
        hundred["conversation"]: hundred["runtime_session"],
    }

    assert zero["conversation"] != hundred["conversation"]
    assert zero["runtime_session"] != hundred["runtime_session"]
    assert len(bound) == 2
    assert bound[zero["conversation"]] == zero["runtime_session"]
    assert bound[hundred["conversation"]] == hundred["runtime_session"]
    assert zero["runs"] == []
    assert len(hundred["runs"]) == 100
    assert all(item["conversation"] == hundred["conversation"] for item in hundred["runs"])

    run_ids = [item["run_id"] for item in hundred["runs"]]
    request_ids = [item["request_id"] for item in hundred["runs"]]
    assert len(set(run_ids)) == 100
    assert len(set(request_ids)) == 100
