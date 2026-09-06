"""Operating one deployment: every step asks what exists before it changes anything."""
import hashlib
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
    # The release manifests a build machine ships with the images, delivered to the host's
    # runtime directory: release and rollback refuse to touch a site without one.
    for tag in ("v0.3.0", "v0.4.0"):
        assert not (admin.ROOT / "infra" / "releases" / f"{tag}.json").exists(), "test tags must not collide with real manifests"
        _manifest(tmp_path / "state" / "manifests" / f"{tag}.json", tag)
    return tmp_path


def _fresh_host(tag="v0.4.0"):
    """Forget a release record AND the current-version record: the next release is a first one again."""
    admin.forget_release(RELEASE, tag)
    current = admin.runtime_dir(RELEASE) / "releases" / "current.json"
    if current.exists():
        current.unlink()


def _manifest(path, tag, image_id=None, *, image=None, registry="registry.example.com/dsherp"):
    """A release manifest as infra/release_images.py writes it; ids default to what _docker reports."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frappe = image or f"{registry}/dsherp-frappe:{tag}"
    payload = {"tag": tag, "images": {frappe: {"id": image_id if image_id is not None else f"sha256:id-{tag}"},
                                      f"{registry}/dsherp-worker:{tag}": {"id": f"sha256:worker-{tag}"}}}
    path.write_text(json.dumps(payload))
    return path


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
            if "--archived-sites-path" in arguments:
                archive = arguments[arguments.index("--archived-sites-path") + 1]
                name = arguments[2] if not self.config.get("archive_collision") else arguments[2] + "1"
                if not self.config.get("archive_lost"):
                    self.config.setdefault("archived", {}).setdefault(archive, []).append(name)
        if arguments[:2] == ("sh", "-c") and "test -w" in arguments[2]:
            return "" if self.config.get("archive_unwritable") else "writable\n"
        if arguments[:2] == ("sh", "-c") and arguments[2].startswith("ls -1 "):
            path = arguments[2].split()[2]
            archived = self.config.get("archived", {})
            if path in archived:
                return "".join(name + "\n" for name in sorted(archived[path]))
            for archive, names in archived.items():
                for name in names:
                    if path == f"{archive}/{name}/private/backups":
                        return "20260905_120000-acme-database.sql.gz\n20260905_120000-acme-files.tar\n"
            return ""
        if arguments[:2] == ("sh", "-c") and "site_config.json && echo present" in arguments[2]:
            path = arguments[2].split()[2].rsplit("/site_config.json", 1)[0]
            archive, _, name = path.rpartition("/")
            return "present\n" if name in self.config.get("archived", {}).get(archive, []) else ""
        return ""

    def python(self, site, body, timeout=900):
        self.calls.append(("python", site, body.splitlines()[0][:40]))
        if "dsherp_runtime_user" in body and "assert" in body:
            return json.dumps({"site": site, "apps": ["dsherp_bridge", "erpnext", "frappe"],
                               "agent_sources": self.config.get("dsherp_agent_sources")}) + "\n"
        if "is_scheduler_disabled" in body and "enable_scheduler" in body:
            state = "kept" if self.config.get("scheduler") else "enabled"
            self.config["scheduler"] = True
            return json.dumps(state) + "\n"
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
            rotate = "rotate=True" in body
            if self.config.get("runtime_keys") and not rotate:
                return json.dumps({"user": f"runtime@{site}", "state": "kept"}) + "\n"
            self.config["runtime_keys"] = True
            return json.dumps({"user": f"runtime@{site}", "state": "rotated" if rotate else "issued",
                               "api_key": "k", "api_secret": "s"}) + "\n"
        if "System Settings" in body:
            values = json.loads(json.loads(body.splitlines()[0].split("=", 1)[1][len("json.loads("):-1]))
            defaults = json.loads(json.loads(body.splitlines()[1].split("=", 1)[1][len("json.loads("):-1]))
            changed = [key for key, value in values.items() if self.config.get(key) != value]
            changed += [key for key in defaults if not self.config.get(key)]
            self.config.update(values)
            self.config.update({key: defaults[key] for key in defaults if not self.config.get(key)})
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


def fake_networks(command, **kwargs):
    """docker network inspect answers for the agent and worker networks."""
    subnet = {"dsherp_agent": "192.168.0.0/20", "dsherp_worker": "192.168.16.0/20"}.get(command[3], "")
    return type("Result", (), {"returncode": 0 if subnet else 1, "stdout": subnet + "\n", "stderr": ""})()


def test_provisioning_a_new_tenant_creates_the_site_once_and_records_it(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    result = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    assert result["site"] == "acme.tenant.example.com"
    assert ("run", "bench", "new-site", "acme.tenant.example.com") in bench.calls
    assert dict(result["steps"])["site"] == "created"
    assert dict(result["steps"])["app"] == "created"
    assert dict(result["steps"])["password-login"] == "disabled"
    # A fresh Site cannot save System Settings without these; a rerun leaves them alone.
    assert dict(result["steps"])["system-settings"] == "bootstrapped:language,time_zone"
    assert bench.config["language"] == "zh" and bench.config["time_zone"] == "Asia/Shanghai"
    assert dict(result["steps"])["healthcheck"] == "acme.tenant.example.com"
    assert dict(result["steps"])["runtime-identity"] == "issued"
    assert result["runtime_identity"]["api_secret"] == "s"
    # The run capability may be used from run containers and from the host worker's loopback
    # path, whose packets arrive from the worker bridge; never left fail-open.
    assert bench.config["dsherp_agent_sources"] == ["192.168.0.0/20", "192.168.16.0/20"]
    assert dict(result["steps"])["scheduler"] == "enabled"
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
    admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.calls.clear()
    again = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    assert dict(again["steps"])["site"] == "kept"
    assert dict(again["steps"])["app"] == "kept"
    assert dict(again["steps"])["site-config"] == "kept"
    assert dict(again["steps"])["scheduler"] == "kept"
    # A rerun never rotates the runtime key silently, and never shows the secret again.
    assert dict(again["steps"])["runtime-identity"] == "kept"
    assert "runtime_identity" not in again
    assert dict(again["steps"])["password-login"] == "kept"
    assert dict(again["steps"])["system-settings"] == "kept"
    assert dict(again["steps"])["enterprise"] == "kept"
    assert dict(again["steps"])["oauth-client"] == "kept"
    assert dict(again["steps"])["social-login-key"] == "kept"
    assert not [call for call in bench.calls if call[:3] == ("run", "bench", "new-site")]
    assert len(admin.load_tenants(PROD)) == 1


def test_provisioning_without_control_secrets_stops_before_touching_a_container(host):
    bench = FakeBench()
    with pytest.raises(admin.Fault):
        admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    assert not [call for call in bench.calls if call[0] == "run"]


def test_a_tenant_slug_that_is_not_a_slug_never_reaches_a_command(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    for bad in ("Acme", "acme.evil", "../etc", ""):
        with pytest.raises(ValueError):
            admin.provision_tenant(PROD, bad, bench_factory=lambda kind: bench)
    assert not bench.calls


def _retire_bench():
    """A bench that can also answer the final backup's staging verbs and its snapshot."""
    bench = SnapshotBench([SAME])
    bench.existing = set()
    return bench


def test_retiring_a_tenant_archives_before_it_drops_and_leaves_the_ingress_correct(host):
    admin.ensure_secrets(RELEASE)
    bench = _retire_bench()
    admin.provision_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    admin.provision_tenant(RELEASE, "beta", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.calls.clear()
    result = admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=_restic(bench))
    verbs = [call for call in bench.calls if call[0] == "run"]
    assert ("run", "bench", "--site", "acme.tenant.example.com") in verbs
    assert ("run", "bench", "drop-site", "acme.tenant.example.com") in bench.calls
    assert verbs.index(("run", "bench", "--site", "acme.tenant.example.com")) < \
        verbs.index(("run", "bench", "drop-site", "acme.tenant.example.com"))
    assert dict(result["steps"])["site"] == "dropped"
    assert [row["slug"] for row in admin.load_tenants(RELEASE)] == ["beta"]
    ingress = (admin.runtime_dir(RELEASE) / "caddy" / "Caddyfile").read_text()
    assert "acme.tenant.example.com" not in ingress and "beta.tenant.example.com" in ingress


