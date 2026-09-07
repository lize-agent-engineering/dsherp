"""A restore drill proves the newest complete off-site set restores into an isolated stack
running the build the set recorded, matches the snapshot taken in its window, and decrypts."""
import json

import pytest

from dsherp import admin, backup, backup_status, restore_drill
from tests.test_admin_cli import DRIFTED, RELEASE, SAME, _restic, _tenant_row
from tests.test_admin_cli import host  # noqa: F401
from tests.test_backup_cli import StagingBench, _backup, _prepare

SITE = "acme.tenant.example.com"
PLATFORM = "platform.tenant.example.com"


class DrillRunner:
    """docker/compose for the drill stack: up, health, fetch, image inspection, volumes, down."""

    def __init__(self, source, *, healthy=True, volumes=False, image_id="sha256:id-v0.4.0"):
        self.source = source          # the bench whose staged files the repositories hold
        self.calls = []
        self.healthy = healthy
        self.volumes = volumes
        self.image_id = image_id
        self.fetched = {}

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        text = " ".join(command)
        out = ""
        if "ps" in command and "-q" in command:
            out = "cid-restore-backend\n"
        elif "ps" in command and "--format" in command and "json" in text:
            health = "healthy" if self.healthy else "starting"
            out = "\n".join(json.dumps({"Service": service, "Health": health})
                            for service in ("db", "redis-cache", "redis-queue", "backend"))
        elif command[:3] == ["docker", "volume", "ls"]:
            names = getattr(self, "volume_names", None)
            if names is not None:
                out = "\n".join(names) + "\n"
            else:
                out = "dsherp-restore_restore-db\n" if self.volumes else ""
        elif command[:2] == ["docker", "inspect"]:
            out = f"local/dsherp-frappe:v0.4.0 {self.image_id}\n"
        elif "run" in command and any(word.startswith("restore-fetch-") for word in command):
            side = "data" if "restore-fetch-data" in command else "secrets"
            self.fetched[side] = command[-1]
        elif "up" in command and "-d" in command:
            self.volumes = True
        elif "down" in command:
            if "-v" in command:
                self.volumes = False
        return type("Result", (), {"returncode": 0, "stdout": out, "stderr": ""})()


class DrillBench:
    """The isolated stack's bench: serves the fetched files from what the source staged."""

    def __init__(self, source, snapshot, *, decrypt_failed=(), site=SITE, expectations=()):
        self.source = source
        self.snapshot = snapshot
        self.expectations = list(expectations)
        self.decrypt_failed = list(decrypt_failed)
        self.site = site
        self.verbs = []
        self.scripts = []
        self.existing = set()

    def _fetched(self, path):
        """/home/frappe/fetched/<side>/backups/<bench>/... mirrors what the bench staged."""
        rest = path.split("/fetched/", 1)[1]
        side, _, tail = rest.partition("/backups/")
        _, _, inner = tail.partition("/")
        return ("/home/frappe/backups/" + inner) if side == "data" else ("/home/frappe/backup-secrets/" + inner)

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        self.verbs.append(" ".join(arguments))
        text = arguments[2] if arguments[:2] == ("sh", "-c") else ""
        if text.startswith("ls -d "):
            return text.split("ls -d ", 1)[1].split()[0].replace("*", "tenant") + "\n"
        if text.startswith("cat "):
            path = text.split("cat ", 1)[1].strip()
            if path.endswith("site_config_backup.json"):
                # What Frappe copies: the database password, the deployment keys, and the one
                # key without which nothing encrypted can be read again.
                return json.dumps({"db_password": "old-db-password", "encryption_key": "FERNET-KEY==",
                                   "host_name": "https://old-host", "dsherp_agent_sources": ["10.0.0.0/8"]})
            return json.dumps(self.source.written[self._fetched(path)])
        if "sha256sum" in text:
            names = [word for word in text.split() if word.endswith((".sql.gz", ".tar", ".json"))]
            lines = []
            for name in names:
                document = self.source.written.get(self._fetched(name.replace("/snapshot.json", "/set.json")))
                leaf = name.rsplit("/", 1)[-1]
                if leaf == "snapshot.json":
                    lines.append(f"{document['snapshot_sha256']}  {name}")
                elif leaf == "site_config_backup.json":
                    pair = self.source.written[self._fetched(name.rsplit("/", 1)[0] + "/pair.json")]
                    lines.append(f"{pair['config_sha256']}  {name}")
                else:
                    lines.append(f"{'ab' * 32}  {name}")
            return "\n".join(lines) + "\n"
        return ""

    def script(self, body, timeout=900, secrets=()):
        """The real Bench.script: an interpreter reading the body from stdin, so credentials
        never reach a command line."""
        self.scripts.append(body)
        self.verbs.append("script " + body.splitlines()[1][:40])
        if "_new_site" in body and "source_sql" not in body:
            self.existing.add(self.site)
            return "DSHERP_NEWSITE {}\n"
        if "source_sql" in body:
            return "DSHERP_RESTORED {}\n"
        if "update_site_config" in body:
            return "DSHERP_CONFIG []\n"
        return "DSHERP_DONE {}\n"

    def python(self, site, body, timeout=900):
        self.verbs.append("python " + site)
        if "DSHERP_SNAPSHOT" in body:
            return "DSHERP_SNAPSHOT " + json.dumps(self.snapshot) + "\n"
        if "DSHERP_DECRYPT" in body:
            return "DSHERP_DECRYPT " + json.dumps({"checked": 3, "failed": self.decrypt_failed}) + "\n"
        if "EXPECTED_CHANGES" in body:
            return "DSHERP_EXPECTATIONS " + json.dumps(self.expectations) + "\n"
        return "{}\n"

    def site_state(self, site):
        return "present" if site in self.existing else "absent"

    def site_exists(self, site):
        return self.site_state(site) == "present"


