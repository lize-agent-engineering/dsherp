"""One environment file decides images, domains and networks for a whole deployment.

Nothing here reads the working copy: a run is identified by the release tag it was
started from, so a production host can be rebuilt from the repository tag alone.
"""
import os
import re
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
# Pinned upstream base; development keeps running straight from it, releases build on top.
BASE_IMAGE = 'frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd'
ENVIRONMENTS = ('dev', 'prod')
SLUG = re.compile('[a-z][a-z0-9-]*')
DOMAIN = re.compile('[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*')
TAG = re.compile('[A-Za-z0-9][A-Za-z0-9._-]*')
REGISTRY = re.compile('[a-z0-9][a-z0-9.:-]*(/[a-z0-9][a-z0-9._-]*)*')
CALLBACK_PATH = '/api/method/dsherp_bridge.sso.callback'
START_PATH = '/api/method/dsherp_bridge.sso.start'
DEFAULTS = {
    'DSHERP_ENV': 'dev',
    'DSHERP_PROJECT': 'dsherp-validation',
    'DSHERP_BASE_DOMAIN': 'localhost',
    'DSHERP_PLATFORM_SLUG': 'dsherp-platform',
    'DSHERP_IMAGE_TAG': '',
    'DSHERP_IMAGE_REGISTRY': '',
    'DSHERP_AGENT_UID': '',
    'DSHERP_AGENT_GID': '',
    'DSHERP_ORIGINS': '',
}


def env_file(name, root=ROOT):
    return Path(root) / 'infra' / 'env' / f'{name}.env'


def _text(values, key):
    value = values.get(key)
    return value.strip() if isinstance(value, str) else ''


def _match(pattern, value, message):
    if not value or not pattern.fullmatch(value):
        raise ValueError(message + ': ' + repr(value))
    return value


def _identity(values):
    raw_uid = _text(values, 'DSHERP_AGENT_UID') or str(os.getuid())
    raw_gid = _text(values, 'DSHERP_AGENT_GID') or str(os.getgid())
    if not re.fullmatch('[0-9]+', raw_uid) or not re.fullmatch('[0-9]+', raw_gid):
        raise ValueError('Agent container identity must be numeric')
    if int(raw_uid) == 0 or int(raw_gid) == 0:
        raise ValueError('Agent container must never run as root')
    return int(raw_uid), int(raw_gid)


def _origins(values):
    overrides = {}
    for entry in _text(values, 'DSHERP_ORIGINS').split(','):
        entry = entry.strip()
        if not entry:
            continue
        slug, separator, origin = entry.partition('=')
        if not separator or not origin.startswith(('http://', 'https://')) or origin.endswith('/'):
            raise ValueError('Invalid origin override: ' + repr(entry))
        overrides[_match(SLUG, slug.strip(), 'Invalid origin slug')] = origin
    return overrides


def settings(environ=None, root=ROOT):
    """Resolve one environment: process variables win over the environment file."""
    environ = dict(os.environ if environ is None else environ)
    name = _text(environ, 'DSHERP_ENV') or DEFAULTS['DSHERP_ENV']
    if name not in ENVIRONMENTS:
        raise ValueError('Unknown deployment environment: ' + repr(name))
    path = env_file(name, root)
    file_values = dotenv_values(path, interpolate=False) if path.exists() else {}
    values = {**DEFAULTS, **{key: value for key, value in file_values.items() if isinstance(value, str)},
              **{key: value for key, value in environ.items() if key in DEFAULTS}}
    project = _match(SLUG, _text(values, 'DSHERP_PROJECT'), 'Invalid compose project')
    base_domain = _match(DOMAIN, _text(values, 'DSHERP_BASE_DOMAIN'), 'Invalid base domain')
    platform_slug = _match(SLUG, _text(values, 'DSHERP_PLATFORM_SLUG'), 'Invalid platform slug')
    registry = _text(values, 'DSHERP_IMAGE_REGISTRY')
    if registry:
        _match(REGISTRY, registry, 'Invalid image registry')
    tag = _text(values, 'DSHERP_IMAGE_TAG')
    if name == 'prod':
        _match(TAG, tag, 'Production requires an explicit release tag')
        if tag in ('latest', 'main', 'master'):
            raise ValueError('Production release tag must identify one build: ' + repr(tag))
        prefix = registry + '/' if registry else ''
        frappe_image = f'{prefix}dsherp-frappe:{tag}'
        worker_image = f'{prefix}dsherp-worker:{tag}'
    else:
        frappe_image = worker_image = BASE_IMAGE
    uid, gid = _identity(values)
    resolved = {
        'env': name,
        'project': project,
        'base_domain': base_domain,
        'platform_slug': platform_slug,
        'platform_site': f'{platform_slug}.{base_domain}',
        'registry': registry,
        'image_tag': tag,
        'frappe_image': frappe_image,
        'worker_image': worker_image,
        'base_image': BASE_IMAGE,
        'agent_network': f'{project}_agent',
        'egress_network': f'{project}_egress',
        'agent_uid': uid,
        'agent_gid': gid,
        'agent_user': f'{uid}:{gid}',
        'scheme': 'https' if name == 'prod' else 'http',
        'origins': _origins(values),
    }
    return resolved


def site_name(resolved, slug):
    return _match(SLUG, slug, 'Invalid tenant slug') + '.' + resolved['base_domain']


def public_origin(resolved, slug):
    _match(SLUG, slug, 'Invalid tenant slug')
    override = resolved['origins'].get(slug)
    return override or f"{resolved['scheme']}://{site_name(resolved, slug)}"


def callback_url(resolved, slug):
    return public_origin(resolved, slug) + CALLBACK_PATH


def start_url(resolved, slug):
    return public_origin(resolved, slug) + START_PATH
