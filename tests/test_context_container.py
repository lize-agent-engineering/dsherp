from pathlib import Path

import pytest

from dsherp import deploy_env
from dsherp.context_container import docker_command


DEV = deploy_env.settings({"DSHERP_ENV": "dev"})
PROD = deploy_env.settings({
    "DSHERP_ENV": "prod", "DSHERP_PROJECT": "dsherp", "DSHERP_BASE_DOMAIN": "tenant.example.com",
    "DSHERP_PLATFORM_SLUG": "platform", "DSHERP_IMAGE_TAG": "v0.3.0",
    "DSHERP_IMAGE_REGISTRY": "registry.example.com/dsherp",
    "DSHERP_AGENT_UID": "1000", "DSHERP_AGENT_GID": "1000",
})


def _command(resolved):
    return docker_command(Path("/project"), Path("/work/run.json"), Path("/state/tenant/user/conversation"),
                          "dsherp-context-test", resolved)


def _mounts(command):
    return [command[index + 1] for index, value in enumerate(command) if value == "-v"]


def test_business_container_mounts_only_current_session_and_run():
    command = _command(DEV)
    mounts = _mounts(command)
    assert "/work/run.json:/run/business.json:ro" in mounts
    assert "/state/tenant/user/conversation:/session:rw" in mounts
    assert "/project/runtime:/opt/dsherp/runtime:ro" in mounts
    assert "/project/business-skills:/opt/dsherp/business-skills:ro" in mounts
    assert not any("/state:" in mount or "/project:/opt" in mount for mount in mounts)
    assert command[command.index("--memory") + 1] == "384m"
    assert command[command.index("--cpus") + 1] == "0.1"
    assert "--read-only" in command and "--cap-drop=ALL" in command
    assert command[-2:] == ["-m", "dsherp.context_runner"]


def test_no_control_plane_file_is_mounted_into_a_tenant_container():
    for resolved in (DEV, PROD):
        mounts = _mounts(_command(resolved))
        assert not any("compose" in mount or "prepare_agent_runtime" in mount or "infra/" in mount
                       for mount in mounts), resolved["env"]


def test_the_container_runs_as_a_non_root_user_on_the_internal_agent_network():
    for resolved, expected in ((DEV, DEV["agent_user"]), (PROD, "1000:1000")):
        command = _command(resolved)
        assert command[command.index("--user") + 1] == expected
        assert command[command.index("--user") + 1] != "0:0"
        assert command[command.index("--network") + 1] == resolved["agent_network"]
        assert command[command.index("--network") + 1].endswith("_agent")
        assert "--security-opt=no-new-privileges" in command


def test_production_runs_the_release_image_with_no_working_copy_at_all():
    command = _command(PROD)
    mounts = _mounts(command)
    assert mounts == ["/work/run.json:/run/business.json:ro", "/state/tenant/user/conversation:/session:rw"]
    assert "registry.example.com/dsherp/dsherp-worker:v0.3.0" in command
    assert "--pull=missing" in command
    assert not any(mount.startswith("/project") for mount in mounts)


def test_development_keeps_the_working_copy_and_the_pinned_base_image():
    command = _command(DEV)
    assert deploy_env.BASE_IMAGE in command
    assert "--pull=never" in command
    assert "dsherp-v16-agent-runtime:/opt/runtime:ro" in _mounts(command)


def test_a_root_container_identity_is_refused_before_docker_is_invoked():
    with pytest.raises(ValueError):
        _command({**PROD, "agent_uid": 0, "agent_gid": 0, "agent_user": "0:0"})
