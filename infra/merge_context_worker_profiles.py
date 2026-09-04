#!/usr/bin/env python3
"""Merge legacy single-Site worker profiles into one multi-Site profile."""
import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALPHA = Path(".runtime/context-worker.json")
DAILY = Path(".runtime/context-worker-daily.json")
TARGET = Path(".runtime/context-worker-sites.json")


def _load_site(path):
    if not path.is_file():
        raise ValueError("Missing worker profile")
    try:
        profile = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError("Invalid worker profile") from error
    if not isinstance(profile, dict):
        raise ValueError("Invalid worker profile")
    site = profile.get("site")
    if not isinstance(site, str) or not site.strip():
        raise ValueError("Invalid worker Site")
    return profile, site


def merge_context_worker_profiles(root):
    root = Path(root)
    alpha, alpha_site = _load_site(root / ALPHA)
    daily, daily_site = _load_site(root / DAILY)
    if alpha_site == daily_site:
        raise ValueError("Duplicate worker Site")
    target = root / TARGET
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"slots": 3, "sites": [alpha, daily]}, file)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(merge_context_worker_profiles(args.root))


if __name__ == "__main__":
    main()
