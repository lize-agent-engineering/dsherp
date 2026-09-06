"""dsherp-admin backup: one stable window per Site (held server-side and worker-side,
quiesced, drained) produces the four pieces and the G2 snapshot, stages them as one set in
two volumes, and records exactly what happened."""
import fcntl
import json

import pytest

from dsherp import admin, backup, backup_sets, backup_status, site_holds
from tests.test_admin_cli import RELEASE, RUNNING_NEW, SAME, SnapshotBench, _tenant_row
from tests.test_admin_cli import host  # noqa: F401  the autouse fixture that isolates runtime and secrets dirs


StagingBench = SnapshotBench  # the bench fake now answers the staging verbs for every test


def _prepare():
    admin.ensure_secrets(RELEASE)
    _tenant_row()


def _backup(bench, resolved=RELEASE, **kwargs):
    kwargs.setdefault("clock", lambda: 1_788_660_007.0)   # 2026-09-06T02:00:07Z
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("runner", RUNNING_NEW)
    return backup.backup(resolved, bench_factory=lambda kind: bench, **kwargs)


def test_a_backup_opens_a_held_quiesced_drained_window_stages_one_set_and_records_it(host):
    _prepare()
    bench = StagingBench([SAME, SAME])
    report = _backup(bench)
    assert report["ok"] is True and set(report["sets"]) == {"acme.tenant.example.com", "platform.tenant.example.com"}
    site = "acme.tenant.example.com"
    verbs = bench.verbs
    order = [next(i for i, v in enumerate(verbs) if v == needle) for needle in (
        f"bench --site {site} set-config --parse dsherp_hold 1",
        f"bench --site {site} set-config --parse maintenance_mode 1",
        f"bench --site {site} set-config --parse pause_scheduler 1",
        f"bench --site {site} backup --with-files",
        f"snapshot {site}",
        f"bench --site {site} set-config --parse maintenance_mode 0",
        f"bench --site {site} set-config --parse dsherp_hold 0")]
    assert order == sorted(order), "hold, quiesce, drain, back up, snapshot, then undo in reverse"
    assert [call for call in bench.calls if call[0] == "writers"], "the window waits for in-flight writers"
    set_doc = report["sets"][site]
    assert set_doc["stamp"] == "20260906_020007" and set_doc["kind"] == "scheduled"
    assert set_doc["image_tag"] == "v0.4.0" and set_doc["image_id"] == "sha256:id-v0.4.0"
    staged = [text for text in bench.staged if site in text]
    assert any("cp " in text and f"/home/frappe/backups/sets/{site}/{set_doc['set_id']}/" in text for text in staged)
    assert any("mv " in text and "site_config_backup.json" in text
               and f"/home/frappe/backup-secrets/{site}/{set_doc['set_id']}/" in text for text in staged)
    assert not any("cp " in text and "site_config_backup.json" in text for text in staged), "the secret piece moves, never copies"
    assert any("chmod 700" in text for text in staged) and any("chmod 600" in text for text in staged)
    written = bench.written
    data_dir = f"/home/frappe/backups/sets/{site}/{set_doc['set_id']}"
    secret_dir = f"/home/frappe/backup-secrets/{site}/{set_doc['set_id']}"
    assert written[f"{data_dir}/set.json"] == set_doc
    assert f"{data_dir}/snapshot.json" in written
    pair = written[f"{secret_dir}/pair.json"]
    assert backup_sets.pair_matches(set_doc, pair) and pair["config_sha256"] == "ab" * 32
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"][site]["backup"]["last_success"]["set_id"] == set_doc["set_id"]
    assert status["sets"][set_doc["set_id"]]["state"] == "staged" and status["runs"]["backup"]["last_success"]
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()


