"""One command builds the four-Site development stack from nothing and one takes it down.

The provisioning scripts under infra/ are fail-closed: each refuses when its Site, user or
credential file already exists. The driver above them must therefore know what is done, must
check the stack agrees, and must stop - not re-run - when the two disagree.
"""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dsherp import admin, deploy_env
from infra import dev_stack

BENCH_PYTHON = "/home/frappe/frappe-bench/env/bin/python"


def _done(command, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def _private(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


class FakeHost:
    """Stands in for subprocess.run. Sites, users, settings and profile files appear only when
    the step that makes them has run, so ordering and skipping are observable."""

    RUNNING = "agent-egress\nbackend\nbeta-backend\ndb\nfrontend\nplatform-backend\nplatform-frontend\nredis\n"

    def __init__(self, runtime, *, images=True, secrets_readable=True, db_up=True,
                 runtime_venv_ok=True, running=None):
        self.runtime = Path(runtime)
        self.calls = []
        self.images = images
        self.secrets_readable = secrets_readable
        self.db_up = db_up
        self.runtime_venv_ok = runtime_venv_ok
        self.running = self.RUNNING if running is None else running
        self.sites = set()
        self.facts = set()
        self.volumes = set()
        self.backup_present = False

    # what a host script or a control container leaves behind
    def _effect(self, script, argv):
        r = self.runtime
        if script == "run_validation_provision.py":
            self.sites.add("dsherp-validation.localhost")
            # Real api_keys are 15-24 characters; a two-letter stand-in collides with ordinary
            # words (an "rk" matches inside "worker") and the scan is substring-based.
            users = {"reader": {"user": "dsherp-reader@example.invalid", "api_key": "READER-KEY-VALUE-0001",
                                "api_secret": "READER-SECRET-VALUE"},
                     "denied": {"user": "dsherp-denied@example.invalid", "api_key": "DENIED-KEY-VALUE-0002",
                                "api_secret": "DENIED-SECRET-VALUE"}}
            _private(r / "erp-users.json", users)
            _private(r / "erp-reader.json", users["reader"])
            _private(r / "erp-denied.json", users["denied"])
        elif script == "provision_validation_company.py":
            site = "dsherp-validation.localhost" if argv[-1] == "alpha" else "dsherp-beta.localhost"
            self.facts.add(f"{site}:setup")
        elif script == "run_identity_seed.py":
            kind = argv[-1]
            user = "member@example.invalid" if kind == "platform" else "beta-reader@example.invalid"
            self.facts.add(f"dsherp-{kind}.localhost:User:{user}")
            _private(r / f"{kind}-users.json", {"operator": {"password": f"{kind.upper()}-PASSWORD-VALUE"}})
        elif script == "setup_platform.py":
            self.facts.add("dsherp-platform.localhost:setup")
        elif script == "bind_identity.py":
            self.facts.update({"dsherp-platform.localhost:DS Enterprise:alpha",
                               "dsherp-platform.localhost:DS Enterprise:beta"})
        elif script == "provision_desk_oauth.py":
            self.facts.add("dsherp-platform.localhost:OAuth Client:DSHERP alpha Desk")
        elif script == "provision_daily_agent.py":
            self.facts.add("dsherp-platform.localhost:DS Enterprise:daily")
            _private(r / "context-worker-daily.json", {"api_secret": "DAILY-RUNTIME-SECRET"})
        elif script == "initialize_daily_synthetic.py":
            self.facts.add("dsherp-daily.localhost:User:daily-operator@example.invalid")
        elif script == "provision_context_worker.py":
            self.facts.add("dsherp-validation.localhost:conf:dsherp_runtime_user")
            _private(r / "context-worker.json", {"api_secret": "ALPHA-RUNTIME-SECRET"})
        elif script == "provision_context_writer.py":
            self.facts.add("dsherp-validation.localhost:User:dsherp-writer@example.invalid")
            _private(r / "context-writer.json", {"password": "WRITER-PASSWORD-VALUE"})
        elif script == "merge_context_worker_profiles.py":
            _private(r / "context-worker-sites.json", {"slots": 3, "sites": []})
        elif script == "provision_preview_operator.py":
            self.facts.add("dsherp-beta.localhost:User:dsherp-preview@example.invalid")
            _private(r / "preview-operator.json", {"password": "PREVIEW-PASSWORD-VALUE"})
        elif script == "provision_configuration_preview.py":
            self.facts.add("dsherp-validation.localhost:conf:dsherp_configuration_preview")
            _private(r / "configuration-preview.json", {"secret": "PAIR-SECRET-VALUE"})
        elif script == "provision_eval_identity.py":
            # Two identities: the business operator, and a configurator for the configuration
            # domain (which reads DocType definitions an ordinary business user cannot).
            _private(r / "eval-users.json", {"site": "dsherp-daily.localhost",
                                             "operator": {"api_secret": "EVAL-OPERATOR-SECRET"},
                                             "configurator": {"api_secret": "EVAL-CONFIG-SECRET"}})
        elif script == "provision_eval_fixtures.py":
            self.facts.add("dsherp-daily.localhost:Sales Order:DSHERP-EVAL-SO-01")
        elif script in ("provision_alpha_doctype_policies.py", "provision_manufacturing_fixture.py"):
            pass
        else:
            raise AssertionError("unexpected host script " + script)

    def _probe(self, site, body):
        if "is_setup_complete" in body:
            return f"{site}:setup" in self.facts
        for doctype in ("User", "DS Enterprise"):
            match = re.search(r"exists\('%s', '([^']+)'" % doctype, body)
            if match:
                return f"{site}:{doctype}:{match.group(1)}" in self.facts
        match = re.search(r"'OAuth Client', \{'app_name': '([^']+)'", body)
        if match:
            return f"{site}:OAuth Client:{match.group(1)}" in self.facts
        match = re.search(r"frappe\.conf\.get\('([^']+)'\)", body)
        if match:
            return f"{site}:conf:{match.group(1)}" in self.facts
        match = re.search(r"'Sales Order', \{'po_no': '([^']+)'", body)
        if match:
            return f"{site}:Sales Order:{match.group(1)}" in self.facts
        raise AssertionError("unexpected probe:\n" + body)

    def __call__(self, command, **kwargs):
        argv = list(command)
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return _done(argv, 0 if self.images else 1)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return _done(argv, 0 if argv[3] in self.volumes else 1)
        if argv[:3] == ["docker", "volume", "rm"]:
            self.volumes.discard(argv[3])
            return _done(argv)
        if argv[:2] == ["docker", "run"]:
            ready = self.runtime_venv_ok and dev_stack.AGENT_RUNTIME_VOLUME in self.volumes
            return _done(argv, 0 if ready else 1, "", "" if ready else "ModuleNotFoundError: deepseek_harness")
        if argv[0] == "sh" and argv[1].endswith("prepare_agent_runtime.sh"):
            self.volumes.add(dev_stack.AGENT_RUNTIME_VOLUME)
            return _done(argv)
        if argv[0] == "sh":
            return _done(argv)
        if argv[0] == sys.executable and "/infra/" in argv[1]:
            self._effect(Path(argv[1]).name, argv)
            return _done(argv, stdout="ok\n")
        assert argv[:2] == ["docker", "compose"], argv
        if "run" in argv:
            tail = argv[argv.index("run") + 1:]
            if "--entrypoint" in tail:
                return _done(argv, 0 if self.secrets_readable else 1,
                             "readable\n" if self.secrets_readable else "", "Permission denied")
            self.sites.add({"platform-provision": "dsherp-platform.localhost",
                            "beta-provision": "dsherp-beta.localhost",
                            "daily-provision": "dsherp-daily.localhost",
                            "test-provision": "dsherp-test.localhost",
                            "platform-test-provision": "dsherp-platform-test.localhost"}[tail[-1]])
            return _done(argv)
        if "down" in argv:
            self.sites.clear()
            self.facts.clear()
            return _done(argv)
        if "up" in argv:
            return _done(argv)
        if "ps" in argv:
            return _done(argv, stdout=self.running)
        index = argv.index("exec")
        rest = argv[index + 3:]
        joined = " ".join(rest)
        if "mariadb-admin" in joined:
            return _done(argv, 0 if self.db_up else 1)
        if "redis-cli" in joined:
            return _done(argv, stdout="PONG\n")
        if "socket.create_connection" in joined:
            return _done(argv)
        if "site_config.json" in joined and rest[:2] == ["sh", "-c"]:
            site = re.search(r"sites/([^/]+)/site_config.json", joined).group(1)
            return _done(argv, stdout="present\n" if site in self.sites else "absent\n")
        if "private/backups" in joined:
            return _done(argv, stdout="yes\n" if self.backup_present else "no\n")
        if rest[:2] == ["bench", "--site"] and "backup" in rest:
            self.backup_present = True
            return _done(argv)
        if rest[:2] == ["bench", "--site"] and "clear-cache" in rest:
            return _done(argv)
        if rest == [BENCH_PYTHON, "-"]:
            body = kwargs["input"]
            site = re.search(r"frappe\.init\(site='([^']+)'", body).group(1)
            return _done(argv, stdout=json.dumps(self._probe(site, body)) + "\n")
        raise AssertionError("unexpected command: " + " ".join(argv))


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.delenv("DSHERP_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DSHERP_SECRETS_DIR", raising=False)
    resolved = deploy_env.settings({"DSHERP_ENV": "dev", "DSHERP_AGENT_UID": "1000",
                                    "DSHERP_AGENT_GID": "1000"}, root=tmp_path)
    host = FakeHost(tmp_path / ".runtime")
    clock = {"now": 0.0}
    built = dev_stack.Stack(resolved, root=tmp_path, runner=host,
                            resolve=lambda name, port: [(None, None, None, None, ("127.0.0.1", 0))],
                            http=lambda request, timeout=10: _Response(200),
                            sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
                            clock=lambda: clock["now"])
    built.host = host
    return built


def _mutations(host):
    """The calls that change the stack: host scripts and control containers, in order."""
    out = []
    for argv in host.calls:
        if argv[0] == sys.executable:
            out.append(Path(argv[1]).name + "".join(" " + a for a in argv[2:]))
        elif argv[0] == "sh" and argv[1].endswith(".sh"):
            out.append(Path(argv[1]).name)
        elif "run" in argv and argv[:2] == ["docker", "compose"] and "--entrypoint" not in argv:
            out.append("compose run " + argv[-1])
        elif "exec" in argv and ("backup" in argv or "clear-cache" in argv):
            out.append(" ".join(argv[argv.index("exec") + 3:]))
    return out


EXPECTED_ORDER = [
    "prepare_agent_runtime.sh", "prepare_dev_backup_volumes.sh",
    "run_validation_provision.py", "provision_validation_company.py --target alpha",
    "compose run platform-provision", "run_identity_seed.py platform", "setup_platform.py",
    "compose run beta-provision", "provision_validation_company.py --target beta",
    "run_identity_seed.py beta",
    "bind_identity.py", "provision_desk_oauth.py",
    "compose run daily-provision", "provision_daily_agent.py", "initialize_daily_synthetic.py",
    "provision_context_worker.py", "provision_context_writer.py", "merge_context_worker_profiles.py",
    "provision_preview_operator.py", "provision_configuration_preview.py",
    "provision_alpha_doctype_policies.py --site dsherp-validation.localhost --policy-set manufacturing",
    "provision_alpha_doctype_policies.py --site dsherp-daily.localhost --policy-set manufacturing",
    "provision_alpha_doctype_policies.py --site dsherp-beta.localhost",
    "provision_manufacturing_fixture.py --site dsherp-validation.localhost",
    "provision_manufacturing_fixture.py --site dsherp-daily.localhost",
    "provision_eval_identity.py --site dsherp-daily.localhost",
    "provision_eval_fixtures.py --site dsherp-daily.localhost",
    "bench --site dsherp-validation.localhost clear-cache",
    "bench --site dsherp-daily.localhost clear-cache",
    "bench --site dsherp-beta.localhost clear-cache",
    "bench --site dsherp-daily.localhost backup --with-files",
    "compose run test-provision", "compose run platform-test-provision",
]


def test_a_fresh_host_runs_every_step_in_the_mapped_order_and_records_each(stack):
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == EXPECTED_ORDER
    assert report["skipped"] == [] and report["ran"] == [step.name for step in dev_stack.STEPS]
    ledger = json.loads(stack.ledger_path.read_text())
    assert ledger["format"] == 1 and set(ledger["steps"]) == {step.name for step in dev_stack.STEPS}
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", row["finished_at"])
               for row in ledger["steps"].values())
    assert ledger["steps"]["validation-site"]["produces"] == ["erp-users.json", "erp-reader.json", "erp-denied.json"]


def test_a_second_run_changes_nothing_and_skips_every_step(stack):
    dev_stack.provision(stack)
    stack.host.calls.clear()
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == [] and report["ran"] == []
    assert len(report["skipped"]) == len(dev_stack.STEPS)


def test_a_step_whose_result_is_only_half_there_is_reported_as_half_there(stack):
    """The Site exists but its credential file is gone. That is not "not done" - re-running the
    fail-closed provisioner would just hit 'Site already exists'. Say which half is missing."""
    dev_stack.provision(stack)
    (stack.runtime / "erp-users.json").unlink()
    stack.host.calls.clear()
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    message = str(error.value)
    assert "validation-site" in message and "down --volumes" in message
    assert "只存在一部分" in message and "[False, True]" in message
    assert "run_validation_provision.py" not in " ".join(_mutations(stack.host))


def test_a_recorded_step_whose_result_vanished_entirely_stops_and_names_the_step(stack):
    dev_stack.provision(stack)
    stack.host.sites.discard("dsherp-validation.localhost")
    for name in ("erp-users.json", "erp-reader.json", "erp-denied.json"):
        (stack.runtime / name).unlink()
    stack.host.calls.clear()
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    message = str(error.value)
    assert "validation-site" in message and "台账记录" in message and "down --volumes" in message
    assert "只存在一部分" not in message
    assert "run_validation_provision.py" not in " ".join(_mutations(stack.host))


def test_a_result_that_exists_without_a_record_is_never_reprovisioned(stack):
    stack.host.sites.add("dsherp-validation.localhost")  # a Site nobody recorded
    for name in ("erp-users.json", "erp-reader.json", "erp-denied.json"):
        _private(stack.runtime / name, {})
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert "validation-site" in str(error.value)
    assert not any(m.startswith("run_validation_provision") for m in _mutations(stack.host))


def test_rerun_safe_steps_are_simply_rerun_when_ledger_and_stack_disagree(stack):
    dev_stack.provision(stack)
    (stack.runtime / "context-worker-sites.json").unlink()
    stack.host.calls.clear()
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == ["merge_context_worker_profiles.py"] and report["ran"] == ["worker-profile"]


def test_a_runtime_volume_whose_venv_never_built_is_not_treated_as_done(stack):
    """`docker volume create` runs before the pip install, so the volume can exist and be empty.
    The probe asks the venv the same question the script itself asks."""
    dev_stack.provision(stack)
    stack.host.runtime_venv_ok = False
    stack.host.calls.clear()
    report = dev_stack.provision(stack)
    assert report["ran"] == ["agent-runtime-volume"]
    assert _mutations(stack.host) == ["prepare_agent_runtime.sh"]
    probes = [c for c in stack.host.calls if c[:2] == ["docker", "run"]]
    assert probes and all("--pull=never" in c and dev_stack.AGENT_RUNTIME_VOLUME in " ".join(c) for c in probes)


def test_the_runtime_probe_never_creates_the_volume_it_is_asking_about(stack):
    """`docker run -v <name>:...` would create an absent volume as a side effect; the probe
    must ask whether the volume exists first."""
    report = dev_stack.provision(stack, skip=tuple(s.name for s in dev_stack.STEPS if s.name != "agent-runtime-volume"))
    order = [c for c in stack.host.calls if c[:3] == ["docker", "volume", "inspect"] or c[:2] == ["docker", "run"]]
    assert order and order[0][:3] == ["docker", "volume", "inspect"]
    assert report["ran"] == ["agent-runtime-volume"]


def test_provisioning_refuses_before_the_backends_are_up(stack):
    """prepare_dev_backup_volumes.sh docker-execs into two running containers; a driver that
    starts provisioning against a stopped stack fails deep inside a shell script instead."""
    stack.host.running = "db\nredis\n"
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert "platform-backend" in str(error.value) and "up" in str(error.value)
    assert _mutations(stack.host) == []


def test_skipping_the_runtime_volume_leaves_it_out_of_the_run_and_the_ledger(stack):
    dev_stack.provision(stack, skip=("agent-runtime-volume",))
    assert "prepare_agent_runtime.sh" not in _mutations(stack.host)
    assert "agent-runtime-volume" not in json.loads(stack.ledger_path.read_text())["steps"]


def test_a_corrupt_ledger_is_reported_not_rebuilt(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True, parents=True)
    stack.ledger_path.write_text("{not json")
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert str(stack.ledger_path) in str(error.value) and not stack.host.calls


def test_the_ledger_is_written_as_privately_as_every_other_runtime_file(stack):
    dev_stack.provision(stack)
    assert stat.S_IMODE(stack.ledger_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(stack.runtime.stat().st_mode) & 0o077 == 0
    assert not list(stack.runtime.glob(".dev-stack.json.*")), "no temporary file left behind"


# ---- up / status ---------------------------------------------------------------------

def test_up_refuses_when_a_host_name_does_not_resolve_to_loopback_and_says_which_line_to_add(stack):
    def resolve(name, port):
        if name == "preview.localhost":
            raise OSError("Name or service not known")
        return [(None, None, None, None, ("127.0.0.1", 0))]

    stack.resolve = resolve
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    assert "preview.localhost" in str(error.value) and dev_stack.HOSTS_LINE in str(error.value)
    assert not any(argv[:2] == ["docker", "compose"] for argv in stack.host.calls)


def test_up_refuses_when_a_pinned_image_is_missing_and_names_the_pull_command(stack):
    stack.host.images = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    for image in dev_stack.IMAGES:
        assert f"docker pull {image}" in str(error.value)
    assert not any("up" in argv for argv in stack.host.calls if argv[:2] == ["docker", "compose"])


def test_up_creates_the_control_secrets_privately_and_reports_only_their_names(stack):
    report = dev_stack.up(stack)
    directory = stack.runtime / "control"
    assert set(report["secrets"]["created"]) == set(dev_stack.CONTROL_SECRETS)
    assert set(dev_stack.CONTROL_SECRETS) == set(admin.CONTROL_SECRETS_BY_ENV["dev"]) | {
        "platform_admin_password", "beta_admin_password"}
    for name in dev_stack.CONTROL_SECRETS:
        path = directory / name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(path.read_text().strip()) >= 32
    values = [(directory / name).read_text().strip() for name in dev_stack.CONTROL_SECRETS]
    assert not any(value in json.dumps(report) for value in values)
    assert dev_stack.up(stack)["secrets"]["created"] == []  # never regenerated


def test_up_waits_for_db_redis_and_every_backend_before_declaring_ready(stack):
    dev_stack.up(stack)
    compose = [argv for argv in stack.host.calls if argv[:2] == ["docker", "compose"]]
    started = next(i for i, argv in enumerate(compose) if "up" in argv)
    after = [" ".join(argv) for argv in compose[started + 1:]]
    assert any("mariadb-admin ping" in line and "MYSQL_PWD=$(cat /run/secrets/db_root_password)" in line
               for line in after)
    assert not any("MYSQL_PWD=" in line and "$(cat" not in line for line in after)  # never a literal
    assert any("redis-cli ping" in line for line in after)
    for service in dev_stack.BACKENDS:
        assert any(f"exec -T {service}" in line and "socket.create_connection" in line for line in after)


def test_up_gives_up_on_a_database_that_never_answers(stack):
    stack.host.db_up = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    assert "db" in str(error.value) and "logs" in str(error.value)


def test_up_stops_when_the_frappe_user_cannot_read_the_secrets_and_gives_the_linux_remedy(stack):
    stack.host.secrets_readable = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack, provision_too=True)
    assert "chown 1000" in str(error.value)
    assert not any(argv[0] == sys.executable for argv in stack.host.calls)  # nothing was provisioned


def test_up_with_provision_runs_the_steps_after_the_stack_is_ready(stack):
    report = dev_stack.up(stack, provision_too=True, skip_runtime_volume=True)
    assert report["provision"]["ran"][0] == "backup-volumes"
    assert "agent-runtime-volume" not in report["provision"]["ran"]


def test_a_stack_only_ever_points_at_this_repository_and_this_project(stack, tmp_path, monkeypatch):
    monkeypatch.setenv("DSHERP_RUNTIME_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.Stack(stack.resolved, root=stack.root, runner=stack.host)
    assert "DSHERP_RUNTIME_DIR" in str(error.value)
    monkeypatch.delenv("DSHERP_RUNTIME_DIR")
    with pytest.raises(dev_stack.Fault):
        dev_stack.Stack({**stack.resolved, "project": "somewhere-else"}, root=stack.root, runner=stack.host)
    with pytest.raises(dev_stack.Fault):
        dev_stack.Stack({**stack.resolved, "env": "prod"}, root=stack.root, runner=stack.host)


def test_status_reports_ledger_services_and_pings_without_any_secret(stack):
    dev_stack.up(stack, provision_too=True, skip_runtime_volume=True)
    report = dev_stack.status(stack)
    assert set(report) == {"ledger", "running_services", "pings", "hosts"}
    assert report["pings"] == {"alpha": 200, "platform": 200, "beta": 200, "daily": 200}
    assert "backend" in report["running_services"] and "validation-site" in report["ledger"]
    assert dev_stack.leaked_secrets(stack.runtime, [json.dumps(report)], as_text=True) == []


# ---- down / scan-artifacts / CLI ------------------------------------------------------

def test_down_with_volumes_removes_exactly_the_produced_files_and_the_ledger(stack):
    dev_stack.up(stack, provision_too=True)
    keep = [stack.runtime / "control" / "db_root_password",
            stack.runtime / "business-sessions" / "s1" / "note",
            stack.runtime / "agent-worker.log"]
    for path in keep[1:]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep")
    produced = [stack.runtime / name for name in dev_stack.produced_files()]
    assert all(path.exists() for path in produced) and stack.ledger_path.exists()
    report = dev_stack.down(stack, volumes=True)
    assert not any(path.exists() for path in produced) and not stack.ledger_path.exists()
    assert all(path.exists() for path in keep)
    assert set(report["removed"]) == set(dev_stack.produced_files()) | {dev_stack.LEDGER_NAME}
    compose = [" ".join(argv) for argv in stack.host.calls if argv[:2] == ["docker", "compose"]]
    assert any("down --remove-orphans -v" in line for line in compose)
    assert ["docker", "volume", "rm", dev_stack.AGENT_RUNTIME_VOLUME] in stack.host.calls
    assert dev_stack.produced_files() == [
        "erp-users.json", "erp-reader.json", "erp-denied.json", "platform-users.json", "beta-users.json",
        "context-worker-daily.json", "context-worker.json", "context-writer.json",
        "context-worker-sites.json", "preview-operator.json", "configuration-preview.json",
        "eval-users.json"]


def test_down_without_volumes_only_stops_containers(stack):
    dev_stack.up(stack, provision_too=True)
    dev_stack.down(stack, volumes=False)
    assert stack.ledger_path.exists() and (stack.runtime / "erp-users.json").exists()
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in stack.host.calls)


def test_down_refuses_while_the_resident_worker_is_alive(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True, parents=True)
    (stack.runtime / dev_stack.WORKER_PID).write_text(str(os.getpid()))
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.down(stack, volumes=True)
    assert "launchctl bootout" in str(error.value) and dev_stack.WORKER_LABEL in str(error.value)
    assert not any(argv[:2] == ["docker", "compose"] for argv in stack.host.calls)


def test_a_dead_pid_file_does_not_block_down(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True, parents=True)
    (stack.runtime / dev_stack.WORKER_PID).write_text("999999999")
    dev_stack.down(stack, volumes=False)


def test_the_ledger_never_contains_a_secret_value(stack):
    dev_stack.up(stack, provision_too=True)
    assert dev_stack.leaked_secrets(stack.runtime, [stack.ledger_path]) == []
    text = stack.ledger_path.read_text()
    assert "SECRET-VALUE" not in text and "PASSWORD-VALUE" not in text
    for name in dev_stack.CONTROL_SECRETS:
        assert (stack.runtime / "control" / name).read_text().strip() not in text


def test_scan_artifacts_names_the_leaking_secret_and_the_file_but_never_the_value(stack, tmp_path, capsys, monkeypatch):
    dev_stack.up(stack, provision_too=True)
    log = tmp_path / "compose.log"
    log.write_text("gunicorn ... token READER-KEY-VALUE-0001:READER-SECRET-VALUE ...\n"
                   + (stack.runtime / "control" / "db_root_password").read_text())
    clean = tmp_path / "junit.xml"
    clean.write_text("<testsuite tests='1'/>")
    found = dev_stack.leaked_secrets(stack.runtime, [log, clean])
    # one name per value: erp-reader.json sorts before erp-users.json and holds the same secret
    assert found == [("control/db_root_password", str(log)), ("erp-reader.json:api_key", str(log)),
                     ("erp-reader.json:api_secret", str(log))]
    monkeypatch.setattr(dev_stack.deploy_env, "settings", lambda *a, **k: stack.resolved)
    monkeypatch.setattr(dev_stack, "Stack", lambda resolved: stack)
    code = dev_stack.main(["scan-artifacts", str(log), str(clean)])
    out = capsys.readouterr()
    assert code == 1 and "erp-reader.json:api_secret" in out.out
    assert "READER-SECRET-VALUE" not in out.out + out.err
    assert dev_stack.main(["scan-artifacts", str(clean)]) == 0


def test_native_tests_hands_the_run_to_the_shared_driver_with_this_stack_s_runner(stack, tmp_path, monkeypatch):
    """The driver owns how a native run is judged (dsherp/native_tests.py); dev_stack only
    supplies the resolved environment, the output directory and the runner."""
    seen = {}
    monkeypatch.setattr(dev_stack.native_tests, "run_native_tests",
                        lambda resolved, **options: seen.update(resolved=resolved, **options) or {"ok": True})
    assert dev_stack.run_native_tests(stack.resolved, out=tmp_path / "native", runner=stack.host) == {"ok": True}
    assert seen["resolved"] is stack.resolved and seen["out"] == tmp_path / "native" and seen["runner"] is stack.host


def test_provisioning_the_two_test_sites_is_a_recorded_rerun_safe_step(stack):
    """`bench run-tests` truncates tables, so it may never touch the four development Sites.
    The throwaway Sites are made by the same driver that makes the stack, last."""
    step = dev_stack.STEPS[-1]
    assert step.name == "test-sites" and step.rerun_safe
    dev_stack.provision(stack)
    assert _mutations(stack.host)[-2:] == ["compose run test-provision", "compose run platform-test-provision"]
    assert "test-sites" in json.loads(stack.ledger_path.read_text())["steps"]


def test_the_cli_maps_a_fault_to_exit_code_2_and_prints_it_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(dev_stack.deploy_env, "settings",
                        lambda *a, **k: {"env": "prod", "project": "dsherp"})
    assert dev_stack.main(["status"]) == 2
    assert "DSHERP_ENV=dev" in capsys.readouterr().err


def test_the_driver_runs_as_a_plain_file_path_not_only_as_a_module():
    """README tells the operator `python infra/dev_stack.py ...`, which puts infra/ on sys.path
    rather than the repository root."""
    root = Path(__file__).resolve().parents[1]
    for command in ([sys.executable, "infra/dev_stack.py", "--help"],
                    [sys.executable, "-m", "infra.dev_stack", "--help"]):
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=120)
        assert result.returncode == 0, command[1:3] + [result.stderr[-400:]]
        for name in ("secrets", "up", "provision", "status", "native-tests", "down", "scan-artifacts"):
            assert name in result.stdout, (command[1:3], name)


