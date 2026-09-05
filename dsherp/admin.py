"""dsherp-admin: the single entry point for operating one deployment.

Every command is a chain of idempotent steps. Each step asks what already exists
before it changes anything, so a half-finished tenant can be resumed instead of
being torn down, and a repeat run is a no-op rather than a duplicate.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from dsherp import deploy_env


ROOT = deploy_env.ROOT
# Control-plane material. Generated once, never printed, never regenerated silently.
# Development keeps the names its compose file already references.
CONTROL_SECRETS_BY_ENV = {
    'prod': ('db_root_password', 'platform_admin_password', 'tenant_admin_password'),
    'dev': ('db_root_password', 'admin_password', 'daily_admin_password'),
}
CONTROL_SECRETS = CONTROL_SECRETS_BY_ENV['prod']


def control_secrets(resolved):
    return CONTROL_SECRETS_BY_ENV[resolved['env']]
COMPOSE = {'dev': 'infra/compose.validation.yml', 'prod': 'infra/compose.prod.yml'}
SERVICES = {'dev': {'tenant': 'backend', 'platform': 'platform-backend'},
            'prod': {'tenant': 'backend', 'platform': 'platform-backend'}}
BENCH_PYTHON = '/home/frappe/frappe-bench/env/bin/python'
SITES = '/home/frappe/frappe-bench/sites'


class Fault(Exception):
    """An operator-facing failure: says what to inspect, never how to force through."""


def runtime_dir(resolved, root=ROOT):
    # deploy_env already merged the environment file and the process environment, so
    # this is the same value compose interpolates from the same file.
    return Path(os.environ.get('DSHERP_RUNTIME_DIR') or resolved.get('runtime_dir') or Path(root) / '.runtime')


def secrets_dir(resolved, root=ROOT):
    return Path(os.environ.get('DSHERP_SECRETS_DIR') or resolved.get('secrets_dir')
                or runtime_dir(resolved, root) / 'control')


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(text)
    return path


def ensure_secrets(resolved, root=ROOT, names=None):
    """Create the control-plane passwords once; report, never overwrite."""
    directory = secrets_dir(resolved, root)
    names = names or control_secrets(resolved)
    created, kept, insecure = [], [], []
    for name in names:
        path = directory / name
        if path.exists():
            kept.append(name)
            if path.stat().st_mode & 0o077:
                insecure.append(name)
            continue
        _write_private(path, secrets.token_urlsafe(32) + '\n')
        created.append(name)
    if insecure:
        raise Fault('这些密钥文件对其他用户可读，先收紧权限再继续：' + '、'.join(insecure))
    return {'created': created, 'kept': kept, 'directory': str(directory)}


def read_secret(resolved, name, root=ROOT):
    path = secrets_dir(resolved, root) / name
    if not path.exists():
        raise Fault(f'缺少控制面密钥 {name}；先运行 dsherp-admin secrets init')
    return path.read_text().strip()


def tenants_path(resolved, root=ROOT):
    return runtime_dir(resolved, root) / 'tenants.json'


def load_tenants(resolved, root=ROOT):
    path = tenants_path(resolved, root)
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or any(not isinstance(row, dict) or 'slug' not in row for row in rows):
        raise Fault('租户清单已损坏；不要重建，先检查 ' + str(path))
    return rows


def save_tenants(resolved, rows, root=ROOT):
    path = tenants_path(resolved, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + f'.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(sorted(rows, key=lambda row: row['slug']), handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def render_ingress(resolved, tenants, root=ROOT):
    """One Caddy site block per live Site; hosts are explicit so HTTP-01 can answer them."""
    template = (Path(root) / 'infra/caddy/Caddyfile.template').read_text()
    blocks = [f"{resolved['platform_site']} {{\n\timport site platform-frontend:8080\n}}"]
    for row in sorted(tenants, key=lambda item: item['slug']):
        host = deploy_env.site_name(resolved, row['slug'])
        blocks.append(f"{host} {{\n\timport site frontend:8080\n}}")
    text = template.replace('__SITE_BLOCKS__', '\n\n'.join(blocks) + '\n')
    target = runtime_dir(resolved, root) / 'caddy' / 'Caddyfile'
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(text)
    return target


class Bench:
    """Runs one command inside a Site container of this deployment."""

    def __init__(self, resolved, kind, root=ROOT, runner=subprocess.run):
        self.resolved = resolved
        self.service = SERVICES[resolved['env']][kind]
        self.root = Path(root)
        self.runner = runner

    def _compose(self, *arguments):
        file = self.root / COMPOSE[self.resolved['env']]
        command = ['docker', 'compose', '-p', self.resolved['project']]
        # The same file deploy_env resolved from, so compose interpolates the same values.
        env_file = deploy_env.env_file(self.resolved['env'], self.root)
        if env_file.exists():
            command += ['--env-file', str(env_file)]
        return command + ['-f', str(file), *arguments]

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        command = self._compose('exec', '-T', self.service, *arguments)
        # A container command must never inherit the operator's stdin: when this CLI is
        # driven from a script piped over ssh, bench would read the rest of that script.
        io = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
        result = self.runner(command, text=True, capture_output=True, timeout=timeout, **io)
        if result.returncode:
            tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-6:])
            for value in secrets:
                if value:
                    tail = tail.replace(value, '[redacted]')
            raise Fault(f'容器命令失败（{self.service}）：{" ".join(arguments[:3])}\n{tail}')
        return result.stdout

    def python(self, site, body, timeout=900):
        """Run a snippet with a connected Frappe context; only its stdout is returned."""
        script = ("import json,os,sys,frappe\n"
                  f"os.chdir({SITES!r})\n"
                  f"frappe.init(site={site!r},sites_path={SITES!r});frappe.connect()\n"
                  "frappe.set_user('Administrator')\n"
                  "try:\n"
                  + ''.join('    ' + line + '\n' for line in body.splitlines()) +
                  "finally:\n    frappe.destroy()\n")
        return self.run(BENCH_PYTHON, '-', stdin=script, timeout=timeout)

    def site_state(self, site):
        """'present' (has site_config.json), 'partial' (directory only) or 'absent'."""
        listed = self.run('sh', '-c',
                          f'if [ -f {SITES}/{site}/site_config.json ]; then echo present; '
                          f'elif [ -e {SITES}/{site} ]; then echo partial; else echo absent; fi', timeout=60)
        return listed.strip().splitlines()[-1]

    def site_exists(self, site):
        return self.site_state(site) == 'present'


BENCH_APPS = 'frappe\nerpnext\ndsherp_bridge\ndsherp_platform\n'


def bench_config(resolved):
    """Database and queue addresses as the compose services expose them."""
    if resolved['env'] == 'prod':
        return {'db_host': 'db', 'db_port': 3306,
                'redis_cache': 'redis://redis-cache:6379', 'redis_queue': 'redis://redis-queue:6379',
                'redis_socketio': 'redis://redis-queue:6379'}
    return {'db_host': 'db', 'db_port': 3306,
            'redis_cache': 'redis://redis:6379/0', 'redis_queue': 'redis://redis:6379/1',
            'redis_socketio': 'redis://redis:6379/1'}


def ensure_bench(bench, resolved):
    """Bootstrap a bench volume. The image ships placeholders (an apps.txt without our
    Apps, an empty common_site_config.json), so presence means nothing: apps are added
    when missing, the config is written only while it names no database. A config that
    already points at a database is never rewritten: that is how a live Site disappears."""
    config = json.dumps(bench_config(resolved), indent=1, sort_keys=True)
    apps = ' '.join(BENCH_APPS.split())
    script = (f"cd {SITES} && state=''\n"
              "touch apps.txt; added=0\n"
              # The image's apps.txt has no trailing newline; appending to it would fuse names.
              "if [ -s apps.txt ] && [ -n \"$(tail -c1 apps.txt)\" ]; then echo >> apps.txt; fi\n"
              f"for app in {apps}; do grep -qx \"$app\" apps.txt || {{ echo \"$app\" >> apps.txt; added=$((added+1)); }}; done\n"
              "if [ \"$added\" -eq 0 ]; then state=\"$state apps.txt:kept\"; else state=\"$state apps.txt:added=$added\"; fi\n"
              "if [ -f common_site_config.json ] && grep -q '\"db_host\"' common_site_config.json; then state=\"$state config:kept\"; "
              f"else cat > common_site_config.json <<'JSON'\n{config}\nJSON\nstate=\"$state config:created\"; fi\n"
              "if [ -e assets ]; then state=\"$state assets:kept\"; else ln -s ../assets assets; state=\"$state assets:created\"; fi\n"
              "echo \"$state\"")
    return bench.run('sh', '-c', script, timeout=60).split()


def ensure_role(bench, site, role):
    body = (f"role={role!r}\n"
            "if frappe.db.exists('Role',role):state='kept'\n"
            "else:frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':1}).insert();state='created'\n"
            "frappe.db.commit();print(json.dumps(state))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def ensure_site(bench, resolved, site, admin_password, db_root_password, apps=('erpnext',)):
    """Create the Site only when its directory is absent; never touch an existing one."""
    state = bench.site_state(site)
    if state == 'present':
        return 'kept'
    if state == 'partial':
        raise Fault(f'{SITES}/{site} 存在但没有 site_config.json：上一次建站半途失败。'
                    '先检查库里是否留下同名数据库，再删除该目录重跑；不会自动清理')
    install = [argument for app in apps for argument in ('--install-app', app)]
    bench.run('bench', 'new-site', site, '--db-host', 'db', '--db-root-username', 'root',
              '--db-root-password', db_root_password, '--admin-password', admin_password,
              '--mariadb-user-host-login-scope', '%', *install, timeout=1800,
              secrets=(db_root_password, admin_password))
    return 'created'


def ensure_app(bench, site, app):
    installed = bench.python(site, f"print(json.dumps({app!r} in frappe.get_installed_apps()))")
    if json.loads(installed.strip().splitlines()[-1]):
        return 'kept'
    bench.run('bench', '--site', site, 'install-app', app, timeout=1800)
    return 'created'


def ensure_site_config(bench, site, values):
    """Merge keys into site_config; report which ones actually changed."""
    body = (f"values=json.loads({json.dumps(json.dumps(values))})\n"
            "from frappe.installer import update_site_config\n"
            "changed=[key for key,value in values.items() if frappe.conf.get(key)!=value]\n"
            "for key in changed:update_site_config(key,values[key])\n"
            "frappe.db.commit();print(json.dumps(changed))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def ensure_system_settings(bench, site, values, defaults=None):
    """Frappe's own switches; password login is one of them, so use it rather than a hook.

    `values` are enforced. `defaults` are written only while empty: a fresh Site has no
    language or time zone and Frappe refuses to save System Settings without them, but
    a tenant that later chose its own must keep it."""
    body = (f"values=json.loads({json.dumps(json.dumps(values))})\n"
            f"defaults=json.loads({json.dumps(json.dumps(defaults or {}))})\n"
            "settings=frappe.get_single('System Settings')\n"
            "changed=[key for key,value in values.items() if settings.get(key)!=value]\n"
            "changed+=[key for key,value in defaults.items() if not settings.get(key)]\n"
            "for key in changed:settings.set(key,values.get(key,defaults.get(key)))\n"
            "if changed:settings.save()\n"
            "frappe.db.commit();print(json.dumps(changed))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def ensure_runtime_identity(bench, site, user, rotate=False):
    """The Site's own execution identity. Keys are issued when the user has none, or on an
    explicit rotation; a plain rerun never rotates them, so the secret really is shown once."""
    body = (f"user={user!r};rotate={rotate!r}\n"
            "if not frappe.db.exists('User',user):\n"
            "    frappe.get_doc({'doctype':'User','email':user,'first_name':'DSHERP Runtime',\n"
            "        'enabled':1,'user_type':'System User','send_welcome_email':0}).insert();state='created'\n"
            "else:state='kept'\n"
            "result={'user':user,'state':state}\n"
            "if rotate or not frappe.db.get_value('User',user,'api_key'):\n"
            "    from frappe.core.doctype.user.user import generate_keys\n"
            "    result.update(generate_keys(user));result['state']='rotated' if rotate else 'issued'\n"
            "frappe.db.commit();print(json.dumps(result))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def ensure_enterprise(bench, resolved, slug, site):
    """One platform record per tenant; an existing one is corrected, never duplicated."""
    values = {'enterprise_id': slug, 'title': slug, 'site': site,
              'base_url': resolved['tenant_internal_url'], 'status': 'Ready'}
    body = (f"values=json.loads({json.dumps(json.dumps(values))})\n"
            "if not frappe.db.exists('DS Enterprise',values['enterprise_id']):\n"
            "    frappe.get_doc({'doctype':'DS Enterprise',**values}).insert();state='created'\n"
            "else:\n"
            "    doc=frappe.get_doc('DS Enterprise',values['enterprise_id'])\n"
            "    drift=[key for key in ('site','base_url') if doc.get(key)!=values[key]]\n"
            "    state='kept'\n"
            "    if drift:\n"
            "        for key in drift:doc.set(key,values[key])\n"
            "        doc.save();state='updated:'+','.join(drift)\n"
            "frappe.db.commit();print(json.dumps(state))")
    return json.loads(bench.python(site_for_platform(resolved), body).strip().splitlines()[-1])


def site_for_platform(resolved):
    return resolved['platform_site']


def ensure_oauth_client(bench, resolved, slug):
    """The tenant's OAuth Client on the platform; its secret is returned, never regenerated."""
    app_name = f'DSHERP {slug} Desk'
    callback = deploy_env.callback_url(resolved, slug)
    body = (f"app_name={app_name!r};callback={callback!r}\n"
            "name=frappe.db.get_value('OAuth Client',{'app_name':app_name},'name')\n"
            "if not name:\n"
            "    import secrets as _s\n"
            "    client=frappe.get_doc({'doctype':'OAuth Client','app_name':app_name,\n"
            "        'client_secret':_s.token_urlsafe(32),'scopes':'openid',\n"
            "        'redirect_uris':callback,'default_redirect_uri':callback,\n"
            "        'grant_type':'Authorization Code','response_type':'Code','skip_authorization':0,\n"
            "        'allowed_roles':[{'role':'DSHERP Member'}]}).insert();state='created'\n"
            "else:\n"
            "    client=frappe.get_doc('OAuth Client',name);state='kept'\n"
            "    if client.default_redirect_uri!=callback:\n"
            "        client.redirect_uris=callback;client.default_redirect_uri=callback\n"
            "        client.save();state='updated'\n"
            "frappe.db.commit()\n"
            "print(json.dumps({'state':state,'client_id':client.client_id,'client_secret':client.get_password('client_secret')}))")
    return json.loads(bench.python(site_for_platform(resolved), body).strip().splitlines()[-1])