def _complete_set(host, sites=(SITE,)):
    """One real backup window per Site, then a real (faked-restic) sync, so the repositories
    hold a genuine set with genuine manifests."""
    _prepare()
    bench = StagingBench([SAME] * 4)
    report = _backup(bench)
    restic = _restic(bench)
    backup.backup_sync(RELEASE, runner=restic, clock=lambda: 1_788_660_100.0)
    return bench, restic, {site: doc for site, doc in report["sets"].items()}


def _drill(bench, restic, drill_runner, drill_bench, **kwargs):
    def runner(command, **kwargs2):
        if any(word.startswith("backup-sync-") for word in command):
            return restic(command, **kwargs2)
        return drill_runner(command, **kwargs2)
    return restore_drill.restore_drill(RELEASE, runner=runner, stack_bench_factory=lambda stack: drill_bench,
                                       clock=lambda: 1_788_736_000.0, sleep=lambda seconds: None, **kwargs)


def test_the_drill_restores_the_newest_complete_pair_in_an_isolated_stack_and_removes_it(host):
    bench, restic, sets = _complete_set(host)
    ca = admin.secrets_dir(RELEASE) / "backup_storage_ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    ca.chmod(0o600)
    drill_runner = DrillRunner(bench)
    drill_bench = DrillBench(bench, SAME)
    report = _drill(bench, restic, drill_runner, drill_bench, sites=[SITE])
    assert report["ok"] is True, report
    assert report["sites"][SITE]["set_id"] == sets[SITE]["set_id"]
    calls = [" ".join(command) for command in drill_runner.calls]
    up = next(call for call in calls if "up -d" in call)
    assert "-p dsherp-restore" in up and "compose.restore.yml" in up
    assert calls.index(up) < min(index for index, call in enumerate(calls) if "restore-fetch-" in call), \
        "the stack that runs the recorded build is up before anything is fetched"
    assert drill_runner.fetched["data"] == "/fetched" and drill_runner.fetched["secrets"] == "/fetched", \
        "each fetch container owns its whole volume; the bench sees the two halves side by side"
    fetches = [" ".join(command) for command in drill_runner.calls if "restore-fetch-" in " ".join(command)]
    assert all("RESTIC_CACERT" in call for call in fetches), "a private certificate authority reaches the fetch too"
    assert any("inspect" in call for call in calls), "the running image is checked against the set"
    scripts = "\n".join(drill_bench.scripts)
    assert "_new_site" in scripts and "restore" in scripts
    assert not any("--db-root-password" in verb or "--admin-password" in verb for verb in drill_bench.verbs), \
        "credentials go through the interpreter's stdin, never on a command line"
    assert "encryption_key" in scripts
    assert not any("migrate" in verb for verb in drill_bench.verbs), "a drill restores, it does not upgrade"
    assert calls[-1].endswith("down -v --remove-orphans") or "down -v" in calls[-1]
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sites"][SITE]["verified"]["last_success"]["set_id"] == sets[SITE]["set_id"]
    assert status["sets"][sets[SITE]["set_id"]]["state"] == "verified"
    assert status["runs"]["drill"]["last_success"]


