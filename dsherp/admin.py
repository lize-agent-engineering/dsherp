"""dsherp-admin: the single entry point for operating one deployment.

Every command is a chain of idempotent steps. Each step asks what already exists
before it changes anything, so a half-finished tenant can be resumed instead of
being torn down, and a repeat run is a no-op rather than a duplicate.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from dsherp import deploy_env


ROOT = deploy_env.ROOT
# Control-plane material. Generated once, never printed, never regenerated silently.
# Development keeps the names its compose file already references.
CONTROL_SECRETS_BY_ENV = {
    'prod': ('db_root_password', 'platform_admin_password', 'tenant_admin_password',
             'backup_repository_password', 'backup_secrets_repository_password'),
    'dev': ('db_root_password', 'admin_password', 'daily_admin_password',
            'backup_repository_password', 'backup_secrets_repository_password',
            'test_admin_password'),
}
# Identities the operator obtains from the object storage provider; `secrets init` cannot
# invent them, so doctor only checks that they are present and private once repositories are
# configured. The two are separate on purpose: the identity that reads the dumps must not be
# able to read the site_config copies.
PROVIDED_SECRETS = ('backup_storage_credentials', 'backup_secrets_storage_credentials')
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
    """Runs one command inside a Site container of this deployment.

    `project`, `compose_file` and `environment` let a drill drive an isolated stack with the
    same class: a different compose project, a different file, and the image tag and secrets
    directory that stack must use."""

    def __init__(self, resolved, kind, root=ROOT, runner=subprocess.run, *, project=None, compose_file=None,
                 environment=None, service=None):
        self.resolved = resolved
        self.service = service or SERVICES[resolved['env']][kind]
        self.root = Path(root)
        self.runner = runner
        self.project = project or resolved['project']
        self.compose_file = compose_file
        self.environment = dict(environment or {})

    def _compose(self, *arguments):
        file = Path(self.compose_file) if self.compose_file else self.root / COMPOSE[self.resolved['env']]
        command = ['docker', 'compose', '-p', self.project]
        # The same file deploy_env resolved from, so compose interpolates the same values.
        env_file = deploy_env.env_file(self.resolved['env'], self.root)
        if env_file.exists() and not self.compose_file:
            command += ['--env-file', str(env_file)]
        return command + ['-f', str(file), *arguments]

    def _environment(self):
        return {**os.environ, **self.environment} if self.environment else None

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        command = self._compose('exec', '-T', self.service, *arguments)
        # A container command must never inherit the operator's stdin: when this CLI is
        # driven from a script piped over ssh, bench would read the rest of that script.
        io = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
        if self.environment:
            io['env'] = self._environment()
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

    def script(self, body, timeout=900, secrets=()):
        """Run a snippet in the bench's interpreter without connecting to a Site.

        Credentials belong here, not on a command line: bench logs its own argv, and this
        stdin never reaches a log. Used for `_new_site`, restore and site_config writes."""
        return self.run(BENCH_PYTHON, '-', stdin='import json,os,sys\n' + body, timeout=timeout, secrets=secrets)

    def site_state(self, site):
        """'present' (has site_config.json), 'partial' (directory only) or 'absent'."""
        listed = self.run('sh', '-c',
                          f'if [ -f {SITES}/{site}/site_config.json ]; then echo present; '
                          f'elif [ -e {SITES}/{site} ]; then echo partial; else echo absent; fi', timeout=60)
        return listed.strip().splitlines()[-1]

    def site_exists(self, site):
        return self.site_state(site) == 'present'


BENCH_APPS = 'frappe\nerpnext\ndsherp_bridge\ndsherp_platform\n'


# RQ queue names come from the bench path, which is the same in every container built
# from the image; two benches on one redis DB would take each other's jobs. Each bench
# therefore gets its own redis database index.
QUEUE_DB = {'tenant': 0, 'platform': 1}


def bench_config(resolved, kind='tenant'):
    """Database and queue addresses as the compose services expose them."""
    queue = QUEUE_DB[kind]
    if resolved['env'] == 'prod':
        return {'db_host': 'db', 'db_port': 3306,
                'redis_cache': 'redis://redis-cache:6379', 'redis_queue': f'redis://redis-queue:6379/{queue}',
                'redis_socketio': f'redis://redis-queue:6379/{queue}'}
    return {'db_host': 'db', 'db_port': 3306,
            'redis_cache': 'redis://redis:6379/0', 'redis_queue': 'redis://redis:6379/1',
            'redis_socketio': 'redis://redis:6379/1'}


def ensure_bench(bench, resolved, kind='tenant'):
    """Bootstrap a bench volume. The image ships placeholders (an apps.txt without our
    Apps, an empty common_site_config.json), so presence means nothing: apps are added
    when missing, the config is written only while it names no database. A config that
    already points at a database is never rewritten: that is how a live Site disappears."""
    config = json.dumps(bench_config(resolved, kind), indent=1, sort_keys=True)
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


def ensure_scheduler_enabled(bench, site):
    body = ("from frappe.utils.scheduler import is_scheduler_disabled,enable_scheduler\n"
            "if is_scheduler_disabled():enable_scheduler();state='enabled'\n"
            "else:state='kept'\n"
            "frappe.db.commit();print(json.dumps(state))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def agent_sources(resolved, runner=subprocess.run):
    """Subnets a run capability may be used from: the agent network for run containers and
    the worker network, whose gateway is what the host worker's loopback calls arrive as."""
    subnets = []
    for network in (resolved['agent_network'], f"{resolved['project']}_worker"):
        result = runner(['docker', 'network', 'inspect', network, '--format', '{{(index .IPAM.Config 0).Subnet}}'],
                        text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        if result.returncode or not result.stdout.strip():
            raise Fault(f'读不到网络 {network} 的网段；先 compose up 再开站')
        subnets.append(result.stdout.strip())
    return subnets


def ensure_role(bench, site, role):
    body = (f"role={role!r}\n"
            "if frappe.db.exists('Role',role):state='kept'\n"
            "else:frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':1}).insert();state='created'\n"
            "frappe.db.commit();print(json.dumps(state))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


# `bench new-site` is `frappe.init(site, new_site=True)` followed by `_new_site(...)`
# (frappe/commands/site.py). Calling those two directly is the same site, built the same way,
# with one difference that matters: `--db-root-password` and `--admin-password` would go on
# bench's argv, and bench appends its own argv to `sites/bench.log` on every invocation. That
# log stays in the tenant volume and goes out in every backup. `Bench.script` puts them on
# stdin instead, which is never logged - the same route `restore_drill._new_site` already uses.
NEW_SITE = (
    "payload=PAYLOAD\n"
    f"os.chdir({SITES!r})\n"
    "import frappe\n"
    "from frappe.installer import _new_site\n"
    # Without new_site=True the config reader refuses a Site directory that does not exist yet.
    "frappe.init(payload['site'],new_site=True)\n"
    # db_name None is what `bench new-site` passes without --db-name: Frappe derives it.
    "_new_site(None,payload['site'],db_root_username='root',"
    "db_root_password=payload['db_root_password'],admin_password=payload['admin_password'],"
    "verbose=False,install_apps=payload['apps'],db_host='db',mariadb_user_host_login_scope='%')\n"
    "print('DSHERP_SITE_CREATED '+json.dumps({'site':payload['site']}))\n")
# `bench drop-site` is `_drop_site(...)` from the sites directory (frappe/commands/site.py);
# `--db-root-password` had the same argv problem, and a retired tenant's bench.log went out in
# every later backup of the volume.
DROP_SITE = (
    "payload=PAYLOAD\n"
    f"os.chdir({SITES!r})\n"
    "import frappe\n"
    "from frappe.commands.site import _drop_site\n"
    "_drop_site(payload['site'],db_root_username='root',db_root_password=payload['db_root_password'],"
    "archived_sites_path=payload['archived_sites_path'],force=False,no_backup=True)\n"
    "print('DSHERP_SITE_DROPPED '+json.dumps({'site':payload['site']}))\n")
# `bench --site X restore` is `frappe.init(site)` then `_restore(...)` under the site_restore
# lock; force=True is what `--force` set: no interactive downgrade prompt in a container.
RESTORE_SITE = (
    "payload=PAYLOAD\n"
    f"os.chdir({SITES!r})\n"
    "import frappe\n"
    "from frappe.commands.site import _restore\n"
    "from frappe.utils.synchronization import filelock\n"
    "frappe.init(payload['site'])\n"
    "with filelock('site_restore',timeout=1):\n"
    "    _restore(site=payload['site'],sql_file_path=payload['database'],db_root_username='root',"
    "db_root_password=payload['db_root_password'],force=True,"
    "with_public_files=payload.get('files'),with_private_files=payload.get('private_files'))\n"
    "print('DSHERP_SITE_RESTORED '+json.dumps({'site':payload['site']}))\n")


def _marked_script(bench, template, payload, marker, hint, *, timeout, secrets):
    """Run a credential-carrying snippet and require its marker on the last line: the bench
    command it replaces failed loudly, a snippet that printed nothing would read as success."""
    output = bench.script(template.replace('PAYLOAD', json.dumps(payload)), timeout=timeout, secrets=secrets)
    if not _last_line(output).startswith(marker + ' '):
        raise Fault(f'脚本没有输出 {marker}：{hint}')
    return output


def ensure_site(bench, resolved, site, admin_password, db_root_password, apps=('erpnext',)):
    """Create the Site only when its directory is absent; never touch an existing one."""
    state = bench.site_state(site)
    if state == 'present':
        return 'kept'
    if state == 'partial':
        raise Fault(f'{SITES}/{site} 存在但没有 site_config.json：上一次建站半途失败。'
                    '先检查库里是否留下同名数据库，再删除该目录重跑；不会自动清理')
    payload = {'site': site, 'apps': list(apps),
               'db_root_password': db_root_password, 'admin_password': admin_password}
    _marked_script(bench, NEW_SITE, payload, 'DSHERP_SITE_CREATED',
                   f'{site} 的状态未知，先用 site_state 查这个目录再决定是否重跑；不会自动清理',
                   timeout=1800, secrets=(db_root_password, admin_password))
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


def ensure_enterprise(bench, resolved, slug, site, status='Ready'):
    """One platform record per tenant; an existing one is corrected, never duplicated.

    `status='Provisioning'` is the recovery form: the record exists so the platform knows the
    tenant, but members cannot enter until a later call says Ready. Only a Ready enterprise
    is taken into Provisioning, and only a Provisioning one is taken to Ready: an operator's
    Disabled or Failed is a decision of its own, and a recovery must not undo it."""
    values = {'enterprise_id': slug, 'title': slug, 'site': site,
              'base_url': resolved['tenant_internal_url'], 'status': status}
    body = (f"values=json.loads({json.dumps(json.dumps(values))})\n"
            "if not frappe.db.exists('DS Enterprise',values['enterprise_id']):\n"
            "    frappe.get_doc({'doctype':'DS Enterprise',**values}).insert();state='created'\n"
            "else:\n"
            "    doc=frappe.get_doc('DS Enterprise',values['enterprise_id'])\n"
            "    drift=[key for key in ('site','base_url') if doc.get(key)!=values[key]]\n"
            "    if values['status']=='Provisioning' and doc.status=='Ready':drift.append('status')\n"
            "    if values['status']=='Ready' and doc.status=='Provisioning':drift.append('status')\n"
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


def provision_platform(resolved, *, root=ROOT, runner=subprocess.run, bench_factory=None, closed=False):
    """The control-plane Site. Separate bench and separate volume from every tenant.

    `closed=True` (recovery, R4): maintenance from the moment the Site exists."""
    site = resolved['platform_site']
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    platform = factory('platform')
    steps = []
    admin_password = read_secret(resolved, 'platform_admin_password' if resolved['env'] == 'prod'
                                else 'admin_password', root)
    db_root = read_secret(resolved, 'db_root_password', root)
    steps.append(('bench', ' '.join(ensure_bench(platform, resolved, 'platform'))))
    steps.append(('site', ensure_site(platform, resolved, site, admin_password, db_root, apps=())))
    if closed:
        _set_flag(platform, site, 'maintenance_mode', 1)
        steps.append(('maintenance', 'kept closed'))
    steps.append(('app', ensure_app(platform, site, 'dsherp_platform')))
    steps.append(('scheduler', ensure_scheduler_enabled(platform, site)))
    # OAuth Clients for tenants are restricted to this role; the platform must own it.
    steps.append(('member-role', ensure_role(platform, site, 'DSHERP Member')))
    changed = ensure_site_config(platform, site, {'host_name': f"{resolved['scheme']}://{site}"})
    steps.append(('site-config', 'changed:' + ','.join(changed) if changed else 'kept'))
    return {'site': site, 'steps': steps}


def ensure_healthy(bench, site):
    """The last step answers the only question that matters: does this Site work."""
    body = ("import dsherp_bridge\n"
            "from frappe.utils.scheduler import is_scheduler_disabled\n"
            "apps=sorted(frappe.get_installed_apps())\n"
            "assert 'dsherp_bridge' in apps,'bridge App 未安装'\n"
            "assert frappe.conf.get('dsherp_runtime_user'),'运行服务身份未配置'\n"
            "assert not is_scheduler_disabled(),'scheduler 未启用'\n"
            "print(json.dumps({'site':frappe.local.site,'apps':apps,'agent_sources':frappe.conf.get('dsherp_agent_sources')}))")
    return json.loads(bench.python(site, body, timeout=120).strip().splitlines()[-1])


def provision_tenant(resolved, slug, *, root=ROOT, runner=subprocess.run, bench_factory=None,
                     rotate_runtime_key=False, closed=False):
    """Bring one tenant from nothing to reachable; every step is resumable.

    `closed=True` is the recovery form (R4): the Site goes into maintenance the moment it
    exists, the enterprise is recorded as Provisioning rather than Ready, and nothing is
    published - no tenant-list entry, no platform endpoint, no ingress. A later ordinary call
    opens it once the restored data has been judged."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    steps = []
    admin = read_secret(resolved, 'tenant_admin_password' if resolved['env'] == 'prod' else 'admin_password', root)
    db_root = read_secret(resolved, 'db_root_password', root)
    steps.append(('bench', ' '.join(ensure_bench(tenant, resolved, 'tenant'))))
    steps.append(('site', ensure_site(tenant, resolved, site, admin, db_root)))
    if closed:
        _set_flag(tenant, site, 'maintenance_mode', 1)
        steps.append(('maintenance', 'kept closed'))
    steps.append(('app', ensure_app(tenant, site, 'dsherp_bridge')))
    steps.append(('scheduler', ensure_scheduler_enabled(tenant, site)))
    runtime_user = f'runtime@{site}'
    identity = ensure_runtime_identity(tenant, site, runtime_user, rotate=rotate_runtime_key)
    steps.append(('runtime-identity', identity['state']))
    changed = ensure_site_config(tenant, site, {
        'dsherp_runtime_user': runtime_user,
        'host_name': deploy_env.public_origin(resolved, slug),
        'dsherp_agent_sources': agent_sources(resolved, runner) if resolved['env'] == 'prod' else None,
        # The deliberate switch for "every session comes through the platform"; the System
        # Settings password switch below is the user-facing half of the same decision.
        'dsherp_sso_required': 1,
    } if resolved['env'] == 'prod' else {
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
    steps.append(('enterprise', ensure_enterprise(platform, resolved, slug, site,
                                                  status='Provisioning' if closed else 'Ready')))
    credentials = ensure_oauth_client(platform, resolved, slug)
    steps.append(('oauth-client', credentials['state']))
    steps.append(('social-login-key',
                  ensure_social_login_key(tenant, resolved, slug, site, credentials)['state']))
    changed = ensure_site_config(tenant, site, {'dsherp_platform_oauth': {
        'provider': 'dsherp_platform', 'enterprise': slug, 'platform_site': resolved['platform_site']}})
    steps.append(('platform-oauth', 'changed' if changed else 'kept'))
    if closed:
        # Nothing that lets a member in: the list, the endpoints and the ingress come with
        # the ordinary call that follows a clean judgement.
        steps.append(('published', 'not yet'))
    else:
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


# The two tenant quotas, in the same words as `dsherp_bridge.run_budget.QUOTA_DEFAULTS`.
# 0 means unlimited and 0 is what a Site has until somebody writes something else, so until
# this command is run the only cost ceiling in force on a tenant is the per-run budget.
QUOTA_KEYS = ('user_daily_model_calls', 'site_monthly_tokens')
QUOTA_READ = "print(json.dumps(frappe.conf.get('dsherp_quota') or {}))"


def _validate_quota(values, source):
    for key, value in values.items():
        # The rule the Site itself enforces on read: `>= 0`, because 0 is how a quota is
        # turned back off. A Site whose dsherp_quota is malformed refuses every message.
        if key not in QUOTA_KEYS or type(value) is not int or value < 0:
            raise Fault(f'{source} 里的额度 {key}={value!r} 无效：只能是 '
                        f'{"、".join(QUOTA_KEYS)}，取值为 >= 0 的整数（0 表示不限）')


def tenant_quota(resolved, slug, values=None, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Read or write one tenant's usage quotas; writing merges into `dsherp_quota`.

    Judged on the Site before a run exists (`dsherp_bridge.context_api._check_quota`), so this
    is the only place they are set. Both default to 0 = unlimited: without a real usage
    distribution a refusal for everyone would be a gate against ordinary work rather than
    against abuse, so the values are an operator decision, not a default this code invents."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    if not tenant.site_exists(site):
        raise Fault(f'站点 {site} 不存在；先 provision-tenant {slug}')
    stored = json.loads(_last_line(tenant.python(site, QUOTA_READ, timeout=120)) or '{}')
    if not isinstance(stored, dict):
        raise Fault(f'{site} 的 dsherp_quota 不是对象；该站会拒绝每一次提问，先修 site_config')
    _validate_quota(stored, f'{site} 的 site_config')
    effective = {key: 0 for key in QUOTA_KEYS} | stored
    if not values:
        return {'site': site, 'quota': effective, 'state': 'read'}
    _validate_quota(values, '命令行')
    merged = effective | values
    changed = ensure_site_config(tenant, site, {'dsherp_quota': merged})
    return {'site': site, 'quota': merged, 'state': 'changed' if changed else 'kept'}


# Where `bench drop-site` moves a Site directory (backup included). Named explicitly so
# it is the same path the backend persists as a volume (compose) and the image prepared
# for the bench user (Dockerfile): an archive left in the container's writable layer
# vanished on the next recreate, and a root-owned empty volume fails the move after the
# database is already gone.
ARCHIVE = '/home/frappe/frappe-bench/archived/sites'


def _listing(bench, path):
    text = bench.run('sh', '-c', f'ls -1 {path} 2>/dev/null || true', timeout=60)
    return [line.strip() for line in text.splitlines() if line.strip()]


def retire_tenant(resolved, slug, **keywords):
    return _with_operations_lock('retire-tenant', _retire_tenant, resolved, slug, **keywords)


def _retire_tenant(resolved, slug, *, root=ROOT, runner=subprocess.run, bench_factory=None, archive=True):
    """Prove the archive is writable, back up, drop (which moves the whole Site directory
    under ARCHIVE), read the archive back, and only then take the tenant off the list."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    if not tenant.site_exists(site):
        raise Fault(f'站点 {site} 不存在；不要用下线命令清理残留')
    writable = tenant.run('sh', '-c', f'mkdir -p {ARCHIVE} && test -w {ARCHIVE} && echo writable', timeout=60)
    if writable.strip().splitlines()[-1:] != ['writable']:
        raise Fault(f'归档目录 {ARCHIVE} 不存在或不可写；先修正 backend 的 tenant-archive 卷挂载再下线，站点未动')
    before = set(_listing(tenant, ARCHIVE))
    steps = []
    set_id = None
    if archive:
        # The final state of a tenant is the one backup nothing will ever take again, so it is
        # produced in a stable window, staged as a set, and must reach the off-site
        # repositories before the Site is destroyed.
        from dsherp import backup as backup_module
        backup_module.require_repositories(resolved)
        images = _running_images(resolved, runner, root)
        outcome = backup_module.backup_window(resolved, tenant, site, 'tenant', root=root, kind='retire', images=images)
        if isinstance(outcome, str):
            raise Fault(f'站点 {site} 仍有在途运行或写入（{outcome}），无法在稳定窗口里做最终备份；先停 worker 再下线，站点未动')
        status_doc = backup_module.load_status(resolved, root)
        backup_module.backup_status.record_set(status_doc, outcome, 'staged')
        backup_module.save_status(resolved, status_doc, root)
        steps.append(('backup', 'created'))
        backup_module.send_set(resolved, outcome, root=root, runner=runner, fatal=True)
        set_id = outcome['set_id']
        steps.append(('set', set_id))
    db_root = read_secret(resolved, 'db_root_password', root)
    _marked_script(tenant, DROP_SITE, {'site': site, 'archived_sites_path': ARCHIVE, 'db_root_password': db_root},
                   'DSHERP_SITE_DROPPED',
                   f'站点 {site} 的状态未知；不要重跑下线命令，先检查 {SITES}/{site} 与 {ARCHIVE}，租户清单未改',
                   timeout=1800, secrets=(db_root,))
    steps.append(('site', 'dropped'))
    added = sorted(set(_listing(tenant, ARCHIVE)) - before)
    # Frappe suffixes a counter when the same Site was archived before.
    candidates = [name for name in added if name == site or (name.startswith(site) and name[len(site):].isdigit())]
    if len(candidates) != 1:
        raise Fault(f'站点 {site} 已删除，但 {ARCHIVE} 下没有出现唯一的归档目录（新增：{"、".join(added) or "无"}）；'
                    '不要重跑下线命令，先检查该目录与备份，租户清单未改')
    path = f'{ARCHIVE}/{candidates[0]}'
    present = tenant.run('sh', '-c', f'test -f {path}/site_config.json && echo present', timeout=60)
    if present.strip().splitlines()[-1:] != ['present']:
        raise Fault(f'归档目录 {path} 缺少 site_config.json；不要重跑下线命令，先检查该目录，租户清单未改')
    backups = _listing(tenant, f'{path}/private/backups')
    steps.append(('archive', path))
    rows = [row for row in load_tenants(resolved, root) if row['slug'] != slug]
    save_tenants(resolved, rows, root)
    steps.append(('ingress', str(render_ingress(resolved, rows, root))))
    return {'site': site, 'archive': path, 'backups': backups, 'set_id': set_id, 'steps': steps}


def agent_firewall_rules(resolved, runner=subprocess.run):
    """Host rules that keep run containers off the host itself. Docker's `internal`
    only drops FORWARD traffic; the bridge gateway (the host) stays reachable unless the
    host's INPUT chain says otherwise. Docker's embedded DNS answers inside the
    container namespace, so dropping INPUT from the bridge breaks nothing a run needs."""
    result = runner(['docker', 'network', 'inspect', resolved['agent_network'], '--format', '{{.Id}}'],
                    text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    if result.returncode or not result.stdout.strip():
        raise Fault(f"读不到网络 {resolved['agent_network']}；先 compose up")
    bridge = 'br-' + result.stdout.strip()[:12]
    tag = '-m comment --comment dsherp-agent-firewall'
    # The same rules dsherp-agent-firewall.service applies at boot; printed for a host
    # without systemd or for inspection. The tag lets either side replace the other's.
    return {'bridge': bridge,
            'iptables': [f'iptables -I INPUT 1 -i {bridge} -m conntrack --ctstate ESTABLISHED,RELATED {tag} -j ACCEPT',
                         f'iptables -I INPUT 2 -i {bridge} {tag} -j DROP'],
            'nft': [f'nft add rule inet filter input iifname "{bridge}" ct state established,related '
                    'comment "dsherp-agent-firewall" accept',
                    f'nft add rule inet filter input iifname "{bridge}" comment "dsherp-agent-firewall" drop'],
            'undo': [f'iptables -D INPUT -i {bridge} {tag} -j DROP',
                     f'iptables -D INPUT -i {bridge} -m conntrack --ctstate ESTABLISHED,RELATED {tag} -j ACCEPT'],
            'unit': 'dsherp-agent-firewall.service',
            'script': '/usr/local/sbin/dsherp-agent-firewall'}


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
    if resolved.get('backup_repository'):
        for name in PROVIDED_SECRETS:
            path = directory / name
            if not path.exists():
                findings.append(f'缺少对象存储凭据文件：{name}（配置了异地仓库就必须提供，格式为 AWS_ACCESS_KEY_ID=… 两行）')
            elif path.stat().st_mode & 0o077:
                findings.append(f'对象存储凭据文件权限过宽：{name}')
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
    findings += _rotation_findings(resolved, root)
    return findings


def _rotation_findings(resolved, root):
    """Long-lived credentials nobody has changed. Reported here because the operator reads
    doctor before a release, and a credential's age is exactly that kind of fact."""
    from dsherp import rotation
    try:
        rows = rotation.read(runtime_dir(resolved, root) / 'rotations.json')
    except ValueError as error:
        return [str(error)]
    return [f"凭据轮换：{item['kind']} {item['target']} {item['note']}"
            f"（{item['age_days']} 天 / 窗口 {item['window_days']} 天）"
            if item['age_days'] is not None else
            f"凭据轮换：{item['kind']} {item['target']} {item['note']}"
            for item in rotation.findings(rows, rotation_targets(resolved, root), datetime.datetime.now())]


# --- release and rollback -------------------------------------------------------
# Gate G2: an upgrade is only trusted when the data before and after it can be compared
# field by field, and a rollback only when the restored data equals the pre-upgrade
# snapshot. The reader (dsherp/release_snapshot.py) runs inside the bench container; the
# judgement (dsherp/release_compare.py) runs here.
from dsherp import release_compare, release_snapshot

# On the archive volume of each bench (compose mounts it; the image prepares the directory),
# so the pre-upgrade backup set outlives Frappe's own purge of private/backups (23 hours).
ARCHIVED_RELEASES = '/home/frappe/frappe-bench/archived/releases'
BACKUP_PIECES = ('database.sql.gz', 'site_config_backup.json', 'files.tar', 'private-files.tar')
# The only thing a restore itself writes: it re-applies the scheduler flag it read first
# (the Single's modified stamps are never compared).
RESTORE_EXPECTATIONS = [{'patch': 'frappe.restore', 'doctype': 'System Settings', 'fields': ['enable_scheduler']}]
# `active` is what release/rollback refuse to start on (any unfinished run). `running` is the
# narrower question a backup window asks: is an executor actually in flight? Queued runs are
# frozen by the hold and must not keep a window waiting forever (review round 2, item 1).
SITE_FLAGS = ("has=frappe.db.exists('DocType','DS Model Run')\n"
              "counts={s:(frappe.db.count('DS Model Run',{'status':s}) if has else 0) "
              "for s in ('Queued','Running','Cancelling','NeedsInput')}\n"
              "print(json.dumps({'active':counts['Queued']+counts['Running']+counts['Cancelling'],"
              "'running':counts['Running']+counts['Cancelling'],'counts':counts,"
              "'maintenance_mode':int(frappe.conf.get('maintenance_mode') or 0),"
              "'pause_scheduler':int(frappe.conf.get('pause_scheduler') or 0),"
              "'dsherp_hold_until':int(frappe.conf.get('dsherp_hold_until') or 0)}))")
EXPECTATIONS = ("import importlib,os\n"
                "found=[]\n"
                "for app in frappe.get_installed_apps():\n"
                "    if app in ('frappe','erpnext'):continue\n"
                "    path=os.path.join(frappe.get_app_path(app),'patches.txt')\n"
                "    if not os.path.exists(path):continue\n"
                "    for line in open(path):\n"
                "        line=line.split('#')[0].strip()\n"
                "        if not line or line.startswith('['):continue\n"
                "        module=importlib.import_module(line.split()[0])\n"
                "        for entry in getattr(module,'EXPECTED_CHANGES',None) or []:\n"
                "            found.append({'patch':line.split()[0],**entry})\n"
                "print('DSHERP_EXPECTATIONS '+json.dumps(found))")


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + f'.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True, default=str)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def _release_dir(resolved, tag, root=ROOT):
    return runtime_dir(resolved, root) / 'releases' / tag


def _release_report(resolved, name, payload, root=ROOT):
    """Timestamped, never overwritten; the tag-named copy is the latest one."""
    directory = runtime_dir(resolved, root) / 'releases'
    stamped = _write_json(directory / f"{name}-{time.strftime('%Y%m%d_%H%M%S')}.json", payload)
    _write_json(directory / f'{name}.json', payload)
    return stamped


def _targets(resolved, root, factory):
    """Every Site this deployment runs, with the bench that serves it: tenants and the platform."""
    targets = [('tenant', row['site']) for row in load_tenants(resolved, root)]
    targets.append(('platform', resolved['platform_site']))
    benches = {}
    return [(benches.setdefault(kind, factory(kind)), site) for kind, site in targets]


def _last_line(text):
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ''


def _site_flags(bench, site):
    return json.loads(_last_line(bench.python(site, SITE_FLAGS, timeout=120)))


def _set_flag(bench, site, key, value):
    bench.run('bench', '--site', site, 'set-config', '--parse', key, str(int(value)), timeout=120)


def _quiesce(bench, site):
    """No run may be in flight and no cron may fire while the data is being judged."""
    flags = _site_flags(bench, site)
    if flags['active']:
        raise Fault(f"站点 {site} 仍有 {flags['active']} 个运行未结束（Queued/Running/Cancelling）；"
                    '先停 worker（systemctl stop dsherp-agent-worker）并等它们结束，再发布或回滚')
    _set_flag(bench, site, 'maintenance_mode', 1)
    _set_flag(bench, site, 'pause_scheduler', 1)
    return flags


def _release_site(bench, site, flags):
    _set_flag(bench, site, 'maintenance_mode', flags.get('maintenance_mode', 0))
    _set_flag(bench, site, 'pause_scheduler', flags.get('pause_scheduler', 0))


def _hash_columns_of(snapshot):
    """The column set each table's digests cover in a snapshot (older files: all columns)."""
    return {table: row.get('hash_columns') or row['columns'] for table, row in snapshot['tables'].items()}


def take_snapshot(resolved, site, *, root=ROOT, runner=subprocess.run, bench_factory=None, bench=None,
                  hash_columns=None, like=None):
    """Read the Site's data inside its bench; any table it cannot read aborts the snapshot.
    `like` aligns the digests with an existing snapshot so the two can be compared."""
    if bench is None:
        factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
        bench = factory('platform' if site == resolved['platform_site'] else 'tenant')
    if like is not None:
        hash_columns = _hash_columns_of(like)
    script = release_snapshot.container_script(hash_columns=hash_columns)
    return release_snapshot.parse_output(bench.python(site, script, timeout=3600))


def compare_snapshots(before, after, expectations=None):
    return release_compare.compare(before, after, expectations, allow_inserts_in=release_snapshot.LOG_DOCTYPES)


def _expected_changes(bench, site):
    line = _last_line(bench.python(site, EXPECTATIONS, timeout=300))
    marker = 'DSHERP_EXPECTATIONS '
    if not line.startswith(marker):
        raise Fault(f'读不到 {site} 上 patch 声明的预期变化')
    return json.loads(line[len(marker):])


def _archive_backup(bench, site, tag):
    """Find the set `bench backup --with-files` just wrote and copy it where the purge cannot reach."""
    listing = bench.run('sh', '-c', f'ls -1 {SITES}/{site}/private/backups', timeout=60).split()
    slug = site.replace('.', '_')
    databases = sorted(name for name in listing if name.endswith(f'-{slug}-database.sql.gz'))
    if not databases:
        raise Fault(f'站点 {site} 备份后在 private/backups 下没有数据库转储；备份集不完整，不迁移')
    prefix = databases[-1][:-len('-database.sql.gz')]
    pieces = {piece: f'{prefix}-{piece}' for piece in BACKUP_PIECES}
    missing = [name for name in pieces.values() if name not in listing]
    if missing:
        raise Fault(f'站点 {site} 的备份集不完整，缺 {"、".join(missing)}；不迁移')
    target = f'{ARCHIVED_RELEASES}/{tag}/{site}'
    config = pieces['site_config_backup.json']
    data = ' '.join(f'{SITES}/{site}/private/backups/{pieces[piece]}'
                    for piece in ('database.sql.gz', 'files.tar', 'private-files.tar'))
    # The copy of site_config carries the database password and the encryption key; it never
    # shares a directory or a mode with the dump (design §4.1, runbook §11).
    bench.run('sh', '-c', f'umask 077 && mkdir -p {target}/secrets && chmod 700 {target}/secrets && '
                          f'cp {data} {target}/ && cp {SITES}/{site}/private/backups/{config} {target}/secrets/ && '
                          f'chmod 600 {target}/secrets/{config}', timeout=600)
    return {'database': f"{target}/{pieces['database.sql.gz']}",
            'site_config': f"{target}/secrets/{config}",
            'files': f"{target}/{pieces['files.tar']}",
            'private_files': f"{target}/{pieces['private-files.tar']}"}


def bench_service_of(resolved, site):
    return SERVICES[resolved['env']]['platform' if site == resolved['platform_site'] else 'tenant']


def _running_images(resolved, runner, root):
    """What the two bench services actually run, from the containers, not from prod.env."""
    file = Path(root) / COMPOSE[resolved['env']]
    env_file = deploy_env.env_file(resolved['env'], root)
    images = {}
    for service in ('backend', 'platform-backend'):
        command = ['docker', 'compose', '-p', resolved['project']]
        if env_file.exists():
            command += ['--env-file', str(env_file)]
        command += ['-f', str(file), 'ps', '-q', service]
        listed = runner(command, text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        container = [line.strip() for line in (listed.stdout or '').splitlines() if line.strip()] if not listed.returncode else []
        if not container:
            raise Fault(f'服务 {service} 没有在运行的容器；先 compose up -d 再发布或回滚')
        if len(container) > 1:
            # Checking the first and letting the others serve would make the identity check order-dependent.
            raise Fault(f'服务 {service} 有 {len(container)} 个容器在运行（{", ".join(container)}）；发布与回滚要求每个 '
                        'bench 服务恰好一个容器，先收敛到一个再来')
        inspected = runner(['docker', 'inspect', '-f', '{{.Config.Image}} {{.Image}}', container[0]],
                           text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        parts = (inspected.stdout or '').split()
        if inspected.returncode or len(parts) < 2:
            raise Fault(f'读不到服务 {service} 的镜像身份')
        images[service] = {'image': parts[0], 'image_id': parts[1]}
    return images


MANIFESTS = 'manifests'  # under the runtime directory: where a delivered release manifest goes


def _valid_image_id(value):
    return isinstance(value, str) and value.startswith('sha256:') and len(value) > len('sha256:')


def _read_manifest_id(path, tag, image):
    """The immutable id one manifest file recorded for `image`. Never None: a file that is
    missing, unreadable, for another tag or without a usable id is a Fault, because 'no
    expected id' must not turn into 'any id will do'."""
    hint = (f'；清单由构建机的 release_images.py 写出并随镜像一起交付（--bundle），'
            f'放到 <runtime>/{MANIFESTS}/{tag}.json 或用 --manifest 指定')
    if not path.is_file():
        raise Fault(f'没有 {tag} 的发布清单（找过 {path}），无法核对运行镜像的 id，不改动站点' + hint)
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise Fault(f'发布清单 {path} 读不出来或不是 JSON（{error}），无法核对镜像 id，不改动站点') from error
    recorded = payload.get('tag') if isinstance(payload, dict) else None
    if recorded != tag:
        raise Fault(f'发布清单 {path} 记录的 tag 是 {recorded!r}，不是 {tag}；不是这个版本的清单，不改动站点')
    rows = payload.get('images')
    row = rows.get(image) if isinstance(rows, dict) else None
    if not isinstance(row, dict):
        raise Fault(f'发布清单 {path} 没有 {image} 的记录，无法核对镜像 id，不改动站点')
    image_id = row.get('id')
    if not _valid_image_id(image_id):
        raise Fault(f'发布清单 {path} 里 {image} 的镜像 id 是 {image_id!r}，不是可核对的镜像 id，不改动站点')
    return image_id


def _manifest_image_id(resolved, root, tag, image, explicit=None):
    """The immutable id on record for `image` under `tag`, and where it came from. An explicit
    --manifest is the only file consulted; otherwise the copy delivered to the runtime
    directory and the repository copy (the manifest enters git after the tag it describes, so
    a tree fetched by tag never has it) — when both exist they must agree."""
    if explicit is not None:
        if not str(explicit).strip():
            raise Fault('--manifest 需要一个文件路径')
        path = Path(explicit)
        return _read_manifest_id(path, tag, image), f'{tag} 的发布清单 {path}'
    delivered = runtime_dir(resolved, root) / MANIFESTS / f'{tag}.json'
    repo = Path(root) / 'infra' / 'releases' / f'{tag}.json'
    present = [path for path in (delivered, repo) if path.is_file()]
    if not present:
        raise Fault(f'没有 {tag} 的发布清单（找过 {delivered} 与 {repo}），无法核对运行镜像的 id，不改动站点；'
                    f'清单由构建机的 release_images.py 写出并随镜像一起交付（--bundle），'
                    f'放到 <runtime>/{MANIFESTS}/{tag}.json 或用 --manifest 指定')
    ids = {path: _read_manifest_id(path, tag, image) for path in present}
    if len(set(ids.values())) > 1:
        raise Fault(f'{tag} 的两份发布清单不一致：' + '；'.join(f'{path} 记录 {image_id}' for path, image_id in ids.items())
                    + '；删掉过期的那份或用 --manifest 指定，不改动站点')
    return ids[present[0]], f'{tag} 的发布清单 {present[0]}'


def _require_image_names(images, expected_image):
    """Every bench must run exactly the expected image by full name (registry, repository, tag)."""
    for service, row in images.items():
        if row['image'] != expected_image:
            raise Fault(f'服务 {service} 运行的镜像是 {row["image"]}，不是 {expected_image}；'
                        '先把 prod.env 改到正确的 tag 并 compose up -d')


def _require_image_ids(images, expected_ids, source):
    """Every bench must run the immutable id `source` recorded; a service with no id on record
    is refused, never skipped."""
    for service, row in images.items():
        wanted = expected_ids.get(service)
        if not wanted:
            raise Fault(f'{source}没有服务 {service} 的镜像 id，无法核对，不改动站点')
        if row['image_id'] != wanted:
            raise Fault(f'服务 {service} 运行的 {row["image"]} 镜像 id 是 {row["image_id"]}，{source}记录的是 {wanted}；'
                        '这不是同一个制品，先核对镜像来源')


def _previous_ids(recorded, images, expected_image, action='回滚', record='发布记录的 previous_images'):
    """The ids of what ran before the upgrade, from the release record: None when the record
    holds none (a first release), a Fault when it holds an unusable one; never a partial set."""
    if recorded is None:
        return None
    ids = {}
    for service in images:
        row = recorded.get(service) if isinstance(recorded, dict) else None
        image_id = row.get('image_id') if isinstance(row, dict) else None
        if not isinstance(row, dict) or row.get('image') != expected_image or not _valid_image_id(image_id):
            raise Fault(f'{record} 里没有服务 {service} 运行 {expected_image} 的有效镜像 id（{row!r}）；'
                        f'记录不可用，不{action}')
        ids[service] = image_id
    return ids


def _current_path(resolved, root=ROOT):
    return runtime_dir(resolved, root) / 'releases' / 'current.json'


def _current(resolved, root=ROOT):
    path = _current_path(resolved, root)
    return json.loads(path.read_text()) if path.exists() else None


def forget_release(resolved, tag, *, root=ROOT):
    """Drop the host-side record of a release (baseline snapshots and backup pointers); the
    archived backup set inside the benches stays. For drills and for a deliberate redo."""
    import shutil
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('forget-release 需要一个 tag')
    records = (runtime_dir(resolved, root) / 'releases').resolve()
    record = records / tag
    if record.is_symlink() or records not in record.resolve().parents or record.resolve() == records:
        raise Fault(f'{record} 不是发布记录目录下的普通子目录，拒绝删除')
    if record.exists():
        shutil.rmtree(record)
    return {'tag': tag, 'forgotten': str(record)}


def resume_site(resolved, site, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Lift maintenance deliberately after a release or rollback that did not reopen the site."""
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    bench = factory('platform' if site == resolved['platform_site'] else 'tenant')
    _release_site(bench, site, {})
    return {'site': site, 'maintenance_mode': 0, 'pause_scheduler': 0}


def _with_operations_lock(who, function, resolved, *arguments, **keywords):
    """backup, release, rollback and retire-tenant never overlap on one host: each opens or
    closes a Site's window, and two of them at once would judge each other's state."""
    from dsherp import backup as backup_module
    with backup_module.operations_lock(resolved, keywords.get('root', ROOT), who):
        return function(resolved, *arguments, **keywords)


def release(resolved, tag, **keywords):
    return _with_operations_lock('release', _release, resolved, tag, **keywords)


def _release(resolved, tag, *, root=ROOT, runner=subprocess.run, bench_factory=None, from_tag=None, manifest=None):
    """Quiesce, back up and archive, snapshot, migrate, snapshot, judge; per Site, platform included.

    A site reopens only after a completed, clean judgement; anything else leaves it in
    maintenance with a partial report, and `rollback <tag>` is the way out.
    """
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('发布必须给出明确的镜像 tag')
    if resolved['image_tag'] != tag:
        raise Fault(f"infra/env/prod.env 的 DSHERP_IMAGE_TAG 是 {resolved['image_tag']!r}，不是要发布的 {tag!r}；"
                    '先改环境文件并 compose up -d，再发布')
    if from_tag is not None and not deploy_env.TAG.fullmatch(from_tag):
        raise Fault('--from 必须是一个 tag')
    from dsherp import backup as backup_module
    backup_module.require_repositories(resolved)
    images = _running_images(resolved, runner, root)
    _require_image_names(images, resolved['frappe_image'])
    # The manifest is the only record of which build this tag is; without a valid one the
    # running image cannot be vouched for and nothing is touched.
    built, source = _manifest_image_id(resolved, root, tag, resolved['frappe_image'], manifest)
    _require_image_ids(images, {service: built for service in images}, source)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    targets = _targets(resolved, root, factory)
    if len(targets) < 2:
        raise Fault('没有可发布的租户站；先运行 provision-tenant')
    record_root = _release_dir(resolved, tag, root)
    if any((record_root / site / 'before.json').exists() for _, site in targets):
        raise Fault(f'release {tag} 已有升级前基线（{record_root}）：基线只写一次，否则回滚会回到错误的状态。'
                    f'要重来，换一个 tag，或确认不再需要那份基线后执行 forget-release {tag}')
    current = _current(resolved, root)
    if from_tag and current and current.get('tag') != from_tag:
        raise Fault(f"current.json 记录的是 {current.get('tag')}，--from 给的是 {from_tag}；记录与声明不一致，先核对："
                    '记录对就不要给 --from，记录错了就把它改对或删掉（{path}）'.format(path=_current_path(resolved, root)))
    previous = from_tag or (current['tag'] if current else None)
    if not previous:
        raise Fault('没有当前版本的记录：第一次发布必须用 --from <升级前运行的 tag> 说明从哪个版本升上来')
    previous_image = deploy_env.image_name(resolved['registry'], 'frappe', previous)
    previous_images = current.get('images') if current else None
    if previous_images is not None:
        _previous_ids(previous_images, images, previous_image, action='发布', record='current.json 的 images')
    else:
        # A first release: its rollback can only anchor on the previous tag's manifest, so prove
        # now, before anything is touched, that a usable one is at hand.
        _manifest_image_id(resolved, root, previous, previous_image)
    # Pre-flight before anything is recorded or quiesced: a Site with runs in flight stops
    # the whole release while nothing has changed yet.
    for bench, site in targets:
        flags = _site_flags(bench, site)
        if flags['active']:
            raise Fault(f"站点 {site} 仍有 {flags['active']} 个运行未结束（Queued/Running/Cancelling）；"
                        '先停 worker（systemctl stop dsherp-agent-worker）并等它们结束，再发布')
    started_at = time.strftime('%Y-%m-%d %H:%M:%S')
    _write_json(record_root / 'release.json', {'tag': tag, 'previous_tag': previous, 'images': images,
                                               'previous_images': previous_images, 'started': started_at})
    report = {'tag': tag, 'previous_tag': previous, 'images': images, 'sites': {}, 'steps': [], 'clean': False}
    for bench, site in targets:
        record = record_root / site
        started = time.monotonic()
        flags = _quiesce(bench, site)
        report['steps'].append((site, 'quiesced'))
        step = 'backup'
        try:
            bench.run('bench', '--site', site, 'backup', '--with-files', timeout=3600)
            pieces = backup_module.find_pieces(bench, site)
            backup = _archive_backup(bench, site, tag)
            _write_json(record / 'backup.json', backup)
            report['steps'].append((site, 'backed-up'))
            step = 'snapshot-before'
            before = take_snapshot(resolved, site, bench=bench)
            _write_json(record / 'before.json', before)
            report['steps'].append((site, 'snapshot-before'))
            step = 'stage-set'
            # The same set protocol the scheduled backup uses, so this one can leave the host
            # and be restored on another one; it records the build being upgraded away from.
            set_doc = backup_module.stage_set(
                bench, site, pieces, kind='release', stamp=time.strftime('%Y%m%d_%H%M%S', time.gmtime()),
                token=secrets.token_hex(3), window={'started': started_at, 'finished': backup_module.backup_status.now_iso()},
                image_tag=previous, image_id=(previous_images or {}).get(bench_service_of(resolved, site), {}).get(
                    'image_id') or images[bench_service_of(resolved, site)]['image_id'],
                snapshot=before, frappe_version=backup_module.site_version(bench, site))
            status_doc = backup_module.load_status(resolved, root)
            backup_module.backup_status.record_set(status_doc, set_doc, 'staged')
            backup_module.save_status(resolved, status_doc, root)
            report['steps'].append((site, 'staged'))
            step = 'offsite'
            offsite = backup_module.send_set(resolved, set_doc, root=root, runner=runner, fatal=False)
            step = 'migrate'
            migrated = bench.run('bench', '--site', site, 'migrate', timeout=3600)
            report['steps'].append((site, 'migrated'))
            step = 'snapshot-after'
            after = take_snapshot(resolved, site, bench=bench, hash_columns=_hash_columns_of(before))
            _write_json(record / 'after.json', after)
            report['steps'].append((site, 'snapshot-after'))
            step = 'expectations'
            declared = _expected_changes(bench, site)
            executed = sorted(set(after.get('patches', [])) - set(before.get('patches', [])))
            applicable = [entry for entry in declared if entry['patch'] in executed]
            step = 'compare'
            comparison = compare_snapshots(before, after, applicable)
        except Exception as error:
            report['failed'] = {'site': site, 'step': step, 'error': str(error)}
            report['path'] = str(_release_report(resolved, f'release-{tag}', report, root))
            raise Fault(f'站点 {site} 在 {step} 阶段失败，站点保持维护模式；用 rollback {tag} 回到升级前的备份。'
                        f'\n{error}') from error
        report['sites'][site] = {'backup': backup, 'set_id': set_doc['set_id'], 'offsite': offsite,
                                 'comparison': comparison, 'patches_executed': executed,
                                 'expectations_ignored': [entry for entry in declared if entry['patch'] not in executed],
                                 'migrate_tail': migrated.strip().splitlines()[-20:],
                                 'seconds': round(time.monotonic() - started, 1),
                                 'maintenance': 'released' if comparison['clean'] else 'kept'}
        if comparison['clean']:
            _release_site(bench, site, flags)
    report['clean'] = all(row['comparison']['clean'] for row in report['sites'].values())
    if report['clean']:
        _write_json(_current_path(resolved, root), {'tag': tag, 'images': images, 'at': time.strftime('%Y-%m-%d %H:%M:%S')})
    report['path'] = str(_release_report(resolved, f'release-{tag}', report, root))
    return report


def _backup_set(bench, site, database):
    """The four pieces next to a database dump; the tars are used when present."""
    if not database.endswith('-database.sql.gz'):
        raise Fault(f'{site} 的备份文件必须是 *-database.sql.gz：{database}')
    present = bench.run('sh', '-c', f'test -s {database} && echo present', timeout=60).strip()
    if present.splitlines()[-1:] != ['present']:
        raise Fault(f'{site} 的备份文件不存在或为空：{database}')
    stem = database[:-len('database.sql.gz')]
    found = {'database': database}
    for key, piece in (('files', 'files.tar'), ('private_files', 'private-files.tar')):
        path = stem + piece
        listed = bench.run('sh', '-c', f'test -s {path} && echo present', timeout=60).strip()
        if listed.splitlines()[-1:] == ['present']:
            found[key] = path
    return found


def rollback(resolved, tag, **keywords):
    return _with_operations_lock('rollback', _rollback, resolved, tag, **keywords)


def _rollback(resolved, tag, *, root=ROOT, runner=subprocess.run, bench_factory=None, backups=None, before=None,
              manifest=None):
    """Undo `release <tag>`: on the previous images, restore what it archived (or the named
    files) and prove the data is what it was before the upgrade."""
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('回滚必须给出要撤销的发布 tag')
    record_root = _release_dir(resolved, tag, root)
    if not (record_root / 'release.json').exists():
        raise Fault(f'没有 release {tag} 的记录（{record_root}）；不知道升级前是哪个版本，也不知道该恢复什么')
    record = json.loads((record_root / 'release.json').read_text())
    previous = record.get('previous_tag')
    if not isinstance(previous, str) or not deploy_env.TAG.fullmatch(previous):
        raise Fault(f'release {tag} 的记录没有可用的 previous_tag（{previous!r}）；不知道升级前是哪个版本，不回滚')
    if resolved['image_tag'] != previous:
        raise Fault(f"回滚 {tag} 要在升级前的版本 {previous} 上进行：infra/env/prod.env 的 DSHERP_IMAGE_TAG 现在是 "
                    f"{resolved['image_tag']!r}，先改回 {previous} 并 compose up -d")
    images = _running_images(resolved, runner, root)
    _require_image_names(images, resolved['frappe_image'])
    # prod.env is back on the previous tag, so resolved['frappe_image'] names the previous image.
    # Its id is what actually ran before the upgrade (the release record's previous_images, from
    # current.json), complete and valid; only a record with none falls back to the previous
    # tag's manifest, and without a valid manifest either nothing is restored.
    expected_ids = _previous_ids(record.get('previous_images'), images, resolved['frappe_image'])
    if expected_ids is None:
        built, source = _manifest_image_id(resolved, root, previous, resolved['frappe_image'], manifest)
        expected_ids = {service: built for service in images}
    else:
        source = '发布记录的 previous_images（升级前实际运行的镜像）'
        if manifest is not None:
            # Named explicitly, so it is read and must tell the same story as the record.
            built, named = _manifest_image_id(resolved, root, previous, resolved['frappe_image'], manifest)
            if any(image_id != built for image_id in expected_ids.values()):
                raise Fault(f'{named} 记录的 {built} 与{source}记录的 {sorted(set(expected_ids.values()))} 不一致；'
                            '先核对哪一份是对的，不回滚')
    _require_image_ids(images, expected_ids, source)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    targets = [(bench, site) for bench, site in _targets(resolved, root, factory)
               if (record_root / site / 'before.json').exists() or site in (backups or {})]
    if not targets:
        raise Fault(f'release {tag} 的记录里没有任何站的升级前快照，也没有给出备份文件')
    report = {'tag': tag, 'previous_tag': previous, 'images': images, 'sites': {}, 'steps': [], 'clean': False}
    plans = []
    for bench, site in targets:
        site_record = record_root / site
        if backups and site in backups:
            database = backups[site]
        elif (site_record / 'backup.json').exists():
            database = json.loads((site_record / 'backup.json').read_text())['database']
        else:
            raise Fault(f'{site} 没有 release {tag} 归档的备份记录，也没有 --backup 指定文件')
        if before and site in before:
            expected = json.loads(Path(before[site]).read_text())
        elif (site_record / 'before.json').exists():
            expected = json.loads((site_record / 'before.json').read_text())
        else:
            raise Fault(f'{site} 没有 release {tag} 的升级前快照，无法核验回滚结果')
        plans.append((bench, site, _backup_set(bench, site, database), expected))
    db_root = read_secret(resolved, 'db_root_password', root)
    for bench, site, pieces, expected in plans:
        started = time.monotonic()
        flags = _quiesce(bench, site)
        step = 'restore'
        try:
            _marked_script(bench, RESTORE_SITE, {**pieces, 'site': site, 'db_root_password': db_root},
                           'DSHERP_SITE_RESTORED', f'站点 {site} 的状态未知', timeout=3600, secrets=(db_root,))
            report['steps'].append((site, 'restored'))
            step = 'snapshot'
            restored = take_snapshot(resolved, site, bench=bench, hash_columns=_hash_columns_of(expected))
            _write_json(record_root / site / 'restored.json', restored)
            step = 'compare'
            comparison = compare_snapshots(expected, restored, RESTORE_EXPECTATIONS)
        except Exception as error:
            report['failed'] = {'site': site, 'step': step, 'error': str(error)}
            report['path'] = str(_release_report(resolved, f'rollback-{tag}', report, root))
            raise Fault(f'站点 {site} 在 {step} 阶段失败，站点保持维护模式；先检查再决定重试或 resume-site。\n{error}') from error
        report['sites'][site] = {'backup': pieces, 'comparison': comparison,
                                 'seconds': round(time.monotonic() - started, 1),
                                 'maintenance': 'released' if comparison['clean'] else 'kept'}
        if comparison['clean']:
            _release_site(bench, site, flags)
    report['clean'] = all(row['comparison']['clean'] for row in report['sites'].values())
    if report['clean']:
        _write_json(_current_path(resolved, root), {'tag': previous, 'images': images, 'at': time.strftime('%Y-%m-%d %H:%M:%S')})
    report['path'] = str(_release_report(resolved, f'rollback-{tag}', report, root))
    return report


MONTH = re.compile(r'\d{4}-(0[1-9]|1[0-2])')
# Read inside each bench: the Site's own finished runs with the numbers it recorded itself.
USAGE_SCRIPT = ("rows=frappe.get_all('DS Model Run',fields=['creation','status','actual_input_tokens',"
                "'actual_output_tokens','model_calls','duration_ms','usage_unknown_calls'],limit_page_length=0) "
                "if frappe.db.exists('DocType','DS Model Run') else []\n"
                "zone=frappe.db.get_single_value('System Settings','time_zone') or ''\n"
                "for row in rows:row['creation']=str(row['creation']);row['site']=frappe.local.site;row['time_zone']=zone\n"
                "print('DSHERP_USAGE '+json.dumps(rows,default=str))")


def usage_report(resolved, month, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Usage per Site for one calendar month (T5).

    Every Site answers with its own finished runs; the host only adds them up, so a Site that
    is unreachable is a gap the report shows rather than a tenant that looks free."""
    from dsherp import usage as usage_module
    if not MONTH.fullmatch(month or ''):
        raise Fault('用量月份的格式是 YYYY-MM，例如 2026-09')
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    rows, unreachable = [], {}
    for bench, site in _targets(resolved, root, factory):
        try:
            line = _last_line(bench.python(site, USAGE_SCRIPT, timeout=600))
        except Fault as error:
            unreachable[site] = str(error)[:300]
            continue
        marker = 'DSHERP_USAGE '
        if not line.startswith(marker):
            unreachable[site] = '站点没有返回用量数据'
            continue
        rows += json.loads(line[len(marker):])
    report = usage_module.monthly(rows, month)
    for site in {site for _, site in _targets(resolved, root, factory)}:
        report['sites'].setdefault(site, {**{key: 0 for key in ('runs', 'succeeded', 'failed', 'cancelled',
                                                               'budget_exceeded', 'needs_input',
                                                               'unfinished', 'input_tokens', 'output_tokens',
                                                               'model_calls', 'duration_ms', 'unknown_calls',
                                                               'runs_with_unknown_usage')}, 'complete': True})
    report['unreachable'] = unreachable
    # A Site that did not answer has usage this report does not contain. Its row and the
    # totals say so; the numbers that are here stay, as the known part.
    for site in unreachable:
        report['sites'].setdefault(site, {})['complete'] = False
    if unreachable:
        report['totals']['complete'] = False
    report['path'] = str(_write_json(runtime_dir(resolved, root) / 'usage' / f'usage-{month}.json', report))
    return report


SESSION_ROOT = 'business-sessions'


def sessions_report(resolved, *, root=ROOT, days=None, sweep=False):
    """What the host holds of users' native sessions, and the 90-day sweep (T4).

    Sessions written before the enumerable layout are one-way hashes: they are reported, never
    guessed at. Everything else is removed once it has been untouched for the window."""
    from dsherp import sessions as sessions_module
    state_root = runtime_dir(resolved, root) / SESSION_ROOT
    window = sessions_module.RETENTION_DAYS if days is None else days
    if type(window) is not int or window < 1:
        raise Fault('会话保留天数必须是正整数')
    if sweep:
        report = sessions_module.sweep(state_root, days=window)
    else:
        report = {'removed': [], 'kept': 0, 'unattributed': []}
        if state_root.is_dir():
            for site in sorted(state_root.iterdir()):
                if not site.is_dir():
                    continue
                if sessions_module.SCOPE.fullmatch(site.name):
                    report['unattributed'].append(site.name)
                    continue
                for conversation in sorted(site.iterdir()):
                    report['kept'] += sum(1 for scope in conversation.iterdir() if scope.is_dir())
    report['root'] = str(state_root)
    report['retention_days'] = window
    report['swept'] = bool(sweep)
    return report


USER_ID = re.compile(r"[^\s'\"\\]{3,140}")
RESERVED_USERS = ('Administrator', 'Guest')


def _user_checked(user):
    if not USER_ID.fullmatch(user or ''):
        raise Fault('用户标识必须是站点里的登录名（通常是邮箱），且不含空白与引号')
    if user in RESERVED_USERS:
        raise Fault(f'{user} 是系统账号，不接受导出或删除请求')
    return user


def _user_site_bench(resolved, root, factory, site):
    """The bench serving this Site. A Site this deployment does not run is refused, not guessed."""
    for bench, name in _targets(resolved, root, factory):
        if name == site:
            return bench
    raise Fault(f'{site} 不是本部署运行的站点；用 list-tenants 看当前清单')


def _user_read_script(user):
    """Read one person's rows: exactly the columns the boundary declares, nothing wider."""
    from dsherp import user_data as user_data_module
    columns = {doctype: sorted(set(rules['keep']) | set(rules['clear']))
               for doctype, rules in user_data_module.BOUNDARY.items()}
    return ("user=" + repr(user) + "\n"
            "columns=json.loads(" + repr(json.dumps(columns)) + ")\n"
            "def rows(doctype,filters=None,or_filters=None):\n"
            "    if not frappe.db.exists('DocType',doctype):return []\n"
            "    found=frappe.get_all(doctype,filters=filters,or_filters=or_filters,\n"
            "                         fields=columns[doctype],limit_page_length=0,order_by='creation asc')\n"
            "    return [{k:(str(v) if hasattr(v,'isoformat') else v) for k,v in row.items()} for row in found]\n"
            "def merge(*groups):\n"
            "    seen={}\n"
            "    for group in groups:\n"
            "        for row in group:seen.setdefault(row['name'],row)\n"
            "    return [seen[name] for name in sorted(seen)]\n"
            "conversations=rows('DS Conversation',{'owner':user})\n"
            "conversation_names=[row['name'] for row in conversations]\n"
            "run_or=[['owner','=',user]]+([['conversation','in',conversation_names]] if conversation_names else [])\n"
            "runs=merge(rows('DS Model Run',or_filters=run_or))\n"
            "run_names=[row['name'] for row in runs]\n"
            "proposal_or=[['owner','=',user]]+([['model_run','in',run_names]] if run_names else [])\n"
            "proposals=merge(rows('DS Operation Proposal',or_filters=proposal_or))\n"
            "proposal_names=[row['name'] for row in proposals]\n"
            "execution_or=[['owner','=',user]]+([['proposal','in',proposal_names]] if proposal_names else [])\n"
            "executions=merge(rows('DS Execution Record',or_filters=execution_or))\n"
            "print('DSHERP_USER_DATA '+json.dumps({'user':user,'known_user':bool(frappe.db.exists('User',user)),\n"
            "      'conversations':conversations,'runs':runs,'proposals':proposals,'executions':executions},\n"
            "      default=str))")


# Runs still being worked on, or waiting to be. A NeedsInput run that still holds its
# capability has a live executor waiting for the person; one that does not is parked.
INFLIGHT = ("def inflight(user):\n"
            "    rows=frappe.get_all('DS Model Run',filters={'owner':user,'status':['in',['Queued','Running','Cancelling']]},\n"
            "                        fields=['name','status'],limit_page_length=0)\n"
            "    rows+=frappe.get_all('DS Model Run',filters={'owner':user,'status':'NeedsInput','capability_hash':['!=','']},\n"
            "                         fields=['name','status'],limit_page_length=0)\n"
            "    return rows\n")


def _user_settle_script(user):
    """Stop the person's in-flight runs the way the person would: queued ones are cancelled
    outright, running ones are asked to stop. Under the same User row lock send_message
    takes, so no new run of theirs slips in between the look and the write."""
    return ("user=" + repr(user) + "\n" + INFLIGHT +
            "from dsherp_bridge import context_events as events, grants\n"
            "frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))\n"
            "rows=inflight(user)\n"
            "cancelled=[];waiting=[]\n"
            "for row in rows:\n"
            "    if row['status']=='Queued':\n"
            "        frappe.db.set_value('DS Model Run',row['name'],{'status':'Cancelled','error':'用户数据删除：排队中的运行已取消'})\n"
            "        events.record_safely(row['name'],'cancel_requested',{'to_status':'Cancelled','reason':'user_data_deletion'})\n"
            "        events.record_safely(row['name'],'finished',{'status':'Cancelled'})\n"
            "        grants.drop(row['name']);cancelled.append(row['name'])\n"
            "    else:\n"
            "        if row['status']!='Cancelling':\n"
            "            frappe.db.set_value('DS Model Run',row['name'],{'status':'Cancelling'})\n"
            "            events.record_safely(row['name'],'cancel_requested',{'to_status':'Cancelling','reason':'user_data_deletion'})\n"
            "        waiting.append(row['name'])\n"
            "frappe.db.commit()\n"
            "print('DSHERP_USER_SETTLE '+json.dumps({'cancelled':cancelled,'waiting':waiting}))")


def _user_inflight_script(user):
    return ("user=" + repr(user) + "\n" + INFLIGHT +
            "rows=inflight(user)\n"
            "print('DSHERP_USER_INFLIGHT '+json.dumps([[row['name'],row['status']] for row in rows]))")


def _user_clear_script(instructions, user):
    """Clear exactly the rows the plan named, and drop the Version rows that copied them.

    The columns are written at the database level on purpose: a Site's own controllers refuse
    to touch an audit record (ruling #3), and this is the one registered exception - it removes
    a person's content from rows that otherwise stay whole. It runs under the User row lock and
    looks again for in-flight runs first: an executor that started between the settling and
    now would write the person's content straight back (R6)."""
    return ("plan=json.loads(" + repr(json.dumps(instructions)) + ")\n"
            "user=" + repr(user) + "\n" + INFLIGHT +
            "frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))\n"
            "inflight=[row['name'] for row in inflight(user)]\n"
            "if inflight:\n"
            "    frappe.db.rollback()\n"
            "    print('DSHERP_USER_DELETE '+json.dumps({'refused':inflight}))\n"
            "    raise SystemExit(0)\n"
            "cleared={};versions=0;missing=0\n"
            "for doctype,spec in plan.items():\n"
            "    changed=0\n"
            "    for name in spec['names']:\n"
            "        if not frappe.db.exists(doctype,name):\n"
            "            missing+=1;continue\n"
            "        frappe.db.set_value(doctype,name,spec['columns'])\n"
            "        changed+=1\n"
            "        for row in frappe.get_all('Version',filters={'ref_doctype':doctype,'docname':name},\n"
            "                                  fields=['name','data'],limit_page_length=0):\n"
            "            body=row.get('data') or ''\n"
            "            if any(('\"'+column+'\"') in body for column in spec['columns']):\n"
            "                frappe.db.delete('Version',{'name':row['name']});versions+=1\n"
            "    cleared[doctype]=changed\n"
            "frappe.db.commit()\n"
            "print('DSHERP_USER_DELETE '+json.dumps({'runs':cleared.get('DS Model Run',0),\n"
            "      'conversations':cleared.get('DS Conversation',0),'versions':versions,'missing':missing}))")


def _user_rows(bench, site, user):
    line = _last_line(bench.python(site, _user_read_script(user), timeout=600))
    marker = 'DSHERP_USER_DATA '
    if not line.startswith(marker):
        raise Fault(f'{site} 没有返回该用户的数据')
    return json.loads(line[len(marker):])


def _user_file(resolved, root, kind, site, user):
    slug = re.sub(r'[^A-Za-z0-9._-]', '_', f'{site}-{user}')
    return runtime_dir(resolved, root) / 'user-data' / f"{kind}-{slug}-{time.strftime('%Y%m%d_%H%M%S')}.json"


def export_user_data(resolved, site, user, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Everything one Site holds about one person, as one file (T4).

    Reading only: an export never writes to the Site, so a person asking what is held about
    them cannot, by asking, change it."""
    _user_checked(user)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    bench = _user_site_bench(resolved, root, factory, site)
    found = _user_rows(bench, site, user)
    return _user_export(resolved, root, site, user, found)


def _user_export(resolved, root, site, user, found):
    """Write the export document for rows already read, so an export and the deletion that
    follows it describe the same moment rather than two reads with a gap between them."""
    from dsherp import sessions as sessions_module
    from dsherp import user_data as user_data_module
    document = user_data_module.export_document(
        user=user, site=site, conversations=found['conversations'], runs=found['runs'],
        proposals=found['proposals'], executions=found['executions'])
    document['known_user'] = found['known_user']
    state_root = runtime_dir(resolved, root) / SESSION_ROOT
    document['sessions'] = [str(path) for path in sessions_module.of_conversations(
        state_root, site, [row['name'] for row in found['conversations']])]
    path = _write_json(_user_file(resolved, root, 'export', site, user), document)
    return {'site': site, 'user': user, 'known_user': found['known_user'], 'path': str(path),
            'counts': {key: len(document[key]) for key in ('conversations', 'runs', 'proposals', 'executions')},
            'sessions': len(document['sessions']),
            'note': '导出文件含本人的提问、页面快照与回答，权限 0600；请按交付流程转交，不要留在共享目录。'
                    '运行事件（DS Run Event）不在其中，需要时按运行单独调取。'}


SETTLE_WAIT_SECONDS = 120
SETTLE_POLL_SECONDS = 3
# The deletion's hold ends on its own if the command is killed: wait, clear and remove fit in it.
DELETE_HOLD_SECONDS = 1800


def _settle_user(bench, site, user, *, wait, clock, sleep):
    """No executor may still be acting for this person when their content is cleared: it
    would write the content straight back, and a run stripped of its content mid-flight is
    not a deletion anyone can stand behind (R6). Queued runs are cancelled, running ones are
    asked to stop, and the command waits for the executor to let go - or refuses."""
    settled = _marked(bench, site, _user_settle_script(user), 'DSHERP_USER_SETTLE', timeout=300)
    deadline = clock() + wait
    remaining = _marked(bench, site, _user_inflight_script(user), 'DSHERP_USER_INFLIGHT', timeout=120)
    while remaining and clock() < deadline:
        sleep(SETTLE_POLL_SECONDS)
        remaining = _marked(bench, site, _user_inflight_script(user), 'DSHERP_USER_INFLIGHT', timeout=120)
    return settled, remaining


def delete_user_data(resolved, site, user, *, root=ROOT, runner=subprocess.run, bench_factory=None, confirm=False,
                     wait=SETTLE_WAIT_SECONDS, clock=time.monotonic, sleep=time.sleep):
    """Remove one person's own content from one Site, keeping the accountability facts (T4).

    Without `--confirm` it states the plan and stops. With it, the person's data is exported
    first - a deletion that leaves nothing to answer with is not a deletion anyone can check -
    then their in-flight runs are stopped and waited for, the declared columns are cleared
    under the same lock a new run would need, their version history dropped, and their session
    directories removed. What survives, and the only way to remove it, is named in `residue`."""
    from dsherp import sessions as sessions_module
    from dsherp import user_data as user_data_module
    _user_checked(user)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    bench = _user_site_bench(resolved, root, factory, site)
    found = _user_rows(bench, site, user)
    names = {'DS Conversation': [row['name'] for row in found['conversations']],
             'DS Model Run': [row['name'] for row in found['runs']]}
    state_root = runtime_dir(resolved, root) / SESSION_ROOT
    sessions = sessions_module.of_conversations(state_root, site, names['DS Conversation'])
    plan = user_data_module.plan(user=user, conversations=found['conversations'], runs=found['runs'],
                                 proposals=found['proposals'], executions=found['executions'],
                                 sessions=len(sessions))
    plan['site'] = site
    report = {'site': site, 'user': user, 'known_user': found['known_user'], 'applied': False, 'plan': plan,
              'export': _user_export(resolved, root, site, user, found),
              'cleared': {'runs': 0, 'conversations': 0, 'versions': 0, 'missing': 0},
              'sessions_removed': []}
    if not confirm:
        report['next'] = f'确认后重跑：dsherp-admin delete-user-data {site} {user} --confirm'
        report['path'] = str(_write_json(_user_file(resolved, root, 'delete-plan', site, user), report))
        return report
    from dsherp import backup as backup_module, site_holds
    # The database clear commits and releases the User row lock; the directories are removed
    # after that. Without a hold, a run the person starts in between would be claimed, its
    # executor would write a new scope directory under the same conversation, and the removal
    # would take it (R6). So the Site is held - the same hold a backup window uses: the server
    # refuses claims and the worker claims nothing - from before the settling until the last
    # directory is gone, and only the directories the plan enumerated are removed.
    runtime = runtime_dir(resolved, root)
    with backup_module.operations_lock(resolved, root, 'delete-user-data'):
        flags = _site_flags(bench, site)
        site_holds.hold(runtime, site, 'delete-user-data', ttl_seconds=DELETE_HOLD_SECONDS)
        try:
            _set_flag(bench, site, 'dsherp_hold_until', int(time.time()) + DELETE_HOLD_SECONDS)
            report['hold'] = 'held'
            settled, remaining = _settle_user(bench, site, user, wait=wait, clock=clock, sleep=sleep)
            report['settled'] = settled
            if remaining:
                report['inflight'] = remaining
                report['path'] = str(_write_json(_user_file(resolved, root, 'delete-blocked', site, user), report))
                raise Fault(f'{user} 在 {site} 仍有 {len(remaining)} 个运行未结束（已请求取消，等了 {wait} 秒）；'
                            '执行者放手后再重跑，或先停 worker。本次未清除任何内容，导出文件已在 ' + report['export']['path'])
            instructions = {doctype: {'columns': user_data_module.cleared(doctype), 'names': names[doctype]}
                            for doctype in names if names[doctype]}
            if instructions:
                line = _last_line(bench.python(site, _user_clear_script(instructions, user), timeout=900))
                marker = 'DSHERP_USER_DELETE '
                if not line.startswith(marker):
                    raise Fault(f'{site} 没有确认清除结果；请核对该站现状后再重跑')
                report['cleared'] = json.loads(line[len(marker):])
                if report['cleared'].get('refused'):
                    report['inflight'] = report['cleared']['refused']
                    report['path'] = str(_write_json(_user_file(resolved, root, 'delete-blocked', site, user), report))
                    raise Fault(f'{user} 在清除前一刻又有运行进入在途（{len(report["inflight"])} 个）；本次未清除任何内容，请重跑')
            report['sessions_removed'] = sessions_module.remove_scopes(state_root, site, sessions)
        finally:
            _set_flag(bench, site, 'dsherp_hold_until', flags.get('dsherp_hold_until', 0))
            site_holds.release(runtime, site)
            report['hold'] = 'released'
    report['applied'] = True
    report['path'] = str(_write_json(_user_file(resolved, root, 'delete', site, user), report))
    return report


CREDENTIAL_REPORT = ("from frappe.utils import now_datetime\n"
                     "rows=frappe.get_all('DS Business Credential',fields=['user','api_key','issued_at',"
                     "'expires_at','version','revoked','legacy','issued_for'],limit_page_length=0,"
                     "order_by='user asc') if frappe.db.exists('DocType','DS Business Credential') else []\n"
                     "recorded={row['user'] for row in rows}\n"
                     "service={'Administrator','Guest',frappe.conf.get('dsherp_runtime_user')}\n"
                     "keyed=frappe.get_all('User',filters={'enabled':1},fields=['name','api_key'],limit_page_length=0)\n"
                     "unrecorded=sorted(row['name'] for row in keyed\n"
                     "                  if row['api_key'] and row['name'] not in recorded and row['name'] not in service)\n"
                     "print('DSHERP_CREDENTIALS '+json.dumps({'now':str(now_datetime()),\n"
                     "      'service':sorted(name for name in service if name),'rows':rows,\n"
                     "      'unrecorded':unrecorded},default=str))")


def _credential_issue_script(user, enterprise):
    return ("from dsherp_bridge import credentials\n"
            "pair=credentials.issue(" + repr(user) + ",issued_for=" + repr(enterprise or 'operator') + ")\n"
            "frappe.db.commit()\n"
            "print('DSHERP_CREDENTIAL_ISSUED '+json.dumps(pair))")


def _credential_store_script(site, user, pair):
    """Store the pair on the platform's membership. The secret travels on stdin only."""
    return ("pair=json.loads(" + repr(json.dumps(pair)) + ")\n"
            "from frappe.utils import now_datetime\n"
            "name=None\n"
            "for row in frappe.get_all('DS Membership',filters={'erp_user':" + repr(user) + ",'enabled':1},\n"
            "                          fields=['name','enterprise'],limit_page_length=0):\n"
            "    if frappe.db.get_value('DS Enterprise',row['enterprise'],'site')==" + repr(site) + ":\n"
            "        name=row['name'];enterprise=row['enterprise'];break\n"
            "if name is None:\n"
            "    print('DSHERP_CREDENTIAL_STORED '+json.dumps({'membership':None,'enterprise':None}))\n"
            "else:\n"
            "    document=frappe.get_doc('DS Membership',name)\n"
            "    document.api_key=pair['api_key'];document.api_secret=pair['api_secret']\n"
            "    document.credential_issued_at=now_datetime();document.credential_expires_at=pair['expires_at']\n"
            "    document.credential_version=int(pair['version'])\n"
            "    document.save(ignore_permissions=True);frappe.db.commit()\n"
            "    print('DSHERP_CREDENTIAL_STORED '+json.dumps({'membership':name,'enterprise':enterprise}))")


def _marked(bench, site, script, marker, timeout=300):
    line = _last_line(bench.python(site, script, timeout=timeout))
    if not line.startswith(marker + ' '):
        raise Fault(f'{site} 没有返回 {marker} 结果')
    return json.loads(line[len(marker) + 1:])


def credentials_report(resolved, site, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """What credentials this Site has lent out, and until when (S2).

    The Site reports its own clock: its datetimes are naive local times, and judging them
    against the host's clock would call a live credential dead the moment the two differ."""
    from dsherp import business_credentials as policy
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    bench = _user_site_bench(resolved, root, factory, site)
    answer = _marked(bench, site, CREDENTIAL_REPORT, 'DSHERP_CREDENTIALS')
    now = policy.moment(answer['now'])
    rows = [dict(row, state=policy.state(row, now)) for row in answer['rows']]
    return {'site': site, 'now': answer['now'], 'credentials': rows,
            'unrecorded': answer['unrecorded'], 'service': answer['service'],
            'stale': sum(1 for row in rows if row['state'] in ('expired', 'revoked')),
            'window_hours': policy.TTL_HOURS}


def issue_credential(resolved, site, user, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Issue a short-lived credential for one business user and hand it to the platform.

    The operator path for a member who cannot log in through the platform to renew, and the
    way a freshly provisioned identity gets its first window. The secret goes from the Site
    to the platform over stdin and is never returned here."""
    _user_checked(user)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    bench = _user_site_bench(resolved, root, factory, site)
    platform = factory('platform')
    pair = _marked(bench, site, _credential_issue_script(user, 'operator'), 'DSHERP_CREDENTIAL_ISSUED')
    stored = _marked(platform, resolved['platform_site'],
                     _credential_store_script(site, user, pair), 'DSHERP_CREDENTIAL_STORED')
    return {'site': site, 'user': user, 'version': pair['version'], 'expires_at': pair['expires_at'],
            'api_key': pair['api_key'], 'stored': stored,
            'note': ('平台已收到新凭据' if stored.get('membership')
                     else '该业务用户在平台上没有启用中的绑定，凭据只存在于业务站')}


RUNTIME_USER_SCRIPT = "print(frappe.conf.get('dsherp_runtime_user') or '')"


def _rotate_oauth_script(slug, callback):
    return (f"app_name={'DSHERP ' + slug + ' Desk'!r};callback={callback!r}\n"
            "import secrets as _s\n"
            "name=frappe.db.get_value('OAuth Client',{'app_name':app_name},'name')\n"
            # A client renamed by hand is still that tenant's client: its callback says so.
            "name=name or frappe.db.get_value('OAuth Client',{'default_redirect_uri':callback},'name')\n"
            "if not name:\n"
            "    raise SystemExit('该租户的 OAuth Client 尚未创建，先 provision-tenant')\n"
            "client=frappe.get_doc('OAuth Client',name)\n"
            "client.client_secret=_s.token_urlsafe(32);client.save();frappe.db.commit()\n"
            "print(json.dumps({'state':'rotated','client_id':client.client_id,"
            "'client_secret':client.get_password('client_secret')}))")


def _register_oauth_script(credentials):
    return ("values=json.loads(" + repr(json.dumps(credentials)) + ")\n"
            "name=frappe.db.get_value('Social Login Key',{'provider_name':'DSHERP Platform'},'name')\n"
            "if not name:\n"
            "    raise SystemExit('该业务站尚未登记平台登录，先 provision-tenant')\n"
            "doc=frappe.get_doc('Social Login Key',name)\n"
            "doc.client_id=values['client_id'];doc.client_secret=values['client_secret']\n"
            "doc.save();frappe.db.commit()\n"
            "print(json.dumps({'state':'updated'}))")


def _profile_rows(path, site):
    """The rows in this worker profile that run as this Site's identity.

    Both shapes the worker itself accepts: a `sites` list, and the older flat profile that is
    one site on its own."""
    path = Path(path)
    if not path.exists():
        raise Fault(f'找不到 worker profile {path}')
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise Fault(f'worker profile 不是有效的 JSON：{path}') from error
    rows = document['sites'] if isinstance(document.get('sites'), list) else [document]
    matching = [row for row in rows if isinstance(row, dict) and row.get('site') == site]
    if not matching:
        raise Fault(f'worker profile 里没有 {site}：{path}')
    return document, matching


def _profile_pair(path, site, pair):
    """Put a rotated identity where the worker reads it, without touching anything else."""
    document, rows = _profile_rows(path, site)
    for row in rows:
        row['api_key'] = pair['api_key']
        row['api_secret'] = pair['api_secret']
    _write_private_json(Path(path), document)
    return [row['site'] for row in rows]


def _write_private_json(path, payload):
    temporary = path.with_name('.' + path.name + f'.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def _secret_fallback(resolved, root, site, at):
    directory = runtime_dir(resolved, root) / 'rotations'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"runtime-{site}-{at.strftime('%Y%m%d_%H%M%S')}.json"


def rotate(resolved, kind, *, target=None, value=None, file=None, profile=None, print_secret=False,
           root=ROOT, runner=subprocess.run, bench_factory=None):
    """Replace one long-lived credential and write the change into the ledger (S4/S9).

    Three kinds, each rotated where it lives: the provider key in the file the worker reads,
    a Site's own execution identity on that Site, and a tenant's OAuth client secret on the
    platform and on the business Site that trusts it. The ledger records the version, the
    time and a fingerprint - never the value, which is why an operator can read it.

    None of these end a member's session: `runtime_revision` deliberately does not bind the
    provider key, the execution identity belongs to the worker, and the OAuth secret is only
    used while logging in."""
    from dsherp import rotation
    if kind not in rotation.MAX_AGE_DAYS:
        raise Fault('可轮换的类别：' + '、'.join(rotation.KINDS))
    ledger = runtime_dir(resolved, root) / 'rotations.json'
    rows = rotation.read(ledger)
    factory = bench_factory or (lambda name: Bench(resolved, name, root=root, runner=runner))
    at = datetime.datetime.now()
    if kind == 'provider':
        path = Path(file) if file else Path(root) / '.env'
        if not path.exists():
            raise Fault(f'找不到 provider 配置文件 {path}；用 --file 指定 worker 单元读的那个')
        if not value:
            raise Fault('新的 provider key 必须从标准输入给出：dsherp-admin rotate provider < key.txt')
        current = _value_of(path, 'DEEPSEEK_API_KEY')
        if current == value.strip():
            raise Fault('新值与当前值相同，这不是一次轮换')
        try:
            previous = rotation.replace_value(path, 'DEEPSEEK_API_KEY', value.strip())
        except ValueError as error:
            raise Fault(str(error))
        item = rotation.entry(kind=kind, target='host', value=value.strip(), previous=previous,
                              version=rotation.next_version(rows, kind, 'host'), at=at)
        rotation.record(ledger, item)
        return {'kind': kind, 'target': 'host', 'version': item['version'],
                'effective_at': item['effective_at'], 'fingerprint': item['fingerprint'],
                'file': str(path),
                'next': '重启 worker 单元让新 key 生效：systemctl restart dsherp-context-worker'}
    if kind == 'runtime':
        site = target or resolved['platform_site']
        bench = _user_site_bench(resolved, root, factory, site)
        # Whoever the Site says its execution identity is. Deriving the name from the Site
        # would rotate a user that does not exist on a Site provisioned any other way, and
        # report success for a key nobody uses.
        user = _last_line(bench.python(site, RUNTIME_USER_SCRIPT, timeout=120)).strip()
        if not user or user == 'None':
            raise Fault(f'{site} 的 site_config 里没有 dsherp_runtime_user，先确认这个站是否已开通')
        # More than one profile may name this identity; a rotation that updates one and
        # leaves the other is a worker that dies at its next claim. Every file is checked
        # before the key changes: a secret is issued once, and a write that fails afterwards
        # leaves a credential nobody holds.
        paths = [profile] if isinstance(profile, (str, Path)) else list(profile or [])
        # A secret is issued once. It needs somewhere to go before it exists: a profile the
        # worker reads, or the operator's own hands (--print-secret). With neither, the old
        # key would be dead and the new one held by nobody (R3).
        if not paths and not print_secret:
            raise Fault('runtime 轮换需要交付目的地：--profile <worker profile>（可多次）或 --print-secret；'
                        '否则新密钥无人持有，而旧密钥已经作废')
        for path in paths:
            _profile_rows(path, site)
        # The recovery copy is opened - directory made, file created privately - before the
        # key changes. A destination that cannot be written is discovered while the old key
        # still works, not after it is dead (R3).
        try:
            keep = _secret_fallback(resolved, root, site, at)
            descriptor = os.open(keep, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            raise Fault(f'无法准备密钥恢复副本 {runtime_dir(resolved, root) / "rotations"}：{error}；'
                        '未签发新密钥，旧密钥仍然有效') from error
        issued = ensure_runtime_identity(bench, site, user, rotate=True)
        if 'api_secret' not in issued:
            os.close(descriptor)
            keep.unlink(missing_ok=True)
            raise Fault(f'{site} 没有返回新的运行身份凭据')
        record_text = json.dumps({'site': site, 'user': user, 'api_key': issued['api_key'],
                                  'api_secret': issued['api_secret'],
                                  'issued_at': at.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False, indent=1)
        try:
            with os.fdopen(descriptor, 'w') as handle:
                handle.write(record_text + '\n')
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            # The disk failed between the check and the write. The secret is not lost: it is
            # handed to the operator here, once, because nowhere else can take it.
            raise Fault(f'新密钥已签发，但恢复副本 {keep} 写入失败（{error}）。'
                        f'请立即记下并写入 worker profile：api_key={issued["api_key"]} '
                        f'api_secret={issued["api_secret"]}') from error
        updated = []
        try:
            for path in paths:
                updated += _profile_pair(path, site, issued)
        except (Fault, OSError, ValueError) as error:
            raise Fault(f'新密钥已签发但写入 worker profile 失败：{error}\n'
                        f'它保存在 {keep}（仅本用户可读）；手工写入 profile 后删除该文件') from error
        item = rotation.entry(kind=kind, target=site, value=issued['api_secret'], previous=None,
                              version=rotation.next_version(rows, kind, site), at=at)
        rotation.record(ledger, item)
        report = {'kind': kind, 'target': site, 'user': user, 'version': item['version'],
                  'effective_at': item['effective_at'], 'fingerprint': item['fingerprint'],
                  'api_key': issued['api_key'], 'profile_updated': bool(updated),
                  'profile_sites': updated or []}
        if print_secret:
            report['api_secret'] = issued['api_secret']
            report['secret_file'] = str(keep)
            report['next'] = f'把 api_secret 写进 worker profile 后重启 worker 单元，并删除 {keep}'
        else:
            keep.unlink(missing_ok=True)
            report['next'] = '重启 worker 单元'
        return report
    slug = target
    if not slug or not any(row['slug'] == slug for row in load_tenants(resolved, root)):
        raise Fault('oauth-client 轮换需要给出当前清单里的租户 slug')
    site = deploy_env.site_name(resolved, slug)
    platform = factory('platform')
    credentials = json.loads(_last_line(platform.python(
        site_for_platform(resolved), _rotate_oauth_script(slug, deploy_env.callback_url(resolved, slug)))))
    tenant = factory('tenant')
    registered = json.loads(_last_line(tenant.python(site, _register_oauth_script(credentials))))
    item = rotation.entry(kind=kind, target=slug, value=credentials['client_secret'], previous=None,
                          version=rotation.next_version(rows, kind, slug), at=at)
    rotation.record(ledger, item)
    return {'kind': kind, 'target': slug, 'site': site, 'version': item['version'],
            'effective_at': item['effective_at'], 'fingerprint': item['fingerprint'],
            'client': credentials['state'], 'business_site': registered['state'],
            'next': '已在两侧生效；进行中的登录会失败一次，重新登录即可'}


def _value_of(path, key):
    for line in Path(path).read_text().splitlines():
        if line.split('=', 1)[0].strip() == key and '=' in line:
            return line.split('=', 1)[1].strip()
    return None


def rotation_targets(resolved, root=ROOT):
    """What this deployment has that needs rotating, by kind."""
    rows = load_tenants(resolved, root)
    return {'provider': ['host'],
            'runtime': [row['site'] for row in rows] + [resolved['platform_site']],
            'oauth-client': [row['slug'] for row in rows]}


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
    quota_parser = sub.add_parser('tenant-quota',
                                  help='查看或设置某个租户站的用量额度（0 表示不限；不给参数就是只读）')
    quota_parser.add_argument('slug')
    quota_parser.add_argument('--user-daily-model-calls', type=int, default=None, metavar='N',
                              help='每个用户每天的模型调用次数上限（在飞的运行也计入）；0 表示不限')
    quota_parser.add_argument('--site-monthly-tokens', type=int, default=None, metavar='N',
                              help='本站本月已结算 token 上限（在飞的运行尚未计入）；0 表示不限')
    sub.add_parser('render-ingress', help='按当前租户清单重新渲染入口配置')
    sub.add_parser('agent-firewall', help='打印把运行容器挡在宿主之外的 INPUT 规则；生产由 dsherp-agent-firewall.service 在开机时应用')
    backup_parser = sub.add_parser('backup', help='对平台站与全部租户站在稳定窗口内生成四件套与核验快照，暂存为备份集；--sync 随后异地同步')
    backup_parser.add_argument('--sync', action='store_true', help='生成后立即把未完成的备份集推到异地并核对配对')
    backup_parser.add_argument('--site', action='append', default=[], help='只备份这些站（缺省是平台站与全部租户站），可重复')
    restore_parser = sub.add_parser('restore-site', help='在新主机上从异地备份集恢复一个站（G3 冷启动；见 runbook 第 12 节）')
    restore_parser.add_argument('site')
    restore_parser.add_argument('--set', dest='set_id', metavar='SET_ID', help='指定备份集；缺省用该站最新的完整配对')
    sessions_parser = sub.add_parser('sessions', help='查看宿主上的原生会话目录；--sweep 清掉超过保留期的（默认 90 天）')
    sessions_parser.add_argument('--sweep', action='store_true')
    sessions_parser.add_argument('--days', type=int, default=None)
    rotate_parser = sub.add_parser('rotate',
                                   help='轮换一类长期凭据并记入账簿：provider（模型 key，从标准输入读）、'
                                        'runtime（某站点的运行身份）、oauth-client（某租户的平台登录密钥）')
    rotate_parser.add_argument('kind', choices=('provider', 'runtime', 'oauth-client'))
    rotate_parser.add_argument('target', nargs='?', help='runtime 给站点名，oauth-client 给租户 slug')
    rotate_parser.add_argument('--file', default=None, help='provider：worker 单元读的那个 .env')
    rotate_parser.add_argument('--profile', action='append', default=None,
                                   help='runtime：把新凭据写回这个 worker profile（可给多次）')
    rotate_parser.add_argument('--print-secret', action='store_true',
                                   help='runtime：没有 profile 时把新密钥打印一次（并留一份 0600 的副本文件）')
    credentials_parser = sub.add_parser('credentials',
                                        help='查看某站点借出的短期业务凭据及其有效期；--issue 为某个业务用户重新签发并交给平台')
    credentials_parser.add_argument('site')
    credentials_parser.add_argument('--issue', metavar='USER', default=None,
                                    help='为该业务用户签发新凭据（无法通过平台登录续签时的运维路径）')
    export_user = sub.add_parser('export-user-data', help='导出某个站点里属于某个人的全部数据（只读，不改站点）')
    export_user.add_argument('site')
    export_user.add_argument('user', help='站点里的登录名，通常是邮箱')
    delete_user = sub.add_parser('delete-user-data',
                                 help='清除某人在某站点的个人内容：先导出，--confirm 才执行；'
                                      '运行、提案、执行与事件等追责事实按裁决 #10 保留')
    delete_user.add_argument('site')
    delete_user.add_argument('user', help='站点里的登录名，通常是邮箱')
    delete_user.add_argument('--confirm', action='store_true', help='真正执行清除')
    delete_user.add_argument('--wait', type=int, default=SETTLE_WAIT_SECONDS,
                             help='等待该用户在途运行结束的秒数（先取消/请求停止，再等执行者放手）')
    usage_parser = sub.add_parser('usage', help='按月汇总每个站点的真实用量（模型调用、token、时长）')
    usage_parser.add_argument('month', help='YYYY-MM')
    migrate_drill_parser = sub.add_parser('migrate-drill', help='把旧构建的备份集恢复进运行新构建的隔离栈并 migrate，按 G2 口径判定（发布前跑）')
    migrate_drill_parser.add_argument('tag', help='要验证的目标 tag')
    migrate_drill_parser.add_argument('site', nargs='*', help='缺省是平台站与全部租户站')
    migrate_drill_parser.add_argument('--discard-failed', action='store_true')
    drill_parser = sub.add_parser('restore-drill', help='在隔离栈里从最新的完整异地备份集恢复并核验（每周；用毕删除该栈）')
    drill_parser.add_argument('site', nargs='*', help='缺省是平台站与全部租户站')
    drill_parser.add_argument('--discard-failed', action='store_true', help='清掉上一次失败演练留下的隔离栈后再演练')
    notify_parser = sub.add_parser('notify-failure', help='systemd 的 OnFailure 调用：把失败写进 journal 并投递 webhook（worker 停止时的兜底）')
    notify_parser.add_argument('unit')
    sub.add_parser('backup-init', help='一次性初始化两个异地备份仓库（幂等）')
    sub.add_parser('backup-sync', help='把未完成的备份集推到异地并核对配对，按保留策略淘汰，抽读数据校验')
    release_parser = sub.add_parser('release', help='发布到 prod.env 里的 tag：静默站点、备份并归档、快照、migrate、快照、逐字段比对')
    release_parser.add_argument('tag')
    release_parser.add_argument('--from', dest='from_tag', metavar='TAG',
                                help='升级前运行的 tag；有 current.json 记录时可省略，第一次发布必填')
    release_parser.add_argument('--manifest', metavar='FILE',
                                help=f'该 tag 的发布清单；缺省找 infra/releases/<tag>.json，再找 <runtime>/{MANIFESTS}/<tag>.json')
    resume_parser = sub.add_parser('resume-site', help='解除某站的维护模式与调度暂停（发布或回滚未自动解除时，由人确认后执行）')
    resume_parser.add_argument('site')
    forget_parser = sub.add_parser('forget-release', help='删除某个 tag 的宿主侧发布记录（升级前基线与备份指针）；容器内归档不动')
    forget_parser.add_argument('tag')
    rollback_parser = sub.add_parser('rollback', help='撤销一次发布：恢复它归档的升级前备份并与升级前快照比对')
    rollback_parser.add_argument('tag', help='要撤销的发布 tag（release 时用的那个）')
    rollback_parser.add_argument('--backup', action='append', default=[], metavar='SITE=FILE',
                                 help='改用指定的数据库转储（*-database.sql.gz，同名 files tar 一并恢复），可重复')
    rollback_parser.add_argument('--manifest', metavar='FILE',
                                 help='升级前 tag 的发布清单；只在发布记录没有 previous_images 时需要（第一次发布，或 --from 与 current.json 不一致）')
    snapshot_parser = sub.add_parser('snapshot', help='读取一个站的数据快照到文件（演练与排查用）')
    snapshot_parser.add_argument('site')
    snapshot_parser.add_argument('--out', required=True)
    snapshot_parser.add_argument('--like', metavar='SNAPSHOT', help='按这份快照的哈希列集合求哈希，使两者可比')
    compare_parser = sub.add_parser('compare', help='比对两个快照文件；有未声明差异退出码 1')
    compare_parser.add_argument('before')
    compare_parser.add_argument('after')
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
        if arguments.command == 'tenant-quota':
            given = {'user_daily_model_calls': arguments.user_daily_model_calls,
                     'site_monthly_tokens': arguments.site_monthly_tokens}
            _print(tenant_quota(resolved, arguments.slug,
                                {key: value for key, value in given.items() if value is not None}))
            return 0
        if arguments.command == 'agent-firewall':
            _print(agent_firewall_rules(resolved))
            return 0
        if arguments.command == 'render-ingress':
            _print({'rendered': str(render_ingress(resolved, load_tenants(resolved)))})
            return 0
        if arguments.command == 'backup':
            from dsherp import backup as backup_module
            report = backup_module.backup(resolved, sync=arguments.sync, sites=arguments.site or None)
            _print({key: value for key, value in report.items() if key != 'sets'}
                   | {'sets': {site: doc['set_id'] for site, doc in report['sets'].items()}})
            return 0 if report['ok'] else 1
        if arguments.command == 'restore-site':
            from dsherp import restore_drill as drill_module
            _print(drill_module.restore_site(resolved, arguments.site, set_id=arguments.set_id))
            return 0
        if arguments.command == 'sessions':
            report = sessions_report(resolved, days=arguments.days, sweep=arguments.sweep)
            _print(report)
            return 0
        if arguments.command == 'rotate':
            # The new provider key never travels on a command line: bench and shell
            # histories both keep argv, and this one is read from standard input.
            value = sys.stdin.read().strip() if arguments.kind == 'provider' else None
            _print(rotate(resolved, arguments.kind, target=arguments.target, value=value,
                          file=arguments.file, profile=arguments.profile, print_secret=arguments.print_secret))
            return 0
        if arguments.command == 'credentials':
            if arguments.issue:
                _print(issue_credential(resolved, arguments.site, arguments.issue))
                return 0
            report = credentials_report(resolved, arguments.site)
            _print(report)
            # An API key nobody recorded a window for is a finding, not a detail.
            return 0 if not report['unrecorded'] else 1
        if arguments.command == 'export-user-data':
            _print(export_user_data(resolved, arguments.site, arguments.user))
            return 0
        if arguments.command == 'delete-user-data':
            report = delete_user_data(resolved, arguments.site, arguments.user,
                                      confirm=arguments.confirm, wait=arguments.wait)
            _print(report)
            # A command named delete that deleted nothing must not exit 0.
            return 0 if report['applied'] else 1
        if arguments.command == 'usage':
            report = usage_report(resolved, arguments.month)
            _print(report)
            return 1 if report['unreachable'] else 0
        if arguments.command == 'migrate-drill':
            from dsherp import restore_drill as drill_module
            report = drill_module.migrate_drill(resolved, arguments.tag, arguments.site or None,
                                                discard_failed=arguments.discard_failed)
            _print(report)
            return 0 if report['ok'] else 1
        if arguments.command == 'restore-drill':
            from dsherp import restore_drill as drill_module
            report = drill_module.restore_drill(resolved, arguments.site or None,
                                                discard_failed=arguments.discard_failed)
            _print(report)
            return 0 if report['ok'] else 1
        if arguments.command == 'notify-failure':
            from dsherp import backup as backup_module
            outcome = backup_module.notify_failure(resolved, arguments.unit)
            _print(outcome)
            return 0 if outcome['webhook'] in ('posted', 'not configured') else 1
        if arguments.command == 'backup-init':
            from dsherp import backup as backup_module
            _print(backup_module.backup_init(resolved))
            return 0
        if arguments.command == 'backup-sync':
            from dsherp import backup as backup_module
            report = backup_module.backup_sync(resolved)
            _print(report)
            return 0 if report['ok'] else 1
        if arguments.command == 'release':
            report = release(resolved, arguments.tag, from_tag=arguments.from_tag, manifest=arguments.manifest)
            _print({key: value for key, value in report.items() if key != 'sites'} | {
                'sites': {site: {'clean': row['comparison']['clean'], 'summary': row['comparison']['summary'],
                                 'backup': row['backup'], 'seconds': row['seconds']}
                          for site, row in report['sites'].items()}})
            return 0 if report['clean'] else 1
        if arguments.command == 'rollback':
            pairs = {}
            for entry in arguments.backup:
                site, separator, file = entry.partition('=')
                if not separator:
                    raise Fault('备份参数格式为 SITE=FILE')
                pairs[site] = file
            report = rollback(resolved, arguments.tag, backups=pairs or None, manifest=arguments.manifest)
            _print({key: value for key, value in report.items() if key != 'sites'} | {
                'sites': {site: {'clean': row['comparison']['clean'], 'summary': row['comparison']['summary'],
                                 'seconds': row['seconds']} for site, row in report['sites'].items()}})
            return 0 if report['clean'] else 1
        if arguments.command == 'resume-site':
            _print(resume_site(resolved, arguments.site))
            return 0
        if arguments.command == 'forget-release':
            _print(forget_release(resolved, arguments.tag))
            return 0
        if arguments.command == 'snapshot':
            like = json.loads(Path(arguments.like).read_text()) if arguments.like else None
            target = _write_json(Path(arguments.out), take_snapshot(resolved, arguments.site, like=like))
            _print({'site': arguments.site, 'path': str(target)})
            return 0
        if arguments.command == 'compare':
            report = compare_snapshots(json.loads(Path(arguments.before).read_text()),
                                       json.loads(Path(arguments.after).read_text()))
            _print(report)
            return 0 if report['clean'] else 1
    except Fault as fault:
        print(str(fault), file=sys.stderr)
        return 2
    raise AssertionError('unreachable')


if __name__ == '__main__':
    raise SystemExit(main())