def test_queued_runs_alone_never_defer_a_backup_but_a_running_executor_does(host):
    """Review round 2: `active` counts Queued, so a queued run would block the window forever.
    A hold freezes the queue; only an executor actually in flight is waited for."""
    _prepare()
    queued_only = StagingBench([SAME, SAME])
    queued_only.site_status = {"Queued": 3, "Running": 0, "Cancelling": 0, "NeedsInput": 2}   # every Site
    assert _backup(queued_only)["ok"] is True, "a queue is frozen by the hold, not waited for"

    busy = StagingBench([SAME])
    busy.site_status = {"acme.tenant.example.com": {"Queued": 0, "Running": 1, "Cancelling": 0, "NeedsInput": 0}}
    clock = {"t": 0.0}
    report = _backup(busy, clock=lambda: clock["t"], sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds))
    assert report["ok"] is False and report["deferred"]["acme.tenant.example.com"] == "busy"
    assert "platform.tenant.example.com" in report["sets"], "one busy Site does not stop the others"
    assert not any("acme" in verb and "backup --with-files" in verb for verb in busy.verbs)
    assert not any("acme" in verb and "maintenance_mode 1" in verb for verb in busy.verbs)
    assert not any("acme" in verb and "dsherp_hold 1" in verb and verb.endswith("1")
                   for verb in busy.verbs[-2:]), "the hold is lifted again"
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_attempt"]["deferred"] == "busy"


def test_a_window_waits_for_http_and_background_writers_and_defers_when_they_do_not_finish(host):
    _prepare()
    slow = StagingBench([SAME, SAME], active_writers=[2, 1, 0])
    slow.drain_only = "acme.tenant.example.com"
    assert _backup(slow)["ok"] is True
    assert len([call for call in slow.calls if call[0] == "writers" and call[1] == "acme.tenant.example.com"]) >= 3

    stuck = StagingBench([SAME], active_writers=[1] * 100)
    stuck.drain_only = "acme.tenant.example.com"
    clock = {"t": 0.0}
    report = _backup(stuck, clock=lambda: clock["t"], sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds))
    assert report["deferred"]["acme.tenant.example.com"] == "draining"
    assert not any("acme" in verb and "backup --with-files" in verb for verb in stuck.verbs)
    assert stuck.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0", "flags are undone on the way out"
    assert stuck.site_config[("acme.tenant.example.com", "dsherp_hold")] == "0"
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()


def test_a_failure_inside_the_window_undoes_every_step_it_had_taken(host):
    _prepare()

    class BreaksOnSnapshot(StagingBench):
        def python(self, site, body, timeout=900):
            if "DSHERP_SNAPSHOT" in body and site == "acme.tenant.example.com":
                raise admin.Fault("容器命令失败（backend）：snapshot\nout of memory")
            return super().python(site, body, timeout=timeout)

    bench = BreaksOnSnapshot([SAME, SAME])
    report = _backup(bench)
    assert report["ok"] is False and "acme.tenant.example.com" in report["failed"]
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"
    assert bench.site_config[("acme.tenant.example.com", "pause_scheduler")] == "0"
    assert bench.site_config[("acme.tenant.example.com", "dsherp_hold")] == "0"
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_success"] is None
    assert "platform.tenant.example.com" in report["sets"]


def test_an_incomplete_set_a_bad_digest_or_an_unverifiable_image_fails_that_site(host):
    _prepare()
    short = StagingBench([SAME, SAME], backup_pieces=3)
    assert _backup(short)["failed"]["acme.tenant.example.com"]

    class BadDigest(StagingBench):
        def run(self, *arguments, stdin=None, timeout=900, secrets=()):
            text = arguments[2] if arguments[:2] == ("sh", "-c") else ""
            if "sha256sum" in text and "/home/frappe/backups/sets/" in text:
                names = [word for word in text.split() if word.endswith((".sql.gz", ".tar"))]
                return "".join(f"{'cd' * 32}  {name}\n" for name in names)
            return super().run(*arguments, stdin=stdin, timeout=timeout, secrets=secrets)

    drifted = BadDigest([SAME, SAME])
    report = _backup(drifted)
    assert "摘要" in report["failed"]["acme.tenant.example.com"]

    def no_image(command, **kwargs):
        result = RUNNING_NEW(command, **kwargs)
        if command[:2] == ["docker", "inspect"]:
            result.stdout = "registry.example.com/dsherp/dsherp-frappe:v0.4.0 \n"
        return result
    blind = StagingBench([SAME, SAME])
    with pytest.raises(admin.Fault, match="镜像身份"):
        _backup(blind, runner=no_image)
    assert not any("backup --with-files" in verb for verb in blind.verbs)


def test_the_staging_directories_must_be_writable_before_anything_is_quiesced(host):
    _prepare()
    bench = StagingBench([SAME, SAME])
    bench.config["staging_unwritable"] = True
    report = _backup(bench)
    assert "暂存目录" in report["failed"]["acme.tenant.example.com"]
    assert not any("maintenance_mode 1" in verb for verb in bench.verbs)