def test_retiring_moves_the_whole_site_into_the_persistent_archive_and_reports_the_path(host):
    """drop-site moves the site directory, and the backup just taken inside it, to the
    bench's archive; that archive must be on a volume, named explicitly, and read back."""
    admin.ensure_secrets(RELEASE)
    bench = _retire_bench()
    admin.provision_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.calls.clear(); bench.verbs.clear()
    result = admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=_restic(bench))
    drop = [verb for verb in bench.verbs if verb.startswith("bench drop-site")][0]
    assert f"--archived-sites-path {admin.ARCHIVE}" in drop
    assert result["archive"] == f"{admin.ARCHIVE}/acme.tenant.example.com"
    assert [name for name in result["backups"] if name.endswith("-database.sql.gz")], result["backups"]
    assert dict(result["steps"])["archive"] == result["archive"]
    # The archive location is proven writable before anything is backed up or dropped.
    order = [verb.split()[0] + " " + verb.split()[1] for verb in bench.verbs]
    assert order.index("sh -c") < order.index("bench --site") < order.index("bench drop-site")


def test_retiring_refuses_to_drop_anything_when_the_archive_location_is_not_writable(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.calls.clear(); bench.verbs.clear()
    bench.config["archive_unwritable"] = True
    with pytest.raises(admin.Fault, match="归档"):
        admin.retire_tenant(PROD, "acme", bench_factory=lambda kind: bench)
    assert not [verb for verb in bench.verbs if verb.startswith("bench")]
    assert [row["slug"] for row in admin.load_tenants(PROD)] == ["acme"]


def test_retiring_reports_a_collision_suffixed_archive_and_fails_loudly_when_none_appears(host):
    admin.ensure_secrets(RELEASE)
    bench = _retire_bench()
    admin.provision_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.config["archive_collision"] = True
    result = admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=_restic(bench))
    assert result["archive"] == f"{admin.ARCHIVE}/acme.tenant.example.com1"

    admin.provision_tenant(RELEASE, "beta", bench_factory=lambda kind: bench, runner=fake_networks)
    bench.snapshots = [SAME]
    bench.config["archive_lost"] = True
    with pytest.raises(admin.Fault, match="不要重跑"):
        admin.retire_tenant(RELEASE, "beta", bench_factory=lambda kind: bench, runner=_restic(bench))
    # The Site is gone but nothing else was touched: the operator must look before the list changes.
    assert [row["slug"] for row in admin.load_tenants(RELEASE)] == ["beta"]


def test_retiring_a_site_that_does_not_exist_is_refused_rather_than_treated_as_cleanup(host):
    admin.ensure_secrets(RELEASE)
    bench = FakeBench()
    with pytest.raises(admin.Fault):
        admin.retire_tenant(RELEASE, "ghost", bench_factory=lambda kind: bench)
    assert not [call for call in bench.calls if call[0] == "run"]


def test_doctor_names_what_is_missing_instead_of_failing_at_compose_time(host):
    findings = admin.doctor(PROD)
    # Only what the deployment itself lacks; never a finding that depends on a gitignored file.
    assert {row for row in findings if "控制面密钥" in row} == {
        f"缺少控制面密钥：{name}" for name in admin.control_secrets(PROD)}
    complete = admin.doctor(deploy_env.settings({"DSHERP_ENV": "dev"}), admin.ROOT)
    assert not [row for row in complete if "部署指纹清单缺少文件" in row]


class SnapshotBench(FakeBench):
    """A bench whose Site content can be made to change between snapshots.

    Snapshots are recognised by the reader's marker, backups by the set the fake writes
    into the site's private/backups, migrate/restore by their verbs."""

    def __init__(self, snapshots, active_runs=0, expectations=None, backup_pieces=4, active_writers=None):
        super().__init__(existing_sites=("acme.tenant.example.com", "platform.tenant.example.com"),
                         installed=("dsherp_bridge",))
        self.active_writers = list(active_writers or [0])
        self.drain_only = None
        self.written = {}
        self.staged = []
        self.local_sets = {}
        self.snapshots = list(snapshots)
        self.active_runs = active_runs
        self.expectations = expectations or []
        self.backup_pieces = backup_pieces
        self.site_config = {}
        self.archived = []
        self.site_status = None   # {'Queued': n, 'Running': n, ...}; defaults to active_runs as Running

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
        handled = ((arguments[:2] == ("bench", "--site") and arguments[3] in ("set-config", "backup"))
                   or (arguments[:2] == ("sh", "-c") and any(mark in arguments[2] for mark in ("private/backups", "archived/releases", "test -s "))))
        if handled:
            self.calls.append(("run",) + arguments[:3])
            self.verbs.append(" ".join(arguments))
        if arguments[:2] == ("bench", "--site") and arguments[3] == "set-config":
            self.site_config[(arguments[2], arguments[5])] = arguments[6]
            return ""
        if arguments[:2] == ("bench", "--site") and arguments[3] == "backup":
            return "Backup Summary for " + arguments[2] + "\n"
        if arguments[:2] == ("sh", "-c") and "ls -1 " in arguments[2] and "private/backups" in arguments[2]:
            path = arguments[2].split("ls -1 ", 1)[1].split()[0]
            slug = path.split("/sites/", 1)[1].split("/", 1)[0].replace(".", "_")
            pieces = [f"20260906_120000-{slug}-database.sql.gz", f"20260906_120000-{slug}-site_config_backup.json",
                      f"20260906_120000-{slug}-files.tar", f"20260906_120000-{slug}-private-files.tar"]
            return "".join(piece + "\n" for piece in pieces[:self.backup_pieces])
        if arguments[:2] == ("sh", "-c") and "cp " in arguments[2] and "archived/releases" in arguments[2]:
            self.archived.append(arguments[2])
            return ""
        if arguments[:2] == ("sh", "-c") and "test -s " in arguments[2]:
            return "present\n" if "missing" not in arguments[2] else ""
        return super().run(*arguments, stdin=stdin, timeout=timeout, secrets=secrets)

    def python(self, site, body, timeout=900):
        if "DSHERP_WRITERS" in body:
            self.calls.append(("writers", site))
            if self.drain_only is not None and site != self.drain_only:
                return "DSHERP_WRITERS " + json.dumps({"jobs": 0, "connections": 0}) + "\n"
            remaining = self.active_writers.pop(0) if self.active_writers else 0
            return "DSHERP_WRITERS " + json.dumps({"jobs": remaining, "connections": 0}) + "\n"
        if "frappe.__version__" in body:
            return "16.31.0\n"
        if "DSHERP_SNAPSHOT" in body:
            self.calls.append(("python-body", site, body))
            self.calls.append(("snapshot", site))
            self.verbs.append("snapshot " + site)
            return "DSHERP_SNAPSHOT " + json.dumps(self.snapshots.pop(0)) + "\n"
        if "EXPECTED_CHANGES" in body:
            return "DSHERP_EXPECTATIONS " + json.dumps(self.expectations) + "\n"
        if "DS Model Run" in body and "active" in body:
            counts = self.site_status or {"Queued": 0, "Running": self.active_runs, "Cancelling": 0, "NeedsInput": 0}
            if counts and all(isinstance(value, dict) for value in counts.values()):
                counts = counts.get(site, {"Queued": 0, "Running": 0, "Cancelling": 0, "NeedsInput": 0})
            counts = {status: counts.get(status, 0) for status in ("Queued", "Running", "Cancelling", "NeedsInput")}
            flags = {key: int(self.site_config.get((site, key), 0)) for key in ("maintenance_mode", "pause_scheduler", "dsherp_hold")}
            return json.dumps({"active": counts["Queued"] + counts["Running"] + counts["Cancelling"],
                               "running": counts["Running"] + counts["Cancelling"], "counts": counts, **flags}) + "\n"
        return super().python(site, body, timeout=timeout)


