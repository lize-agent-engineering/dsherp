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


def _labelled(image_id, architecture="amd64", layers=("sha256:" + "1" * 64,), **labels):
    return {"Id": image_id, "Architecture": architecture, "Os": "linux",
            "RootFS": {"Type": "layers", "Layers": list(layers)},
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
        "id": "sha256:aa", "architecture": "amd64", "os": "linux",
        "diff_ids": ["sha256:" + "1" * 64],
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


def test_a_tree_without_a_git_checkout_cannot_be_a_build_context(tmp_path):
    """Reviewer bypass: a `git archive` export with a substituted marker file still let a
    modified file build under the original commit. Only a clean checkout proves content."""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "RELEASE_SOURCE").write_text("a" * 40 + " tag: v0.3.0\n")

    def no_git(command, **kwargs):
        raise AssertionError("git must not be consulted without a checkout")

    with pytest.raises(ValueError, match="checkout"):
        release_images.source(tmp_path, "v0.3.0", runner=no_git)
    assert not (release_images.ROOT / "infra/RELEASE_SOURCE").exists()
    assert not (release_images.ROOT / ".gitattributes").exists()


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


def _saving(calls, digests):
    """A docker that writes a real save-format tar (manifest.json with the config digests)."""
    import io
    import tarfile

    def runner(command, **kwargs):
        calls.append(command)
        out = command[command.index("-o") + 1]
        entries = [{"Config": f"blobs/sha256/{digest}", "RepoTags": [image]} for image, digest in digests.items()]
        data = json.dumps(entries).encode()
        with tarfile.open(out, "w") as tar:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return runner


IMAGES = ["registry.example.com/dsherp/dsherp-frappe:v0.3.0", "registry.example.com/dsherp/dsherp-worker:v0.3.0"]
A, B = "a" * 64, "b" * 64


def _manifest_file(path, ids):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tag": "v0.3.0", "images": {image: {"id": "sha256:" + digest} for image, digest in ids.items()}}))
    return path


def test_a_bundle_ships_the_images_and_the_manifest_together(tmp_path):
    """Review round 3: the manifest enters git after the tag, so a host that fetches source by
    tag lacks it. The bundle a build machine hands over carries the manifest next to the images,
    and the tar is proven to hold exactly the images the manifest names."""
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json", {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    calls = []
    written = release_images.bundle(tmp_path / "out", IMAGES, manifest, runner=_saving_oci(calls, dict(LAYERS)))
    assert calls == [["docker", "save", "-o", str(tmp_path / "out" / "dsherp-v0.3.0.tar"), *IMAGES]]
    assert written == {"images": tmp_path / "out" / "dsherp-v0.3.0.tar", "manifest": tmp_path / "out" / "v0.3.0.json"}
    assert (tmp_path / "out" / "v0.3.0.json").read_text() == manifest.read_text()

    def failed(command, **kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "no space left"})()

    with pytest.raises(RuntimeError, match="no space left"):
        release_images.bundle(tmp_path / "out2", IMAGES, manifest, runner=failed)
    assert not (tmp_path / "out2" / "v0.3.0.json").exists()  # no half bundle: the manifest follows the images


def test_a_bundle_whose_tar_does_not_hold_the_manifests_images_is_not_handed_over(tmp_path):
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json", {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    with pytest.raises(RuntimeError, match=IMAGES[1]):
        release_images.bundle(tmp_path / "out3", IMAGES, manifest,
                              runner=_saving_oci([], {IMAGES[0]: LAYERS[IMAGES[0]]}))  # image missing from tar
    assert not (tmp_path / "out3" / "v0.3.0.json").exists()


def test_a_bundle_never_overwrites_an_earlier_one_and_never_lands_in_the_build_context(tmp_path):
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json", {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    calls = []
    release_images.bundle(tmp_path / "out", IMAGES, manifest, runner=_saving_oci(calls, dict(LAYERS)))
    with pytest.raises(RuntimeError, match="dsherp-v0.3.0.tar"):
        release_images.bundle(tmp_path / "out", IMAGES, manifest, runner=_saving_oci(calls, dict(LAYERS)))
    assert len(calls) == 1  # refused before docker save
    with pytest.raises(ValueError, match="build context"):
        release_images.bundle(release_images.ROOT / "dist", IMAGES, manifest, runner=_saving(calls, {}))
    assert len(calls) == 1 and not (release_images.ROOT / "dist").exists()