def test_two_backups_cannot_overlap_and_release_shares_the_same_lock(host):
    _prepare()
    with backup.operations_lock(RELEASE, admin.ROOT, "backup"):
        with pytest.raises(admin.Fault, match="另一项操作"):
            _backup(StagingBench([SAME, SAME]))
        with pytest.raises(admin.Fault, match="另一项操作"):
            admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: StagingBench([SAME] * 4),
                          runner=RUNNING_NEW, from_tag="v0.3.0")
    lock = admin.runtime_dir(RELEASE) / "operations.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)   # the lock is free again


def test_local_pruning_keeps_three_sets_and_never_the_protected_ones(host):
    _prepare()
    bench = StagingBench([SAME, SAME])
    site = "acme.tenant.example.com"
    older = [f"2026090{day}_020000-acme_tenant_example_com-aaaaaa" for day in range(1, 6)]
    bench.local_sets = {site: list(older)}
    status = backup_status.empty()
    backup_status.record_set(status, {"set_id": older[0], "site": site, "kind": "scheduled", "stamp": older[0][:15],
                                      "image_tag": "v0.4.0", "image_id": "sha256:id"}, "verified")
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    report = _backup(bench)
    removed = [text for text in bench.staged if text.startswith("rm -rf ")]
    assert removed and all(older[0] not in text for text in removed), "the newest verified set is protected"
    assert any(older[1] in text for text in removed)
    assert all(f"/home/frappe/backups/sets/{site}/" in text and f"/home/frappe/backup-secrets/{site}/" in text for text in removed)
    assert report["pruned"][site]


def test_a_corrupt_status_file_stops_pruning_but_not_the_backup(host):
    _prepare()
    path = backup.status_path(RELEASE, admin.ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    bench = StagingBench([SAME, SAME])
    bench.local_sets = {"acme.tenant.example.com": [f"2026090{day}_020000-acme_tenant_example_com-aaaaaa" for day in range(1, 5)]}
    report = _backup(bench)
    assert report["sets"] and report["status_state"] == "corrupt"
    assert not [text for text in bench.staged if text.startswith("rm -rf ")], "no delete decides on a corrupt record"
    assert "状态" in report["warnings"][0]


def test_the_cli_wires_backup_with_and_without_sync(monkeypatch):
    seen = {}

    def fake(resolved, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "sites": {}, "sets": {}, "failed": {}, "deferred": {}, "pruned": {}, "warnings": []}
    monkeypatch.setattr(backup, "backup", fake)
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["backup"]) == 0 and seen["sync"] is False
    assert admin.main(["backup", "--sync"]) == 0 and seen["sync"] is True


def _staged_set(bench, site="acme.tenant.example.com"):
    """Run one real backup window so a genuine set exists in the fake's staging area."""
    report = _backup(bench)
    return report["sets"][site]


def test_a_set_is_complete_only_after_both_manifests_are_read_back_and_agree(host):
    """Two snapshot ids only say something was uploaded. complete means the data half's
    set.json and the secret half's pair.json were read out again and describe one set."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    status = backup_status.empty()
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=restic, status=status)
    assert outcome["complete"] == [set_doc["set_id"]] and not outcome["failed"]
    row = status["sets"][set_doc["set_id"]]
    assert row["state"] == "complete" and row["data_snapshot"] and row["secrets_snapshot"]
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"]["set_id"] == set_doc["set_id"]
    dumps = [command for command in restic.calls if "dump" in command]
    assert len(dumps) == 2 and {command[-1].rsplit("/", 1)[-1] for command in dumps} == {"set.json", "pair.json"}
    for command in dumps:
        snapshot = command[command.index("dump") + 1]
        assert len(snapshot) == 64, "a full snapshot id addresses exactly one snapshot"


def test_a_half_uploaded_set_stays_pending_and_the_next_run_only_sends_the_missing_side(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    status = backup_status.empty()
    broken = _restic(bench, fail=(("secrets", "backup"),))
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=broken, status=status)
    assert outcome["pending"] == [set_doc["set_id"]] and "secrets" in outcome["failed"][set_doc["set_id"]]
    row = status["sets"][set_doc["set_id"]]
    assert row["state"] == "data_uploaded" and row["secrets_snapshot"] is None
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"] is None

    healed = _restic(bench)
    healed.state["data"] = broken.state["data"]        # the data half is already there
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=healed, status=status)
    assert outcome["complete"] == [set_doc["set_id"]]
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"]["at"]


def test_a_recorded_snapshot_that_the_repository_no_longer_holds_falls_back_to_pending(host):
    """A set is not complete because the status file says so: every run asks the repository."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    status = backup_status.empty()
    restic = _restic(bench)
    backup.upload_sets(RELEASE, [set_doc], runner=restic, status=status)
    assert status["sets"][set_doc["set_id"]]["state"] == "complete"
    restic.state["secrets"].clear()                     # somebody deleted it over there
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=restic, status=status)
    assert outcome["pending"] == [set_doc["set_id"]]
    assert status["sets"][set_doc["set_id"]]["state"] in ("data_uploaded", "staged")


