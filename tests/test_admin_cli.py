"""Operating one deployment: every step asks what exists before it changes anything."""
import json
import os

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
        self.existing = set(existing_sites)
        self.installed = set(installed)
        self.config = dict(config or {})

    def site_exists(self, site):
        self.calls.append(("exists", site))
        return site in self.existing

    def run(self, *arguments, stdin=None, timeout=900):
        self.calls.append(("run",) + arguments[:3])
        if arguments[:2] == ("bench", "new-site"):
            self.existing.add(arguments[2])
        if arguments[:1] == ("bench",) and "install-app" in arguments:
            self.installed.add(arguments[-1])
        if arguments[:2] == ("bench", "drop-site"):
            self.existing.discard(arguments[2])
        return ""

    def python(self, site, body, timeout=900):
        self.calls.append(("python", site, body.splitlines()[0][:40]))
        if "get_installed_apps" in body:
            return json.dumps("dsherp_bridge" in self.installed) + "\n"
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
    assert any("compose" in row for row in findings)
    assert any("db_root_password" in row for row in findings)
    complete = admin.doctor(deploy_env.settings({"DSHERP_ENV": "dev"}), admin.ROOT)
    assert not [row for row in complete if "部署指纹清单缺少文件" in row]