def test_a_drift_a_failed_decryption_or_a_broken_pair_fails_the_drill_and_keeps_the_stack(host):
    for name, drill_bench_of, expect in (
            ("drift", lambda bench: DrillBench(bench, DRIFTED), "差异"),
            ("decrypt", lambda bench: DrillBench(bench, SAME, decrypt_failed=[["OAuth Client", "x", "client_secret"]]), "解密")):
        bench, restic, sets = _complete_set(host)
        drill_runner = DrillRunner(bench)
        drill_bench = drill_bench_of(bench)
        report = _drill(bench, restic, drill_runner, drill_bench, sites=[SITE])
        assert report["ok"] is False, name
        assert expect in report["sites"][SITE]["error"], (name, report["sites"][SITE]["error"])
        calls = [" ".join(command) for command in drill_runner.calls]
        assert "down" in calls[-1] and "-v" not in calls[-1].split("down", 1)[1], "the stack is kept for inspection"
        bundle = admin.runtime_dir(RELEASE) / "backups" / "drills" / report["drill_id"] / "report.json"
        assert bundle.exists()
        status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
        assert status["sites"][SITE]["verified"]["last_attempt"]["ok"] is False
        assert status["sites"][SITE]["verified"]["last_success"] is None


def test_the_restic_cache_alone_is_not_a_leftover_and_the_teardown_names_the_fetch_profile(host):
    """The cache volume survives an ordinary teardown; counting it as a leftover would make
    every drill after the first refuse to start."""
    bench, restic, sets = _complete_set(host)
    drill_runner = DrillRunner(bench)
    drill_runner.volume_names = ["dsherp-restore_restore-cache"]
    report = _drill(bench, restic, drill_runner, DrillBench(bench, SAME), sites=[SITE])
    assert report["ok"] is True, "a cache volume is not evidence of a failed drill"
    downs = [" ".join(command) for command in drill_runner.calls if "down" in command]
    assert downs and all("--profile fetch" in call for call in downs), \
        "the fetch services' volumes are in scope of the teardown"


def test_a_leftover_failed_drill_blocks_the_next_one_until_it_is_discarded(host):
    bench, restic, sets = _complete_set(host)
    drill_runner = DrillRunner(bench, volumes=True)
    with pytest.raises(admin.Fault, match="上一次演练"):
        _drill(bench, restic, drill_runner, DrillBench(bench, SAME), sites=[SITE])
    report = _drill(bench, restic, drill_runner, DrillBench(bench, SAME), sites=[SITE], discard_failed=True)
    assert report["ok"] is True
    assert any("down" in " ".join(command) and "-v" in command for command in drill_runner.calls)


def test_the_drill_runs_the_build_the_set_recorded_and_refuses_another_one(host):
    bench, restic, sets = _complete_set(host)
    drill_runner = DrillRunner(bench, image_id="sha256:some-other-build")
    report = _drill(bench, restic, drill_runner, DrillBench(bench, SAME), sites=[SITE])
    assert report["ok"] is False
    assert "sha256:some-other-build" in report["sites"][SITE]["error"] or "镜像" in report["sites"][SITE]["error"]
    assert not any("_new_site" in (script or "") for script in DrillBench(bench, SAME).scripts)


