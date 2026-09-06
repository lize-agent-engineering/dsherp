"""Build the two release images from a repository tag and record what was produced.

A release is identified by the tag and the architecture it was built for; the
manifest is the only place that maps a tag to concrete image identifiers, so a
rollback never has to guess which image a Site was running.

The build context is the working directory, so the manifest may only name a commit
the directory provably is: a clean git checkout whose tag points at HEAD. An exported
tree cannot prove its content (a substituted marker file only records where the export
came from, not what was changed afterwards), so a build machine needs git; a target
host only loads images. `--git-commit` is a cross-check, never the source of truth.
The image labels are compared with the same values before the manifest is written.
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
FULL_COMMIT = re.compile("[0-9a-f]{40}")
REVISION_LABEL = "org.opencontainers.image.revision"
VERSION_LABEL = "org.opencontainers.image.version"
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


def _git(root, runner, *arguments):
    return runner(["git", "-C", str(root), *arguments], text=True, capture_output=True, timeout=60)


def _checkout_source(root, image_tag, runner):
    top = _git(root, runner, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != Path(root).resolve():
        raise ValueError("The build context is not the root of a git checkout: " + str(root))
    status = _git(root, runner, "status", "--porcelain")
    if status.returncode:
        raise ValueError("git status failed in " + str(root))
    # The manifest a previous run of this script wrote is the one untracked file tolerated.
    dirty = [line for line in status.stdout.splitlines()
             if line.strip() and not line.startswith("?? infra/releases/")]
    if dirty:
        raise ValueError("Release images are built from a clean checkout; this tree differs from its commit:\n"
                         + "\n".join(dirty))
    head = _git(root, runner, "rev-parse", "HEAD")
    commit = head.stdout.strip()
    if head.returncode or not FULL_COMMIT.fullmatch(commit):
        raise ValueError("Cannot resolve HEAD in " + str(root))
    tag = _git(root, runner, "rev-parse", "--verify", f"{image_tag}^{{commit}}")
    if tag.returncode:
        raise ValueError(f"Tag {image_tag!r} does not exist in this checkout; create it on the release commit first")
    if tag.stdout.strip() != commit:
        raise ValueError(f"Tag {image_tag!r} names {tag.stdout.strip()[:12]}, but HEAD is {commit[:12]}: check out the tag")
    return commit


def source(root, image_tag, *, git_commit=None, runner=subprocess.run):
    """The commit this tree provably is, for the tag being released."""
    if git_commit is not None and not COMMIT.fullmatch(git_commit or ""):
        raise ValueError("Release images require one clean commit id: " + repr(git_commit))
    root = Path(root)
    if not (root / ".git").exists():
        raise ValueError("Release images are built from a clean git checkout only; this tree has no .git: " + str(root))
    commit = _checkout_source(root, image_tag, runner)
    if git_commit is not None and not commit.startswith(git_commit):
        raise ValueError(f"--git-commit {git_commit!r} is not the commit this tree is built from ({commit[:12]})")
    return commit


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
        labels = (row.get("Config") or {}).get("Labels") or {}
        if labels.get(REVISION_LABEL) != git_commit or labels.get(VERSION_LABEL) != resolved["image_tag"]:
            raise ValueError(f"{name} is labelled {labels.get(VERSION_LABEL)!r} at {labels.get(REVISION_LABEL)!r}, "
                             f"not {resolved['image_tag']!r} at {git_commit!r}; do not record it")
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
    parser.add_argument("--git-commit", default=None,
                        help="cross-check only: must be the commit the tree is (checkout HEAD or the exported one)")
    arguments = parser.parse_args(argv)
    resolved = deploy_env.settings(dict(os.environ, DSHERP_ENV="prod"))
    commit = source(ROOT, resolved["image_tag"], git_commit=arguments.git_commit)
    for command in build_commands(resolved, git_commit=commit, platform=arguments.platform):
        print(" ".join(command), file=sys.stderr)
        subprocess.run(command, cwd=ROOT, check=True)
    images = [resolved[key] for _, _, key in TARGETS]
    target = write_manifest(resolved, git_commit=commit, platform=arguments.platform, inspected=inspect(images))
    print(target)


if __name__ == "__main__":
    main()