def _tenant_row():
    admin.save_tenants(PROD, [{"slug": "acme", "site": "acme.tenant.example.com",
                               "origin": "https://acme.tenant.example.com"}])


def _snap(values_by_table, singles=None, patches=("frappe.patches.v16_0.old",)):
    from dsherp import release_compare
    tables = {}
    for table, rows in values_by_table.items():
        tables[table] = {"columns": sorted({key for row in rows.values() for key in row}),
                         "rows": {name: {"hash": release_compare.row_hash(row), "values": row} for name, row in rows.items()}}
    return {"format": 1, "tables": tables, "singles": singles or {}, "auth": {}, "scope": {}, "row_counts": {},
            "patches": sorted(patches)}


def _restic(bench, fail=(), missing=(), corrupt=()):
    """A pair of repositories that hold whatever the bench staged. `fail` names (side, verb)
    pairs that error, `missing` names sides that lose the snapshot, `corrupt` names sides
    whose read-back manifest does not match."""
    state = {"data": {}, "secrets": {}}

    def answer(command):
        service = next(word for word in command if word.startswith("backup-sync-"))
        side = service.removeprefix("backup-sync-")
        verb = command[command.index(service) + 1]
        if (side, verb) in fail:
            return 1, "", f"Fatal: {verb} failed on the {side} repository"
        if verb == "backup":
            tags = [word for word in command if word.startswith(("set=", "site=", "kind="))]
            set_id = next(word for word in tags if word.startswith("set=")).removeprefix("set=")
            path = command[-1]
            if side not in missing:
                digest = hashlib.sha256((set_id + side).encode()).hexdigest()   # restic ids are 64 hex chars
                state[side][set_id] = {"id": digest, "path": path, "tags": tags}
            return 0, "", ""
        if verb == "snapshots":
            wanted = [word.removeprefix("set=") for word in command if word.startswith("set=")]
            rows = [{"id": row["id"], "short_id": row["id"][:8], "time": "2026-09-06T02:01:00Z",
                     "tags": row.get("tags") or [f"set={set_id}"], "paths": [row["path"]]}
                    for set_id, row in state[side].items() if not wanted or set_id in wanted]
            return 0, json.dumps(rows), ""
        if verb == "dump":
            path = command[-1]
            document = bench.written.get(_container_path(path))
            if document is None:
                return 1, "", f"file not found in snapshot: {path}"
            if side in corrupt:
                document = {**document, "set_sha256": "0" * 64}
            return 0, json.dumps(document), ""
        if verb in ("cat", "init", "forget", "prune", "check"):
            if verb == "cat" and not state[side]:
                return 1, "", "repository does not exist"
            return 0, "", ""
        return 0, "", ""

    def _container_path(path):
        """/backups/<bench>/... inside the sync container is /home/frappe/... on the bench."""
        rest = path.removeprefix("/backups/")
        _, _, tail = rest.partition("/")
        return ("/home/frappe/backups/" + tail) if tail.startswith("sets/") else ("/home/frappe/backup-secrets/" + tail)

    calls = []

    def runner(command, **kwargs):
        if command[:2] == ["docker", "compose"] and "run" in command and any(w.startswith("backup-sync-") for w in command):
            calls.append(list(command))
            code, out, err = answer(command)
            return type("Result", (), {"returncode": code, "stdout": out, "stderr": err})()
        return RUNNING_NEW(command, **kwargs)

    runner.state = state
    runner.calls = calls
    return runner


def _docker(images):
    """Answers `compose ps -q <service>` and `docker inspect` for the two bench services."""
    def runner(command, **kwargs):
        if command[:2] == ["docker", "compose"] and "ps" in command and "-q" in command:
            service = command[-1]
            return type("Result", (), {"returncode": 0, "stdout": f"cid-{service}\n", "stderr": ""})()
        if command[:2] == ["docker", "inspect"]:
            service = command[-1].removeprefix("cid-")
            image = images[service]
            return type("Result", (), {"returncode": 0, "stdout": f"{image} sha256:id-{image.rsplit(':', 1)[1]}\n", "stderr": ""})()
        return fake_networks(command, **kwargs)
    return runner


RUNNING_NEW = _docker({"backend": "registry.example.com/dsherp/dsherp-frappe:v0.4.0",
                       "platform-backend": "registry.example.com/dsherp/dsherp-frappe:v0.4.0"})
RUNNING_OLD = _docker({"backend": "registry.example.com/dsherp/dsherp-frappe:v0.3.0",
                       "platform-backend": "registry.example.com/dsherp/dsherp-frappe:v0.3.0"})


SAME = _snap({"tabItem": {"A": {"name": "A", "item_name": "a"}}, "tabDS Model Run": {"r": {"name": "r", "status": "Succeeded"}}})
DRIFTED = _snap({"tabItem": {"A": {"name": "A", "item_name": "edited"}}, "tabDS Model Run": {"r": {"name": "r", "status": "Succeeded"}}})
# A production deployment is configured for off-site backups; every destructive command
# refuses without them (test_production_refuses_to_retire_or_release_... covers that).
REPOSITORIES = {"DSHERP_BACKUP_REPOSITORY": "s3:https://s3.example.com/dsherp-data",
                "DSHERP_BACKUP_SECRETS_REPOSITORY": "s3:https://s3.example.com/dsherp-secrets"}
RELEASE = deploy_env.settings({**REPOSITORIES, **{k: v for k, v in [
    ("DSHERP_ENV", "prod"), ("DSHERP_PROJECT", "dsherp"), ("DSHERP_BASE_DOMAIN", "tenant.example.com"),
    ("DSHERP_PLATFORM_SLUG", "platform"), ("DSHERP_IMAGE_TAG", "v0.4.0"),
    ("DSHERP_IMAGE_REGISTRY", "registry.example.com/dsherp"), ("DSHERP_AGENT_UID", "1000"), ("DSHERP_AGENT_GID", "1000")]}})
UNCONFIGURED = {**RELEASE, "backup_repository": "", "backup_secrets_repository": ""}


BACK = deploy_env.settings({**REPOSITORIES, **{k: v for k, v in [
    ("DSHERP_ENV", "prod"), ("DSHERP_PROJECT", "dsherp"), ("DSHERP_BASE_DOMAIN", "tenant.example.com"),
    ("DSHERP_PLATFORM_SLUG", "platform"), ("DSHERP_IMAGE_TAG", "v0.3.0"),
    ("DSHERP_IMAGE_REGISTRY", "registry.example.com/dsherp"), ("DSHERP_AGENT_UID", "1000"), ("DSHERP_AGENT_GID", "1000")]}})


