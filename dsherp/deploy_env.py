"""One environment file decides images, domains and networks for a whole deployment.

Nothing here reads the working copy: a run is identified by the release tag it was
started from, so a production host can be rebuilt from the repository tag alone.
"""
import hashlib
import json
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
    'DSHERP_AGENT_PROVIDER_BASE_URL': 'http://agent-egress:8890',
    'DSHERP_PROVIDER_HOST': 'api.deepseek.com',
    # Server-to-server addresses inside the compose network; never browser facing.
    'DSHERP_TENANT_INTERNAL_URL': 'http://backend:8000',
    'DSHERP_PLATFORM_INTERNAL_URL': 'http://platform-backend:8000',
    # Host directories compose and the CLI must agree on; empty means <repo>/.runtime.
    'DSHERP_RUNTIME_DIR': '',
    'DSHERP_SECRETS_DIR': '',
    # Off-site backup repositories (restic). Empty means not configured; production refuses
    # every destructive operator action until both are set, because a retired tenant's final
    # state has to leave the host.
    'DSHERP_BACKUP_REPOSITORY': '',
    'DSHERP_BACKUP_SECRETS_REPOSITORY': '',
    # Bootstrap values for a fresh Site's System Settings; Frappe refuses to save without them.
    'DSHERP_SITE_LANGUAGE': 'zh',
    'DSHERP_SITE_TIME_ZONE': 'Asia/Shanghai',
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


# Used when the calling process is root (CI, an installer): the container still is not.
UNPRIVILEGED = 1000


def _identity(values):
    raw_uid = _text(values, 'DSHERP_AGENT_UID')
    raw_gid = _text(values, 'DSHERP_AGENT_GID')
    explicit = bool(raw_uid or raw_gid)
    uid = raw_uid or str(os.getuid() or UNPRIVILEGED)
    gid = raw_gid or str(os.getgid() or UNPRIVILEGED)
    if not re.fullmatch('[0-9]+', uid) or not re.fullmatch('[0-9]+', gid):
        raise ValueError('Agent container identity must be numeric')
    if int(uid) == 0 or int(gid) == 0:
        # Only reachable when someone asked for it; the fallback above never returns root.
        raise ValueError('Agent container must never run as root')
    if explicit and (not raw_uid or not raw_gid):
        raise ValueError('Agent container identity needs both a uid and a gid')
    return int(uid), int(gid)


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


def image_name(registry, kind, tag):
    """The full name of one of our two images for a tag: what compose runs, what the release
    manifest is keyed by, and what release/rollback compare container by container."""
    return f'{registry + "/" if registry else ""}dsherp-{kind}:{tag}'


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
        frappe_image = image_name(registry, 'frappe', tag)
        worker_image = image_name(registry, 'worker', tag)
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
        # The run container has no route to the Internet; this is the proxy in front of the provider.
        'agent_provider_base_url': _text(values, 'DSHERP_AGENT_PROVIDER_BASE_URL'),
        'provider_host': _match(DOMAIN, _text(values, 'DSHERP_PROVIDER_HOST'), 'Invalid provider host'),
        'tenant_internal_url': _text(values, 'DSHERP_TENANT_INTERNAL_URL').rstrip('/'),
        'platform_internal_url': _text(values, 'DSHERP_PLATFORM_INTERNAL_URL').rstrip('/'),
    }
    for key in ('tenant_internal_url', 'platform_internal_url'):
        if not resolved[key].startswith(('http://', 'https://')):
            raise ValueError('Invalid internal service URL: ' + key)
    runtime_dir = Path(_text(values, 'DSHERP_RUNTIME_DIR') or Path(root) / '.runtime')
    secrets_dir = Path(_text(values, 'DSHERP_SECRETS_DIR') or runtime_dir / 'control')
    for key, path in (('DSHERP_RUNTIME_DIR', runtime_dir), ('DSHERP_SECRETS_DIR', secrets_dir)):
        if name == 'prod' and not path.is_absolute():
            raise ValueError(f'{key} must be absolute in production: ' + str(path))
    resolved['runtime_dir'] = runtime_dir
    resolved['secrets_dir'] = secrets_dir
    resolved['site_language'] = _match(re.compile('[a-z]{2}(-[a-z]{2})?'), _text(values, 'DSHERP_SITE_LANGUAGE'), 'Invalid site language')
    resolved['site_time_zone'] = _match(re.compile('[A-Za-z_]+(/[A-Za-z_+-]+)*'), _text(values, 'DSHERP_SITE_TIME_ZONE'), 'Invalid site time zone')
    if not resolved['agent_provider_base_url'].startswith(('http://', 'https://')):
        raise ValueError('Invalid agent provider base URL')
    data_repository = _text(values, 'DSHERP_BACKUP_REPOSITORY')
    secrets_repository = _text(values, 'DSHERP_BACKUP_SECRETS_REPOSITORY')
    if bool(data_repository) != bool(secrets_repository):
        raise ValueError('Configure both backup repositories (data and secrets) or neither')
    if data_repository and data_repository == secrets_repository:
        raise ValueError('The data and the secrets repository must not be the same')
    for key, value in (('DSHERP_BACKUP_REPOSITORY', data_repository), ('DSHERP_BACKUP_SECRETS_REPOSITORY', secrets_repository)):
        if not value:
            continue
        if not value.startswith('s3:http'):
            raise ValueError(key + ' must be an s3: restic repository URL')
        # Ruling #8: anything that leaves the host travels over TLS.
        if name == 'prod' and not value.startswith('s3:https://'):
            raise ValueError(key + ' must use TLS in production')
    resolved['backup_repository'] = data_repository
    resolved['backup_secrets_repository'] = secrets_repository
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


# Files that decide how a deployment is shaped. The digest travels inside run.json:
# a run container never sees a compose definition or a provisioning script.
DEPLOYMENT_FILES = (
    'infra/compose.validation.yml',
    'infra/compose.prod.yml',
    'infra/prepare_agent_runtime.sh',
    'infra/docker/frappe/Dockerfile',
    'infra/docker/worker/Dockerfile',
    'infra/nginx/security-headers.conf',
    'infra/nginx/site.conf.template',
    'infra/nginx/agent-egress.conf',
    'infra/nginx/agent-egress-entrypoint.sh',
    'infra/caddy/Caddyfile.template',
)
DEPLOYMENT_KEYS = ('env', 'project', 'base_domain', 'platform_site', 'image_tag',
                   'frappe_image', 'worker_image', 'agent_network', 'agent_user',
                   'agent_provider_base_url', 'provider_host',
                   'tenant_internal_url', 'platform_internal_url')


def deployment_digest(resolved, root=ROOT):
    """Identity of the deployment definition one run was started from."""
    root = Path(root)
    files = [[name, hashlib.sha256((root / name).read_bytes()).hexdigest()] for name in DEPLOYMENT_FILES]
    material = [files, [str(resolved[key]) for key in DEPLOYMENT_KEYS]]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
