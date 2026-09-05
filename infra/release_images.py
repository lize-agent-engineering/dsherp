"""Build the two release images from a repository tag and record what was produced.

A release is identified by the tag and the architecture it was built for; the
manifest is the only place that maps a tag to concrete image identifiers, so a
rollback never has to guess which image a Site was running.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from dsherp import deploy_env


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("linux/amd64", "linux/arm64")
COMMIT = re.compile("[0-9a-f]{7,40}")
TARGETS = (
    ("frappe", "infra/docker/frappe/Dockerfile", "frappe_image"),
    ("worker", "infra/docker/worker/Dockerfile", "worker_image"),
)


def _checked(resolved, git_commit, platform):
    if resolved["env"] != "prod":
        raise ValueError("Release images are built from a production environment file only")
    if not COMMIT.fullmatch(git_commit or ""):
        raise ValueError("Release images require one clean commit id: " + repr(git_commit))
    if platform is not None and platform not in PLATFORMS:
        raise ValueError("Unsupported release platform: " + repr(platform))
    return [resolved[key] for _, _, key in TARGETS]


def build_commands(resolved, *, git_commit, platform=None, root=ROOT):
    """Return the exact docker invocations; nothing is executed here."""
    _checked(resolved, git_commit, platform)
    commands = []
    for _, dockerfile, key in TARGETS:
        command = ["docker", "buildx", "build"]
        if platform:
            command += ["--platform", platform]
        command += [
            "-f", str(Path(root) / dockerfile),
            "-t", resolved[key],
            "--build-arg", f"DSHERP_BASE_IMAGE={deploy_env.BASE_IMAGE}",
            "--build-arg", f"DSHERP_IMAGE_TAG={resolved['image_tag']}",
            "--build-arg", f"DSHERP_GIT_COMMIT={git_commit}",
            "--load", str(root),
        ]
        commands.append(command)
    return commands


def write_manifest(resolved, *, git_commit, platform, inspected, root=ROOT):
    images = _checked(resolved, git_commit, platform)
    missing = [name for name in images if name not in inspected]
    if missing:
        raise ValueError("Release manifest is missing built images: " + ", ".join(missing))
    payload = {"tag": resolved["image_tag"], "git_commit": git_commit, "platform": platform,
               "base_image": deploy_env.BASE_IMAGE, "registry": resolved["registry"], "images": {}}
    for name in images:
        row = inspected[name]
        architecture = f"{row.get('Os')}/{row.get('Architecture')}"
        if platform and architecture != platform:
            raise ValueError(f"{name} was built for {architecture}, not {platform}")
        payload["images"][name] = {"id": row["Id"], "architecture": row["Architecture"], "os": row["Os"]}
    target = Path(root) / "infra" / "releases" / f"{resolved['image_tag']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return target


def inspect(images):
    result = subprocess.run(["docker", "image", "inspect", *images], text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Built images are not inspectable; do not record a manifest for them")
    return {name: row for name, row in zip(images, json.loads(result.stdout))}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the dsherp release images")
    parser.add_argument("--platform", choices=PLATFORMS, default="linux/amd64")
    parser.add_argument("--git-commit", default=None)
    arguments = parser.parse_args(argv)
    commit = arguments.git_commit or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    resolved = deploy_env.settings(dict(os.environ, DSHERP_ENV="prod"))
    for command in build_commands(resolved, git_commit=commit, platform=arguments.platform):
        print(" ".join(command), file=sys.stderr)
        subprocess.run(command, cwd=ROOT, check=True)
    images = [resolved[key] for _, _, key in TARGETS]
    target = write_manifest(resolved, git_commit=commit, platform=arguments.platform, inspected=inspect(images))
    print(target)


if __name__ == "__main__":
    main()