def test_a_release_binds_the_running_images_records_the_previous_tag_and_refuses_a_second_run_of_the_same_tag():
    """Review R6/R2: the record names what ran before and what runs now, verified against the
    containers, not only prod.env; a tag's pre-upgrade baseline is written once."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    stale = _docker({"backend": "registry.example.com/dsherp/dsherp-frappe:v0.3.0",
                     "platform-backend": "registry.example.com/dsherp/dsherp-frappe:v0.4.0"})
    untouched = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="backend"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=stale, from_tag="v0.3.0")
    assert not untouched.verbs
    with pytest.raises(admin.Fault, match="--from"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: SnapshotBench([SAME] * 4), runner=RUNNING_NEW)
    bench = SnapshotBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["previous_tag"] == "v0.3.0"
    assert report["images"]["backend"] == {"image": "registry.example.com/dsherp/dsherp-frappe:v0.4.0", "image_id": "sha256:id-v0.4.0"}
    record = json.loads((admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json").read_text())
    assert record["previous_tag"] == "v0.3.0" and record["tag"] == "v0.4.0" and "platform-backend" in record["images"]
    assert json.loads((admin.runtime_dir(RELEASE) / "releases" / "current.json").read_text())["tag"] == "v0.4.0"
    again = SnapshotBench([DRIFTED] * 4)
    with pytest.raises(admin.Fault, match="已有"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: again, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not again.verbs
    before = json.loads((admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "acme.tenant.example.com" / "before.json").read_text())
    assert before["tables"] == SAME["tables"]
    # the next release no longer needs --from: the current tag is on record
    admin.forget_release(RELEASE, "v0.4.0")
    later = SnapshotBench([SAME] * 4)
    assert admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: later, runner=RUNNING_NEW)["previous_tag"] == "v0.4.0"


def test_failure_after_migrate_or_an_unclean_result_keeps_the_site_in_maintenance_until_resumed_deliberately():
    """Review R1: only a completed, clean judgement reopens a site."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()

    class SnapshotBreaks(SnapshotBench):
        def python(self, site, body, timeout=900):
            if "DSHERP_SNAPSHOT" in body and len(self.snapshots) == 3:
                raise admin.Fault("容器命令失败（backend）：python -\nsnapshot failed on tabItem")
            return super().python(site, body, timeout=timeout)

    bench = SnapshotBreaks([SAME] * 4)
    with pytest.raises(admin.Fault, match="tabItem"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "1"
    partial = json.loads((admin.runtime_dir(RELEASE) / "releases" / "release-v0.4.0.json").read_text())
    assert partial["failed"]["site"] == "acme.tenant.example.com" and partial["failed"]["step"] == "snapshot-after"
    assert not (admin.runtime_dir(RELEASE) / "releases" / "current.json").exists()

    admin.forget_release(RELEASE, "v0.4.0")
    unclean = SnapshotBench([SAME, DRIFTED, SAME, SAME])
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: unclean, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"] is False
    assert unclean.site_config[("acme.tenant.example.com", "maintenance_mode")] == "1"
    assert unclean.site_config[("platform.tenant.example.com", "maintenance_mode")] == "0"  # that site was clean
    assert report["sites"]["acme.tenant.example.com"]["maintenance"] == "kept"
    assert not (admin.runtime_dir(RELEASE) / "releases" / "current.json").exists()
    admin.resume_site(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: unclean)
    assert unclean.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"


def test_a_rollback_requires_the_previous_images_running_and_keeps_maintenance_on_failure_or_drift():
    """Review R6/R1 for the recovery half."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME] * 4)
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    bench.snapshots = [SAME, SAME]
    with pytest.raises(admin.Fault, match="v0.3.0"):
        admin.rollback(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW)  # prod.env still v0.4.0
    with pytest.raises(admin.Fault, match="backend"):
        admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW)     # containers still v0.4.0
    assert not [verb for verb in bench.verbs if " restore " in verb]

    class RestoreBreaks(SnapshotBench):
        def run(self, *arguments, **kwargs):
            if arguments[:2] == ("bench", "--site") and arguments[3] == "restore":
                raise admin.Fault("容器命令失败（backend）：bench --site restore\ndump corrupt")
            return super().run(*arguments, **kwargs)

    broken = RestoreBreaks([SAME] * 4)
    _fresh_host()
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: broken, runner=RUNNING_NEW, from_tag="v0.3.0")
    broken.snapshots = [SAME, SAME]
    with pytest.raises(admin.Fault, match="restore"):
        admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: broken, runner=RUNNING_OLD)
    assert broken.site_config[("acme.tenant.example.com", "maintenance_mode")] == "1"
    partial = json.loads((admin.runtime_dir(RELEASE) / "releases" / "rollback-v0.4.0.json").read_text())
    assert partial["failed"]["step"] == "restore"

    drift = SnapshotBench([SAME] * 4)
    _fresh_host()
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: drift, runner=RUNNING_NEW, from_tag="v0.3.0")
    drift.snapshots = [DRIFTED, SAME]
    report = admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: drift, runner=RUNNING_OLD)
    assert report["clean"] is False and drift.site_config[("acme.tenant.example.com", "maintenance_mode")] == "1"
    assert json.loads((admin.runtime_dir(RELEASE) / "releases" / "current.json").read_text())["tag"] == "v0.4.0"
    # A fresh bench: the previous scenario deliberately left its Site in maintenance, and the
    # flags a quiesce restores are the ones it found (resume-site is the way out of that).
    clean = SnapshotBench([SAME] * 4)
    _fresh_host()
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: clean, runner=RUNNING_NEW, from_tag="v0.3.0")
    clean.snapshots = [SAME, SAME]
    report = admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: clean, runner=RUNNING_OLD)
    assert report["clean"] is True and clean.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"
    assert json.loads((admin.runtime_dir(RELEASE) / "releases" / "current.json").read_text())["tag"] == "v0.3.0"


def test_only_patches_this_migrate_executed_may_declare_changes_and_the_after_snapshot_hashes_like_the_before():
    """Review R5/R3: a declaration by a patch that did not run now is inert; the second
    snapshot is told which columns the first one had."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    drifted_old = _snap({"tabItem": {"A": {"name": "A", "item_name": "edited"}}, "tabDS Model Run": {"r": {"name": "r", "status": "Succeeded"}}},
                        patches=("frappe.patches.v16_0.old",))
    stale = SnapshotBench([SAME, drifted_old, SAME, SAME],
                          expectations=[{"patch": "frappe.patches.v16_0.old", "doctype": "Item", "fields": ["item_name"]}])
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: stale, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"] is False
    comparison = report["sites"]["acme.tenant.example.com"]["comparison"]
    assert comparison["differences"][0]["declared"] is False and comparison["expectations"] == []
    assert report["sites"]["acme.tenant.example.com"]["patches_executed"] == []
    bodies = [call for call in stale.calls if call[0] == "python-body"]
    after_body = [body for _, site, body in bodies if site == "acme.tenant.example.com"][1]
    assert "hash_columns=json.loads(" in after_body and "tabDS Model Run" in after_body