def test_scan_artifacts_says_which_files_were_missing_instead_of_crashing(stack, tmp_path, capsys, monkeypatch):
    """nightly calls this with `if: always()`, so a step that failed before writing its
    artifact leaves an unexpanded glob behind. Say so; never hide it behind a traceback."""
    dev_stack.up(stack, provision_too=True)
    clean = tmp_path / "junit.xml"
    clean.write_text("<testsuite tests='1'/>")
    monkeypatch.setattr(dev_stack.deploy_env, "settings", lambda *a, **k: stack.resolved)
    monkeypatch.setattr(dev_stack, "Stack", lambda resolved: stack)
    code = dev_stack.main(["scan-artifacts", str(tmp_path / "work/junit-*.xml"), str(clean)])
    out = capsys.readouterr().out
    assert code == 0 and "不存在，未扫描" in out and "junit-*.xml" in out


def test_a_failed_step_reports_enough_to_diagnose_it_and_labels_which_stream_said_what(stack):
    """Eight lines was not enough. The first nightly died 11 minutes into provisioning on a
    JSONDecodeError whose traceback tail filled the whole quota, so the report showed the json
    module's frames and nothing about which call had returned nothing - not the command, not
    the script, not which stream was empty. A failure nobody can read is a failure nobody can
    fix, and on CI there is no second chance to look."""
    def failing(command, **options):
        return subprocess.CompletedProcess(
            command, 2,
            "".join(f"out {i}\n" for i in range(40)),
            "".join(f"err {i}\n" for i in range(40)))
    stack.runner = failing
    with pytest.raises(dev_stack.Fault) as error:
        stack.run(["docker", "compose", "-p", "x", "-f", "y", "up"], timeout=5)
    message = str(error.value)
    assert "err 39" in message and "out 39" in message, "两个流的结尾都要看得到"
    assert "err 10" in message and "out 10" in message, "只给八行不够诊断"
    assert "stderr" in message and "stdout" in message, "要说清哪一段是哪个流"
    assert "up" in message, "命令要完整，不能截断到前五个词"


