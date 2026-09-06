"""dsherp-admin backup: one stable window per Site (held server-side and worker-side,
quiesced, drained) produces the four pieces and the G2 snapshot, stages them as one set in
two volumes, and records exactly what happened."""
import fcntl
import json

import pytest

from dsherp import admin, backup, backup_sets, backup_status, site_holds
from tests.test_admin_cli import RELEASE, RUNNING_NEW, SAME, SnapshotBench, _tenant_row
from tests.test_admin_cli import host  # noqa: F401  the autouse fixture that isolates runtime and secrets dirs


class StagingBench(SnapshotBench):
    """SnapshotBench plus the staging verbs: writability probe, sha256sum, cp/mv, cat > manifest."""

    def __init__(self, *args, **kwargs):
        self.active_writers = kwargs.pop("active_writers", [0])
        super().__init__(*args, **kwargs)
        self.written = {}
        self.staged = []
        self.local_sets = []

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        text = arguments[2] if arguments[:2] == ("sh", "-c") else ""
        if any(mark in text for mark in ("/home/frappe/backups", "/home/frappe/backup-secrets", "sha256sum", "wc -c")):
            self.calls.append(("run",) + arguments[:3])
            self.verbs.append(" ".join(arguments))
            self.staged.append(text)
            if "cat > " in text:
                self.written[text.split("cat > ", 1)[1].split()[0]] = json.loads(stdin)
                return ""
            if "sha256sum" in text:
                names = [word for word in text.split() if word.endswith((".sql.gz", ".tar", ".json"))]
                return "".join(f"{'ab' * 32}  {name}\n" for name in names)
            if "wc -c" in text:
                return "4096\n"
            if text.startswith("ls -1 ") and "/sets/" in text:
                site = text.split("/sets/", 1)[1].split()[0]
                names = self.local_sets if isinstance(self.local_sets, list) else self.local_sets.get(site, [])
                return "".join(name + "\n" for name in names)
            if "test -w" in text:
                return "" if self.config.get("staging_unwritable") else "writable\n"
            return ""
        return super().run(*arguments, stdin=stdin, timeout=timeout, secrets=secrets)

    def python(self, site, body, timeout=900):
        if "DSHERP_WRITERS" in body:
            self.calls.append(("writers", site))
            only = getattr(self, "drain_only", None)
            if only is not None and site != only:
                return "DSHERP_WRITERS " + json.dumps({"jobs": 0, "connections": 0}) + "\n"
            remaining = self.active_writers.pop(0) if self.active_writers else 0
            return "DSHERP_WRITERS " + json.dumps({"jobs": remaining, "connections": 0}) + "\n"
        if "frappe.__version__" in body:
            return "16.31.0\n"
        return super().python(site, body, timeout=timeout)


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