def test_a_site_without_a_complete_pair_is_reported_rather_than_silently_skipped(host):
    bench, restic, sets = _complete_set(host)
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    for row in status["sets"].values():
        row["state"] = "data_uploaded"
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    report = _drill(bench, restic, DrillRunner(bench), DrillBench(bench, SAME), sites=[SITE])
    assert report["ok"] is False and "没有" in report["sites"][SITE]["error"]


def test_the_cli_wires_the_drill_and_reports_a_failed_verification_as_a_failure(monkeypatch):
    seen = {}

    def fake(resolved, sites, **kwargs):
        seen["sites"], seen["kwargs"] = sites, kwargs
        return {"ok": False, "drill_id": "x", "sites": {}}
    monkeypatch.setattr(restore_drill, "restore_drill", fake)
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["restore-drill"]) == 1
    assert seen["sites"] is None and seen["kwargs"]["discard_failed"] is False
    assert admin.main(["restore-drill", SITE, "--discard-failed"]) == 1
    assert seen["sites"] == [SITE] and seen["kwargs"]["discard_failed"] is True


def test_restore_site_refuses_an_existing_site_a_wrong_build_or_an_unpaired_set(host):
    """The cold-start contract: never overwrite, never restore onto another build, never
    restore from a set whose two halves were not both proven."""
    bench, restic, sets = _complete_set(host)
    set_id = sets[SITE]["set_id"]
    existing = StagingBench([SAME])
    existing.existing = {SITE}
    with pytest.raises(admin.Fault, match="已经存在"):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: existing, runner=restic)

    fresh = StagingBench([SAME])
    fresh.existing = set()

    def other_build(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            result = restic(command, **kwargs)
            result.stdout = "registry.example.com/dsherp/dsherp-frappe:v0.4.0 sha256:another-build\n"
            return result
        return restic(command, **kwargs)
    with pytest.raises(admin.Fault, match="sha256:another-build"):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: fresh, runner=other_build)
    assert not any("restore" in verb for verb in fresh.verbs)

    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    status["sets"][set_id]["secrets_snapshot"] = None
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    with pytest.raises(admin.Fault, match="完整配对"):
        restore_drill.restore_site(RELEASE, SITE, set_id=set_id, bench_factory=lambda kind: fresh, runner=restic)


def test_the_cli_wires_restore_site(monkeypatch):
    seen = {}
    monkeypatch.setattr(restore_drill, "restore_site",
                        lambda resolved, site, **kwargs: seen.update({"site": site, **kwargs}) or {"clean": True})
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["restore-site", SITE, "--set", "20260906_020007-acme_tenant_example_com-aaaaaa"]) == 0
    assert seen["site"] == SITE and seen["set_id"].endswith("-aaaaaa")


def test_a_migration_drill_restores_an_older_set_into_the_new_build_migrates_and_judges(host):
    """The automated form of G2 (D4): before a release touches production, take a set from the
    build being upgraded away from, restore it into the isolated stack running the NEW build,
    migrate, and compare - a DocType change without a migration path shows up here."""
    bench, restic, sets = _complete_set(host)
    drill_runner = DrillRunner(bench, image_id="sha256:id-v0.5.0")
    drill_bench = DrillBench(bench, SAME)
    report = restore_drill.migrate_drill(RELEASE, "v0.5.0", sites=[SITE], root=admin.ROOT,
                                         runner=lambda command, **kwargs: (
                                             restic(command, **kwargs) if any(word.startswith("backup-sync-") for word in command)
                                             else drill_runner(command, **kwargs)),
                                         stack_bench_factory=lambda stack: drill_bench,
                                         clock=lambda: 1_788_736_000.0, sleep=lambda seconds: None)
    assert report["ok"] is True, report
    assert report["tag"] == "v0.5.0" and report["sites"][SITE]["set_id"] == sets[SITE]["set_id"]
    calls = [" ".join(command) for command in drill_runner.calls]
    up = next(call for call in calls if "up -d" in call)
    assert "-p dsherp-restore" in up
    verbs = drill_bench.verbs
    assert any("migrate" in verb for verb in verbs), "a migration drill migrates; a restore drill does not"
    scripts = "\n".join(drill_bench.scripts)
    assert "source_sql" in scripts, "the old set is restored before it is migrated"
    assert scripts.index("source_sql") < len(scripts), "restore comes first"
    assert report["sites"][SITE]["patches_executed"] == [] or isinstance(report["sites"][SITE]["patches_executed"], list)