def test_down_activates_every_profile_so_nothing_is_left_pinning_a_volume(stack):
    """`docker compose down` only touches services whose profile is active. The scheduled and
    control services are profile-gated, so a `down` without them leaves their containers behind
    - and an exited container still holds its volumes, which is how a local rebuild found
    v16-sites and v16-logs still there after `down --volumes` claimed to have torn the stack
    down. A teardown that leaves the data behind is not a teardown, and the rebuild it is meant
    to enable would silently start from the old data."""
    dev_stack.up(stack, provision_too=True)
    stack.host.calls.clear()
    dev_stack.down(stack, volumes=True)
    teardown = next(argv for argv in stack.host.calls if "down" in argv)
    named = [teardown[i + 1] for i, word in enumerate(teardown) if word == "--profile"]
    assert set(named) == set(dev_stack.PROFILES), f"漏掉的 profile 会把容器和卷留下：{named}"
    assert set(dev_stack.PROFILES) >= {"control", "scheduled", "ops"}
    assert "-v" in teardown and "--remove-orphans" in teardown


def test_the_leak_scan_treats_api_key_as_a_secret_like_the_rest_of_the_repository(stack, tmp_path):
    """The repository decides elsewhere that an api_key is a secret: DS Membership keeps
    api_key at permlevel 1 so an ordinary member cannot read it, and run_events redacts it.
    The artefact scan disagreed - its key list had api_secret but not api_key - so every
    api_key value in .runtime could ride out to a public artefact unnoticed. A scan that only
    catches half of what the repository calls a secret is worse than none: it reads as proof."""
    assert 'api_key' in dev_stack.SECRET_KEYS
    dev_stack.up(stack, provision_too=True)
    leaking = tmp_path / 'compose.log'
    profiles = json.loads((stack.runtime / 'erp-users.json').read_text())
    leaking.write_text('gunicorn ... token ' + profiles['reader']['api_key'] + ':x ...\n')
    found = dev_stack.leaked_secrets(stack.runtime, [leaking])
    assert found, 'api_key 值出现在工件里却没有被扫出来'
    assert all(profiles['reader']['api_key'] not in name for name, _ in found), '只报名字，不报值'
