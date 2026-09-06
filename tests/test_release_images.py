"""Releases are built from a tag for a named architecture; development never builds over the base."""
import json

import pytest

from dsherp import deploy_env
from infra import release_images


PROD = {
    "DSHERP_ENV": "prod",
    "DSHERP_PROJECT": "dsherp",
    "DSHERP_BASE_DOMAIN": "tenant.example.com",
    "DSHERP_PLATFORM_SLUG": "platform",
    "DSHERP_IMAGE_TAG": "v0.3.0",
    "DSHERP_IMAGE_REGISTRY": "registry.example.com/dsherp",
    "DSHERP_AGENT_UID": "1000",
    "DSHERP_AGENT_GID": "1000",
}


def _settings():
    return deploy_env.settings(PROD)


def test_each_image_is_built_from_the_pinned_base_for_one_explicit_platform():
    commands = release_images.build_commands(_settings(), git_commit="abc1234", platform="linux/amd64")
    assert [command[-1] for command in commands] == [str(release_images.ROOT), str(release_images.ROOT)]
    frappe, worker = commands
    assert frappe[:3] == ["docker", "buildx", "build"]
    assert "--platform" in frappe and frappe[frappe.index("--platform") + 1] == "linux/amd64"
    assert frappe[frappe.index("-t") + 1] == "registry.example.com/dsherp/dsherp-frappe:v0.3.0"
    assert worker[worker.index("-t") + 1] == "registry.example.com/dsherp/dsherp-worker:v0.3.0"
    assert f"DSHERP_BASE_IMAGE={deploy_env.BASE_IMAGE}" in frappe
    assert "DSHERP_IMAGE_TAG=v0.3.0" in frappe and "DSHERP_GIT_COMMIT=abc1234" in frappe
    assert frappe[frappe.index("-f") + 1].endswith("infra/docker/frappe/Dockerfile")
    assert worker[worker.index("-f") + 1].endswith("infra/docker/worker/Dockerfile")


def test_development_cannot_build_release_images_over_the_upstream_digest():
    with pytest.raises(ValueError):
        release_images.build_commands(deploy_env.settings({"DSHERP_ENV": "dev"}), git_commit="abc1234")


def test_an_unknown_platform_is_refused_before_docker_is_invoked():
    with pytest.raises(ValueError):
        release_images.build_commands(_settings(), git_commit="abc1234", platform="linux/sparc")


def test_a_dirty_or_missing_commit_is_refused():
    for commit in ("", "abc 1234", "abc1234-dirty"):
        with pytest.raises(ValueError):
            release_images.build_commands(_settings(), git_commit=commit)


def _labelled(image_id, architecture="amd64", **labels):
    return {"Id": image_id, "Architecture": architecture, "Os": "linux",
            "Config": {"Labels": {"org.opencontainers.image.revision": "abc1234",
                                  "org.opencontainers.image.version": "v0.3.0", **labels}}}


def test_the_release_manifest_records_tag_commit_base_and_every_built_architecture(tmp_path):
    inspected = {
        "registry.example.com/dsherp/dsherp-frappe:v0.3.0": _labelled("sha256:aa"),
        "registry.example.com/dsherp/dsherp-worker:v0.3.0": _labelled("sha256:bb"),
    }
    target = release_images.write_manifest(
        _settings(), git_commit="abc1234", platform="linux/amd64", inspected=inspected, root=tmp_path
    )
    assert target == tmp_path / "infra" / "releases" / "v0.3.0.json"
    payload = json.loads(target.read_text())
    assert payload["tag"] == "v0.3.0" and payload["git_commit"] == "abc1234"
    assert payload["base_image"] == deploy_env.BASE_IMAGE
    assert payload["platform"] == "linux/amd64"
    assert payload["images"]["registry.example.com/dsherp/dsherp-frappe:v0.3.0"] == {
        "id": "sha256:aa", "architecture": "amd64", "os": "linux"
    }
    assert payload["images"]["registry.example.com/dsherp/dsherp-worker:v0.3.0"]["id"] == "sha256:bb"


def test_the_release_manifest_refuses_to_record_an_image_that_was_not_built(tmp_path):
    with pytest.raises(ValueError):
        release_images.write_manifest(
            _settings(), git_commit="abc1234", platform="linux/amd64",
            inspected={"registry.example.com/dsherp/dsherp-frappe:v0.3.0": _labelled("sha256:aa")},
            root=tmp_path,
        )


def test_the_release_manifest_refuses_an_architecture_that_does_not_match_the_request(tmp_path):
    inspected = {
        "registry.example.com/dsherp/dsherp-frappe:v0.3.0": _labelled("sha256:aa", architecture="arm64"),
        "registry.example.com/dsherp/dsherp-worker:v0.3.0": _labelled("sha256:bb"),
    }
    with pytest.raises(ValueError):
        release_images.write_manifest(
            _settings(), git_commit="abc1234", platform="linux/amd64", inspected=inspected, root=tmp_path
        )