def test_a_migration_that_changes_data_without_declaring_it_fails_the_drill(host):
    bench, restic, sets = _complete_set(host)
    drill_bench = DrillBench(bench, DRIFTED)          # the Site's data differs after migrate
    drill_runner = DrillRunner(bench, image_id="sha256:id-v0.5.0")
    report = restore_drill.migrate_drill(RELEASE, "v0.5.0", sites=[SITE], root=admin.ROOT,
                                         runner=lambda command, **kwargs: (
                                             restic(command, **kwargs) if any(word.startswith("backup-sync-") for word in command)
                                             else drill_runner(command, **kwargs)),
                                         stack_bench_factory=lambda stack: drill_bench,
                                         clock=lambda: 1_788_736_000.0, sleep=lambda seconds: None)
    assert report["ok"] is False
    assert "差异" in report["sites"][SITE]["error"]
    downs = [" ".join(command) for command in drill_runner.calls if "down" in command]
    assert downs and "-v" not in downs[-1].split("down", 1)[1], "a failed drill keeps its stack"


def test_the_cli_wires_the_migration_drill(monkeypatch):
    seen = {}
    def fake(resolved, tag, sites=None, **kwargs):
        seen.update({"tag": tag, "sites": sites, **kwargs})
        return {"ok": True, "sites": {}}
    monkeypatch.setattr(restore_drill, "migrate_drill", fake)
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["migrate-drill", "v0.5.0", SITE]) == 0
    assert seen["tag"] == "v0.5.0" and seen["sites"] == [SITE]


class ColdStartBench(StagingBench):
    """A real host's bench during a cold start: serves the fetched halves from what was staged,
    records every flag it was asked to set, and can be told to fail the decryption check."""

    def __init__(self, *args, decrypt_failed=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.existing = set()
        self.decrypt_failed = list(decrypt_failed)
        self.flag_history = []

    def _staged(self, path):
        rest = path.split("/incoming/", 1)[1]
        side, _, tail = rest.partition("/backups/")
        _, _, inner = tail.partition("/")
        return ("/home/frappe/backups/" + inner) if side == "data" else ("/home/frappe/backup-secrets/" + inner)

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        text = arguments[2] if arguments[:2] == ("sh", "-c") else ""
        if "/incoming/" in text:
            self.verbs.append(" ".join(arguments))
            if text.startswith("ls -d "):
                return text.split("ls -d ", 1)[1].split()[0].replace("*", "tenant") + "\n"
            if text.startswith("cat "):
                path = text.split("cat ", 1)[1].strip()
                if path.endswith("site_config_backup.json"):
                    return json.dumps({"db_password": "old", "encryption_key": "FERNET-KEY=="})
                return json.dumps(self.written[self._staged(path)])
            if "sha256sum" in text:
                lines = []
                for name in [w for w in text.split() if w.endswith((".sql.gz", ".tar", ".json"))]:
                    leaf = name.rsplit("/", 1)[-1]
                    if leaf == "snapshot.json":
                        lines.append(f"{self.written[self._staged(name.replace('/snapshot.json', '/set.json'))]['snapshot_sha256']}  {name}")
                    elif leaf == "site_config_backup.json":
                        lines.append(f"{self.written[self._staged(name.rsplit('/', 1)[0] + '/pair.json')]['config_sha256']}  {name}")
                    else:
                        lines.append(f"{'ab' * 32}  {name}")
                return "\n".join(lines) + "\n"
            return ""
        if arguments[:2] == ("bench", "--site") and arguments[3] == "set-config":
            self.flag_history.append((arguments[5], arguments[6]))
        return super().run(*arguments, stdin=stdin, timeout=timeout, secrets=secrets)

    def script(self, body, timeout=900, secrets=()):
        self.scripts.append(body) if hasattr(self, "scripts") else None
        if "source_sql" in body:
            self.existing.add(SITE)
            return "DSHERP_RESTORED {}\n"
        if "update_site_config" in body:
            payload = json.loads(body.split("payload=", 1)[1].split("\n", 1)[0])
            for key, value in payload["values"].items():
                self.flag_history.append((key, str(value)))
            return "DSHERP_CONFIG []\n"
        return "DSHERP_DONE {}\n"

    def python(self, site, body, timeout=900):
        if "DSHERP_DECRYPT" in body:
            return "DSHERP_DECRYPT " + json.dumps({"checked": 3, "failed": self.decrypt_failed}) + "\n"
        return super().python(site, body, timeout=timeout)


def _cold_start(host, **bench_kwargs):
    bench, restic, sets = _complete_set(host)
    cold = ColdStartBench([SAME, SAME], **bench_kwargs)
    cold.written = bench.written
    cold.snapshots = [SAME]
    return cold, restic, sets


def test_a_cold_start_keeps_the_site_closed_until_the_last_check_and_opens_it_only_then(host):
    cold, restic, sets = _cold_start(host)
    report = restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic,
                                        provision=lambda **options: None)
    assert report["clean"] is True and report["maintenance"] == "released"
    flags = [(key, value) for key, value in cold.flag_history if key == "maintenance_mode"]
    assert flags[0] == ("maintenance_mode", "1"), "closed as soon as the site exists on this host"
    assert flags[-1] == ("maintenance_mode", "0"), "opened only at the very end"
    assert ("maintenance_mode", "0") not in flags[:-1], "never opened in between"