def test_a_release_quiesces_backs_up_archives_snapshots_migrates_compares_and_restores_the_site_flags():
    """The upgrade half of G2: every step in order, per site, tenants and the platform alike."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, SAME, SAME, SAME])
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    verbs = [verb for verb in bench.verbs]
    acme = [verb for verb in verbs if "acme.tenant.example.com" in verb or verb == "snapshot acme.tenant.example.com"]
    order = ["set-config" if "set-config" in v else "backup" if " backup " in v else "archive" if "archived/releases" in v
             else "migrate" if v.endswith("migrate") else "snapshot" if v.startswith("snapshot") else None for v in acme]
    order = [step for step in order if step]
    assert order == ["set-config", "set-config", "backup", "archive", "snapshot", "migrate", "snapshot", "set-config", "set-config"], order
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"  # restored at the end
    assert report["clean"] is True and report["tag"] == "v0.4.0"
    assert set(report["sites"]) == {"acme.tenant.example.com", "platform.tenant.example.com"}
    site = report["sites"]["acme.tenant.example.com"]
    assert site["backup"]["database"].endswith("acme_tenant_example_com-database.sql.gz")
    assert site["backup"]["database"].startswith(admin.ARCHIVED_RELEASES + "/v0.4.0/acme.tenant.example.com/")
    assert site["comparison"]["clean"] is True and site["comparison"]["summary"]["rows"] == 2
    releases = admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "acme.tenant.example.com"
    assert json.loads((releases / "before.json").read_text())["tables"] == SAME["tables"]
    assert (releases / "after.json").exists() and json.loads((releases / "backup.json").read_text())["database"] == site["backup"]["database"]
    assert Path(report["path"]).name.startswith("release-v0.4.0-") and (Path(report["path"]).parent / "release-v0.4.0.json").exists()


def test_a_release_reports_undeclared_drift_and_honours_a_patch_declaration():
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, DRIFTED, SAME, SAME])
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"] is False
    difference = report["sites"]["acme.tenant.example.com"]["comparison"]["differences"][0]
    assert difference == {"table": "tabItem", "name": "A", "change": "changed", "field": "item_name",
                          "before": "a", "after": "edited", "declared": False}
    admin.forget_release(RELEASE, "v0.4.0")
    drifted_by_patch = _snap({"tabItem": {"A": {"name": "A", "item_name": "edited"}}, "tabDS Model Run": {"r": {"name": "r", "status": "Succeeded"}}},
                             patches=("frappe.patches.v16_0.old", "dsherp_bridge.patches.rename_items"))
    declared = SnapshotBench([SAME, drifted_by_patch, SAME, SAME],
                             expectations=[{"patch": "dsherp_bridge.patches.rename_items", "doctype": "Item", "fields": ["item_name"]}])
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: declared, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"] is True
    assert report["sites"]["acme.tenant.example.com"]["comparison"]["differences"][0]["declared"] == "dsherp_bridge.patches.rename_items"


def test_a_release_refuses_a_tag_the_environment_does_not_run_active_runs_or_an_incomplete_backup_set():
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    with pytest.raises(admin.Fault, match="DSHERP_IMAGE_TAG"):
        admin.release(RELEASE, "v0.5.0", bench_factory=lambda kind: SnapshotBench([SAME] * 4), runner=RUNNING_NEW, from_tag="v0.3.0")
    busy = SnapshotBench([SAME] * 4, active_runs=1)
    with pytest.raises(admin.Fault, match="运行"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: busy, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not [verb for verb in busy.verbs if " backup " in verb or verb.endswith("migrate")]
    short = SnapshotBench([SAME] * 4, backup_pieces=2)
    with pytest.raises(admin.Fault, match="备份集"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: short, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not [verb for verb in short.verbs if verb.endswith("migrate")]
    with pytest.raises(admin.Fault):
        admin.release(RELEASE, "", bench_factory=lambda kind: SnapshotBench([SAME] * 4), runner=RUNNING_NEW, from_tag="v0.3.0")


def test_a_failed_migrate_leaves_the_site_in_maintenance_and_writes_a_partial_report():
    admin.ensure_secrets(RELEASE)
    _tenant_row()

    class Broken(SnapshotBench):
        def run(self, *arguments, **kwargs):
            if arguments[:2] == ("bench", "--site") and arguments[3] == "migrate":
                self.verbs.append(" ".join(arguments))
                raise admin.Fault("容器命令失败（backend）：bench --site migrate\npatch boom")
            return super().run(*arguments, **kwargs)

    bench = Broken([SAME, SAME, SAME, SAME])
    with pytest.raises(admin.Fault, match="migrate"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "1"
    partial = json.loads((admin.runtime_dir(RELEASE) / "releases" / "release-v0.4.0.json").read_text())
    assert partial["clean"] is False and partial["failed"]["site"] == "acme.tenant.example.com"


def test_a_rollback_restores_the_archived_set_with_files_and_compares_with_the_pre_upgrade_snapshot():
    """The recovery half of G2: rollback finds what release archived, restores it and proves the
    data is what it was before the upgrade; the only tolerated delta is what restore itself writes."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, SAME, SAME, SAME])
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    after_restore = _snap({**{t: {n: dict(r["values"]) for n, r in v["rows"].items()} for t, v in SAME["tables"].items()}},
                          singles={"System Settings": {"enable_scheduler": "1", "modified": "later", "modified_by": "Administrator"}})
    bench.snapshots = [after_restore, after_restore]
    report = admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD)
    restore = [verb for verb in bench.verbs if " restore " in verb and "acme" in verb][0]
    assert "--with-public-files" in restore and "--with-private-files" in restore and "--force" in restore
    assert admin.ARCHIVED_RELEASES + "/v0.4.0/acme.tenant.example.com/20260906_120000-acme_tenant_example_com-database.sql.gz" in restore
    assert report["clean"] is True and report["sites"]["acme.tenant.example.com"]["comparison"]["clean"] is True
    # restore re-applied the scheduler flag: the one difference is declared by 'frappe.restore'
    comparison = report["sites"]["acme.tenant.example.com"]["comparison"]
    assert comparison["summary"]["undeclared"] == 0
    assert [(d["field"], d["declared"]) for d in comparison["differences"]] == [("enable_scheduler", "frappe.restore")]
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"
    assert Path(report["path"]).name.startswith("rollback-v0.4.0-")


def test_a_rollback_that_does_not_reproduce_the_pre_upgrade_data_is_not_clean_and_an_explicit_backup_overrides_discovery():
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, SAME, SAME, SAME])
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    bench.snapshots = [DRIFTED, SAME]
    report = admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD,
                            backups={"acme.tenant.example.com": "/home/frappe/frappe-bench/sites/acme.tenant.example.com/private/backups/x-database.sql.gz"})
    assert report["clean"] is False
    assert report["sites"]["acme.tenant.example.com"]["comparison"]["differences"][0]["field"] == "item_name"
    restore = [verb for verb in bench.verbs if " restore " in verb and "acme" in verb][-1]
    assert "/private/backups/x-database.sql.gz" in restore


def test_a_rollback_without_a_release_record_or_with_a_missing_backup_file_is_refused_before_touching_data():
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="release"):
        admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD)
    assert not [verb for verb in bench.verbs if " restore " in verb]
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    bench.snapshots = [SAME, SAME]
    with pytest.raises(admin.Fault, match="备份"):
        admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD,
                       backups={"acme.tenant.example.com": "/nowhere/missing-database.sql.gz"})
    assert not [verb for verb in bench.verbs if " restore " in verb]


def test_snapshot_and_compare_are_available_as_commands_for_drills():
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, DRIFTED])
    before = admin.take_snapshot(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench)
    after = admin.take_snapshot(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench)
    assert before["tables"] == SAME["tables"]
    report = admin.compare_snapshots(before, after)
    assert report["clean"] is False and report["differences"][0]["after"] == "edited"


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
        admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
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


def test_rotating_the_runtime_key_is_explicit_and_returns_the_new_secret_once():
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    rotated = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, rotate_runtime_key=True, runner=fake_networks)
    assert dict(rotated["steps"])["runtime-identity"] == "rotated"
    assert rotated["runtime_identity"]["api_secret"] == "s"


def test_a_tenant_that_chose_its_own_language_keeps_it_on_rerun():
    admin.ensure_secrets(PROD)
    bench = FakeBench(config={"language": "en", "time_zone": "Europe/Berlin"})
    result = admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=fake_networks)
    assert dict(result["steps"])["system-settings"] == "kept"
    assert bench.config["language"] == "en"


def test_a_container_command_without_input_never_inherits_the_operator_stdin(tmp_path):
    import subprocess as _subprocess
    seen = {}
    def runner(command, **kwargs):
        seen.update(kwargs)
        return type("Result", (), {"returncode": 0, "stdout": "present\n", "stderr": ""})()
    bench = admin.Bench(PROD, "tenant", root=tmp_path, runner=runner)
    bench.site_state("acme.tenant.example.com")
    assert seen.get("stdin") is _subprocess.DEVNULL and "input" not in seen
    bench.python("acme.tenant.example.com", "print(1)")
    assert "input" in seen and seen["input"].startswith("import json")