# --- provenance: the built context must be the commit the manifest names ---------------
import subprocess

HEAD = "a" * 40
FRAPPE = "registry.example.com/dsherp/dsherp-frappe:v0.3.0"
WORKER = "registry.example.com/dsherp/dsherp-worker:v0.3.0"


def _git(responses):
    """A fake git keyed by the arguments after `git -C <root>`; None means a failing command."""
    def runner(command, **kwargs):
        assert command[:2] == ["git", "-C"], command
        key = tuple(command[3:])
        assert key in responses, ("unexpected git call", key)
        out = responses[key]
        return subprocess.CompletedProcess(command, 1 if out is None else 0, stdout=out or "", stderr="")
    return runner


def _clean(root):
    return {("rev-parse", "--show-toplevel"): str(root) + "\n", ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): HEAD + "\n", ("rev-parse", "--verify", "v0.3.0^{commit}"): HEAD + "\n"}


def test_the_source_of_a_release_is_the_clean_checkout_at_the_tag(tmp_path):
    (tmp_path / ".git").mkdir()
    assert release_images.source(tmp_path, "v0.3.0", runner=_git(_clean(tmp_path))) == HEAD
    assert release_images.source(tmp_path, "v0.3.0", git_commit=HEAD[:12], runner=_git(_clean(tmp_path))) == HEAD
    # The manifest a previous run of this script wrote is the one untracked file tolerated.
    dirty_by_manifest = {**_clean(tmp_path), ("status", "--porcelain"): "?? infra/releases/v0.3.0.json\n"}
    assert release_images.source(tmp_path, "v0.3.0", runner=_git(dirty_by_manifest)) == HEAD


def test_a_dirty_tree_a_missing_or_moved_tag_or_a_foreign_commit_is_refused(tmp_path):
    (tmp_path / ".git").mkdir()
    for override in (
        {("status", "--porcelain"): " M dsherp/admin.py\n"},
        {("status", "--porcelain"): "?? docs/new.md\n"},
        {("rev-parse", "--verify", "v0.3.0^{commit}"): None},
        {("rev-parse", "--verify", "v0.3.0^{commit}"): "b" * 40 + "\n"},
        {("rev-parse", "--show-toplevel"): "/somewhere/else\n"},
    ):
        with pytest.raises(ValueError):
            release_images.source(tmp_path, "v0.3.0", runner=_git({**_clean(tmp_path), **override}))
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.3.0", git_commit="b" * 40, runner=_git(_clean(tmp_path)))


def test_an_exported_tree_proves_its_source_through_the_substituted_release_file(tmp_path):
    (tmp_path / "infra").mkdir()
    marker = tmp_path / "infra" / "RELEASE_SOURCE"
    marker.write_text(HEAD + " tag: v0.3.0, HEAD -> main, origin/main\n")

    def no_git(command, **kwargs):
        raise AssertionError("git must not be consulted without a checkout")

    assert release_images.source(tmp_path, "v0.3.0", runner=no_git) == HEAD
    assert release_images.source(tmp_path, "v0.3.0", git_commit=HEAD[:7], runner=no_git) == HEAD
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.4.0", runner=no_git)
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.3.0", git_commit="b" * 7, runner=no_git)
    marker.write_text(HEAD + " HEAD -> main\n")  # exported from a branch tip, not a tag
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.3.0", runner=no_git)
    marker.write_text("$Format:%H %D$\n")  # not an export at all
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.3.0", runner=no_git)
    marker.unlink()
    with pytest.raises(ValueError):
        release_images.source(tmp_path, "v0.3.0", runner=no_git)


def test_the_release_source_file_is_substituted_by_git_archive():
    assert (release_images.ROOT / "infra/RELEASE_SOURCE").read_text().strip() == "$Format:%H %D$"
    assert "infra/RELEASE_SOURCE export-subst" in (release_images.ROOT / ".gitattributes").read_text()


def test_the_release_manifest_refuses_an_image_whose_labels_do_not_name_the_source(tmp_path):
    good = {FRAPPE: _labelled("sha256:aa"), WORKER: _labelled("sha256:bb")}
    assert release_images.write_manifest(_settings(), git_commit="abc1234", platform="linux/amd64",
                                         inspected=good, root=tmp_path).exists()
    for bad in (
        {FRAPPE: _labelled("sha256:aa"), WORKER: _labelled("sha256:bb", **{"org.opencontainers.image.revision": "fff0000"})},
        {FRAPPE: _labelled("sha256:aa", **{"org.opencontainers.image.version": "v0.2.9"}), WORKER: _labelled("sha256:bb")},
        {FRAPPE: {**_labelled("sha256:aa"), "Config": {"Labels": None}}, WORKER: _labelled("sha256:bb")},
    ):
        with pytest.raises(ValueError):
            release_images.write_manifest(_settings(), git_commit="abc1234", platform="linux/amd64", inspected=bad, root=tmp_path)
