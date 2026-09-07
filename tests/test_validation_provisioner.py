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
            stderr = ""
            stdout = (json.dumps({"rebound": 1}) if "platform-backend" in command
                      else json.dumps({"api_key": "reader-key", "api_secret": "fresh-secret"})) + "\n"
        return Result()

    result = run_validation_provision.reissue("reader", tmp_path, run=run)
    assert result == {"actor": "reader", "user": "dsherp-reader@example.invalid", "api_key": "reader-key",
                      "platform_memberships_rebound": 1}
    command, script = calls[0]
    assert command[:4] == ["docker", "compose", "-f", "infra/compose.validation.yml"]
    assert "exec" in command and "backend" in command
    # Through the Site's credential module: a key the Site has no window for is refused (S2).
    assert "credentials.issue" in script and "dsherp-reader@example.invalid" in script
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


def test_reissuing_also_rebinds_the_platform_membership_that_carries_the_same_secret(tmp_path):
    """The platform site stores the member's business api_secret in DS Membership; a
    rotation that only rewrites the files leaves every platform read answering 403."""
    from infra import run_validation_provision

    _existing_profiles(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs.get("input")))

        class Result:
            returncode = 0
            stderr = ""
            stdout = (json.dumps({"api_key": "reader-key", "api_secret": "fresh-secret"}) + "\n"
                      if "backend" in command and "platform-backend" not in command else json.dumps({"rebound": 1}) + "\n")
        return Result()

    result = run_validation_provision.reissue("reader", tmp_path, run=run)
    assert result["platform_memberships_rebound"] == 1
    assert [command[command.index("exec") + 2] for command, _ in calls] == ["backend", "platform-backend"]
    platform_script = calls[1][1]
    assert "DS Membership" in platform_script and "fresh-secret" in platform_script and "dsherp-reader@example.invalid" in platform_script
    assert "dsherp-platform.localhost" in platform_script