def ensure_platform_endpoints(bench, resolved, rows):
    """Where the platform reaches each business Site, and where a browser enters it."""
    business = {row['site']: resolved['tenant_internal_url'] for row in rows}
    desk = {row['site']: deploy_env.start_url(resolved, row['slug']) for row in rows}
    return ensure_site_config(bench, site_for_platform(resolved),
                              {'dsherp_business_sites': business, 'dsherp_desk_sites': desk})


def ensure_social_login_key(bench, resolved, slug, site, credentials):
    """The business Site's view of the platform; secrets come from the caller, once."""
    platform = resolved['platform_internal_url']
    values = {
        'doctype': 'Social Login Key', 'provider_name': 'DSHERP Platform',
        'social_login_provider': 'Custom', 'enable_social_login': 0, 'sign_ups': 'Deny',
        'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
        'base_url': platform,
        'authorize_url': f"{deploy_env.public_origin(resolved, resolved['platform_slug'])}"
                         '/api/method/frappe.integrations.oauth2.authorize',
        'access_token_url': platform + '/api/method/frappe.integrations.oauth2.get_token',
        'api_endpoint': platform + '/api/method/dsherp_platform.api.desk_identity',
        'redirect_url': deploy_env.callback_url(resolved, slug),
        'auth_url_data': json.dumps({'response_type': 'code', 'scope': 'openid'}),
        'user_id_property': 'sub',
    }
    body = (f"values=json.loads({json.dumps(json.dumps(values))})\n"
            "name=frappe.db.get_value('Social Login Key',{'provider_name':values['provider_name']},'name')\n"
            "if not name:\n"
            "    doc=frappe.get_doc(values).insert();state='created'\n"
            "else:\n"
            "    doc=frappe.get_doc('Social Login Key',name)\n"
            "    drift=[key for key,value in values.items() if key not in ('doctype','client_secret') and doc.get(key)!=value]\n"
            "    state='kept'\n"
            "    if drift:\n"
            "        for key in drift:doc.set(key,values[key])\n"
            "        doc.save();state='updated:'+','.join(sorted(drift))\n"
            "frappe.db.commit();print(json.dumps({'state':state,'provider':doc.name}))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def provision_platform(resolved, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """The control-plane Site. Separate bench and separate volume from every tenant."""
    site = resolved['platform_site']
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    platform = factory('platform')
    steps = []
    admin_password = read_secret(resolved, 'platform_admin_password' if resolved['env'] == 'prod'
                                else 'admin_password', root)
    db_root = read_secret(resolved, 'db_root_password', root)
    steps.append(('bench', ' '.join(ensure_bench(platform, resolved))))
    steps.append(('site', ensure_site(platform, resolved, site, admin_password, db_root, apps=())))
    steps.append(('app', ensure_app(platform, site, 'dsherp_platform')))
    # OAuth Clients for tenants are restricted to this role; the platform must own it.
    steps.append(('member-role', ensure_role(platform, site, 'DSHERP Member')))
    changed = ensure_site_config(platform, site, {'host_name': f"{resolved['scheme']}://{site}"})
    steps.append(('site-config', 'changed:' + ','.join(changed) if changed else 'kept'))
    return {'site': site, 'steps': steps}


def ensure_healthy(bench, site):
    """The last step answers the only question that matters: does this Site work."""
    body = ("import dsherp_bridge\n"
            "apps=sorted(frappe.get_installed_apps())\n"
            "assert 'dsherp_bridge' in apps,'bridge App 未安装'\n"
            "assert frappe.conf.get('dsherp_runtime_user'),'运行服务身份未配置'\n"
            "print(json.dumps({'site':frappe.local.site,'apps':apps}))")
    return json.loads(bench.python(site, body, timeout=120).strip().splitlines()[-1])


def provision_tenant(resolved, slug, *, root=ROOT, runner=subprocess.run, bench_factory=None,
                     rotate_runtime_key=False):
    """Bring one tenant from nothing to reachable; every step is resumable."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    steps = []
    admin = read_secret(resolved, 'tenant_admin_password' if resolved['env'] == 'prod' else 'admin_password', root)
    db_root = read_secret(resolved, 'db_root_password', root)
    steps.append(('bench', ' '.join(ensure_bench(tenant, resolved))))
    steps.append(('site', ensure_site(tenant, resolved, site, admin, db_root)))
    steps.append(('app', ensure_app(tenant, site, 'dsherp_bridge')))
    runtime_user = f'runtime@{site}'
    identity = ensure_runtime_identity(tenant, site, runtime_user, rotate=rotate_runtime_key)
    steps.append(('runtime-identity', identity['state']))
    changed = ensure_site_config(tenant, site, {
        'dsherp_runtime_user': runtime_user,
        'host_name': deploy_env.public_origin(resolved, slug),
    })
    steps.append(('site-config', 'changed:' + ','.join(changed) if changed else 'kept'))
    bootstrap = {'language': resolved['site_language'], 'time_zone': resolved['site_time_zone']}
    if resolved['env'] == 'prod':
        # Production reaches a business Site through the platform only.
        hardened = ensure_system_settings(tenant, site, {'disable_user_pass_login': 1}, defaults=bootstrap)
        steps.append(('password-login', 'disabled' if 'disable_user_pass_login' in hardened else 'kept'))
    else:
        hardened = ensure_system_settings(tenant, site, {}, defaults=bootstrap)
    steps.append(('system-settings', 'bootstrapped:' + ','.join(sorted(set(hardened) - {'disable_user_pass_login'}))
                  if set(hardened) - {'disable_user_pass_login'} else 'kept'))
    platform = factory('platform')
    steps.append(('enterprise', ensure_enterprise(platform, resolved, slug, site)))
    credentials = ensure_oauth_client(platform, resolved, slug)
    steps.append(('oauth-client', credentials['state']))
    steps.append(('social-login-key',
                  ensure_social_login_key(tenant, resolved, slug, site, credentials)['state']))
    changed = ensure_site_config(tenant, site, {'dsherp_platform_oauth': {
        'provider': 'dsherp_platform', 'enterprise': slug, 'platform_site': resolved['platform_site']}})
    steps.append(('platform-oauth', 'changed' if changed else 'kept'))
    rows = [row for row in load_tenants(resolved, root) if row['slug'] != slug]
    rows.append({'slug': slug, 'site': site, 'origin': deploy_env.public_origin(resolved, slug)})
    save_tenants(resolved, rows, root)
    steps.append(('tenant-list', 'saved'))
    steps.append(('platform-endpoints',
                  'changed' if ensure_platform_endpoints(platform, resolved, rows) else 'kept'))
    steps.append(('ingress', str(render_ingress(resolved, rows, root))))
    steps.append(('healthcheck', ensure_healthy(tenant, site)['site']))
    result = {'site': site, 'steps': steps}
    if 'api_secret' in identity:
        # Only present when keys were issued or rotated; the caller stores them now or never.
        result['runtime_identity'] = identity
    return result


def retire_tenant(resolved, slug, *, root=ROOT, runner=subprocess.run, bench_factory=None, archive=True):
    """Archive first, drop second; the archive path is reported before anything is removed."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    if not tenant.site_exists(site):
        raise Fault(f'站点 {site} 不存在；不要用下线命令清理残留')
    steps = []
    if archive:
        tenant.run('bench', '--site', site, 'backup', '--with-files', timeout=3600)
        steps.append(('archive', 'created'))
    tenant.run('bench', 'drop-site', site, '--db-root-username', 'root',
               '--db-root-password', read_secret(resolved, 'db_root_password', root),
               '--no-backup', timeout=1800)
    steps.append(('site', 'dropped'))
    rows = [row for row in load_tenants(resolved, root) if row['slug'] != slug]
    save_tenants(resolved, rows, root)
    steps.append(('ingress', str(render_ingress(resolved, rows, root))))
    return {'site': site, 'steps': steps}


def doctor(resolved, root=ROOT, runner=subprocess.run):
    """Compare what compose will interpolate with what the code resolved."""
    findings = []
    file = Path(root) / COMPOSE[resolved['env']]
    if not file.exists():
        findings.append(f'缺少 compose 文件：{file}')
    for name in deploy_env.DEPLOYMENT_FILES:
        if not (Path(root) / name).exists():
            findings.append(f'部署指纹清单缺少文件：{name}')
    directory = secrets_dir(resolved, root)
    for name in control_secrets(resolved):
        path = directory / name
        if not path.exists():
            findings.append(f'缺少控制面密钥：{name}')
        elif path.stat().st_mode & 0o077:
            findings.append(f'控制面密钥权限过宽：{name}')
    if resolved['env'] == 'prod':
        for key in ('DSHERP_PROJECT', 'DSHERP_IMAGE_TAG', 'DSHERP_IMAGE_REGISTRY', 'DSHERP_BASE_DOMAIN'):
            if not os.environ.get(key) and not (deploy_env.env_file('prod', root).exists()):
                findings.append(f'compose 插值需要 {key}，但既不在进程环境也不在 infra/env/prod.env')
        if not resolved['registry']:
            findings.append('生产必须给出镜像仓库地址，否则 compose 的 image 无法解析')
        env_file = deploy_env.env_file('prod', root)
        text = env_file.read_text() if env_file.exists() else ''
        for key in ('DSHERP_RUNTIME_DIR', 'DSHERP_SECRETS_DIR'):
            if not re.search(rf'^{key}=\S', text, re.MULTILINE):
                findings.append(f'{key} 必须写在 infra/env/prod.env 里：compose 与 CLI 才会读到同一个目录，'
                                '否则 compose 会回落到 ../.runtime 下的开发密钥')
        if resolved['agent_uid'] == 0:
            findings.append('Agent 容器身份解析为 root')
    return findings


# --- release and rollback -------------------------------------------------------
# An upgrade is only reversible if the state before it was captured and backed up.
# Both are hard preconditions here, and the field-level comparison is the evidence.
SNAPSHOT = """
digests={}
for doctype in sorted(frappe.get_all('DocType',filters={'issingle':0,'istable':0},pluck='name')):
    try:rows=frappe.get_all(doctype,fields=['*'],order_by='name asc',limit_page_length=0,ignore_permissions=True)
    except Exception:continue
    if not rows:continue
    import hashlib
    material=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str,separators=(',',':'))
    digests[doctype]={'count':len(rows),'digest':hashlib.sha256(material.encode()).hexdigest()}
