"""Operating one deployment: every step asks what exists before it changes anything."""
import json
import os
from pathlib import Path

import pytest

from dsherp import admin, deploy_env


PROD = deploy_env.settings({
    "DSHERP_ENV": "prod", "DSHERP_PROJECT": "dsherp", "DSHERP_BASE_DOMAIN": "tenant.example.com",
    "DSHERP_PLATFORM_SLUG": "platform", "DSHERP_IMAGE_TAG": "v0.3.0",
    "DSHERP_IMAGE_REGISTRY": "registry.example.com/dsherp",
    "DSHERP_AGENT_UID": "1000", "DSHERP_AGENT_GID": "1000",
})


@pytest.fixture(autouse=True)
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("DSHERP_RUNTIME_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DSHERP_SECRETS_DIR", str(tmp_path / "state" / "control"))
    return tmp_path


class FakeBench:
    """Records what an operator's command chain actually asked the containers to do."""

    def __init__(self, existing_sites=(), installed=(), config=None):
        self.calls = []
        self.verbs = []
        self.oauth_callbacks = []
        self.existing = set(existing_sites)
        self.installed = set(installed)
        self.config = dict(config or {})

    def site_state(self, site):
        self.calls.append(("exists", site))
        if site in self.existing:
            return "present"
        return "partial" if site in getattr(self, "partial", set()) else "absent"

    def site_exists(self, site):
        return self.site_state(site) == "present"

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        self.calls.append(("run",) + arguments[:3])
        self.verbs.append(" ".join(arguments))
        if arguments[:2] == ("sh", "-c") and "apps.txt" in arguments[2]:
            if self.config.get("bench"):
                return " apps.txt:kept config:kept assets:kept\n"
            self.config["bench"] = True
            return " apps.txt:added=2 config:created assets:created\n"
        if arguments[:2] == ("bench", "new-site"):
            self.existing.add(arguments[2])
        if arguments[:1] == ("bench",) and "install-app" in arguments:
            self.installed.add(arguments[-1])
        if arguments[:2] == ("bench", "drop-site"):
            self.existing.discard(arguments[2])
        return ""

    def python(self, site, body, timeout=900):
        self.calls.append(("python", site, body.splitlines()[0][:40]))
        if "dsherp_runtime_user" in body and "assert" in body:
            return json.dumps({"site": site, "apps": ["dsherp_bridge", "erpnext", "frappe"]}) + "\n"
        if "'Role'" in body:
            state = "kept" if self.config.get("role") else "created"
            self.config["role"] = True
            return json.dumps(state) + "\n"
        if "DS Enterprise" in body:
            state = "kept" if self.config.get("enterprise") else "created"
            self.config["enterprise"] = True
            return json.dumps(state) + "\n"
        if "OAuth Client" in body:
            state = "kept" if self.config.get("oauth") else "created"
            self.config["oauth"] = True
            self.oauth_callbacks.append(body.split("callback=", 1)[1].split("'")[1])
            return json.dumps({"state": state, "client_id": "cid", "client_secret": "csecret"}) + "\n"
        if "Social Login Key" in body:
            state = "kept" if self.config.get("social") else "created"
            self.config["social"] = True
            return json.dumps({"state": state, "provider": "dsherp_platform"}) + "\n"
        if "get_installed_apps" in body:
            app = body.split("'")[1]
            return json.dumps(app in self.installed) + "\n"
        if "generate_keys" in body:
            return json.dumps({"user": f"runtime@{site}", "api_key": "k", "api_secret": "s"}) + "\n"
        if "System Settings" in body:
            values = json.loads(json.loads(body.splitlines()[0].split("=", 1)[1][len("json.loads("):-1]))
            changed = [key for key, value in values.items() if self.config.get(key) != value]
            self.config.update(values)
            return json.dumps(changed) + "\n"
        if "update_site_config" in body:
            values = json.loads(json.loads(body.splitlines()[0].split("=", 1)[1][len("json.loads("):-1]))
            changed = [key for key, value in values.items() if self.config.get(key) != value]
            self.config.update(values)
            return json.dumps(changed) + "\n"
        raise AssertionError("unexpected snippet")


def test_control_secrets_are_created_once_and_never_regenerated(host):
    first = admin.ensure_secrets(PROD)
    assert set(first["created"]) == set(admin.control_secrets(PROD)) and not first["kept"]
    directory = admin.secrets_dir(PROD)
    values = {name: (directory / name).read_text() for name in admin.control_secrets(PROD)}
    for name in admin.control_secrets(PROD):
        assert (directory / name).stat().st_mode & 0o777 == 0o600
    second = admin.ensure_secrets(PROD)
    assert not second["created"] and set(second["kept"]) == set(admin.control_secrets(PROD))
    assert {name: (directory / name).read_text() for name in admin.control_secrets(PROD)} == values