def test_the_platform_bench_never_shares_the_tenant_queue_namespace():
    tenant = admin.bench_config(PROD, "tenant")
    platform = admin.bench_config(PROD, "platform")
    assert tenant["redis_queue"] == "redis://redis-queue:6379/0"
    assert platform["redis_queue"] == "redis://redis-queue:6379/1"
    assert tenant["redis_cache"] == platform["redis_cache"]


def test_provisioning_refuses_to_write_an_agent_allowlist_it_cannot_derive(host):
    admin.ensure_secrets(PROD)
    bench = FakeBench()
    def no_networks(command, **kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "not found"})()
    with pytest.raises(admin.Fault):
        admin.provision_tenant(PROD, "acme", bench_factory=lambda kind: bench, runner=no_networks)


def test_firewall_rules_target_only_the_agent_bridge():
    def networks(command, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "db512087a978abcdef0123456789\n", "stderr": ""})()
    rules = admin.agent_firewall_rules(PROD, runner=networks)
    assert rules["bridge"] == "br-db512087a978"
    assert all("br-db512087a978" in rule for rule in rules["iptables"] + rules["nft"] + rules["undo"])
    assert rules["iptables"][-1].endswith("-j DROP")
    # The same tag the systemd unit's script uses, so either can remove what the other added.
    assert all("-m comment --comment dsherp-agent-firewall" in rule for rule in rules["iptables"] + rules["undo"])
    assert rules["unit"] == "dsherp-agent-firewall.service"
    assert rules["script"] == "/usr/local/sbin/dsherp-agent-firewall"


def test_forget_release_validates_the_tag_and_never_leaves_the_release_records_root(host):
    """Review P1: an unvalidated tag joined the path and reached shutil.rmtree."""
    admin.ensure_secrets(RELEASE)
    outside = admin.runtime_dir(RELEASE) / "unrelated-user-files"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("keep")
    for bad in ("../unrelated-user-files", "", "a/b", "..", ".hidden"):
        with pytest.raises(admin.Fault):
            admin.forget_release(RELEASE, bad)
    assert (outside / "keep.txt").exists()
    records = admin.runtime_dir(RELEASE) / "releases"
    (records / "v0.4.0").mkdir(parents=True)
    link = records / "v0.4.1"
    link.symlink_to(outside)
    with pytest.raises(admin.Fault):
        admin.forget_release(RELEASE, "v0.4.1")  # a symlink pointing outside the records root
    assert (outside / "keep.txt").exists()
    assert admin.forget_release(RELEASE, "v0.4.0")["forgotten"].endswith("/releases/v0.4.0")
    assert not (records / "v0.4.0").exists()


def test_image_identity_is_checked_by_full_name_and_by_manifest_id_not_by_tag_suffix(tmp_path, host):
    """Review P1: `unrelated/product:v0.4.0` passed, and the recorded image id was never used."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    (host / "state" / "manifests" / "v0.4.0.json").unlink()  # this test brings its own repository copy
    lookalike = _docker({"backend": "unrelated/product:v0.4.0", "platform-backend": "unrelated/product:v0.4.0"})
    untouched = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="registry.example.com/dsherp/dsherp-frappe:v0.4.0"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=lookalike, from_tag="v0.3.0")
    assert not untouched.verbs
    # With a release manifest on record, the running image id must be the built one.
    manifest = tmp_path / "infra" / "releases" / "v0.4.0.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"tag": "v0.4.0", "images": {
        "registry.example.com/dsherp/dsherp-frappe:v0.4.0": {"id": "sha256:built-frappe"},
        "registry.example.com/dsherp/dsherp-worker:v0.4.0": {"id": "sha256:built-worker"}}}))
    with pytest.raises(admin.Fault, match="sha256:built-frappe"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: SnapshotBench([SAME] * 4), runner=RUNNING_NEW,
                      from_tag="v0.3.0", root=tmp_path)
    built = _docker({"backend": "registry.example.com/dsherp/dsherp-frappe:v0.4.0",
                     "platform-backend": "registry.example.com/dsherp/dsherp-frappe:v0.4.0"})
    original = built

    def with_ids(command, **kwargs):
        result = original(command, **kwargs)
        if command[:2] == ["docker", "inspect"]:
            result.stdout = "registry.example.com/dsherp/dsherp-frappe:v0.4.0 sha256:built-frappe\n"
        return result
    bench = SnapshotBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=with_ids, from_tag="v0.3.0", root=tmp_path)
    assert report["images"]["backend"]["image_id"] == "sha256:built-frappe"
    # rollback: the previous images are those current.json recorded, name and id alike
    record = admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json"
    assert json.loads(record.read_text())["previous_images"] is None  # first release: nothing on record yet
    admin.forget_release(RELEASE, "v0.4.0")
    (admin.runtime_dir(RELEASE) / "releases" / "current.json").write_text(json.dumps({"tag": "v0.3.0", "images": {
        "backend": {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0", "image_id": "sha256:old-frappe"},
        "platform-backend": {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0", "image_id": "sha256:old-frappe"}}}))
    bench = SnapshotBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=with_ids, root=tmp_path)
    assert json.loads(record.read_text())["previous_images"]["backend"]["image_id"] == "sha256:old-frappe"
    bench.snapshots = [SAME, SAME]
    with pytest.raises(admin.Fault, match="sha256:old-frappe"):
        admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD, root=tmp_path)  # right name, wrong id
    assert not [verb for verb in bench.verbs if " restore " in verb]

    def old_ids(command, **kwargs):
        result = RUNNING_OLD(command, **kwargs)
        if command[:2] == ["docker", "inspect"]:
            result.stdout = "registry.example.com/dsherp/dsherp-frappe:v0.3.0 sha256:old-frappe\n"
        return result
    assert admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=old_ids, root=tmp_path)["clean"] is True


def test_snapshot_command_can_align_its_hash_columns_with_an_existing_snapshot(host):
    """Review P2: a plain snapshot hashes over all columns while release's after-snapshot hashes
    over the before-columns; comparing the two must not invent differences."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = SnapshotBench([SAME, SAME])
    reference = admin.take_snapshot(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench)
    aligned = admin.take_snapshot(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench, like=reference)
    body = [call for call in bench.calls if call[0] == "python-body"][-1][2]
    assert "hash_columns=json.loads(" in body and "tabItem" in body
    assert aligned["tables"] == SAME["tables"]


