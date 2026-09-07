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


from dsherp.backup_status import empty, evaluate, record_run, record_set, record_site   # noqa: E402

NOW = 1_788_739_200.0   # 2026-09-07T00:00:00Z
SITES = ("acme.tenant.example.com", "platform.tenant.example.com")


def _healthy(sites=SITES, stamp="20260906_140007", verified_at="2026-09-06T04:30:00Z", first_at=None):
    status = empty()
    for site in sites:
        set_id = f"{stamp}-{site.replace('.', '_')}-aaaaaa"
        record_site(status, site, "backup", at=first_at or "2026-08-01T14:01:00Z", ok=True, set_id=set_id, stamp=stamp)
        record_site(status, site, "backup", at="2026-09-06T14:01:00Z", ok=True, set_id=set_id, stamp=stamp)
        record_site(status, site, "offsite", at="2026-09-06T14:03:00Z", ok=True, set_id=set_id, stamp=stamp)
        if verified_at:
            record_site(status, site, "verified", at=verified_at, ok=True, set_id=set_id, image_tag="v0.4.0")
    for phase, at in (("backup", "2026-09-06T14:01:00Z"), ("sync", "2026-09-06T14:03:00Z"),
                      ("check", "2026-09-06T14:05:00Z"), ("drill", verified_at or "2026-09-06T04:30:00Z")):
        record_run(status, phase, at=at, ok=True)
    return status


def _keys(result):
    return sorted(alert.key for alert in result["alerts"])


def test_a_healthy_status_raises_nothing_and_reports_the_ages_it_judged():
    result = evaluate(_healthy(), list(SITES), NOW, status_age_seconds=120)
    assert _keys(result) == []
    gauges = result["gauges"]
    assert gauges["dsherp_backup_sites_expected"] == 2 and gauges["dsherp_backup_sites_rpo_ok"] == 2
    assert gauges["dsherp_backup_last_run_ok"] == 1 and gauges["dsherp_backup_status_age_seconds"] == 120
    assert abs(gauges["dsherp_backup_offsite_oldest_hours"] - 9.999) < 0.01


def test_the_rpo_is_judged_from_the_data_the_set_holds_not_from_when_it_was_uploaded():
    assert _keys(evaluate(_healthy(stamp="20260906_040001"), list(SITES), NOW)) == []               # 19h59m59s
    assert _keys(evaluate(_healthy(stamp="20260906_035959"), list(SITES), NOW)) == ["backup_rpo_warning"]
    assert _keys(evaluate(_healthy(stamp="20260905_235959"), list(SITES), NOW)) == ["backup_rpo_unmet"]
    unmet = [alert for alert in evaluate(_healthy(stamp="20260905_235959"), list(SITES), NOW)["alerts"]][0]
    assert unmet.severity == "critical" and "acme.tenant.example.com" in unmet.message


def test_the_expected_sites_come_from_the_caller_and_an_unbacked_site_is_unmet():
    status = _healthy(sites=("acme.tenant.example.com",))
    result = evaluate(status, ["acme.tenant.example.com", "new.tenant.example.com"], NOW)
    assert "backup_rpo_unmet" in _keys(result)
    assert result["gauges"]["dsherp_backup_offsite_oldest_hours"] == -1
    assert result["gauges"]["dsherp_backup_sites_rpo_ok"] == 1
    assert "new.tenant.example.com" in [alert for alert in result["alerts"] if alert.key == "backup_rpo_unmet"][0].message


def test_a_scope_that_cannot_be_read_is_reported_rather_than_narrowed():
    """The alternative - falling back to whatever the record happens to mention - would hide
    exactly the Site nobody is backing up."""
    result = evaluate(_healthy(), None, NOW)
    assert _keys(result) == ["backup_scope_unknown"]
    assert result["gauges"]["dsherp_backup_sites_expected"] == -1


def test_a_failed_attempt_after_a_success_is_a_warning_and_a_missing_or_stale_record_is_critical():
    status = _healthy()
    record_site(status, "acme.tenant.example.com", "offsite", at="2026-09-06T23:00:00Z", ok=False, error="restic: timeout")
    assert _keys(evaluate(status, list(SITES), NOW)) == ["backup_run_failed"]
    assert _keys(evaluate(None, list(SITES), NOW)) == ["backup_status_missing"]
    late = _healthy()
    record_run(late, "backup", at="2026-09-06T10:59:00Z", ok=True)      # 13h01m ago: the timer did not fire
    assert "backup_status_missing" in _keys(evaluate(late, list(SITES), NOW))
    deferred = _healthy()
    record_site(deferred, "acme.tenant.example.com", "backup", at="2026-09-06T23:00:00Z", ok=False, error=None, deferred="busy")
    assert "backup_run_failed" not in _keys(evaluate(deferred, list(SITES), NOW)), "a deferred Site is not a failure"


def test_being_unverified_for_more_than_eight_days_is_a_warning_with_a_grace_for_new_sites():
    assert _keys(evaluate(_healthy(verified_at="2026-08-29T23:59:00Z"), list(SITES), NOW)) == ["restore_unverified"]
    assert _keys(evaluate(_healthy(verified_at="2026-08-30T00:01:00Z"), list(SITES), NOW)) == []
    fresh = _healthy(verified_at=None, first_at="2026-09-06T14:01:00Z")
    assert _keys(evaluate(fresh, list(SITES), NOW)) == [], "a Site backed up yesterday has not missed a drill yet"
    old = _healthy(verified_at=None, first_at="2026-08-01T00:00:00Z")
    assert _keys(evaluate(old, list(SITES), NOW)) == ["restore_unverified"]


def test_the_rpo_answer_is_the_newest_proven_set_not_the_last_upload_to_finish():
    """A run that catches up on an older set must not make the Site look freshly backed up,
    and must not make it look stale either."""
    status = _healthy(sites=("acme.tenant.example.com",), stamp="20260906_140007")
    site = "acme.tenant.example.com"
    for stamp, state in (("20260906_140007", "complete"), ("20260301_020000", "complete")):
        record_set(status, {"set_id": f"{stamp}-acme_tenant_example_com-aaaaaa", "site": site,
                            "kind": "scheduled", "stamp": stamp, "image_tag": "v0.4.0", "image_id": "sha256:x"}, state)
    # The record's last_success now names the old set: the catch-up finished last.
    record_site(status, site, "offsite", at="2026-09-06T23:00:00Z", ok=True,
                set_id="20260301_020000-acme_tenant_example_com-aaaaaa", stamp="20260301_020000")
    result = evaluate(status, [site], NOW)
    assert _keys(result) == [], "the newest proven set is ten hours old"
    assert result["gauges"]["dsherp_backup_offsite_oldest_hours"] < 24