def test_manifests_that_do_not_agree_keep_the_set_pending_however_many_snapshots_exist(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    status = backup_status.empty()
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=_restic(bench, corrupt=("secrets",)), status=status)
    assert outcome["pending"] == [set_doc["set_id"]] and "配对" in outcome["failed"][set_doc["set_id"]]
    assert status["sets"][set_doc["set_id"]]["state"] != "complete"


def test_unreachable_storage_is_a_recorded_failure_and_never_a_silent_success(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    status = backup_status.empty()
    dead = _restic(bench, fail=(("data", "backup"), ("secrets", "backup"), ("data", "snapshots"), ("secrets", "snapshots")))
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=dead, status=status)
    assert outcome["complete"] == [] and set_doc["set_id"] in outcome["failed"]
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_attempt"]["ok"] is False


def test_restic_never_carries_the_repository_or_a_credential_on_its_argv(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    seen = []

    def recording(command, **kwargs):
        seen.append(command)
        return _restic(bench)(command, **kwargs)
    backup.upload_sets(RELEASE, [set_doc], runner=recording, status=backup_status.empty())
    restic_calls = [command for command in seen if any(word.startswith("backup-sync-") for word in command)]
    assert restic_calls
    joined = " ".join(word for command in restic_calls for word in command)
    assert "s3:" not in joined and "RESTIC_PASSWORD" not in joined and "AWS_" not in joined
    assert "--host dsherp" in joined and "run --rm -T" in joined
    data_call = next(command for command in restic_calls if "backup-sync-data" in command)
    assert data_call[-1] == f"/backups/tenant/sets/acme.tenant.example.com/{set_doc['set_id']}"
    secrets_call = next(command for command in restic_calls if "backup-sync-secrets" in command)
    assert secrets_call[-1] == f"/backups/tenant/acme.tenant.example.com/{set_doc['set_id']}"


def test_a_private_certificate_authority_is_handed_to_restic_only_when_one_is_installed(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    seen = []

    def recording(command, **kwargs):
        seen.append(" ".join(command))
        return _restic(bench)(command, **kwargs)
    backup.upload_sets(RELEASE, [set_doc], runner=recording, status=backup_status.empty())
    assert not any("RESTIC_CACERT" in call for call in seen)
    ca = admin.secrets_dir(RELEASE) / "backup_storage_ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    ca.chmod(0o600)
    seen.clear()
    backup.upload_sets(RELEASE, [set_doc], runner=recording, status=backup_status.empty())
    assert all(f"-v {ca}:/run/secrets/backup_storage_ca.pem:ro" in call and "RESTIC_CACERT" in call
               for call in seen if "backup-sync-" in call)


def test_backup_init_is_idempotent_and_refuses_without_repositories(host):
    from tests.test_admin_cli import UNCONFIGURED, _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    with pytest.raises(admin.Fault, match="DSHERP_BACKUP_REPOSITORY"):
        backup.backup_init(UNCONFIGURED, runner=_restic(bench, initialised=False))
    restic = _restic(bench, initialised=False)
    assert backup.backup_init(RELEASE, runner=restic) == {"data": "created", "secrets": "created"}
    assert backup.backup_init(RELEASE, runner=restic) == {"data": "kept", "secrets": "kept"}


def test_retiring_a_tenant_drops_it_only_after_its_final_set_is_complete_in_both_repositories(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME])
    bench.existing = {"acme.tenant.example.com"}
    restic = _restic(bench)
    report = admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=restic)
    steps = [name for name, _ in report["steps"]]
    assert steps.index("set") < steps.index("site"), "the final set is complete before the Site is destroyed"
    assert report["set_id"] in restic.state["data"] and report["set_id"] in restic.state["secrets"]
    assert bench.written[f"/home/frappe/backups/sets/acme.tenant.example.com/{report['set_id']}/set.json"]["kind"] == "retire"
    assert not admin.load_tenants(RELEASE)