def test_release_refuses_unless_a_valid_manifest_vouches_for_the_running_image_id(tmp_path, host):
    """Review round 3 (P1): a missing, unreadable or incomplete manifest turned into 'no expected
    id' and any image id was then accepted. Now each of those refuses before a site is touched,
    each for its own stated reason (so a regression to 'no id, no check' cannot hide behind a
    later refusal)."""
    import re
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    delivered = host / "state" / "manifests" / "v0.4.0.json"
    record = admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json"

    def refused(reason, *, manifest=None, root=admin.ROOT):
        untouched = SnapshotBench([SAME] * 4)
        with pytest.raises(admin.Fault, match=reason):
            admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=RUNNING_NEW,
                          from_tag="v0.3.0", manifest=manifest, root=root)
        assert not untouched.verbs and not record.exists()

    delivered.unlink()
    refused("没有 v0.4.0 的发布清单（找过 " + re.escape(str(delivered)))            # missing
    delivered.write_text("{not json")
    refused("不是 JSON")                                                           # corrupt
    _manifest(delivered, "v0.4.0", image="registry.example.com/dsherp/other:v0.4.0")
    refused("没有 registry.example.com/dsherp/dsherp-frappe:v0.4.0 的记录")        # no entry for the image
    _manifest(delivered, "v0.4.0", image_id="")
    refused("镜像 id 是 ''，不是可核对的镜像 id")                                   # empty id
    delivered.write_text(json.dumps({"tag": "v0.4.0", "images": {
        "registry.example.com/dsherp/dsherp-frappe:v0.4.0": {"id": None}}}))
    refused("镜像 id 是 None，不是可核对的镜像 id")                                 # null id
    _manifest(delivered, "v0.4.0", image_id="id-v0.4.0")
    refused("不是可核对的镜像 id")                                                 # not a sha256: digest
    _manifest(delivered, "v0.3.9")
    refused("记录的 tag 是 'v0.3.9'，不是 v0.4.0")                                  # a manifest for another tag
    _manifest(delivered, "v0.4.0", image_id="sha256:some-other-build")
    refused("记录的是 sha256:some-other-build")                                    # valid manifest, wrong running id
    refused("--manifest", manifest="")                                            # an empty path is not 'no path'
    # An explicit --manifest is the only manifest consulted, even when the delivered one is right.
    _manifest(delivered, "v0.4.0")
    wrong = _manifest(tmp_path / "wrong" / "v0.4.0.json", "v0.4.0", image_id="sha256:from-the-wrong-file")
    refused("sha256:from-the-wrong-file", manifest=str(wrong))
    # Two default copies (repository and delivered) must agree, and the refusal names both.
    repo = _manifest(tmp_path / "repo" / "infra" / "releases" / "v0.4.0.json", "v0.4.0", image_id="sha256:stale-repo-copy")
    refused(re.escape(str(repo)) + ".*" + re.escape(str(delivered)) + "|" + re.escape(str(delivered)) + ".*" + re.escape(str(repo)),
            root=tmp_path / "repo")
    _manifest(repo, "v0.4.0")
    bench = SnapshotBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW,
                           from_tag="v0.3.0", root=tmp_path / "repo")
    assert report["clean"] and report["images"]["backend"]["image_id"] == "sha256:id-v0.4.0"
    # An explicit --manifest works alone, and so does a repository copy alone.
    _fresh_host()
    delivered.unlink()
    shipped = _manifest(tmp_path / "shipped" / "v0.4.0.json", "v0.4.0")
    bench = SnapshotBench([SAME] * 4)
    assert admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW,
                         from_tag="v0.3.0", manifest=str(shipped))["clean"]
    _fresh_host()
    bench = SnapshotBench([SAME] * 4)
    assert admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW,
                         from_tag="v0.3.0", root=tmp_path / "repo")["clean"]