def test_a_failed_decryption_leaves_the_cold_started_site_closed_and_says_so(host):
    """The comparison used to be the only failure that kept maintenance on; a decryption
    failure, a snapshot error or a failing second provision all left the Site open (R4)."""
    cold, restic, sets = _cold_start(host, decrypt_failed=[["User", "Administrator", "api_secret"]])
    with pytest.raises(admin.Fault, match="无法解密"):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic,
                                   provision=lambda **options: None)
    flags = [value for key, value in cold.flag_history if key == "maintenance_mode"]
    assert flags and flags[-1] == "1" and "0" not in flags
    report = json.loads((admin.runtime_dir(RELEASE) / "backups" / f"restore-{SITE}.json").read_text())
    assert report["maintenance"] == "kept" and "无法解密" in report["error"]


def test_a_failing_second_provision_also_keeps_the_site_closed(host):
    cold, restic, sets = _cold_start(host)
    calls = []

    def provision(**options):
        calls.append(options)
        if len(calls) == 2:
            raise admin.Fault("synthetic provision failure")
    with pytest.raises(admin.Fault, match="synthetic provision failure"):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic, provision=provision)
    flags = [value for key, value in cold.flag_history if key == "maintenance_mode"]
    assert flags[-1] == "1" and "0" not in flags


def test_the_secret_half_is_fetched_onto_the_secrets_volume_and_both_halves_are_removed_afterwards(host):
    """Both halves used to land on the data backups volume, which the data-side sync
    container reads whole: one restore handed it the encryption key (R5)."""
    cold, restic, sets = _cold_start(host)
    restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic, provision=lambda **options: None)
    mounts = {}
    for command in restic.calls:
        if "restore" in command:
            side = next(w for w in command if w.startswith("backup-sync-")).removeprefix("backup-sync-")
            mounts[side] = command[command.index("-v") + 1]
    assert mounts["data"].endswith("tenant-backups:/incoming")
    assert mounts["secrets"].endswith("tenant-backup-secrets:/incoming")
    assert mounts["data"] != mounts["secrets"]
    removed = [verb for verb in cold.verbs if verb.startswith("sh -c rm -rf ")]
    assert any("/home/frappe/backups/incoming/data" in verb for verb in removed)
    assert any("/home/frappe/backup-secrets/incoming/secrets" in verb for verb in removed)


def test_the_halves_are_removed_even_when_the_cold_start_fails(host):
    cold, restic, sets = _cold_start(host, decrypt_failed=[["User", "Administrator", "api_secret"]])
    with pytest.raises(admin.Fault):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic, provision=lambda **options: None)
    removed = [verb for verb in cold.verbs if verb.startswith("sh -c rm -rf ")]
    assert len(removed) == 2


