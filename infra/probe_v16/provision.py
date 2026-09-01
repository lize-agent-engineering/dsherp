"""Fresh-provision the isolated v16 probe Site; never reuse an existing Site."""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path("/home/frappe/frappe-bench")
SITES = ROOT / "sites"
SITE = "dsherp-v16probe.localhost"


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment: {name}")
    return value


if (SITES / SITE).exists():
    raise SystemExit("Probe Site already exists; destroy the probe project before retrying")

(SITES / "apps.txt").write_text("frappe\nerpnext\ndsherp_bridge\ndsherp_platform\n")
(SITES / "common_site_config.json").write_text(json.dumps({
    "db_host": "db",
    "db_port": 3306,
    "default_site": SITE,
    "redis_cache": "redis://redis:6379/0",
    "redis_queue": "redis://redis:6379/1",
    "redis_socketio": "redis://redis:6379/1",
}))
assets = SITES / "assets"
if not assets.exists():
    assets.symlink_to(ROOT / "assets", target_is_directory=True)

subprocess.run([
    "bench", "new-site", SITE,
    "--db-host", "db",
    "--db-root-username", "root",
    "--db-root-password", required_env("DSHERP_V16_PROBE_DB_ROOT_PASSWORD"),
    "--admin-password", required_env("DSHERP_V16_PROBE_ADMIN_PASSWORD"),
    "--mariadb-user-host-login-scope", "%",
    "--set-default",
    "--install-app", "erpnext",
], cwd=ROOT, check=True)
for app in ("dsherp_bridge", "dsherp_platform"):
    subprocess.run(["bench", "--site", SITE, "install-app", app], cwd=ROOT, check=True)
subprocess.run(["bench", "--site", SITE, "clear-cache"], cwd=ROOT, check=True)
subprocess.run(["bench", "--site", SITE, "list-apps"], cwd=ROOT, check=True)
