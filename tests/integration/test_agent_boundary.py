"""G4 boundary, measured on a real container built the way a real run builds one.

Everything here is observed inside the container the worker would actually start:
the same image, the same user, the same network, the same mounts.
"""
import json
import subprocess
from pathlib import Path

import pytest

from dsherp import deploy_env
from dsherp.context_container import docker_command


ROOT = Path(__file__).resolve().parents[2]
DEV = deploy_env.settings({"DSHERP_ENV": "dev"})
NAME = "dsherp-context-" + "b" * 32
PROBE = r"""
import json, os, socket, urllib.request

def reachable(host, port, timeout=5):
    try:
        socket.create_connection((host, port), timeout).close()
        return True
    except OSError:
        return False

report = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "public_dns": reachable("api.deepseek.com", 443),
    "public_ip": reachable("1.1.1.1", 443),
    "egress": reachable("agent-egress", 8890),
    "business": reachable("dsherp-validation-backend-1", 8000),
    "platform_frontend": reachable("dsherp-validation-platform-frontend-1", 8080),
    "control_plane_files": sorted(
        name for name in ("/opt/dsherp/infra", "/opt/dsherp/infra/compose.validation.yml",
                          "/opt/dsherp/infra/prepare_agent_runtime.sh", "/opt/dsherp/.env",
                          "/opt/dsherp/.runtime") if os.path.exists(name)),
    "writable_root": os.access("/opt/dsherp", os.W_OK),
}
print("DSHERP_PROBE " + json.dumps(report))
"""


@pytest.fixture(scope="module")
def boundary(tmp_path_factory, module_residue):
    # `docker run --rm` removes the container when the probe exits; it stays when the docker
    # client is killed by the timeout below while the probe still runs, and a same-name
    # leftover makes the next `docker run --name` fail outright. Register before running.
    module_residue.container(NAME)
    directory = tmp_path_factory.mktemp("boundary")
    secret = directory / "run.json"
    secret.write_text(json.dumps({"run_id": "probe", "capability": "probe"}))
    secret.chmod(0o600)
    session = directory / "session"
    session.mkdir(mode=0o700)
    command = docker_command(ROOT, secret, session, NAME, DEV)
    # Same container in every respect except the program it runs.
    command = list(command)
    command[command.index("--entrypoint") + 1] = "python3"
    image = deploy_env.BASE_IMAGE
    command = command[:command.index(image) + 1] + ["-c", PROBE]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    line = [row for row in result.stdout.splitlines() if row.startswith("DSHERP_PROBE ")]
    assert line, result.stdout[-2000:]
    return json.loads(line[0].removeprefix("DSHERP_PROBE "))


def test_the_run_container_never_runs_as_root(boundary):
    assert boundary["uid"] != 0 and boundary["gid"] != 0
    assert boundary["uid"] == DEV["agent_uid"]


def test_the_run_container_cannot_reach_anything_on_the_public_internet(boundary):
    assert boundary["public_dns"] is False
    assert boundary["public_ip"] is False


def test_the_run_container_reaches_only_the_provider_proxy_and_its_business_site(boundary):
    assert boundary["egress"] is True
    assert boundary["business"] is True
    assert boundary["platform_frontend"] is False


def test_no_control_plane_file_is_visible_from_inside_the_run_container(boundary):
    assert boundary["control_plane_files"] == []
    assert boundary["writable_root"] is False


def test_the_container_is_attached_to_the_internal_agent_network_only():
    inspected = subprocess.run(
        ["docker", "network", "inspect", DEV["agent_network"], "--format", "{{json .Internal}}"],
        capture_output=True, text=True, timeout=60)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout.strip()) is True


def test_the_provider_is_reachable_through_the_proxy_and_only_through_it():
    """The proxy answers for the provider; the same request without it cannot resolve."""
    image = deploy_env.BASE_IMAGE
    script = ("import http.client,json\n"
              "c=http.client.HTTPConnection('agent-egress',8890,timeout=20)\n"
              "c.request('GET','/models',headers={'Authorization':'Bearer invalid'})\n"
              "print('DSHERP_STATUS',c.getresponse().status)")
    result = subprocess.run(
        ["docker", "run", "--rm", "--pull=never", "--network", DEV["agent_network"],
         "--user", DEV["agent_user"], "-e", "HOME=/tmp", "--entrypoint", "python3", image, "-c", script],
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-1000:]
    # 401 is the provider itself refusing an invalid token: the request left the network.
    assert "DSHERP_STATUS 401" in result.stdout, result.stdout
