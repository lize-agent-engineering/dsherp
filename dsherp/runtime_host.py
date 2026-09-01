"""Shared pinned, single-slot host settings for on-demand business Runtime containers."""
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd'
KEYS = ('DEEPSEEK_API_KEY', 'DSH_MODEL', 'DEEPSEEK_BASE_URL')


def load_settings(path):
    values = dotenv_values(path, interpolate=False)
    if any(not isinstance(values.get(key), str) or not values[key].strip() for key in KEYS):
        raise ValueError('Explicit provider file must contain all three required settings')
    return {key: values[key] for key in KEYS}


def container_base(name):
    return ['docker', 'run', '--rm', '--pull=never', '--name', name,
            '--memory', '384m', '--memory-swap', '384m', '--cpus', '0.1', '--pids-limit', '96',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user', '0:0',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m', '--network', 'dsherp-validation_api']