def test_sync_uploads_pending_sets_expires_only_pairs_dropped_on_both_sides_and_checks_the_data(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    report = backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    assert report["ok"] and set_doc["set_id"] in report["complete"]
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_doc["set_id"]]["state"] == "complete"
    assert status["runs"]["sync"]["last_success"] and status["runs"]["check"]["last_success"]
    checks = [command for command in restic.calls if "check" in command]
    assert len(checks) == 2 and all(any(word.startswith("--read-data-subset=") for word in command) for command in checks), \
        "structure alone is not proof: a slice of the data is read every run"
    assert not [command for command in restic.calls if "forget" in command], "nothing is old enough to expire"


def test_retention_forgets_a_set_on_both_sides_or_on_neither(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    restic = _restic(bench)
    status = backup_status.empty()
    site = "acme.tenant.example.com"
    monthly = [f"{2025 + month // 12}{month % 12 + 1:02d}01_020000-acme_tenant_example_com-aaaaaa" for month in range(0, 20)]
    for set_id in monthly:
        doc = {"set_id": set_id, "site": site, "kind": "scheduled", "stamp": set_id[:15],
               "image_tag": "v0.4.0", "image_id": "sha256:id-v0.4.0"}
        import hashlib
        ids = {side: hashlib.sha256((set_id + side).encode()).hexdigest() for side in ("data", "secrets")}
        backup_status.record_set(status, doc, "complete", data_snapshot=ids["data"], secrets_snapshot=ids["secrets"])
        for side in ("data", "secrets"):
            restic.state[side][set_id] = {"id": ids[side], "path": backup.set_paths(RELEASE, site, set_id)[side]}
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    del restic.state["secrets"][monthly[0]]      # the oldest one is already gone on one side
    report = backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    dropped_ids = {snapshot for command in restic.calls if "forget" in command
                   for snapshot in command[command.index("forget") + 1:]}
    import hashlib
    assert hashlib.sha256((monthly[0] + "data").encode()).hexdigest() not in dropped_ids, \
        "a set the other repository no longer holds is not forgotten here"
    assert hashlib.sha256((monthly[1] + "data").encode()).hexdigest() in dropped_ids
    assert hashlib.sha256((monthly[-1] + "data").encode()).hexdigest() not in dropped_ids, "the newest complete set stays"
    assert [command for command in restic.calls if "prune" in command], "space is only reclaimed after forget"
    # A retried upload can leave more than one snapshot under the same tag; expiry takes them all.
    duplicate = hashlib.sha256((monthly[1] + "data").encode()).hexdigest()
    assert duplicate in dropped_ids
    assert report["forgotten"]
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert monthly[1] not in status["sets"], "a forgotten set leaves the record too"


def test_sync_refuses_to_expire_anything_while_the_status_file_is_corrupt(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    _staged_set(bench)
    path = backup.status_path(RELEASE, admin.ROOT)
    path.write_text("{not json")
    restic = _restic(bench)
    report = backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    assert not [command for command in restic.calls if "forget" in command or "prune" in command]
    assert report["ok"] is False and any("状态" in warning for warning in report["warnings"])


def test_a_failing_repository_check_is_reported_and_does_not_pass_as_success(host):
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    _staged_set(bench)
    report = backup.backup_sync(RELEASE, runner=_restic(bench, fail=(("data", "check"),)),
                                clock=lambda: 1_788_660_100.0)
    assert report["ok"] is False and report["check_ok"] is False
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["runs"]["check"]["last_attempt"]["ok"] is False and status["runs"]["check"]["last_success"] is None


def test_the_cli_wires_backup_init_and_backup_sync(monkeypatch):
    seen = {}
    monkeypatch.setattr(backup, "backup_init", lambda resolved, **kw: seen.setdefault("init", True) or {"data": "kept", "secrets": "kept"})
    monkeypatch.setattr(backup, "backup_sync", lambda resolved, **kw: {"ok": False, "complete": [], "pending": ["x"],
                                                                       "forgotten": [], "errors": ["boom"], "warnings": [],
                                                                       "check_ok": True})
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["backup-init"]) == 0 and seen["init"]
    assert admin.main(["backup-sync"]) == 1, "a run that left something pending is not a success"


def test_sync_demotes_a_set_whose_copy_disappeared_from_one_repository_and_heals_it(host):
    """The status file is a record, not evidence: every run asks both repositories what they
    hold, and a set that is no longer complete over there stops counting as complete here."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_doc["set_id"]]["state"] == "complete"

    lost = _restic(bench)
    lost.state["data"] = dict(restic.state["data"])          # somebody removed the secret half
    report = backup.backup_sync(RELEASE, runner=lost, clock=lambda: 1_788_660_200.0)
    assert any("不再完整" in error for error in report["errors"])
    assert set_doc["set_id"] in report["complete"], "the missing half is sent again in the same run"
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_doc["set_id"]]["state"] == "complete"

    gone = _restic(bench, missing=("secrets",))
    gone.state["data"] = dict(restic.state["data"])
    report = backup.backup_sync(RELEASE, runner=gone, clock=lambda: 1_788_660_300.0)
    assert report["ok"] is False and set_doc["set_id"] in report["pending"]
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_doc["set_id"]]["state"] != "complete", "it cannot be re-sent, so it is not complete"


def test_a_set_the_repositories_hold_but_the_record_forgot_is_adopted_and_can_expire(host):
    """Otherwise a leftover copy would live in the repositories forever: unknown to the
    record, so never verified, so never a candidate for expiry."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    status["sets"].pop(set_doc["set_id"])                 # the record loses it, the repositories do not
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    report = backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_200.0)
    assert any("收养" in warning for warning in report["warnings"])
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_doc["set_id"]]["state"] == "complete", "adopted, read back, understood again"


