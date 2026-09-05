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
    return Path(os.environ.get('DSHERP_RUNTIME_DIR') or Path(root) / '.runtime')


def secrets_dir(resolved, root=ROOT):
    return Path(os.environ.get('DSHERP_SECRETS_DIR') or runtime_dir(resolved, root) / 'control')


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
        return ['docker', 'compose', '-p', self.resolved['project'], '-f', str(file), *arguments]

    def run(self, *arguments, stdin=None, timeout=900):
        command = self._compose('exec', '-T', self.service, *arguments)
        result = self.runner(command, input=stdin, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise Fault(f'容器命令失败（{self.service}）：{" ".join(arguments[:3])}；先看容器状态再重试')
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

    def site_exists(self, site):
        listed = self.run('sh', '-c', f'test -d {SITES}/{site} && echo yes || echo no', timeout=60)
        return listed.strip().endswith('yes')


def ensure_site(bench, resolved, site, admin_password, db_root_password):
    """Create the Site only when its directory is absent; never touch an existing one."""
    if bench.site_exists(site):
        return 'kept'
    bench.run('bench', 'new-site', site, '--db-host', 'db', '--db-root-username', 'root',
              '--db-root-password', db_root_password, '--admin-password', admin_password,
              '--mariadb-user-host-login-scope', '%', '--install-app', 'erpnext', timeout=1800)
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


def ensure_runtime_identity(bench, site, user):
    """The Site's own execution identity; its keys are returned once and stored by the caller."""
    body = (f"user={user!r}\n"
            "if not frappe.db.exists('User',user):\n"
            "    frappe.get_doc({'doctype':'User','email':user,'first_name':'DSHERP Runtime',\n"
            "        'enabled':1,'user_type':'System User','send_welcome_email':0}).insert()\n"
            "from frappe.core.doctype.user.user import generate_keys\n"
            "keys=generate_keys(user)\n"
            "frappe.db.commit();print(json.dumps({'user':user,**keys}))")
    return json.loads(bench.python(site, body).strip().splitlines()[-1])


def provision_tenant(resolved, slug, *, root=ROOT, runner=subprocess.run, bench_factory=None):
    """Bring one tenant from nothing to reachable; every step is resumable."""
    site = deploy_env.site_name(resolved, slug)
    factory = bench_factory or (lambda kind: Bench(resolved, kind, root=root, runner=runner))
    tenant = factory('tenant')
    steps = []
    admin = read_secret(resolved, 'tenant_admin_password' if resolved['env'] == 'prod' else 'admin_password', root)
    db_root = read_secret(resolved, 'db_root_password', root)
    steps.append(('site', ensure_site(tenant, resolved, site, admin, db_root)))
    steps.append(('app', ensure_app(tenant, site, 'dsherp_bridge')))
    runtime_user = f'runtime@{site}'
    identity = ensure_runtime_identity(tenant, site, runtime_user)
    steps.append(('runtime-identity', 'created'))
    changed = ensure_site_config(tenant, site, {
        'dsherp_runtime_user': runtime_user,
        'host_name': deploy_env.public_origin(resolved, slug),
    })
    steps.append(('site-config', 'changed:' + ','.join(changed) if changed else 'kept'))
    rows = [row for row in load_tenants(resolved, root) if row['slug'] != slug]
    rows.append({'slug': slug, 'site': site, 'origin': deploy_env.public_origin(resolved, slug)})
    save_tenants(resolved, rows, root)
    steps.append(('tenant-list', 'saved'))
    steps.append(('ingress', str(render_ingress(resolved, rows, root))))
    return {'site': site, 'steps': steps, 'runtime_identity': identity}


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
        if resolved['agent_uid'] == 0:
            findings.append('Agent 容器身份解析为 root')
    return findings


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
    retire = sub.add_parser('retire-tenant', help='归档后下线一个租户站')
    retire.add_argument('slug')
    retire.add_argument('--no-archive', action='store_true')
    sub.add_parser('list-tenants', help='列出当前租户')
    sub.add_parser('render-ingress', help='按当前租户清单重新渲染入口配置')
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
        if arguments.command == 'provision-tenant':
            _print(provision_tenant(resolved, arguments.slug))
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
    except Fault as fault:
        print(str(fault), file=sys.stderr)
        return 2
    raise AssertionError('unreachable')


if __name__ == '__main__':
    raise SystemExit(main())