def test_a_world_readable_control_secret_stops_the_command(host):
    admin.ensure_secrets(PROD)
    path = admin.secrets_dir(PROD) / "db_root_password"
    path.chmod(0o644)
    with pytest.raises(admin.Fault):
        admin.ensure_secrets(PROD)


def test_the_ingress_lists_every_live_host_explicitly(host):
    rows = [{"slug": "beta", "site": "beta.tenant.example.com"}, {"slug": "acme", "site": "acme.tenant.example.com"}]
    target = admin.render_ingress(PROD, rows)
    text = target.read_text()
    assert "__SITE_BLOCKS__" not in text
    assert "platform.tenant.example.com {" in text
    assert text.index("acme.tenant.example.com {") < text.index("beta.tenant.example.com {")
    assert text.count("import site frontend:8080") == 2
    assert "import site platform-frontend:8080" in text
    assert "{$DSHERP_ACME_EMAIL}" in text


def test_provisioning_a_new_tenant_creates_the_site_once_and_records_it(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    result = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    assert result["site"] == "acme.tenant.example.com"
    assert ("run", "bench", "new-site", "acme.tenant.example.com") in bench.calls
    assert dict(result["steps"])["site"] == "created"
    assert dict(result["steps"])["app"] == "created"
    assert dict(result["steps"])["password-login"] == "disabled"
    assert dict(result["steps"])["healthcheck"] == "acme.tenant.example.com"
    assert dict(result["steps"])["enterprise"] == "created"
    assert dict(result["steps"])["oauth-client"] == "created"
    assert dict(result["steps"])["social-login-key"] == "created"
    assert bench.oauth_callbacks == ["https://acme.tenant.example.com/api/method/dsherp_bridge.sso.callback"]
    assert bench.config["dsherp_platform_oauth"] == {
        "provider": "dsherp_platform", "enterprise": "acme",
        "platform_site": "platform.tenant.example.com"}
    assert bench.config["dsherp_business_sites"] == {"acme.tenant.example.com": "http://backend:8000"}
    assert bench.config["dsherp_desk_sites"] == {
        "acme.tenant.example.com": "https://acme.tenant.example.com/api/method/dsherp_bridge.sso.start"}
    rows = admin.load_tenants(PROD)
    assert rows == [{"slug": "acme", "site": "acme.tenant.example.com",
                     "origin": "https://acme.tenant.example.com"}]
    assert admin.tenants_path(PROD).stat().st_mode & 0o777 == 0o600
    assert "acme.tenant.example.com {" in (admin.runtime_dir(PROD) / "caddy" / "Caddyfile").read_text()


def test_provisioning_the_same_tenant_again_changes_nothing(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    bench.calls.clear()
    again = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    assert dict(again["steps"])["site"] == "kept"
    assert dict(again["steps"])["app"] == "kept"
    assert dict(again["steps"])["site-config"] == "kept"
    assert dict(again["steps"])["password-login"] == "kept"
    assert dict(again["steps"])["enterprise"] == "kept"
    assert dict(again["steps"])["oauth-client"] == "kept"
    assert dict(again["steps"])["social-login-key"] == "kept"
    assert not [call for call in bench.calls if call[:3] == ("run", "bench", "new-site")]
    assert len(admin.load_tenants(PROD)) == 1


def test_provisioning_without_control_secrets_stops_before_touching_a_container(host):
    bench = FakeBench()
    with pytest.raises(admin.Fault):
        admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    assert not [call for call in bench.calls if call[0] == "run"]


def test_a_tenant_slug_that_is_not_a_slug_never_reaches_a_command(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    for bad in ("Acme", "acme.evil", "../etc", ""):
        with pytest.raises(ValueError):
            admin.provision_tenant(PROD, bad, bench_factory=lambda kind: bench)
    assert not bench.calls


def test_retiring_a_tenant_archives_before_it_drops_and_leaves_the_ingress_correct(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    admin.provision_tenant(PROD, "beta", bench_factory=lambda kind: bench)
    bench.calls.clear()
    result = admin.retire_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    verbs = [call for call in bench.calls if call[0] == "run"]
    assert verbs[0][:3] == ("run", "bench", "--site")
    assert ("run", "bench", "drop-site", "acme.tenant.example.com") in bench.calls
    assert verbs.index(("run", "bench", "drop-site", "acme.tenant.example.com")) > 0
    assert dict(result["steps"])["site"] == "dropped"
    assert [row["slug"] for row in admin.load_tenants(PROD)] == ["beta"]
    ingress = (admin.runtime_dir(PROD) / "caddy" / "Caddyfile").read_text()
    assert "acme.tenant.example.com" not in ingress and "beta.tenant.example.com" in ingress


def test_retiring_a_site_that_does_not_exist_is_refused_rather_than_treated_as_cleanup(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    with pytest.raises(admin.Fault):
        admin.retire_tenant(PROD, "ghost", bench_factory=lambda kind: bench)
    assert not [call for call in bench.calls if call[0] == "run"]


def test_doctor_names_what_is_missing_instead_of_failing_at_compose_time(host):
    findings = admin.doctor(PROD)
    # Only what the deployment itself lacks; never a finding that depends on a gitignored file.
    assert {row for row in findings if "控制面密钥" in row} == {
        f"缺少控制面密钥：{name}" for name in admin.control_secrets(PROD)}
    complete = admin.doctor(deploy_env.settings({"DSHERP_ENV": "dev"}), admin.ROOT)
    assert not [row for row in complete if "部署指纹清单缺少文件" in row]


class SnapshotBench(FakeBench):
    """A bench whose Site content can be made to change between two snapshots."""

    def __init__(self, snapshots):
        super().__init__(existing_sites=("acme.tenant.example.com",), installed=("dsherp_bridge",))
        self.snapshots = list(snapshots)

    def python(self, site, body, timeout=900):
        if "digests" in body:
            self.calls.append(("snapshot", site))
            self.verbs.append("snapshot " + site)
            return json.dumps(self.snapshots.pop(0)) + "\n"
        return super().python(site, body, timeout=timeout)


def _tenant_row():
    admin.save_tenants(PROD, [{"slug": "acme", "site": "acme.tenant.example.com",
                               "origin": "https://acme.tenant.example.com"}])


def test_a_release_backs_up_before_it_migrates_and_reports_the_data_unchanged():
    admin.ensure_secrets(PROD)
    _tenant_row()
    same = {"Item": {"count": 3, "digest": "aa"}, "DS Model Run": {"count": 9, "digest": "bb"}}
    bench = SnapshotBench([same, dict(same)])
    report = admin.release(PROD, "v0.4.0", bench_factory=lambda kind: bench)
    phases = [verb.split()[0] if verb.startswith("snapshot") else verb.split()[3] for verb in bench.verbs]
    assert phases == ["backup", "snapshot", "migrate", "snapshot"]
    assert report["clean"] is True
    assert report["sites"]["acme.tenant.example.com"]["differences"] == []
    assert json.loads(Path(report["path"]).read_text())["tag"] == "v0.4.0"


def test_a_release_that_changes_stored_data_is_reported_as_not_clean():
    admin.ensure_secrets(PROD)
    _tenant_row()
    before = {"Item": {"count": 3, "digest": "aa"}}
    after = {"Item": {"count": 3, "digest": "cc"}}
    bench = SnapshotBench([before, after])
    report = admin.release(PROD, "v0.4.0", bench_factory=lambda kind: bench)
    assert report["clean"] is False
    assert report["sites"]["acme.tenant.example.com"]["differences"][0]["doctype"] == "Item"


def test_a_release_without_a_tag_or_without_sites_is_refused():
    admin.ensure_secrets(PROD)
    bench = SnapshotBench([])
    with pytest.raises(admin.Fault):
        admin.release(PROD, "", bench_factory=lambda kind: bench)
    with pytest.raises(admin.Fault):
        admin.release(PROD, "v0.4.0", bench_factory=lambda kind: bench)


def test_a_rollback_will_not_guess_which_backup_to_restore():
    admin.ensure_secrets(PROD)
    _tenant_row()
    bench = SnapshotBench([{"Item": {"count": 3, "digest": "aa"}}])
    with pytest.raises(admin.Fault):
        admin.rollback(PROD, "v0.3.0", bench_factory=lambda kind: bench)
    report = admin.rollback(PROD, "v0.3.0", bench_factory=lambda kind: bench,
                            backups={"acme.tenant.example.com": "/backups/pre-upgrade.sql.gz"})
    assert any("restore" in verb for verb in bench.verbs)
    assert report["sites"]["acme.tenant.example.com"]["Item"]["count"] == 3


def test_snapshot_comparison_names_added_removed_and_changed_doctypes():
    before = {"Item": {"count": 1, "digest": "a"}, "Gone": {"count": 2, "digest": "b"}}
    after = {"Item": {"count": 1, "digest": "z"}, "New": {"count": 4, "digest": "c"}}
    differences = admin.compare_snapshots(before, after)
    assert [row["doctype"] for row in differences] == ["Gone", "Item", "New"]
    assert differences[0]["after"] == {"count": 0, "digest": None}
    assert differences[2]["before"] == {"count": 0, "digest": None}
    assert admin.compare_snapshots(before, dict(before)) == []


def test_the_platform_site_is_created_without_erpnext_and_with_its_own_app():
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    result = admin.provision_platform(PROD, bench_factory=lambda kind: bench)
    assert result["site"] == "platform.tenant.example.com"
    steps = dict(result["steps"])
    assert steps["bench"] == "apps.txt:added=2 config:created assets:created"
    assert steps["member-role"] == "created"
    assert bench.verbs.index([v for v in bench.verbs if "apps.txt" in v][0]) < \
        bench.verbs.index([v for v in bench.verbs if "new-site" in v][0])
    created = [verb for verb in bench.verbs if "new-site" in verb][0]
    assert "--install-app erpnext" not in created
    assert any("install-app dsherp_platform" in verb for verb in bench.verbs)
    bench.calls.clear()
    bench.verbs.clear()
    again = admin.provision_platform(PROD, bench_factory=lambda kind: bench)
    assert dict(again["steps"])["site"] == "kept" and dict(again["steps"])["app"] == "kept"
    assert dict(again["steps"])["bench"] == "apps.txt:kept config:kept assets:kept"
    assert dict(again["steps"])["member-role"] == "kept"
    assert not [verb for verb in bench.verbs if "new-site" in verb]


def test_the_bench_bootstrap_never_rewrites_an_existing_database_address():
    bench = FakeBench()
    first = admin.ensure_bench(bench, PROD)
    assert first == ["apps.txt:added=2", "config:created", "assets:created"]
    assert admin.ensure_bench(bench, PROD) == ["apps.txt:kept", "config:kept", "assets:kept"]
    body = [verb for verb in bench.verbs if "apps.txt" in verb][0]
    # Presence is not configuration: only a config that names a database is left alone.
    assert "grep -q '\"db_host\"' common_site_config.json" in body and "redis-queue" in body and "redis-cache" in body
    assert "grep -qx" in body and "touch apps.txt" in body
    # The base image ships apps.txt without a final newline; a blind append fused two names.
    assert 'tail -c1 apps.txt' in body and body.index('tail -c1 apps.txt') < body.index('grep -qx')
    assert "dsherp_bridge" in body and "dsherp_platform" in body


def test_compose_calls_carry_the_environment_file_the_code_resolved_from(tmp_path, monkeypatch):
    recorded = []
    def runner(command, **kwargs):
        recorded.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "yes\n"})()
    (tmp_path / "infra" / "env").mkdir(parents=True)
    (tmp_path / "infra" / "env" / "prod.env").write_text("DSHERP_ENV=prod\n")
    bench = admin.Bench(PROD, "tenant", root=tmp_path, runner=runner)
    bench.site_exists("acme.tenant.example.com")
    command = recorded[0]
    assert command[:4] == ["docker", "compose", "-p", "dsherp"]
    assert command[command.index("--env-file") + 1] == str(tmp_path / "infra" / "env" / "prod.env")
    assert command[command.index("-f") + 1].endswith("infra/compose.prod.yml")
    assert "exec" in command and "backend" in command


def test_a_half_created_site_directory_stops_provisioning_instead_of_being_kept():
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    bench.partial = {"acme.tenant.example.com"}
    with pytest.raises(admin.Fault) as failure:
        admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    assert "site_config.json" in str(failure.value)
    assert not [verb for verb in bench.verbs if "new-site" in verb]


def test_a_failed_container_command_reports_its_stderr_without_the_secrets_it_was_given(tmp_path):
    def runner(command, **kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "",
                                   "stderr": "line one\nMySQL error with password hunter2\n"})()
    bench = admin.Bench(PROD, "tenant", root=tmp_path, runner=runner)
    with pytest.raises(admin.Fault) as failure:
        bench.run("bench", "new-site", "x", "--db-root-password", "hunter2", secrets=("hunter2",))
    message = str(failure.value)
    assert "MySQL error with password [redacted]" in message and "hunter2" not in message


def test_doctor_refuses_a_production_file_that_lets_compose_fall_back_to_development_secrets(tmp_path):
    directory = tmp_path / "infra" / "env"
    directory.mkdir(parents=True)
    directory.joinpath("prod.env").write_text("DSHERP_ENV=prod\nDSHERP_PROJECT=dsherp\n")
    findings = admin.doctor(PROD, tmp_path)
    assert [row for row in findings if "DSHERP_SECRETS_DIR" in row and "prod.env" in row]
    directory.joinpath("prod.env").write_text(
        "DSHERP_RUNTIME_DIR=/srv/dsherp/state\nDSHERP_SECRETS_DIR=/srv/dsherp/state/control\n")
    assert not [row for row in admin.doctor(PROD, tmp_path) if "DSHERP_SECRETS_DIR" in row]
