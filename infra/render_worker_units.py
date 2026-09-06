#!/usr/bin/env python3
"""Render the supervisor unit for the multi-Site context worker.

systemd is the production form; the macOS LaunchAgent stays as the development one.
Both are rendered from the same inputs so a contract test can hold them together.
"""
import argparse
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.dsherp.agent-worker-v16"
UNIT_NAME = "dsherp-agent-worker.service"
FIREWALL_UNIT_NAME = "dsherp-agent-firewall.service"
# Installed by root from infra/systemd/dsherp-agent-firewall.sh; root must not execute
# code from the service account's tree, so the unit names the installed copy.
FIREWALL_SCRIPT = "/usr/local/sbin/dsherp-agent-firewall"
FIREWALL_UNIT = """[Unit]
Description=dsherp host firewall for the agent network
Documentation=file://{root}/docs/engineering/deployment-runbook.md
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={script} apply {network}
ExecStop={script} remove {network}

[Install]
WantedBy=multi-user.target
"""
UNIT = """[Unit]
Description=dsherp business agent worker
Documentation=file://{root}/docs/engineering/deployment-runbook.md
# The host rules that keep run containers off this host must be in place first, and
# the worker goes down with them.
Requires=docker.service dsherp-agent-firewall.service
After=docker.service network-online.target dsherp-agent-firewall.service

[Service]
Type=notify
NotifyAccess=main
WatchdogSec={watchdog}s
Restart=always
RestartSec=10
User={user}
Group={group}
SupplementaryGroups=docker
WorkingDirectory={root}
# The unit is the production supervisor: without this the worker resolves the
# development environment and looks for a Runtime volume that only exists there.
Environment=DSHERP_ENV=prod
ExecStart={python} -m dsherp.context_worker --profile {profile} --provider-env {provider_env}
KillSignal=SIGTERM
TimeoutStopSec={stop_timeout}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dsherp-agent-worker

# The worker owns two directories and nothing else on the host.
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths={root}/.runtime -{root}/work

[Install]
WantedBy=multi-user.target
"""
NAME = re.compile('[A-Za-z_][A-Za-z0-9_-]*')
NETWORK = re.compile('[A-Za-z0-9][A-Za-z0-9_.-]*')


def _write(target, text):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f'.{target.name}.{os.getpid()}')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(text)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def render_systemd_unit(root=ROOT, *, user, group='dsherp', target=None, python=None,
                        profile=None, provider_env=None, watchdog=60, stop_timeout=120):
    root = Path(root).resolve()
    for value in (user, group):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise ValueError('Invalid service account: ' + repr(value))
    if type(watchdog) is not int or not 10 <= watchdog <= 600:
        raise ValueError('Invalid watchdog interval')
    if type(stop_timeout) is not int or stop_timeout < 30:
        raise ValueError('Stop timeout must outlast the shutdown drain')
    text = UNIT.format(
        root=root, user=user, group=group, watchdog=watchdog, stop_timeout=stop_timeout,
        python=python or f'{root}/.venv/bin/python',
        profile=profile or f'{root}/.runtime/context-worker-sites.json',
        provider_env=provider_env or f'{root}/.env',
    )
    return _write(Path(target) if target else root / '.runtime' / UNIT_NAME, text)


def render_firewall_unit(root=ROOT, *, agent_network, target=None):
    """The oneshot unit that applies the host INPUT rules for the agent network's bridge
    at boot and whenever it is restarted; the worker unit requires it."""
    root = Path(root).resolve()
    if not isinstance(agent_network, str) or not NETWORK.fullmatch(agent_network):
        raise ValueError('Invalid agent network name: ' + repr(agent_network))
    text = FIREWALL_UNIT.format(root=root, script=FIREWALL_SCRIPT, network=agent_network)
    return _write(Path(target) if target else root / '.runtime' / FIREWALL_UNIT_NAME, text)


def main():
    parser = argparse.ArgumentParser(description='Render the dsherp worker and firewall systemd units')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--user', required=True)
    parser.add_argument('--group', default='dsherp')
    parser.add_argument('--target', type=Path, help='worker unit path; the firewall unit lands beside it')
    parser.add_argument('--agent-network', help='defaults to the agent network of infra/env/prod.env under --root')
    arguments = parser.parse_args()
    network = arguments.agent_network
    if not network:
        from dsherp import deploy_env
        network = deploy_env.settings(dict(os.environ, DSHERP_ENV='prod'), root=arguments.root)['agent_network']
    worker = render_systemd_unit(arguments.root, user=arguments.user, group=arguments.group, target=arguments.target)
    print(worker)
    print(render_firewall_unit(arguments.root, agent_network=network, target=worker.with_name(FIREWALL_UNIT_NAME)))


if __name__ == '__main__':
    main()
