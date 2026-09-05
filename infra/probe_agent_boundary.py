#!/usr/bin/env python3
"""Measure the G4 boundary from inside a run container built exactly like a real one.

Runs on any host with the resolved deployment environment: the same image, user,
network and hardening flags `context_container` would use, with the entrypoint replaced
by a probe. Prints one JSON line and exits 1 when any expectation fails.
"""
import argparse
import json
import subprocess
import sys

from dsherp import deploy_env
from dsherp.context_container import docker_command


PROBE = r"""
import json, os, platform, socket, sys
def reach(host, port, timeout=5):
    try:
        socket.create_connection((host, port), timeout).close(); return True
    except OSError:
        return False
gateway = os.environ.get('DSHERP_PROBE_GATEWAY')
report = {
    'machine': platform.machine(), 'uid': os.getuid(), 'gid': os.getgid(),
    'public_dns': reach('api.deepseek.com', 443), 'public_ip': reach('1.1.1.1', 443),
    'egress': reach('agent-egress', 8890), 'backend': reach(os.environ['DSHERP_PROBE_BACKEND'], 8000),
    'platform_backend': reach(os.environ['DSHERP_PROBE_PLATFORM'], 8000),
    'db': reach('db', 3306), 'redis': reach('redis-queue', 6379) or reach('redis', 6379),
    'host_gateway_ssh': reach(gateway, 22) if gateway else None,
    'host_gateway_loopback_port': reach(gateway, 8000) if gateway else None,
    'control_plane_files': [p for p in ('/opt/dsherp/infra', '/opt/dsherp/.env', '/opt/dsherp/.runtime', '/run/secrets')
                            if os.path.exists(p)],
    'code_writable': os.access('/opt/dsherp', os.W_OK), 'runtime_writable': os.access('/opt/runtime', os.W_OK),
    'no_new_privs': 'NoNewPrivs:\t1' in open('/proc/self/status').read(),
    'cap_bounding_zero': [l for l in open('/proc/self/status') if l.startswith('CapBnd')][0].split()[1].strip('0') == '',
}
print('DSHERP_PROBE ' + json.dumps(report))
"""
# What a correct boundary looks like; None means "not asserted".
EXPECT = {'public_dns': False, 'public_ip': False, 'egress': True, 'backend': True, 'platform_backend': False,
          'db': False, 'redis': False, 'host_gateway_ssh': False, 'control_plane_files': [],
          'code_writable': False, 'runtime_writable': False, 'no_new_privs': True, 'cap_bounding_zero': True}


def gateway_of(network, runner=subprocess.run):
    result = runner(['docker', 'network', 'inspect', network, '--format', '{{(index .IPAM.Config 0).Gateway}}'],
                    text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    return result.stdout.strip() if result.returncode == 0 else ''


def probe_command(resolved, root, backend, platform, gateway):
    """The real run-container argv with only the program swapped for the probe."""
    command = list(docker_command(root, root / 'infra' / 'probe_agent_boundary.py', root, 'dsherp-context-probe', resolved))
    command[command.index('--entrypoint') + 1] = 'python3'
    image = resolved['base_image'] if resolved['env'] == 'dev' else resolved['worker_image']
    head = command[:command.index(image) + 1]
    # No run.json and no session directory for a probe: drop the two run-specific mounts.
    cleaned = []
    skip = False
    for token in head:
        if skip:
            skip = False; continue
        if token == '-v' and (':/run/business.json:' in head[head.index(token) + 1] or ':/session:' in head[head.index(token) + 1]):
            skip = True; continue
        cleaned.append(token)
    env = ['-e', f'DSHERP_PROBE_BACKEND={backend}', '-e', f'DSHERP_PROBE_PLATFORM={platform}', '-e', 'HOME=/tmp']
    if gateway:
        env += ['-e', f'DSHERP_PROBE_GATEWAY={gateway}']
    # Drop --rm's sibling flags that need a session: the probe never writes.
    return cleaned[:-1] + env + [image, '-c', PROBE]


def evaluate(report):
    return {key: (report.get(key), want) for key, want in EXPECT.items() if want is not None and report.get(key) != want}


def main(argv=None):
    parser = argparse.ArgumentParser(description='G4 boundary probe from a real run container')
    parser.add_argument('--backend', default=None, help='business backend host as the container sees it')
    parser.add_argument('--platform', default='platform-backend')
    parser.add_argument('--no-gateway', action='store_true', help='skip the host-gateway probe')
    arguments = parser.parse_args(argv)
    resolved = deploy_env.settings()
    backend = arguments.backend or ('dsherp-validation-backend-1' if resolved['env'] == 'dev' else 'backend')
    gateway = '' if arguments.no_gateway else gateway_of(resolved['agent_network'])
    command = probe_command(resolved, deploy_env.ROOT, backend, arguments.platform, gateway)
    result = subprocess.run(command, text=True, capture_output=True, timeout=240)
    line = [row for row in result.stdout.splitlines() if row.startswith('DSHERP_PROBE ')]
    if result.returncode or not line:
        print(result.stderr[-1500:], file=sys.stderr)
        return 2
    report = json.loads(line[0].removeprefix('DSHERP_PROBE '))
    failures = evaluate(report)
    print(json.dumps({'report': report, 'failures': failures}, ensure_ascii=False, indent=1))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