print(json.dumps(digests))
"""


def snapshot(bench, site):
    return json.loads(bench.python(site, SNAPSHOT.strip(), timeout=1800).strip().splitlines()[-1])


def compare_snapshots(before, after):
    """Field-level differences, named by DocType so a report points at the cause."""
    differences = []
    for doctype in sorted(set(before) | set(after)):
        first, second = before.get(doctype), after.get(doctype)
        if first == second:
            continue
        differences.append({'doctype': doctype,
                            'before': first or {'count': 0, 'digest': None},
                            'after': second or {'count': 0, 'digest': None}})
    return differences


def _release_report(resolved, name, payload, root=ROOT):
    target = runtime_dir(resolved, root) / 'releases' / f'{name}.json'
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return target


def release(resolved, tag, *, root=ROOT, runner=subprocess.run, bench_factory=None, sites=None):
    """Back up, migrate every Site to the new tag, then prove the data is unchanged."""
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('发布必须给出明确的镜像 tag')
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    targets = sites or [row['site'] for row in load_tenants(resolved, root)]
    if not targets:
        raise Fault('没有可发布的站点；先运行 provision-tenant')
    before, steps = {}, []
    for site in targets:
        tenant.run('bench', '--site', site, 'backup', '--with-files', timeout=3600)
        before[site] = snapshot(tenant, site)
        steps.append((site, 'backed-up'))
    for site in targets:
        tenant.run('bench', '--site', site, 'migrate', timeout=3600)
        steps.append((site, 'migrated'))
    report = {'tag': tag, 'sites': {}, 'steps': steps}
    for site in targets:
        differences = compare_snapshots(before[site], snapshot(tenant, site))
        report['sites'][site] = {'differences': differences, 'doctypes': len(before[site])}
    report['clean'] = not any(row['differences'] for row in report['sites'].values())
    report['path'] = str(_release_report(resolved, f'release-{tag}', report, root))
    return report


def rollback(resolved, tag, *, root=ROOT, runner=subprocess.run, bench_factory=None, sites=None, backups=None):
    """Restore the pre-upgrade backup on the previous tag; the caller names the files."""
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('回滚必须给出要回到的镜像 tag')
    if not backups:
        raise Fault('回滚必须给出每站升级前的备份文件；不要让命令自己猜')
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    targets = sites or sorted(backups)
    missing = [site for site in targets if site not in backups]
    if missing:
        raise Fault('这些站点没有指定备份文件：' + '、'.join(missing))
    steps, report = [], {'tag': tag, 'sites': {}}
    for site in targets:
        tenant.run('bench', '--site', site, 'restore', backups[site],
                   '--db-root-username', 'root',
                   '--db-root-password', read_secret(resolved, 'db_root_password', root), timeout=3600)
        steps.append((site, 'restored'))
        report['sites'][site] = snapshot(tenant, site)
    report['steps'] = steps
    report['path'] = str(_release_report(resolved, f'rollback-{tag}', report, root))
    return report


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dsherp-admin', description='dsherp 部署与租户运维入口')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='检查环境、密钥与部署文件是否自洽')
    secrets_parser = sub.add_parser('secrets', help='控制面密钥')
    secrets_parser.add_argument('action', choices=['init'])
    provision = sub.add_parser('provision-tenant', help='幂等开通一个租户站')
    provision.add_argument('slug')
    provision.add_argument('--rotate-runtime-key', action='store_true',
                           help='重新签发该站运行服务身份的 API 密钥（会作废旧密钥）')
    retire = sub.add_parser('retire-tenant', help='归档后下线一个租户站')
    retire.add_argument('slug')
    retire.add_argument('--no-archive', action='store_true')
    sub.add_parser('provision-platform', help='幂等开通平台站')
    sub.add_parser('list-tenants', help='列出当前租户')
    sub.add_parser('render-ingress', help='按当前租户清单重新渲染入口配置')
    release_parser = sub.add_parser('release', help='升级到新 tag：先备份，逐站 migrate，再逐字段比对')
    release_parser.add_argument('tag')
    rollback_parser = sub.add_parser('rollback', help='回到旧 tag 并从升级前备份恢复')
    rollback_parser.add_argument('tag')
    rollback_parser.add_argument('--backup', action='append', default=[], metavar='SITE=FILE',
                                 help='每站升级前的备份文件，可重复')
    arguments = parser.parse_args(argv)
    resolved = deploy_env.settings()
    try:
        if arguments.command == 'doctor':
            findings = doctor(resolved)
            _print({'env': resolved['env'], 'findings': findings})
            return 1 if findings else 0
        if arguments.command == 'secrets':
            _print(ensure_secrets(resolved))
            return 0
        if arguments.command == 'provision-platform':
            _print(provision_platform(resolved))
            return 0
        if arguments.command == 'provision-tenant':
            _print(provision_tenant(resolved, arguments.slug, rotate_runtime_key=arguments.rotate_runtime_key))
            return 0
        if arguments.command == 'retire-tenant':
            _print(retire_tenant(resolved, arguments.slug, archive=not arguments.no_archive))
            return 0
        if arguments.command == 'list-tenants':
            _print(load_tenants(resolved))
            return 0
        if arguments.command == 'render-ingress':
            _print({'rendered': str(render_ingress(resolved, load_tenants(resolved)))})
            return 0
        if arguments.command == 'release':
            report = release(resolved, arguments.tag)
            _print(report)
            return 0 if report['clean'] else 1
        if arguments.command == 'rollback':
            pairs = {}
            for entry in arguments.backup:
                site, separator, file = entry.partition('=')
                if not separator:
                    raise Fault('备份参数格式为 SITE=FILE')
                pairs[site] = file
            _print(rollback(resolved, arguments.tag, backups=pairs))
            return 0
    except Fault as fault:
        print(str(fault), file=sys.stderr)
        return 2
    raise AssertionError('unreachable')


if __name__ == '__main__':
    raise SystemExit(main())
