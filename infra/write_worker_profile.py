#!/usr/bin/env python3
"""Put one tenant's runtime credential into the host worker profile, keeping the others.

The runbook used to write this file with a single-element `sites` list and `O_TRUNC`, so
provisioning a second tenant silently removed the first one from the worker's profile: that
Site kept queueing runs nobody claimed, and nothing said so. Ruling #2's first batch is up to
three tenant Sites on one host, so "the second one wipes the first" is on the ordinary path,
not an edge case.

Reads `dsherp-admin provision-tenant` output on stdin (that is the only moment the runtime
`api_secret` exists), merges the Site into the existing profile, and writes the whole profile
back atomically at 0600. Rerunning with the same Site replaces that one entry and leaves the
rest alone. The merged result is validated with the worker's own `normalize_profile`, so a
profile that would fail at worker start fails here instead.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path(".runtime/context-worker-sites.json")
DEFAULTS = {"slots": 3, "metrics_port": 9109}
# The host worker claims and heartbeats over the backend's loopback port; run containers reach
# the business Site by service name on the agent network. Both are runbook §7 values.
BASE_URL = "http://127.0.0.1:8000"
BUSINESS_URL = "http://backend:8000"


def _identity(provisioned):
    if not isinstance(provisioned, dict):
        raise ValueError("provision-tenant 的输出不是对象")
    identity = provisioned.get("runtime_identity")
    if not isinstance(identity, dict) or not identity.get("api_key") or not identity.get("api_secret"):
        raise ValueError(
            "这次 provision-tenant 没有签发运行密钥，输出里没有 runtime_identity；"
            "重跑时加 --rotate-runtime-key（旧密钥随即作废），不要写一个没有凭据的 profile")
    site = provisioned.get("site") or str(identity.get("user", "")).split("@", 1)[-1]
    if not site:
        raise ValueError("provision-tenant 的输出里没有站点名")
    return {"site": site, "base_url": BASE_URL, "business_url": BUSINESS_URL,
            "api_key": identity["api_key"], "api_secret": identity["api_secret"]}


def _existing(path):
    """The profile as the worker would read it, or an empty one. Never a silent reset."""
    from dsherp.context_worker import normalize_profile

    if not path.exists():
        return {**DEFAULTS, "sites": []}
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{path} 读不出来（{error}）；先看这个文件，不要让它被覆盖") from error
    # The same validation the worker runs at start: a profile it would refuse is refused here.
    return normalize_profile(current)


def write_worker_profile(provisioned, root=ROOT, base_url=BASE_URL, business_url=BUSINESS_URL):
    from dsherp.context_worker import normalize_profile

    entry = {**_identity(provisioned), "base_url": base_url, "business_url": business_url}
    path = Path(root) / PROFILE
    profile = _existing(path)
    kept = [site for site in profile["sites"] if site["site"] != entry["site"]]
    replaced = len(kept) != len(profile["sites"])
    merged = {key: value for key, value in profile.items() if key != "sites" and value is not None}
    merged["sites"] = kept + [entry]
    normalize_profile(merged)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(merged, handle)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(path), "site": entry["site"],
            "state": "replaced" if replaced else "added",
            "sites": [site["site"] for site in merged["sites"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base-url", default=BASE_URL, help="宿主 worker 领取/心跳用的回环地址")
    parser.add_argument("--business-url", default=BUSINESS_URL, help="运行容器在 agent 网络内访问业务站的地址")
    arguments = parser.parse_args(argv)
    provisioned = json.loads(sys.stdin.read())
    report = write_worker_profile(provisioned, root=arguments.root,
                                  base_url=arguments.base_url, business_url=arguments.business_url)
    # The secret went to the file, never to stdout: this line is safe in a terminal history.
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