def test_a_leftover_snapshot_from_a_retried_upload_does_not_make_the_set_unverifiable(host):
    """A tag can carry more than one snapshot; the good pair is found rather than assumed to
    be the newest."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    backup.upload_sets(RELEASE, [set_doc], runner=restic, status=backup_status.empty())
    good = dict(restic.state["data"][set_doc["set_id"]])
    # An earlier attempt left a snapshot under the same tag whose manifest belongs elsewhere.
    stale = {"id": "e" * 64, "path": good["path"], "tags": good["tags"], "stale": True}
    calls = []

    def with_stale(command, **kwargs):
        calls.append(command)
        result = restic(command, **kwargs)
        if "snapshots" in command and "backup-sync-data" in command:
            rows = json.loads(result.stdout or "[]")
            rows.append({"id": stale["id"], "short_id": stale["id"][:8], "time": "2026-09-07T00:00:00Z",
                         "tags": stale["tags"], "paths": [stale["path"]]})
            result.stdout = json.dumps(rows)
        if "dump" in command and stale["id"] in command:
            result.returncode, result.stdout = 0, json.dumps({"set_id": "somebody_elses_set", "format": 1})
        return result
    status = backup_status.empty()
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=with_stale, status=status)
    assert outcome["complete"] == [set_doc["set_id"]], "the good pair is found behind the stale one"
    assert status["sets"][set_doc["set_id"]]["data_snapshot"] == good["id"]


def test_a_remote_copy_that_no_longer_describes_the_set_is_replaced_from_the_staging_area(host):
    """Existence is not enough: if what is over there does not describe this set, it is sent
    again from the copy this host still holds, and only the new pair may count."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    set_doc = _staged_set(bench)
    restic = _restic(bench)
    status = backup_status.empty()
    backup.upload_sets(RELEASE, [set_doc], runner=restic, status=status)
    # The data half over there is replaced by something describing another set.
    entry = restic.state["data"][set_doc["set_id"]]
    wrong = {"set_id": "20260101_000000-acme_tenant_example_com-zzzzzz", "format": 1}
    uploads = []

    def stale_content(command, **kwargs):
        result = restic(command, **kwargs)
        if "dump" in command and "backup-sync-data" in command and entry["id"] in command and not uploads:
            result.stdout = json.dumps(wrong)
        if "backup" in command and "backup-sync-data" in command:
            uploads.append(command)
        return result
    status["sets"][set_doc["set_id"]]["state"] = "staged"
    outcome = backup.upload_sets(RELEASE, [set_doc], runner=stale_content, status=status)
    assert outcome["complete"] == [set_doc["set_id"]]
    assert uploads, "the set was sent again rather than accepted as it was"
    assert status["sets"][set_doc["set_id"]]["state"] == "complete"


