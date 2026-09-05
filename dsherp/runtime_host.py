"""Shared host settings for on-demand business Runtime containers.

The image, the network and the container identity all come from the resolved
deployment environment: development keeps the pinned upstream base and the working
copy, production runs the release image on an internal network with no way out.
"""
from dotenv import dotenv_values

from dsherp import deploy_env


ROOT = deploy_env.ROOT
IMAGE = deploy_env.BASE_IMAGE
KEYS = ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL')


def load_settings(path):
    values = dotenv_values(path, interpolate=False)
    if any(not isinstance(values.get(key), str) or not values[key].strip() for key in KEYS):
        raise ValueError('Explicit provider file must contain API key and base URL')
    return {key: values[key] for key in KEYS}


def agent_settings(path, resolved=None):
    """Provider settings as the run container will see them, plus the deployment identity.

    The container reaches the provider only through the egress proxy, so the base URL it
    is given is not the one the host uses for its own circuit probe.
    """
    resolved = resolved or deploy_env.settings()
    values = load_settings(path)
    return {'DEEPSEEK_API_KEY': values['DEEPSEEK_API_KEY'],
            'DEEPSEEK_BASE_URL': resolved['agent_provider_base_url'],
            'deployment_digest': deploy_env.deployment_digest(resolved)}


def container_base(name, resolved=None):
    resolved = resolved or deploy_env.settings()
    if resolved['agent_uid'] == 0 or resolved['agent_gid'] == 0:
        raise ValueError('Agent container must never run as root')
    pull = '--pull=never' if resolved['env'] == 'dev' else '--pull=missing'
    return ['docker', 'run', '--rm', pull, '--name', name,
            '--label', 'dsherp.project=' + resolved['project'],
            '--memory', '384m', '--memory-swap', '384m', '--cpus', '0.1', '--pids-limit', '96',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user', resolved['agent_user'],
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
            '--network', resolved['agent_network']]