def test_a_first_release_proves_the_previous_tags_manifest_is_at_hand_so_its_rollback_cannot_be_blocked(host):
    """Verification finding: with no current.json the rollback can only anchor on the previous
    tag's manifest, so the release must fail before touching a site when that manifest is
    missing or unusable, not the rollback afterwards."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    old_manifest = host / "state" / "manifests" / "v0.3.0.json"
    record = admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json"
    old_manifest.unlink()
    untouched = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="没有 v0.3.0 的发布清单"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not untouched.verbs and not record.exists()
    _manifest(old_manifest, "v0.3.0", image_id="")
    with pytest.raises(admin.Fault, match="不是可核对的镜像 id"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not untouched.verbs and not record.exists()
    _manifest(old_manifest, "v0.3.0")
    bench = SnapshotBench([SAME] * 4)
    assert admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")["clean"]


def test_release_refuses_a_from_tag_that_contradicts_what_current_json_recorded(host):
    """Verification finding: --from overriding current.json silently dropped the host's own
    record of what ran (previous_images became None) and anchored the rollback on the typed tag."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    _manifest(host / "state" / "manifests" / "v0.2.0.json", "v0.2.0")
    (admin.runtime_dir(RELEASE) / "releases").mkdir(parents=True, exist_ok=True)
    (admin.runtime_dir(RELEASE) / "releases" / "current.json").write_text(json.dumps({"tag": "v0.3.0", "images": {
        "backend": {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0", "image_id": "sha256:id-v0.3.0"},
        "platform-backend": {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0", "image_id": "sha256:id-v0.3.0"}}}))
    untouched = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="current.json 记录的是 v0.3.0"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=RUNNING_NEW, from_tag="v0.2.0")
    assert not untouched.verbs
    bench = SnapshotBench([SAME] * 4)
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    record = json.loads((admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json").read_text())
    assert record["previous_images"]["backend"]["image_id"] == "sha256:id-v0.3.0"  # the record kept, not dropped


def test_more_than_one_container_per_bench_service_is_refused_not_half_checked():
    """Verification finding: only the first container listed by `compose ps -q` was inspected;
    a second one of the same service ran unchecked."""
    admin.ensure_secrets(RELEASE)
    _tenant_row()

    def doubled(command, **kwargs):
        result = RUNNING_NEW(command, **kwargs)
        if command[:2] == ["docker", "compose"] and "ps" in command and command[-1] == "backend":
            result.stdout = "cid-backend\ncid-backend-second\n"
        return result
    untouched = SnapshotBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="backend .*2 个容器"):
        admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: untouched, runner=doubled, from_tag="v0.3.0")
    assert not untouched.verbs


def test_require_image_ids_refuses_a_service_without_an_expected_id():
    images = {"backend": {"image": "x", "image_id": "sha256:a"}, "platform-backend": {"image": "x", "image_id": "sha256:a"}}
    with pytest.raises(admin.Fault, match="没有服务 platform-backend 的镜像 id"):
        admin._require_image_ids(images, {"backend": "sha256:a"}, "记录")
    with pytest.raises(admin.Fault, match="没有服务 backend 的镜像 id"):
        admin._require_image_ids(images, {"backend": "", "platform-backend": "sha256:a"}, "记录")
    admin._require_image_ids(images, {"backend": "sha256:a", "platform-backend": "sha256:a"}, "记录")


def test_rollback_needs_complete_previous_images_or_a_valid_old_manifest_and_never_restores_blind(tmp_path, host):
    """Review round 3 (P1): the same fallback let a rollback proceed with nothing to compare the
    old image against. previous_images must be complete and valid; without it the old tag's
    manifest must be valid; with neither, nothing is restored."""
    import re
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    old_manifest = host / "state" / "manifests" / "v0.3.0.json"
    record = admin.runtime_dir(RELEASE) / "releases" / "v0.4.0" / "release.json"
    bench = SnapshotBench([SAME] * 4)
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert json.loads(record.read_text())["previous_images"] is None  # first release: nothing ran on record

    def refused(reason, *, manifest=None):
        bench.snapshots = [SAME, SAME]
        before = list(bench.verbs)
        with pytest.raises(admin.Fault, match=reason):
            admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD, manifest=manifest)
        assert bench.verbs == before  # nothing restored, nothing even quiesced

    old_manifest.unlink()
    refused("没有 v0.3.0 的发布清单（找过 " + re.escape(str(old_manifest)))       # no previous_images, no manifest
    old_manifest.write_text("[]")
    refused("记录的 tag 是 None，不是 v0.3.0")                                     # a manifest that is not an object
    old_manifest.write_text("{")
    refused("不是 JSON")
    _manifest(old_manifest, "v0.3.0", image="registry.example.com/dsherp/other:v0.3.0")
    refused("没有 registry.example.com/dsherp/dsherp-frappe:v0.3.0 的记录")
    _manifest(old_manifest, "v0.3.0", image_id="")
    refused("不是可核对的镜像 id")
    _manifest(old_manifest, "v0.3.0", image_id="sha256:not-what-runs")
    refused("记录的是 sha256:not-what-runs")
    old_manifest.unlink()
    shipped = _manifest(tmp_path / "shipped" / "v0.3.0.json", "v0.3.0")
    bench.snapshots = [SAME, SAME]
    assert admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD, manifest=str(shipped))["clean"]
    # A release record whose previous_images is present but incomplete or without ids is not a
    # fallback case: the record itself is unusable and the rollback is refused even though a
    # valid old manifest is at hand.
    _manifest(old_manifest, "v0.3.0")
    payload = json.loads(record.read_text())
    payload["previous_images"] = {"backend": {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0",
                                              "image_id": "sha256:id-v0.3.0"}}
    record.write_text(json.dumps(payload))
    refused("previous_images 里没有服务 platform-backend .*记录不可用，不回滚")   # one service missing
    for bad in ("", "sha256:", "id-v0.3.0", None):
        payload["previous_images"]["platform-backend"] = {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0", "image_id": bad}
        record.write_text(json.dumps(payload))
        refused("记录不可用，不回滚")                                                # an unusable id
    payload["previous_images"]["platform-backend"] = {"image": "unrelated/product:v0.3.0", "image_id": "sha256:id-v0.3.0"}
    record.write_text(json.dumps(payload))
    refused("记录不可用，不回滚")                                                    # a different image name
    payload["previous_images"]["platform-backend"] = {"image": "registry.example.com/dsherp/dsherp-frappe:v0.3.0",
                                                      "image_id": "sha256:id-v0.3.0"}
    record.write_text(json.dumps(payload))
    # With previous_images on record an explicit --manifest is still read: it must exist and agree.
    refused("没有 v0.3.0 的发布清单（找过 " + re.escape(str(tmp_path / "nowhere.json")), manifest=str(tmp_path / "nowhere.json"))
    disagreeing = _manifest(tmp_path / "other" / "v0.3.0.json", "v0.3.0", image_id="sha256:another-build")
    refused("sha256:another-build.*不一致|不一致.*sha256:another-build", manifest=str(disagreeing))
    bench.snapshots = [SAME, SAME]
    assert admin.rollback(BACK, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_OLD, manifest=str(shipped))["clean"]
    # A record without a usable previous_tag is refused as such, not with a KeyError.
    admin.forget_release(RELEASE, "v0.4.0")
    (admin.runtime_dir(RELEASE) / "releases" / "current.json").unlink()
    bench = SnapshotBench([SAME] * 4)
    admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    payload = json.loads(record.read_text())
    del payload["previous_tag"]
    record.write_text(json.dumps(payload))
    refused("previous_tag")


def test_the_cli_passes_an_explicit_manifest_to_release_and_rollback(monkeypatch):
    seen = {}

    def fake(name):
        def call(resolved, tag, **kw):
            seen[name] = kw
            return {"clean": True, "sites": {}, "steps": []}
        return call
    monkeypatch.setattr(admin, "release", fake("release"))
    monkeypatch.setattr(admin, "rollback", fake("rollback"))
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["release", "v0.4.0", "--from", "v0.3.0", "--manifest", "/tmp/v0.4.0.json"]) == 0
    assert seen["release"]["manifest"] == "/tmp/v0.4.0.json" and seen["release"]["from_tag"] == "v0.3.0"
    assert admin.main(["rollback", "v0.4.0", "--manifest", "/tmp/v0.3.0.json"]) == 0
    assert seen["rollback"]["manifest"] == "/tmp/v0.3.0.json"


def test_a_release_stages_its_pre_upgrade_backup_as_a_release_set_and_keeps_the_archive_secrets_apart(host):
    """The set a rollback depends on must be able to leave the host under the same protocol,
    and the archive copy no longer puts site_config next to the dump."""
    from tests.test_backup_cli import StagingBench
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = StagingBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"]
    row = report["sites"]["acme.tenant.example.com"]
    assert row["backup"]["site_config"].startswith(admin.ARCHIVED_RELEASES + "/v0.4.0/acme.tenant.example.com/secrets/")
    assert row["backup"]["database"].startswith(admin.ARCHIVED_RELEASES + "/v0.4.0/acme.tenant.example.com/2")
    assert any("chmod 700" in verb and "/v0.4.0/acme.tenant.example.com/secrets" in verb for verb in bench.verbs)
    set_id = row["set_id"]
    staged = bench.written[f"/home/frappe/backups/sets/acme.tenant.example.com/{set_id}/set.json"]
    assert staged["kind"] == "release" and staged["image_tag"] == "v0.3.0", "the set records the build it was taken on"
    from dsherp import backup_status
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sets"][set_id]["state"] == "staged" and status["sets"][set_id]["kind"] == "release"


def test_retiring_a_tenant_stages_its_final_set_and_refuses_to_drop_until_it_has_left_the_host(host):
    """The last backup of a tenant is the one nothing will ever take again: the Site is not
    destroyed until that set is complete in both repositories."""
    from tests.test_backup_cli import StagingBench
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = StagingBench([SAME])
    with pytest.raises(admin.Fault, match="没能完整到达异地"):
        admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench, runner=RUNNING_NEW)
    assert not [verb for verb in bench.verbs if "drop-site" in verb], "nothing destructive ran"
    assert admin.load_tenants(RELEASE), "the tenant list is untouched"
    staged = [key for key in bench.written if key.endswith("set.json")]
    assert staged and bench.written[staged[0]]["kind"] == "retire"
    assert any("dsherp_hold 1" in verb for verb in bench.verbs), "the final backup runs in a stable window"


def test_production_refuses_to_retire_or_release_before_the_off_site_repositories_are_configured(host):
    """The final state of a retired tenant must leave the host; a deployment without
    repositories has no way to do that, so the destructive step never starts."""
    from tests.test_backup_cli import StagingBench
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    untouched = StagingBench([SAME] * 4)
    with pytest.raises(admin.Fault, match="DSHERP_BACKUP_REPOSITORY"):
        admin.retire_tenant(UNCONFIGURED, "acme", bench_factory=lambda kind: untouched)
    assert not [verb for verb in untouched.verbs if "drop-site" in verb]
    assert admin.load_tenants(RELEASE), "the tenant list is untouched"
    with pytest.raises(admin.Fault, match="DSHERP_BACKUP_REPOSITORY"):
        admin.release(UNCONFIGURED, "v0.4.0", bench_factory=lambda kind: untouched, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert not [verb for verb in untouched.verbs if "migrate" in verb]
    # Development may drill without object storage, but only when it says so.
    dev = deploy_env.settings({"DSHERP_ENV": "dev"})
    assert dev["backup_repository"] == ""


def test_doctor_asks_for_the_backup_key_material_only_once_repositories_are_configured(host):
    """Two generated passwords and two supplied storage identities: the pair that reads dumps
    must not be the pair that can decrypt site_config copies."""
    admin.ensure_secrets(UNCONFIGURED)
    assert not [finding for finding in admin.doctor(UNCONFIGURED) if "backup" in finding]
    admin.ensure_secrets(RELEASE)
    directory = admin.secrets_dir(RELEASE)
    for name in ("backup_repository_password", "backup_secrets_repository_password"):
        assert (directory / name).exists(), "secrets init generates the repository passwords"
        assert (directory / name).read_text().strip()
    assert (directory / "backup_repository_password").read_text() != (directory / "backup_secrets_repository_password").read_text()
    findings = admin.doctor(RELEASE)
    assert any("backup_storage_credentials" in finding for finding in findings)
    assert any("backup_secrets_storage_credentials" in finding for finding in findings)
    for name in ("backup_storage_credentials", "backup_secrets_storage_credentials"):
        (directory / name).write_text("AWS_ACCESS_KEY_ID=x\nAWS_SECRET_ACCESS_KEY=y\n")
        (directory / name).chmod(0o600)
    assert not [finding for finding in admin.doctor(RELEASE) if "backup" in finding]
    (directory / "backup_storage_credentials").chmod(0o644)
    assert any("backup_storage_credentials" in finding and "权限" in finding for finding in admin.doctor(RELEASE))
