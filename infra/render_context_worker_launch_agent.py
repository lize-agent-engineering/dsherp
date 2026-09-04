#!/usr/bin/env python3
"""Render the macOS LaunchAgent for the multi-Site v16 context worker."""

import argparse
import os
from pathlib import Path
import plistlib


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.dsherp.agent-worker-v16"


def render_launch_agent(root=ROOT, *, target=None):
    root = Path(root).resolve()
    target = Path(target) if target else root / ".runtime" / f"{LABEL}.plist"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = Path.home() / "Library" / "Logs" / "dsherp-agent-worker-v16.log"
    launch_agent = {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            "-m",
            "dsherp.context_worker",
            "--profile",
            str(root / ".runtime/context-worker.json"),
            "--provider-env",
            str(root / ".env"),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    temporary = target.with_name(f".{target.name}.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            plistlib.dump(launch_agent, file, sort_keys=False)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    print(render_launch_agent(args.root, target=args.target))


if __name__ == "__main__":
    main()