def test_the_first_provisioning_of_a_cold_start_publishes_nothing_and_the_second_opens_it(host, monkeypatch):
    """The reviewer's probe: the real provision_tenant wired into the real restore_site. The
    first call must run in its closed form - maintenance set the moment the Site exists, the
    enterprise recorded as Provisioning, no tenant-list entry, no endpoint, no ingress - and
    only the ordinary second call may mark it Ready and publish it (R4)."""
    cold, restic, sets = _cold_start(host)
    timeline = []

    def create_site(bench, resolved, site, *args, **kwargs):
        new = site not in bench.existing
        bench.existing.add(site)
        timeline.append(("site", "created" if new else "kept", cold.site_config.get((site, "maintenance_mode"))))
        return "created" if new else "kept"

    def enterprise(bench, resolved, slug, site, status="Ready"):
        timeline.append(("enterprise", status, cold.site_config.get((site, "maintenance_mode"))))
        return "created"

    def publish(name):
        def record(*args, **kwargs):
            timeline.append((name, None, cold.site_config.get((SITE, "maintenance_mode"))))
            return "/synthetic/ingress" if name == "ingress" else False
        return record

    monkeypatch.setattr(admin, "ensure_site", create_site)
    monkeypatch.setattr(admin, "ensure_enterprise", enterprise)
    monkeypatch.setattr(admin, "render_ingress", publish("ingress"))
    monkeypatch.setattr(admin, "ensure_platform_endpoints", publish("endpoints"))
    monkeypatch.setattr(admin, "save_tenants", publish("tenant-list"))
    for name, value in {"ensure_bench": [], "ensure_app": "installed", "ensure_scheduler_enabled": "enabled",
                        "agent_sources": [],
                        "ensure_runtime_identity": {"state": "kept"}, "ensure_site_config": [],
                        "ensure_system_settings": [], "ensure_oauth_client": {"state": "kept"},
                        "ensure_social_login_key": {"state": "kept"}, "load_tenants": [],
                        "ensure_healthy": {"site": SITE}}.items():
        monkeypatch.setattr(admin, name, lambda *a, _v=value, **k: _v)
    report = restore_drill.restore_site(
        RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic,
        provision=lambda **options: admin.provision_tenant(RELEASE, "acme", bench_factory=lambda kind: cold, **options))
    assert report["clean"] is True
    first_enterprise = next(item for item in timeline if item[0] == "enterprise")
    assert first_enterprise[1] == "Provisioning" and first_enterprise[2] == "1", timeline
    before_ready = timeline[:next(i for i, item in enumerate(timeline) if item == ("enterprise", "Ready", "1"))]
    assert not any(item[0] in ("ingress", "endpoints", "tenant-list") for item in before_ready), timeline
    assert [item for item in timeline if item[0] == "enterprise"][-1][1] == "Ready"
    assert any(item[0] == "ingress" for item in timeline), "the ordinary second call publishes"


def test_a_provisioner_that_cannot_run_closed_is_refused_rather_than_run_open(host):
    cold, restic, sets = _cold_start(host)
    with pytest.raises(admin.Fault, match="closed=True"):
        restore_drill.restore_site(RELEASE, SITE, bench_factory=lambda kind: cold, runner=restic,
                                   provision=lambda: None)
    flags = [value for key, value in cold.flag_history if key == "maintenance_mode"]
    assert "0" not in flags


def test_closed_provisioning_moves_only_a_ready_enterprise_and_ordinary_provisioning_only_a_provisioning_one():
    """An operator's Disabled or Failed is a decision of its own; a recovery must not undo it
    (third-round R4). The real transitions run in tests/integration/test_enterprise_recovery_state.py;
    this pins the two conditions the generated script carries."""
    import inspect
    source = inspect.getsource(admin.ensure_enterprise)
    assert "values['status']=='Provisioning' and doc.status=='Ready'" in source
    assert "values['status']=='Ready' and doc.status=='Provisioning'" in source
    assert "doc.status!='Provisioning'" not in source, "anything but Ready must be left alone"