def test_the_cli_bundles_after_the_manifest_is_written(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("DSHERP_ENV", "prod")
    resolved = _settings()
    monkeypatch.setattr(release_images.deploy_env, "settings", lambda *a, **k: resolved)
    monkeypatch.setattr(release_images, "source", lambda *a, **k: "abc1234")
    monkeypatch.setattr(release_images.subprocess, "run", lambda *a, **k: seen.setdefault("built", []).append(a[0]))
    monkeypatch.setattr(release_images, "inspect", lambda images: {name: _labelled("sha256:" + A) for name in images})
    monkeypatch.setattr(release_images, "write_manifest", lambda *a, **k: tmp_path / "infra" / "releases" / "v0.3.0.json")
    def bundled(target, images, manifest):
        seen["bundle"] = (target, images, manifest)
        return {}
    monkeypatch.setattr(release_images, "bundle", bundled)
    release_images.main(["--platform", "linux/amd64", "--bundle", str(tmp_path / "dist")])
    assert seen["bundle"] == (str(tmp_path / "dist"), IMAGES, tmp_path / "infra" / "releases" / "v0.3.0.json")
    assert len(seen["built"]) == 2


def _saving_oci(calls, contents):
    """A Docker 29 `docker save`: an OCI tar whose config blob digest is **not** the image id.

    Docker keeps a schema2 config and reports its digest as `.Id`; `docker save` writes an OCI
    layout whose config is a different document, so the same image gets a different digest on
    the two sides. `docker save` has no `--format`, so this is the only shape a real build
    produces. What both formats agree on is `rootfs.diff_ids`.
    """
    import hashlib
    import io
    import tarfile

    def runner(command, **kwargs):
        calls.append(command)
        out = command[command.index("-o") + 1]
        with tarfile.open(out, "w") as tar:
            def add(name, data):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

            entries = []
            for image, diff_ids in contents.items():
                config = json.dumps({"rootfs": {"type": "layers", "diff_ids": diff_ids}}).encode()
                digest = hashlib.sha256(config).hexdigest()
                add(f"blobs/sha256/{digest}", config)
                entries.append({"Config": f"blobs/sha256/{digest}", "RepoTags": [image]})
            add("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
            add("index.json", b'{"schemaVersion":2,"manifests":[]}')
            add("manifest.json", json.dumps(entries).encode())
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return runner


LAYERS = {IMAGES[0]: ["sha256:" + "1" * 64, "sha256:" + "2" * 64], IMAGES[1]: ["sha256:" + "3" * 64]}


def _layered_manifest(path, ids, layers):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tag": "v0.3.0", "images": {
        image: {"id": "sha256:" + digest, "diff_ids": layers[image]} for image, digest in ids.items()}}))
    return path


def test_a_bundle_is_handed_over_when_the_tar_holds_the_same_layers(tmp_path):
    """Until 2026-09-13 the bundle compared the tar's config digest with the local image id.
    On Docker 29 those never match (OCI vs schema2), so **every** real bundle was refused and
    G1 had no way to produce a handover set — while the images themselves were correct.
    """
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json",
                                 {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    written = release_images.bundle(tmp_path / "out", IMAGES, manifest,
                                    runner=_saving_oci([], dict(LAYERS)))
    assert written["manifest"].read_text() == manifest.read_text()


def test_a_bundle_whose_tar_holds_other_layers_is_still_refused(tmp_path):
    """The negative control the fix must not cost: a tar that is not these images stays refused.
    Without it, 'compare the layers' could be satisfied by comparing nothing at all."""
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json",
                                 {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    swapped = {IMAGES[0]: LAYERS[IMAGES[0]], IMAGES[1]: ["sha256:" + "9" * 64]}
    with pytest.raises(RuntimeError, match=IMAGES[1]):
        release_images.bundle(tmp_path / "out", IMAGES, manifest, runner=_saving_oci([], swapped))
    assert not (tmp_path / "out" / "v0.3.0.json").exists()


def test_a_manifest_without_recorded_layers_is_refused(tmp_path):
    """A manifest from before this change cannot be checked against an OCI tar; say so rather
    than fall back to the comparison that never held."""
    manifest = _manifest_file(tmp_path / "infra" / "releases" / "v0.3.0.json", {IMAGES[0]: A, IMAGES[1]: B})
    with pytest.raises(RuntimeError, match="diff_ids"):
        release_images.bundle(tmp_path / "out", IMAGES, manifest, runner=_saving_oci([], dict(LAYERS)))


def test_a_tar_whose_config_blob_is_absent_is_refused_with_a_readable_reason(tmp_path):
    """`_saving` writes the pre-Docker-29 shape: a manifest.json naming configs the tar does not
    carry as blobs. Reading it used to raise KeyError from tarfile; a handover refusal has to
    say what is wrong with the archive."""
    manifest = _layered_manifest(tmp_path / "infra" / "releases" / "v0.3.0.json",
                                 {IMAGES[0]: A, IMAGES[1]: B}, LAYERS)
    with pytest.raises(RuntimeError, match="not a readable docker save"):
        release_images.bundle(tmp_path / "out", IMAGES, manifest,
                              runner=_saving([], {IMAGES[0]: A, IMAGES[1]: B}))
