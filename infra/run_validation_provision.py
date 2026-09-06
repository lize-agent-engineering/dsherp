"""Run alpha control-plane provisioning and store credentials privately.

`--reissue <actor>` rotates one synthetic actor's API secret on the Site and rewrites
that actor's profile files in place: the only sanctioned way to bring the files back in
line after something on the Site regenerated the keys (the files are otherwise never
overwritten).
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("erp-users.json", "erp-reader.json", "erp-denied.json")
ACTORS = ("reader", "denied")
BASE_URL = "http://127.0.0.1:18081"
SITE = "dsherp-validation.localhost"
BENCH_PYTHON = "/home/frappe/frappe-bench/env/bin/python"
PLATFORM_SITE = "dsherp-platform.localhost"
# The platform keeps a copy of the member's business secret in DS Membership; a rotation
# that does not reach it turns every platform read for that member into a 403.
REBIND_SCRIPT = """import os, json, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site={site!r}); frappe.connect(); frappe.set_user('Administrator')
rebound = 0
from dsherp_platform import business_credentials as policy
from frappe.utils import now_datetime
for name in frappe.get_all('DS Membership', filters={{'erp_user': {user!r}, 'api_key': {api_key!r}}}, pluck='name'):
    doc = frappe.get_doc('DS Membership', name); doc.api_secret = {api_secret!r}
    # The window the Site just gave this key, so the platform does not judge it by an older one.
    doc.credential_issued_at = now_datetime(); doc.credential_expires_at = policy.expiry(now_datetime())
    doc.credential_version = int(doc.credential_version or 0) + 1; doc.credential_erp_user = doc.erp_user
    doc.save(); rebound += 1
frappe.db.commit()
print(json.dumps({{'rebound': rebound}}))
frappe.destroy()
"""
# Issued through the Site's own credential module, not `generate_keys` directly: a key with
# no recorded window is refused by the Site (S2), so a rotation that skipped the record would
# hand these fixtures a secret that authenticates and is then turned away.
REISSUE_SCRIPT = """import os, json, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site={site!r}); frappe.connect(); frappe.set_user('Administrator')
from dsherp_bridge import credentials
keys = credentials.issue({user!r}, issued_for='validation-provision'); frappe.db.commit()
print(json.dumps({{'api_key': keys['api_key'], 'api_secret': keys['api_secret']}}))
frappe.destroy()
"""


def _write_private(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _replace_private(path, value):
    """Rewrite an existing profile atomically, keeping it private throughout."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        _write_private(temporary, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reissue(actor, runtime_dir=ROOT / ".runtime", run=subprocess.run):
    """Rotate one actor's API secret on the Site and rewrite only that actor's profiles."""
    if actor not in ACTORS:
        raise ValueError("Unknown validation actor: " + repr(actor))
    runtime_dir = Path(runtime_dir)
    users_path = runtime_dir / TARGETS[0]
    if not users_path.is_file():
        raise RuntimeError("Validation credential profile missing; provision first")
    profiles = json.loads(users_path.read_text())
    entry = profiles[actor]
    script = REISSUE_SCRIPT.format(site=entry["site"], user=entry["user"])
    result = run(
        ["docker", "compose", "-f", "infra/compose.validation.yml", "exec", "-T", "backend", BENCH_PYTHON, "-"],
        cwd=ROOT, input=script, text=True, capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Reissuing keys failed inside the Site; inspect container state before retrying")
    keys = json.loads(result.stdout.strip().splitlines()[-1])
    if keys["api_key"] != entry["api_key"]:
        raise RuntimeError("The Site reports a different api_key than the profile; reprovision instead of patching")
    profiles[actor] = {**entry, "api_secret": keys["api_secret"]}
    _replace_private(users_path, profiles)
    _replace_private(runtime_dir / f"erp-{actor}.json", profiles[actor])
    rebind = run(
        ["docker", "compose", "-f", "infra/compose.validation.yml", "exec", "-T", "platform-backend", BENCH_PYTHON, "-"],
        cwd=ROOT, input=REBIND_SCRIPT.format(site=PLATFORM_SITE, user=entry["user"], api_key=entry["api_key"],
                                            api_secret=keys["api_secret"]),
        text=True, capture_output=True, timeout=120,
    )
    if rebind.returncode:
        raise RuntimeError("Files were rewritten but the platform membership could not be rebound; fix DS Membership by hand")
    rebound = json.loads(rebind.stdout.strip().splitlines()[-1])["rebound"]
    return {"actor": actor, "user": entry["user"], "api_key": entry["api_key"], "platform_memberships_rebound": rebound}


def provision(runtime_dir=ROOT / ".runtime"):
    runtime_dir = Path(runtime_dir)
    targets = [runtime_dir / name for name in TARGETS]
    if any(path.exists() for path in targets):
        raise RuntimeError("Validation credential profile exists; refusing to overwrite")
    result = subprocess.run(
        [
            "docker", "compose", "-f", "infra/compose.validation.yml",
            "--profile", "control", "run", "--rm", "--no-deps",
            "validation-provision",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError("Validation provisioning failed; inspect container state before retrying")
    payload = json.loads(result.stdout)
    expected = {"reader", "denied"}
    if set(payload) != expected:
        raise RuntimeError("Validation provisioner returned an unexpected credential set")
    profiles = {
        actor: {**profile, "base_url": BASE_URL, "site": SITE}
        for actor, profile in payload.items()
    }
    try:
        _write_private(targets[0], profiles)
        _write_private(targets[1], profiles["reader"])
        _write_private(targets[2], profiles["denied"])
    except Exception:
        for path in targets:
            path.unlink(missing_ok=True)
        raise
    print("Validation Site and two synthetic credential profiles provisioned.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision the validation Site, or reissue one actor's secret")
    parser.add_argument("--reissue", metavar="ACTOR", choices=ACTORS)
    arguments = parser.parse_args()
    if arguments.reissue:
        print(json.dumps(reissue(arguments.reissue)))
    else:
        provision()
