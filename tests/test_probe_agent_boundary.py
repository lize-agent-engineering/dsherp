"""The boundary probe runs the real run-container argv, not a weaker hand-typed one."""
from pathlib import Path

from dsherp import deploy_env
from infra import probe_agent_boundary as probe


PROD = deploy_env.settings({
    "DSHERP_ENV": "prod", "DSHERP_PROJECT": "dsherp", "DSHERP_BASE_DOMAIN": "tenant.example.com",
    "DSHERP_PLATFORM_SLUG": "platform", "DSHERP_IMAGE_TAG": "v0.3.0",
    "DSHERP_IMAGE_REGISTRY": "registry.example.com/dsherp", "DSHERP_AGENT_UID": "1000", "DSHERP_AGENT_GID": "1000"})


def test_probe_keeps_every_hardening_flag_of_a_real_run_container():
    command = probe.probe_command(PROD, Path("/srv/dsherp"), "backend", "platform-backend", "192.168.0.1")
    for flag in ("--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", "--memory"):
        assert flag in command, flag
    assert command[command.index("--user") + 1] == "1000:1000"
    assert command[command.index("--network") + 1] == "dsherp_agent"
    assert command[command.index("--entrypoint") + 1] == "python3"
    assert "registry.example.com/dsherp/dsherp-worker:v0.3.0" in command
    assert not any(":/run/business.json:" in token or ":/session:" in token for token in command)
    assert "DSHERP_PROBE_GATEWAY=192.168.0.1" in command
    assert command[-2] == "-c" and "host_gateway_ssh" in command[-1]


def test_evaluate_names_every_expectation_the_container_violates():
    good = {"public_dns": False, "public_ip": False, "egress": True, "backend": True, "platform_backend": False,
            "db": False, "redis": False, "host_gateway_ssh": False, "control_plane_files": [],
            "code_writable": False, "runtime_writable": False, "no_new_privs": True, "cap_bounding_zero": True}
    assert probe.evaluate(good) == {}
    bad = dict(good, host_gateway_ssh=True, public_ip=True)
    assert set(probe.evaluate(bad)) == {"host_gateway_ssh", "public_ip"}
