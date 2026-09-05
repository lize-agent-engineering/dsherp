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


def test_the_release_manifest_records_tag_commit_base_and_every_built_architecture(tmp_path):
    inspected = {
        "registry.example.com/dsherp/dsherp-frappe:v0.3.0": {"Id": "sha256:aa", "Architecture": "amd64", "Os": "linux"},
        "registry.example.com/dsherp/dsherp-worker:v0.3.0": {"Id": "sha256:bb", "Architecture": "amd64", "Os": "linux"},
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
            inspected={"registry.example.com/dsherp/dsherp-frappe:v0.3.0": {"Id": "sha256:aa", "Architecture": "amd64", "Os": "linux"}},
            root=tmp_path,
        )


def test_the_release_manifest_refuses_an_architecture_that_does_not_match_the_request(tmp_path):
    inspected = {
        "registry.example.com/dsherp/dsherp-frappe:v0.3.0": {"Id": "sha256:aa", "Architecture": "arm64", "Os": "linux"},
        "registry.example.com/dsherp/dsherp-worker:v0.3.0": {"Id": "sha256:bb", "Architecture": "amd64", "Os": "linux"},
    }
    with pytest.raises(ValueError):
        release_images.write_manifest(
            _settings(), git_commit="abc1234", platform="linux/amd64", inspected=inspected, root=tmp_path
        )
