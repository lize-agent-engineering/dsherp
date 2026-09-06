"""The status file is the single place the host records what the backup chain last did,
per Site and per phase; a failure never erases the last success."""
import json

import pytest

from dsherp import backup_status


def test_an_empty_status_has_every_phase_and_load_rejects_missing_or_broken_files(tmp_path):
    status = backup_status.empty()
    assert set(status["runs"]) == set(backup_status.PHASES) and status["sites"] == {} and status["sets"] == {}
    assert backup_status.load(tmp_path / "missing.json") is None
    (tmp_path / "broken.json").write_text("{nope")
    assert backup_status.load(tmp_path / "broken.json") is None
    (tmp_path / "old.json").write_text(json.dumps({"format": 0}))
    assert backup_status.load(tmp_path / "old.json") is None
    path = backup_status.save(tmp_path / "state" / "backups" / "status.json", status)
    assert backup_status.load(path) == status and path.stat().st_mode & 0o077 == 0


def test_a_missing_file_and_a_corrupt_file_are_told_apart_because_only_one_may_be_rebuilt(tmp_path):
    """A corrupt status file must not be silently replaced by an empty one: the protection of
    'newest complete' and 'newest verified' sets lives in it."""
    assert backup_status.read(tmp_path / "missing.json") == (None, "missing")
    (tmp_path / "broken.json").write_text("{nope")
    assert backup_status.read(tmp_path / "broken.json") == (None, "corrupt")
    path = backup_status.save(tmp_path / "status.json", backup_status.empty())
    status, state = backup_status.read(path)
    assert state == "ok" and status["format"] == backup_status.FORMAT


def test_a_failed_attempt_keeps_the_last_success_and_a_success_carries_its_facts():
    status = backup_status.empty()
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-06T02:00:41Z", ok=True,
                              set_id="20260906_020007-acme_tenant_example_com-k3f9qx", stamp="20260906_020007")
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-06T14:00:20Z", ok=False, error="dump failed")
    row = status["sites"]["acme.tenant.example.com"]["backup"]
    assert row["last_success"] == {"at": "2026-09-06T02:00:41Z", "first_at": "2026-09-06T02:00:41Z",
                                   "set_id": "20260906_020007-acme_tenant_example_com-k3f9qx", "stamp": "20260906_020007"}
    assert row["last_attempt"] == {"at": "2026-09-06T14:00:20Z", "ok": False, "error": "dump failed"}
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-07T02:00:30Z", ok=False, error=None, deferred="busy")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_attempt"]["deferred"] == "busy"
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-07T14:00:30Z", ok=True, set_id="x", stamp="20260907_140007")
    assert row["last_success"]["first_at"] == "2026-09-06T02:00:41Z", "the first success is kept for the new-site grace"
    assert row["last_success"]["at"] == "2026-09-07T14:00:30Z"
    backup_status.record_run(status, "backup", at="2026-09-06T14:01:00Z", ok=False, error="1 site failed")
    assert status["runs"]["backup"]["last_attempt"]["ok"] is False and status["runs"]["backup"]["last_success"] is None
    backup_status.record_run(status, "backup", at="2026-09-07T02:01:00Z", ok=True)
    assert status["runs"]["backup"]["last_success"] == {"at": "2026-09-07T02:01:00Z"}
    with pytest.raises(ValueError):
        backup_status.record_site(status, "acme.tenant.example.com", "nowhere", at="x", ok=True)
    with pytest.raises(ValueError):
        backup_status.record_run(status, "nowhere", at="x", ok=True)


def test_sets_move_through_states_and_keep_their_snapshot_ids():
    status = backup_status.empty()
    doc = {"set_id": "20260906_020007-acme_tenant_example_com-k3f9qx", "site": "acme.tenant.example.com", "kind": "scheduled",
           "stamp": "20260906_020007", "image_tag": "v0.4.0", "image_id": "sha256:abc"}
    backup_status.record_set(status, doc, "staged")
    backup_status.record_set(status, doc, "data_uploaded", data_snapshot="1a2b3c4d")
    backup_status.record_set(status, doc, "complete", secrets_snapshot="5e6f7a8b")
    row = status["sets"][doc["set_id"]]
    assert row["state"] == "complete" and row["data_snapshot"] == "1a2b3c4d" and row["secrets_snapshot"] == "5e6f7a8b"
    assert row["site"] == doc["site"] and row["kind"] == "scheduled" and row["stamp"] == "20260906_020007"
    assert row["image_tag"] == "v0.4.0" and row["image_id"] == "sha256:abc" and row["updated"]
    backup_status.record_set(status, doc, "data_uploaded", secrets_snapshot=None)
    assert status["sets"][doc["set_id"]]["secrets_snapshot"] is None, "a repository that lost a snapshot is recorded as lost"
    with pytest.raises(ValueError):
        backup_status.record_set(status, doc, "uploaded")


def test_sets_of_a_site_can_be_listed_with_their_protections():
    status = backup_status.empty()
    def add(set_id, state, **facts):
        backup_status.record_set(status, {"set_id": set_id, "site": "acme.tenant.example.com", "kind": facts.pop("kind", "scheduled"),
                                          "stamp": set_id[:15], "image_tag": "v0.4.0", "image_id": "sha256:abc"}, state, **facts)
    add("20260901_020000-acme_tenant_example_com-aaaaaa", "verified")
    add("20260905_020000-acme_tenant_example_com-bbbbbb", "complete")
    add("20260906_020000-acme_tenant_example_com-cccccc", "staged")
    add("20260906_140000-beta_tenant_example_com-dddddd", "complete")
    status["sets"]["20260906_140000-beta_tenant_example_com-dddddd"]["site"] = "beta.tenant.example.com"
    rows = backup_status.sets_of(status, "acme.tenant.example.com")
    assert [row["set_id"][:8] for row in rows] == ["20260901", "20260905", "20260906"]
    assert backup_status.protected_sets(status, "acme.tenant.example.com") == {
        "20260901_020000-acme_tenant_example_com-aaaaaa", "20260905_020000-acme_tenant_example_com-bbbbbb"}
