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
