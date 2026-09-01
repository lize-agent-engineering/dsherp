"""Run alpha control-plane provisioning and store credentials privately."""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("erp-users.json", "erp-reader.json", "erp-denied.json")
BASE_URL = "http://127.0.0.1:18081"
SITE = "dsherp-validation.localhost"


def _write_private(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


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
    provision()
