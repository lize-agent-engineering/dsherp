"""The generation half of the backup slice against the real development stack: a stable
window produces the four pieces and the snapshot, staged as one set in two volumes."""
import json
import subprocess

import pytest

from dsherp import backup, backup_sets, backup_status, deploy_env


COMPOSE = ["docker", "compose", "-p", "dsherp-validation", "-f", "infra/compose.validation.yml"]


def _exec(service, *command, timeout=120):
    result = subprocess.run([*COMPOSE, "exec", "-T", service, *command], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        pytest.fail(f"{' '.join(command)[:80]} failed: {result.stderr[-400:]}")
    return result.stdout


def test_a_real_backup_stages_one_set_with_matching_digests_and_split_permissions():
    resolved = deploy_env.settings({"DSHERP_ENV": "dev"})
    site = resolved["platform_site"]   # the one Site this deployment's tenant list always contains
    report = backup.backup(resolved, sites=[site])
    assert report["ok"], json.dumps({k: v for k, v in report.items() if k != "sets"}, ensure_ascii=False)
    set_doc = report["sets"][site]
    set_id = set_doc["set_id"]
    data_dir = f"/home/frappe/backups/sets/{site}/{set_id}"
    secret_dir = f"/home/frappe/backup-secrets/{site}/{set_id}"

    listing = _exec("platform-backend", "sh", "-c", f"ls -1 {data_dir}").split()
    assert set(listing) == {*backup_sets.DATA_PIECES, "snapshot.json", "set.json"}, listing
    secret_listing = _exec("platform-backend", "sh", "-c", f"ls -1 {secret_dir}").split()
    assert set(secret_listing) == {backup_sets.CONFIG_PIECE, "pair.json"}, secret_listing

    modes = _exec("platform-backend", "sh", "-c", f"stat -c '%a %n' {secret_dir} {secret_dir}/{backup_sets.CONFIG_PIECE} {data_dir}")
    permissions = dict(reversed(line.split(" ", 1)) for line in modes.strip().splitlines())
    assert permissions[secret_dir] == "700" and permissions[f"{secret_dir}/{backup_sets.CONFIG_PIECE}"] == "600"

    digests = {}
    for line in _exec("platform-backend", "sh", "-c", f"sha256sum {data_dir}/*", timeout=600).splitlines():
        value, path = line.split()
        digests[path.rsplit("/", 1)[-1]] = value
    for piece in backup_sets.DATA_PIECES:
        assert digests[piece] == set_doc["pieces"][piece]["sha256"], piece
    assert digests["snapshot.json"] == set_doc["snapshot_sha256"]

    written = json.loads(_exec("platform-backend", "sh", "-c", f"cat {data_dir}/set.json"))
    pair = json.loads(_exec("platform-backend", "sh", "-c", f"cat {secret_dir}/pair.json"))
    assert written == set_doc and backup_sets.pair_matches(written, pair)
    assert pair["config_sha256"] == _exec("platform-backend", "sh", "-c",
                                          f"sha256sum {secret_dir}/{backup_sets.CONFIG_PIECE}").split()[0]

    remaining = _exec("platform-backend", "sh", "-c",
                      f"ls -1 /home/frappe/frappe-bench/sites/{site}/private/backups").split()
    assert not [name for name in remaining if name.endswith(backup_sets.CONFIG_PIECE)], \
        "the secret piece is moved out of the Site's own backup directory, not copied"

    flags = json.loads(_exec("platform-backend", "sh", "-c",
                             f"cat /home/frappe/frappe-bench/sites/{site}/site_config.json"))
    assert int(flags.get("maintenance_mode") or 0) == 0 and int(flags.get("dsherp_hold") or 0) == 0, \
        "the window is closed again"

    status = backup_status.load(backup.status_path(resolved))
    assert status["sets"][set_id]["state"] == "staged"
    assert status["sites"][site]["backup"]["last_success"]["set_id"] == set_id
    snapshot = json.loads(_exec("platform-backend", "sh", "-c", f"cat {data_dir}/snapshot.json", timeout=600))
    assert snapshot["site"] == site and snapshot["tables"], "the snapshot is the G2 reader's output"


def test_the_staged_set_is_the_shape_the_sync_and_the_drill_expect():
    """The two halves live in different volumes with different permissions, and the manifests
    bind them; this is what upload_sets and restore-drill read back."""
    resolved = deploy_env.settings({"DSHERP_ENV": "dev"})
    site = resolved["platform_site"]
    status = backup_status.load(backup.status_path(resolved))
    if status is None:
        pytest.fail("no backup record; run the generation test first")
    proven = [row for row in backup_status.sets_of(status, site)]
    if not proven:
        pytest.fail(f"no set recorded for {site}")
    set_id = proven[-1]["set_id"]
    data = _exec("platform-backend", "sh", "-c", f"stat -c '%a' /home/frappe/backups/sets/{site}/{set_id}").strip()
    secret = _exec("platform-backend", "sh", "-c", f"stat -c '%a' /home/frappe/backup-secrets/{site}/{set_id}").strip()
    assert secret == "700" and data in ("700", "755")
    set_doc = json.loads(_exec("platform-backend", "sh", "-c", f"cat /home/frappe/backups/sets/{site}/{set_id}/set.json"))
    pair = json.loads(_exec("platform-backend", "sh", "-c", f"cat /home/frappe/backup-secrets/{site}/{set_id}/pair.json"))
    assert backup_sets.pair_matches(set_doc, pair)
    assert set_doc["image_id"].startswith("sha256:"), "the build the Site ran is on record"
    listed = _exec("platform-backend", "sh", "-c",
                   f"ls -1 /home/frappe/backup-secrets/{site}/{set_id}").split()
    assert set(listed) == {backup_sets.CONFIG_PIECE, "pair.json"}, "the secret half holds nothing else"
