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
REISSUE_SCRIPT = """import os, json, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site={site!r}); frappe.connect(); frappe.set_user('Administrator')
from frappe.core.doctype.user.user import generate_keys
keys = generate_keys({user!r}); frappe.db.commit()
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
    return {"actor": actor, "user": entry["user"], "api_key": entry["api_key"]}


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