def test_a_failure_notification_reports_posted_only_when_the_delivery_succeeded(host, capsys):
    """The worker's notifier swallows a failed webhook; this path must not, because it is the
    one that speaks when the worker is down."""
    profile = admin.runtime_dir(RELEASE) / "context-worker-sites.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({"alert_webhook": "https://hooks.example.com/x", "sites": []}))
    posted = []

    class Client:
        def post(self, url, json=None, timeout=None):
            posted.append((url, json))
            return type("R", (), {"raise_for_status": lambda self: None})()
    outcome = backup.notify_failure(RELEASE, "dsherp-backup.service", profile_path=profile, client=Client())
    assert outcome == {"unit": "dsherp-backup.service", "webhook": "posted"}
    assert posted[0][0] == "https://hooks.example.com/x" and posted[0][1]["key"] == "backup_unit_failed"
    assert "dsherp-backup.service" in posted[0][1]["message"]
    assert "backup_unit_failed" in capsys.readouterr().out, "the journal line is written whatever happens"

    class Dead:
        def post(self, url, json=None, timeout=None):
            raise ConnectionError("no route to host")
    outcome = backup.notify_failure(RELEASE, "dsherp-backup.service", profile_path=profile, client=Dead())
    assert outcome["webhook"] == "failed" and outcome["error_class"] == "ConnectionError"

    class Rejecting:
        def post(self, url, json=None, timeout=None):
            def raise_for_status():
                raise RuntimeError("500")
            return type("R", (), {"raise_for_status": staticmethod(raise_for_status)})()
    assert backup.notify_failure(RELEASE, "u", profile_path=profile, client=Rejecting())["webhook"] == "failed"

    profile.write_text(json.dumps({"sites": []}))
    assert backup.notify_failure(RELEASE, "u", profile_path=profile, client=Client())["webhook"] == "not configured"
    assert backup.notify_failure(RELEASE, "u", profile_path=admin.runtime_dir(RELEASE) / "gone.json")["webhook"] == "not configured"


def test_the_cli_reports_a_failed_notification_with_a_non_zero_exit(monkeypatch):
    monkeypatch.setattr(backup, "notify_failure", lambda resolved, unit, **kw: {"unit": unit, "webhook": "failed"})
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["notify-failure", "dsherp-backup.service"]) == 1
    monkeypatch.setattr(backup, "notify_failure", lambda resolved, unit, **kw: {"unit": unit, "webhook": "posted"})
    assert admin.main(["notify-failure", "dsherp-backup.service"]) == 0


def test_unreachable_storage_is_reported_in_seconds_and_changes_nothing(host):
    """restic retries a dead endpoint for a quarter of an hour per call; the run asks once,
    up front, and stops."""
    from tests.test_admin_cli import _restic
    _prepare()
    bench = StagingBench([SAME, SAME])
    _staged_set(bench)
    dead = _restic(bench, fail=(("data", "cat"), ("secrets", "cat")))
    report = backup.backup_sync(RELEASE, runner=dead, clock=lambda: 1_788_660_100.0)
    probes = [command for command in dead.calls if "cat" in command]
    assert probes and all("--no-cache" in command for command in probes), \
        "restic answers `cat config` from its local cache, so the probe must bypass it"
    assert report["ok"] is False and report["check_ok"] is False
    assert any("data" in error for error in report["errors"])
    assert not [command for command in dead.calls if any(verb in command for verb in ("backup", "forget", "prune", "check"))], \
        "nothing is uploaded, expired or checked against a repository that did not answer"
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["runs"]["sync"]["last_attempt"]["ok"] is False


def test_a_restic_call_that_never_returns_becomes_a_refusal_not_a_hang(host):
    _prepare()
    import subprocess as sp

    def hanging(command, **kwargs):
        raise sp.TimeoutExpired(command, kwargs.get("timeout", 1))
    removed = []

    def hanging_then_listing(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return type("R", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""})()
        if command[:3] == ["docker", "rm", "-f"]:
            removed.append(command)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return hanging(command, **kwargs)
    with pytest.raises(admin.Fault, match="没有返回"):
        backup.restic(RELEASE, "data", ["cat", "config"], runner=hanging_then_listing, timeout=60)
    assert removed and removed[0][-1] == "abc123", "the container the timed-out client left behind is removed"
