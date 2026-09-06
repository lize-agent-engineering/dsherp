import json
from pathlib import Path
import stat

import pytest


def test_validation_provisioner_writes_three_private_profiles(tmp_path, monkeypatch):
    from infra import run_validation_provision

    payload = {
        "reader": {
            "user": "dsherp-reader@example.invalid",
            "api_key": "reader-key",
            "api_secret": "reader-secret",
        },
        "denied": {
            "user": "dsherp-denied@example.invalid",
            "api_key": "denied-key",
            "api_secret": "denied-secret",
        },
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(run_validation_provision.subprocess, "run", lambda *args, **kwargs: Result())
    run_validation_provision.provision(tmp_path)

    users = json.loads((tmp_path / "erp-users.json").read_text())
    reader = json.loads((tmp_path / "erp-reader.json").read_text())
    denied = json.loads((tmp_path / "erp-denied.json").read_text())
    assert users == {
        name: {
            **profile,
            "base_url": "http://127.0.0.1:18081",
            "site": "dsherp-validation.localhost",
        }
        for name, profile in payload.items()
    }
    assert reader == users["reader"]
    assert denied == users["denied"]
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (tmp_path / "erp-users.json", tmp_path / "erp-reader.json", tmp_path / "erp-denied.json")
    )


def test_validation_provisioner_refuses_any_existing_profile(tmp_path):
    from infra import run_validation_provision

    (tmp_path / "erp-reader.json").write_text("existing")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        run_validation_provision.provision(tmp_path)


def test_container_provisioner_uses_native_documents_and_fastfails_existing_site():
    source = (Path(__file__).resolve().parents[1] / "infra" / "provision_validation_site.py").read_text()
    assert "Site already exists; inspect before retrying" in source
    assert '"bench", "new-site"' in source
    assert "frappe.get_doc" in source
    assert "frappe.permissions.add_permission" in source
    assert "User Permission" in source
    assert "frappe.db.sql" not in source
    assert 'replace(secret, "[redacted]")' in source
    assert 'os.chdir(SITES)' in source


@pytest.mark.parametrize(
    "name",
    ("provision_identity.py", "provision_daily_site.py", "verify_daily_backup.py"),
)
def test_other_bench_control_scripts_redact_secrets_from_persistent_log(name):
    source = (Path(__file__).resolve().parents[1] / "infra" / name).read_text()
    assert 'replace(secret, "[redacted]")' in source


def test_validation_company_provisioner_uses_native_setup_once():
    source = (Path(__file__).resolve().parents[1] / "infra" / "provision_validation_company.py").read_text()
    assert "frappe.is_setup_complete()" in source
    assert "Company already initialized; inspect instead of retrying" in source
    assert "from frappe.desk.page.setup_wizard.setup_wizard import setup_complete" in source
    assert "DSHERP 原生验收测试公司" in source
    assert "'company_abbr':'DVT'" in source
    assert "DSHERP 隔离预览合成公司" in source
    assert "'company_abbr':'DPR'" in source
    assert "--target" in source
    assert "frappe.db.sql" not in source


def test_beta_provisioner_persists_all_preview_isolation_flags_as_integers():
    source = (Path(__file__).resolve().parents[1] / "infra" / "provision_identity.py").read_text()
    for key in (
        "dsherp_preview",
        "mute_emails",
        "disable_scheduler",
        "pause_scheduler",
        "disable_async",
    ):
        assert repr(key) in source
    assert "run(['bench','--site',site,'set-config','--parse',key,'1'])" in source


def test_alpha_writer_provisioning_reuses_the_native_sales_baseline_fixture():
    root = Path(__file__).resolve().parents[1]
    writer = (root / "infra" / "provision_context_writer.py").read_text()
    fixture = (root / "infra" / "provision_alpha_sales_baseline.py").read_text()
    assert "from provision_alpha_sales_baseline import FIXTURE_SCRIPT" in writer
    assert "SCRIPT+=FIXTURE_SCRIPT" in writer
    for value in (
        "DSHERP-UI-ITEM",
        "DSHERP-HITL-CUSTOMER",
        "SAL-ORD-2026-00001",
        "DSHERP-TEST-CUSTOMER",
    ):
        assert value in fixture
    assert "frappe.get_doc" in fixture
    assert "frappe.db.sql" not in fixture


def _existing_profiles(tmp_path):
    from infra import run_validation_provision

    profiles = {
        "reader": {"user": "dsherp-reader@example.invalid", "api_key": "reader-key", "api_secret": "stale-secret",
                   "base_url": "http://127.0.0.1:18081", "site": "dsherp-validation.localhost"},
        "denied": {"user": "dsherp-denied@example.invalid", "api_key": "denied-key", "api_secret": "denied-secret",
                   "base_url": "http://127.0.0.1:18081", "site": "dsherp-validation.localhost"},
    }
    run_validation_provision._write_private(tmp_path / "erp-users.json", profiles)
    run_validation_provision._write_private(tmp_path / "erp-reader.json", profiles["reader"])
    run_validation_provision._write_private(tmp_path / "erp-denied.json", profiles["denied"])
    return profiles


def test_reissuing_one_actor_rotates_its_secret_on_the_site_and_rewrites_only_its_profile(tmp_path):
    """A test that calls generate_keys on the shared reader leaves the files stale (reader → 401)."""
    from infra import run_validation_provision

    _existing_profiles(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs.get("input")))

        class Result:
            returncode = 0
            stdout = json.dumps({"api_key": "reader-key", "api_secret": "fresh-secret"}) + "\n"
            stderr = ""
        return Result()

    result = run_validation_provision.reissue("reader", tmp_path, run=run)
    assert result == {"actor": "reader", "user": "dsherp-reader@example.invalid", "api_key": "reader-key"}
    command, script = calls[0]
    assert command[:4] == ["docker", "compose", "-f", "infra/compose.validation.yml"]
    assert "exec" in command and "backend" in command
    assert "generate_keys" in script and "dsherp-reader@example.invalid" in script
    users = json.loads((tmp_path / "erp-users.json").read_text())
    assert users["reader"]["api_secret"] == "fresh-secret" and users["reader"]["api_key"] == "reader-key"
    assert users["reader"]["base_url"] == "http://127.0.0.1:18081" and users["reader"]["site"] == "dsherp-validation.localhost"
    assert users["denied"]["api_secret"] == "denied-secret"
    assert json.loads((tmp_path / "erp-reader.json").read_text()) == users["reader"]
    assert json.loads((tmp_path / "erp-denied.json").read_text()) == users["denied"]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600
               for path in (tmp_path / "erp-users.json", tmp_path / "erp-reader.json"))


def test_reissuing_refuses_an_unknown_actor_a_missing_profile_and_a_mismatched_key(tmp_path):
    from infra import run_validation_provision

    def run(command, **kwargs):
        class Result:
            returncode = 0
            stdout = json.dumps({"api_key": "other-key", "api_secret": "fresh-secret"}) + "\n"
            stderr = ""
        return Result()

    with pytest.raises(RuntimeError, match="profile"):
        run_validation_provision.reissue("reader", tmp_path, run=run)
    profiles = _existing_profiles(tmp_path)
    with pytest.raises(ValueError):
        run_validation_provision.reissue("writer", tmp_path, run=run)
    with pytest.raises(RuntimeError, match="api_key"):
        run_validation_provision.reissue("reader", tmp_path, run=run)
    assert json.loads((tmp_path / "erp-users.json").read_text()) == profiles
