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
UNIT = """[Unit]
Description=dsherp business agent worker
Documentation=file://{root}/docs/engineering/deployment-runbook.md
Requires=docker.service
After=docker.service network-online.target

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
    target = Path(target) if target else root / '.runtime' / UNIT_NAME
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


def main():
    parser = argparse.ArgumentParser(description='Render the dsherp worker systemd unit')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--user', required=True)
    parser.add_argument('--group', default='dsherp')
    parser.add_argument('--target', type=Path)
    arguments = parser.parse_args()
    print(render_systemd_unit(arguments.root, user=arguments.user, group=arguments.group, target=arguments.target))


if __name__ == '__main__':
    main()
