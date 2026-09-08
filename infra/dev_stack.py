#!/usr/bin/env python3
"""从零拉起四站开发栈的唯一入口；也是拆掉它的唯一入口。

infra/ 下的开通脚本各自 fail-closed 且不可续跑：发现站点、用户或凭据文件已存在就停下，不猜。
这个驱动是它们上面的"可续跑层"：把做完的步骤记进台账（.runtime/dev-stack.json），跳过一步之前
先向栈本身核实该步的结果确实在（探针）。台账与探针必须一致；不一致就停下，说出是哪一步、去查
什么——盲目重跑一个 fail-closed 脚本，正是这些脚本被写成拒绝的事。

台账里没有任何密钥值：只有步骤名、时间戳和该步产出的凭据文件名。

Those provisioning scripts each hardcode `<repo>/.runtime` (or a CWD-relative `.runtime`) and a
bare `docker compose -f infra/compose.validation.yml` with the file's own project name. This
driver therefore refuses to run against any other runtime directory or compose project rather
than silently probing a directory the scripts will never write to.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

# Run either as `python infra/dev_stack.py` or `python -m infra.dev_stack`; the first puts
# infra/ on sys.path, not the repository root (same bootstrap as infra/load_runs.py).
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dsherp import admin, deploy_env, native_tests  # noqa: E402  # after the sys.path bootstrap above


ROOT = deploy_env.ROOT
COMPOSE_FILE = 'infra/compose.validation.yml'
# The one project name the provisioning scripts assume (compose.validation.yml's own `name:`).
PROJECT = 'dsherp-validation'
# 与 compose 里三处 image: 完全一致。这里只核对存在，从不拉取（pull_policy: never）。
IMAGES = (
    deploy_env.BASE_IMAGE,
    'mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3',
    'redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015',
)
AGENT_RUNTIME_VOLUME = 'dsherp-v16-agent-runtime'
# Every profile the development compose file declares. `docker compose down` only touches
# services whose profile is active, and an exited container still holds its volumes - so a
# teardown that forgets one of these leaves the data behind and the next "from zero" rebuild
# quietly starts from the old data. Found by the local rebuild: v16-sites and v16-logs were
# still there afterwards, pinned by two long-exited `scheduled` containers.
PROFILES = ('control', 'scheduled', 'ops')
# The three imports prepare_agent_runtime.sh itself asserts after building the venv.
RUNTIME_IMPORTS = 'import deepseek_harness, mcp, httpx'
HOSTS = ('preview.localhost', 'daily.localhost', 'platform.localhost', 'dsherp-validation.localhost')
HOSTS_LINE = '127.0.0.1 ' + ' '.join(HOSTS)
# compose 的 secrets: 段引用的五个，加上 provision_identity.py 从 /run/dsherp-control 读的两个。
CONTROL_SECRETS = admin.CONTROL_SECRETS_BY_ENV['dev'] + ('platform_admin_password', 'beta_admin_password')
LEDGER_NAME = 'dev-stack.json'
LEDGER_FORMAT = 1
WORKER_PID = 'agent-worker.pid'
WORKER_LABEL = 'com.dsherp.agent-worker-v16'
BENCH_PYTHON = admin.BENCH_PYTHON
# kind -> (compose service, Site). Not admin.SITES, which is the container sites path.
SITE_TARGETS = {'alpha': ('backend', 'dsherp-validation.localhost'),
                'daily': ('backend', 'dsherp-daily.localhost'),
                'platform': ('platform-backend', 'dsherp-platform.localhost'),
                'beta': ('beta-backend', 'dsherp-beta.localhost')}
BACKENDS = ('backend', 'platform-backend', 'beta-backend')
# prepare_dev_backup_volumes.sh docker-execs into these two; they must already be up.
EXEC_TARGETS = ('backend', 'platform-backend')
PINGS = (('alpha', 'http://127.0.0.1:18082/api/method/ping', 'localhost'),
         ('platform', 'http://127.0.0.1:18083/api/method/ping', 'platform.localhost'),
         ('beta', 'http://127.0.0.1:18085/api/method/ping', 'preview.localhost'),
         ('daily', 'http://127.0.0.1:18086/api/method/ping', 'daily.localhost'))
# 口令永远不上 argv：mariadb-admin 从环境变量读，值来自容器里的密钥文件。
DB_PING = 'MYSQL_PWD=$(cat /run/secrets/db_root_password) mariadb-admin ping -h127.0.0.1 -uroot'
REDIS_PING = 'redis-cli ping | grep -q PONG'
PORT_OPEN = "import socket;socket.create_connection(('127.0.0.1',8000),timeout=2).close()"
SECRETS_READABLE = 'cat /run/secrets/db_root_password /run/secrets/validation_admin_password >/dev/null && echo readable'
BACKUP_PRESENT = ('cd /home/frappe/frappe-bench/sites/dsherp-daily.localhost/private/backups 2>/dev/null && '
                  'ls *-database.sql.gz *-site_config_backup.json *-files.tar *-private-files.tar >/dev/null 2>&1 '
                  '&& echo yes || echo no')
# What this repository already calls a secret elsewhere: DS Membership keeps api_key at
# permlevel 1 so an ordinary member cannot read it, and dsherp/run_events.py redacts the same
# set out of event payloads. The scan had api_secret but not api_key, so every api_key value in
# .runtime could ride out to a public artefact unnoticed - a scan that catches half of what the
# repository calls a secret is worse than none, because it reads as proof.
SECRET_KEYS = ('api_key', 'api_secret', 'password', 'client_secret', 'secret',
               'token', 'access_token', 'refresh_token')
Fault = admin.Fault


def _stream(name, text, lines=40):
    """The tail of one stream, said to be that stream and no other.

    The first nightly died eleven minutes into provisioning and the report was eight lines of
    the json module's own frames: no command, no script, no way to tell which call had returned
    nothing. Two streams sharing one small quota means the one that matters can be crowded out
    entirely, and on CI nobody gets to look again."""
    body = (text or '').strip()
    if not body:
        return f'--- {name}：空 ---\n'
    rows = body.splitlines()
    elided = f'（省略前 {len(rows) - lines} 行）\n' if len(rows) > lines else ''
    return f'--- {name}（最后 {min(len(rows), lines)} 行）---\n' + elided + '\n'.join(rows[-lines:]) + '\n'


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


class Stack:
    """Where the repository is, how to run a process, and the benches of the four Sites."""

    def __init__(self, resolved, root=ROOT, runner=subprocess.run, *, resolve=socket.getaddrinfo,
                 http=None, sleep=time.sleep, clock=time.monotonic):
        if resolved.get('env') != 'dev':
            raise Fault('dev_stack 只操作 DSHERP_ENV=dev 的隔离栈')
        if resolved.get('project') != PROJECT:
            raise Fault(f'开发栈的开通脚本都写死了 compose 项目 {PROJECT}（compose.validation.yml 的 name:）；'
                        f'当前 DSHERP_PROJECT 是 {resolved.get("project")!r}，改回去再试')
        self.resolved = resolved
        self.root = Path(root)
        self.runner = runner
        self.resolve = resolve
        self.http = http or build_opener(ProxyHandler({})).open
        self.sleep = sleep
        self.clock = clock
        self.runtime = admin.runtime_dir(resolved, self.root)
        expected = (self.root / '.runtime').resolve()
        if self.runtime.resolve() != expected:
            raise Fault(f'开发栈的开通脚本只写 {expected}；不要设置 DSHERP_RUNTIME_DIR / DSHERP_SECRETS_DIR'
                        f'（现在解析到 {self.runtime}），否则台账与探针看的是另一个目录')

    # ---- processes -------------------------------------------------------------------
    def compose(self, *arguments):
        command = ['docker', 'compose', '-p', self.resolved['project']]
        env_file = deploy_env.env_file('dev', self.root)
        if env_file.exists():
            command += ['--env-file', str(env_file)]
        return command + ['-f', str(self.root / COMPOSE_FILE), *arguments]

    def run(self, command, *, timeout=600, stdin=None):
        io = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
        result = self.runner(command, cwd=str(self.root), text=True, capture_output=True, timeout=timeout, **io)
        if result.returncode:
            raise Fault(f'命令失败（退出码 {result.returncode}）：{" ".join(command)}\n'
                        + _stream('stderr', result.stderr) + _stream('stdout', result.stdout))
        return result.stdout or ''

    def succeeds(self, command, *, timeout=120):
        result = self.runner(command, cwd=str(self.root), text=True, capture_output=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
        return result.returncode == 0

    def host_script(self, name, *arguments):
        # 以文件路径而不是 -m 运行：provision_context_writer.py 靠 sys.path[0] == infra/ 导入同目录模块。
        return self.run([sys.executable, str(self.root / 'infra' / name), *arguments], timeout=1800)

    def compose_run(self, service):
        return self.run(self.compose('--profile', 'control', 'run', '--rm', '--no-deps', service), timeout=1800)

    def running_services(self):
        return sorted(self.run(self.compose('ps', '--services', '--status', 'running'), timeout=60).split())

    # ---- the four Sites ----------------------------------------------------------------
    def bench(self, kind):
        service, _site = SITE_TARGETS[kind]
        return admin.Bench(self.resolved, 'tenant', self.root, self.runner, service=service)

    def site_present(self, kind):
        return self.bench(kind).site_exists(SITE_TARGETS[kind][1])

    def db_probe(self, kind, body):
        out = self.bench(kind).python(SITE_TARGETS[kind][1], body, timeout=120)
        return json.loads(out.strip().splitlines()[-1])

    def volume_exists(self, name):
        return self.succeeds(['docker', 'volume', 'inspect', name])

    def wait_for(self, what, check, *, timeout, interval=5):
        started = self.clock()
        while True:
            if check():
                return
            if self.clock() - started > timeout:
                raise Fault(f'等待 {what} 超过 {timeout} 秒；看 docker compose -f {COMPOSE_FILE} logs')
            self.sleep(interval)

    # ---- ledger ------------------------------------------------------------------------
    @property
    def ledger_path(self):
        return self.runtime / LEDGER_NAME

    def load_ledger(self):
        if not self.ledger_path.exists():
            return {'format': LEDGER_FORMAT, 'steps': {}}
        try:
            data = json.loads(self.ledger_path.read_text())
        except ValueError as error:
            raise Fault(f'台账损坏，不要重建，先看 {self.ledger_path}：{error}')
        if data.get('format') != LEDGER_FORMAT or not isinstance(data.get('steps'), dict):
            raise Fault(f'台账格式不是 {LEDGER_FORMAT}，先看 {self.ledger_path}')
        return data

    def record(self, step):
        """Atomic and private, the way every other file under .runtime is written."""
        ledger = self.load_ledger()
        ledger['steps'][step.name] = {'finished_at': now_iso(), 'produces': list(step.produces)}
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.ledger_path.with_name(f'.{LEDGER_NAME}.{os.getpid()}')
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(ledger, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write('\n')
            os.replace(temporary, self.ledger_path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class Step:
    def __init__(self, name, run, probe=None, produces=(), rerun_safe=False, inspect=''):
        self.name, self.run, self.probe = name, run, probe
        self.produces, self.rerun_safe, self.inspect = tuple(produces), rerun_safe, inspect


# ---- probes: each returns a list of booleans (one per piece of evidence) -----------------
def _file(name):
    return lambda stack: [(stack.runtime / name).is_file()]


def _site(kind):
    return lambda stack: [stack.site_present(kind)]


def _db(kind, body):
    # A question to a Site that does not exist is not "no": the Site itself is the first evidence.
    def probe(stack):
        if not stack.site_present(kind):
            return [False]
        return [stack.db_probe(kind, body)]
    return probe


def _both(*probes):
    return lambda stack: [flag for probe in probes for flag in probe(stack)]


def _runtime_ready(stack):
    """The volume is created before the pip install, so its existence proves nothing. Ask the
    venv the same question prepare_agent_runtime.sh asks - but only once the volume is there,
    because `docker run -v <name>:...` would otherwise create an empty one as a side effect."""
    if not stack.volume_exists(AGENT_RUNTIME_VOLUME):
        return [False]
    return [stack.succeeds(['docker', 'run', '--rm', '--pull=never',
                            '-v', f'{AGENT_RUNTIME_VOLUME}:/opt/runtime:ro',
                            deploy_env.BASE_IMAGE, '/opt/runtime/bin/python', '-c', RUNTIME_IMPORTS],
                           timeout=300)]


def _backup(stack):
    return ['yes' in stack.bench('daily').run('sh', '-c', BACKUP_PRESENT, timeout=60)]


SETUP_COMPLETE = "print(json.dumps(bool(frappe.is_setup_complete())))"


def USER(email):
    return f"print(json.dumps(bool(frappe.db.exists('User', '{email}'))))"


def ENTERPRISE(name):
    return f"print(json.dumps(bool(frappe.db.exists('DS Enterprise', '{name}'))))"


def OAUTH(app_name):
    return f"print(json.dumps(bool(frappe.db.exists('OAuth Client', {{'app_name': '{app_name}'}}))))"


def CONF(key):
    return f"print(json.dumps(bool(frappe.conf.get('{key}'))))"


def _clear_caches(stack):
    for kind in ('alpha', 'daily', 'beta'):
        stack.bench(kind).run('bench', '--site', SITE_TARGETS[kind][1], 'clear-cache', timeout=120)


STEPS = [
    Step('agent-runtime-volume',
         lambda s: s.run(['sh', str(s.root / 'infra/prepare_agent_runtime.sh')], timeout=1800),
         probe=_runtime_ready, rerun_safe=True,
         inspect=f'docker volume inspect {AGENT_RUNTIME_VOLUME}，以及卷里的 /opt/runtime/bin/python'),
    # docker-execs into backend and platform-backend, so it needs them running (see provision()).
    Step('backup-volumes',
         lambda s: s.run(['sh', str(s.root / 'infra/prepare_dev_backup_volumes.sh')], timeout=120),
         rerun_safe=True,
         inspect='backend 与 platform-backend 里的 /home/frappe/backups 与 /home/frappe/backup-secrets'),
    Step('validation-site', lambda s: s.host_script('run_validation_provision.py'),
         probe=_both(_file('erp-users.json'), _site('alpha')),
         produces=('erp-users.json', 'erp-reader.json', 'erp-denied.json'),
         inspect='docker compose exec backend ls /home/frappe/frappe-bench/sites 与 .runtime/erp-*.json'),
    Step('alpha-company', lambda s: s.host_script('provision_validation_company.py', '--target', 'alpha'),
         probe=_db('alpha', SETUP_COMPLETE), inspect='alpha 站 frappe.is_setup_complete()'),
    Step('platform-site', lambda s: s.compose_run('platform-provision'), probe=_site('platform'),
         inspect='docker compose exec platform-backend ls /home/frappe/frappe-bench/sites'),
    Step('platform-users', lambda s: s.host_script('run_identity_seed.py', 'platform'),
         probe=_both(_file('platform-users.json'), _db('platform', USER('member@example.invalid'))),
         produces=('platform-users.json',),
         inspect='平台站 User member@example.invalid 与 .runtime/platform-users.json'),
    Step('platform-setup', lambda s: s.host_script('setup_platform.py'), probe=_db('platform', SETUP_COMPLETE),
         inspect='平台站 frappe.is_setup_complete()'),
    Step('beta-site', lambda s: s.compose_run('beta-provision'), probe=_site('beta'),
         inspect='docker compose exec beta-backend ls /home/frappe/frappe-bench/sites'),
    Step('beta-company', lambda s: s.host_script('provision_validation_company.py', '--target', 'beta'),
         probe=_db('beta', SETUP_COMPLETE), inspect='beta 站 frappe.is_setup_complete()'),
    Step('beta-users', lambda s: s.host_script('run_identity_seed.py', 'beta'),
         probe=_both(_file('beta-users.json'), _db('beta', USER('beta-reader@example.invalid'))),
         produces=('beta-users.json',),
         inspect='beta 站 User beta-reader@example.invalid 与 .runtime/beta-users.json'),
    Step('bind-identity', lambda s: s.host_script('bind_identity.py'), probe=_db('platform', ENTERPRISE('alpha')),
         inspect='平台站 DS Enterprise alpha/beta'),
    Step('desk-oauth', lambda s: s.host_script('provision_desk_oauth.py'),
         probe=_db('platform', OAUTH('DSHERP alpha Desk')),
         inspect='平台站 OAuth Client「DSHERP alpha Desk」与 alpha/beta 的 Social Login Key'),
    Step('daily-site', lambda s: s.compose_run('daily-provision'), probe=_site('daily'),
         inspect='docker compose exec backend ls /home/frappe/frappe-bench/sites'),
    Step('daily-agent', lambda s: s.host_script('provision_daily_agent.py'),
         probe=_both(_file('context-worker-daily.json'), _db('platform', ENTERPRISE('daily'))),
         produces=('context-worker-daily.json',),
         inspect='平台站 DS Enterprise daily 与 .runtime/context-worker-daily.json'),
    Step('daily-synthetic', lambda s: s.host_script('initialize_daily_synthetic.py'),
         probe=_db('daily', USER('daily-operator@example.invalid')),
         inspect='daily 站 User daily-operator@example.invalid'),
    Step('context-worker', lambda s: s.host_script('provision_context_worker.py'),
         probe=_both(_file('context-worker.json'), _db('alpha', CONF('dsherp_runtime_user'))),
         produces=('context-worker.json',),
         inspect='alpha site_config 的 dsherp_runtime_user 与 .runtime/context-worker.json'),
    Step('context-writer', lambda s: s.host_script('provision_context_writer.py'),
         probe=_both(_file('context-writer.json'), _db('alpha', USER('dsherp-writer@example.invalid'))),
         produces=('context-writer.json',),
         inspect='alpha 站 User dsherp-writer@example.invalid 与 .runtime/context-writer.json'),
    Step('worker-profile', lambda s: s.host_script('merge_context_worker_profiles.py'),
         probe=_file('context-worker-sites.json'), produces=('context-worker-sites.json',), rerun_safe=True),
    Step('preview-operator', lambda s: s.host_script('provision_preview_operator.py'),
         probe=_both(_file('preview-operator.json'), _db('beta', USER('dsherp-preview@example.invalid'))),
         produces=('preview-operator.json',),
         inspect='beta 站 User dsherp-preview@example.invalid 与 .runtime/preview-operator.json'),
    Step('configuration-preview', lambda s: s.host_script('provision_configuration_preview.py'),
         probe=_both(_file('configuration-preview.json'), _db('alpha', CONF('dsherp_configuration_preview'))),
         produces=('configuration-preview.json',), rerun_safe=True),
    Step('policies-alpha', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site',
                                                   'dsherp-validation.localhost', '--policy-set', 'manufacturing'),
         rerun_safe=True),
    Step('policies-daily', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site',
                                                   'dsherp-daily.localhost', '--policy-set', 'manufacturing'),
         rerun_safe=True),
    Step('policies-beta', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site',
                                                  'dsherp-beta.localhost'), rerun_safe=True),
    Step('manufacturing-alpha', lambda s: s.host_script('provision_manufacturing_fixture.py', '--site',
                                                        'dsherp-validation.localhost'), rerun_safe=True),
    Step('manufacturing-daily', lambda s: s.host_script('provision_manufacturing_fixture.py', '--site',
                                                        'dsherp-daily.localhost'), rerun_safe=True),
    Step('clear-cache', _clear_caches, rerun_safe=True),
    Step('daily-backup',
         lambda s: s.bench('daily').run('bench', '--site', 'dsherp-daily.localhost', 'backup', '--with-files',
                                        timeout=900),
         probe=_backup, inspect='backend 里 sites/dsherp-daily.localhost/private/backups'),
    # Last, and only once every development Site exists: `bench run-tests` truncates tables,
    # so the native tests get their own throwaway Sites and are never pointed at the four above.
    Step('test-sites',
         lambda s: native_tests.provision_test_sites(s.resolved, root=s.root, runner=s.runner),
         rerun_safe=True,
         inspect='backend 与 platform-backend 里的 dsherp-test.localhost / dsherp-platform-test.localhost'),
]


def produced_files():
    return [name for step in STEPS for name in step.produces]


def require_backends(stack):
    """Several steps docker-exec into a running container; say so before one fails inside a
    shell script that only reports "container is not running"."""
    running = set(stack.running_services())
    missing = [service for service in EXEC_TARGETS if service not in running]
    if missing:
        raise Fault('这些服务没有在运行，开通步骤会 docker exec 进去：' + '、'.join(missing)
                    + f'\n先 `dev_stack.py up`（或 docker compose -f {COMPOSE_FILE} up -d）再 provision')


def provision(stack, *, skip=()):
    ledger = stack.load_ledger()
    require_backends(stack)
    ran, skipped = [], []
    for step in STEPS:
        if step.name in skip:
            continue
        recorded = step.name in ledger['steps']
        found = None if step.probe is None else list(step.probe(stack))
        if found and any(found) and not all(found):
            raise Fault(f'步骤 {step.name} 的结果只存在一部分（探针 {found}）：栈里的站点/记录与 .runtime 里的'
                        f'凭据文件不配套。不重跑 fail-closed 脚本；查 {step.inspect}；'
                        f'确认后用 `dev_stack.py down --volumes` 重建。')
        present = None if found is None else all(found)
        if recorded and present in (True, None):
            skipped.append(step.name)
            continue
        if step.rerun_safe or (not recorded and present in (False, None)):
            step.run(stack)
            stack.record(step)
            ran.append(step.name)
            continue
        if recorded:
            raise Fault(f'台账记录 {step.name} 已完成，但栈里没有它的结果（卷被重建而 .runtime 没清？）。'
                        f'不重跑 fail-closed 脚本；查 {step.inspect}；'
                        f'确认要重建就先 `dev_stack.py down --volumes`。')
        raise Fault(f'台账没有 {step.name}，但栈里已有它的结果（手工开通过或台账丢失？）。'
                    f'不重跑 fail-closed 脚本；查 {step.inspect}；'
                    f'要以这个栈为准就先 `dev_stack.py down --volumes` 重建。')
    return {'ran': ran, 'skipped': skipped}


# ---- up / status ---------------------------------------------------------------------

def check_hosts(stack):
    missing = []
    for name in HOSTS:
        try:
            addresses = {row[4][0] for row in stack.resolve(name, None)}
        except OSError:
            addresses = set()
        if not addresses & {'127.0.0.1', '::1'}:
            missing.append(name)
    if missing:
        raise Fault('这些主机名没有解析到回环地址：' + '、'.join(missing)
                    + '\n在 /etc/hosts 加这一行后重试：\n' + HOSTS_LINE)


def check_images(stack):
    missing = [image for image in IMAGES if not stack.succeeds(['docker', 'image', 'inspect', image])]
    if missing:
        raise Fault('compose 以 pull_policy: never 运行，这些镜像本机没有，先按 digest 拉取：\n'
                    + '\n'.join(f'docker pull {image}' for image in missing))


def wait_ready(stack):
    stack.wait_for('db 应答 ping',
                   lambda: stack.succeeds(stack.compose('exec', '-T', 'db', 'sh', '-c', DB_PING)), timeout=180)
    stack.wait_for('redis 应答 PONG',
                   lambda: stack.succeeds(stack.compose('exec', '-T', 'redis', 'sh', '-c', REDIS_PING)), timeout=60)
    for service in BACKENDS:
        stack.wait_for(f'{service} 监听 8000',
                       lambda service=service: stack.succeeds(
                           stack.compose('exec', '-T', service, BENCH_PYTHON, '-c', PORT_OPEN)),
                       timeout=300)


def check_secrets_readable(stack):
    """Compose bind-mounts file secrets with the host file's ownership. The provisioning containers
    run as the image's frappe user (uid 1000); on a Linux host whose user is not 1000 they cannot
    read a 0600 file. Say so before the first fail-closed script hits it."""
    command = stack.compose('--profile', 'control', 'run', '--rm', '--no-deps', '--entrypoint', 'sh',
                            'validation-provision', '-c', SECRETS_READABLE)
    result = stack.runner(command, cwd=str(stack.root), text=True, capture_output=True,
                          timeout=120, stdin=subprocess.DEVNULL)
    if result.returncode or 'readable' not in (result.stdout or ''):
        raise Fault('容器内的 frappe 用户（uid 1000）读不到 .runtime/control 里的密钥文件。'
                    'Linux 上执行 `sudo chown 1000 .runtime/control/*`（只改文件，目录仍归你；'
                    'bind mount 只看文件权限）后重试；macOS Docker Desktop 不需要。最后输出：'
                    + '\n'.join((result.stderr or '').strip().splitlines()[-3:]))


def up(stack, *, provision_too=False, skip_runtime_volume=False):
    check_hosts(stack)
    check_images(stack)
    report = {'secrets': admin.ensure_secrets(stack.resolved, stack.root, names=CONTROL_SECRETS)}
    stack.run(stack.compose('up', '-d'), timeout=600)
    wait_ready(stack)
    check_secrets_readable(stack)
    if provision_too:
        report['provision'] = provision(stack, skip=('agent-runtime-volume',) if skip_runtime_volume else ())
    return report


def status(stack):
    services = stack.running_services()
    pings = {}
    for kind, url, host in PINGS:
        try:
            with stack.http(Request(url, headers={'Host': host}), timeout=10) as response:
                pings[kind] = response.status
        except URLError as error:
            pings[kind] = getattr(error, 'code', None) or type(error).__name__
        except OSError as error:
            pings[kind] = type(error).__name__
    hosts = {}
    for name in HOSTS:
        try:
            hosts[name] = sorted({row[4][0] for row in stack.resolve(name, None)})
        except OSError:
            hosts[name] = []
    return {'ledger': sorted(stack.load_ledger()['steps']), 'running_services': services,
            'pings': pings, 'hosts': hosts}


# ---- down / artifacts ------------------------------------------------------------------

def require_worker_stopped(stack):
    pid_file = stack.runtime / WORKER_PID
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError):
        return
    except PermissionError:
        pass  # 进程存在但属于别的用户：同样算活着
    raise Fault(f'常驻 worker 仍在运行（pid {pid}）。先 `launchctl bootout gui/$(id -u)/{WORKER_LABEL}`，'
                f'确认 {pid_file} 消失或进程不在，再拆栈。')


def down(stack, *, volumes):
    require_worker_stopped(stack)
    profiles = [word for name in PROFILES for word in ('--profile', name)]
    stack.run(stack.compose(*profiles, 'down', '--remove-orphans', *(['-v'] if volumes else [])),
              timeout=600)
    removed = []
    if not volumes:
        return {'removed': removed}
    if stack.volume_exists(AGENT_RUNTIME_VOLUME):
        stack.run(['docker', 'volume', 'rm', AGENT_RUNTIME_VOLUME], timeout=120)
    for name in produced_files():
        path = stack.runtime / name
        if path.exists():
            path.unlink()
            removed.append(name)
    if stack.ledger_path.exists():
        stack.ledger_path.unlink()
        removed.append(LEDGER_NAME)
    return {'removed': removed}


def _secret_values(runtime_dir):
    """(name, value) for every secret the runtime directory holds, one name per distinct value.
    Names, never values, are reported."""
    runtime_dir = Path(runtime_dir)
    found, seen = [], set()

    def add(name, value):
        if value and value not in seen:
            seen.add(value)
            found.append((name, value))

    control = runtime_dir / 'control'
    if control.is_dir():
        for path in sorted(control.iterdir()):
            if path.is_file():
                try:
                    add(f'control/{path.name}', path.read_text().strip())
                except (PermissionError, UnicodeDecodeError) as error:
                    raise Fault(f'读不到 {path}（{type(error).__name__}）；用 sudo 运行 scan-artifacts')

    def walk(node, trail, file):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in SECRET_KEYS and isinstance(child, str):
                    add(f'{file}:{".".join(trail + [key])}', child)
                else:
                    walk(child, trail + [str(key)], file)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, trail + [str(index)], file)

    for path in sorted(runtime_dir.glob('*.json')):
        if path.name == LEDGER_NAME:
            continue
        try:
            walk(json.loads(path.read_text()), [], path.name)
        except ValueError:
            continue
    return found


def leaked_secrets(runtime_dir, paths, *, as_text=False):
    secrets = _secret_values(runtime_dir)
    leaks = []
    for item in paths:
        text = item if as_text else Path(item).read_text(errors='replace')
        for name, value in secrets:
            if value in text:
                leaks.append((name, 'text' if as_text else str(item)))
    return leaks


def run_native_tests(resolved, *, out, runner=subprocess.run):
    """Frappe's own test runner on the two throwaway test Sites. How a run is judged lives in
    dsherp/native_tests.py, next to the facts about the pinned image that decide it."""
    return native_tests.run_native_tests(resolved, out=out, runner=runner)


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description='从零拉起四站开发栈的唯一入口；也是拆掉它的唯一入口')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('secrets', help='只生成缺失的控制面密钥；从不覆盖、从不打印')
    up_parser = sub.add_parser('up', help='核对主机名与镜像、生成密钥、compose up、等就绪；--provision 接着开通四站')
    up_parser.add_argument('--provision', action='store_true')
    up_parser.add_argument('--skip-runtime-volume', action='store_true', help='跳过 agent 运行时卷（本机已有时省几分钟）')
    provision_parser = sub.add_parser('provision', help='栈已 up 时只做开通步骤')
    provision_parser.add_argument('--skip-runtime-volume', action='store_true')
    sub.add_parser('status', help='台账、运行中的服务、四站 ping、主机名解析；不含任何密钥')
    native = sub.add_parser('native-tests', help='两个测试站上跑 Frappe 原生测试，输出与解析结果写到 --out')
    native.add_argument('--out', required=True, type=Path)
    down_parser = sub.add_parser('down', help='停栈；--volumes 连数据卷、agent 运行时卷、台账与开通产出的凭据文件'
                                              '一起删（控制面密钥保留）')
    down_parser.add_argument('--volumes', action='store_true')
    scan = sub.add_parser('scan-artifacts', help='检查要上传的文件是否含 .runtime 里任一密钥值；有则退出 1，只报名字')
    scan.add_argument('paths', nargs='+', type=Path)
    arguments = parser.parse_args(argv)
    try:
        resolved = deploy_env.settings()
        if resolved['env'] != 'dev':
            raise Fault('dev_stack 只操作 DSHERP_ENV=dev 的隔离栈')
        stack = Stack(resolved)
        if arguments.command == 'secrets':
            _print(admin.ensure_secrets(stack.resolved, stack.root, names=CONTROL_SECRETS))
        elif arguments.command == 'up':
            _print(up(stack, provision_too=arguments.provision, skip_runtime_volume=arguments.skip_runtime_volume))
        elif arguments.command == 'provision':
            _print(provision(stack, skip=('agent-runtime-volume',) if arguments.skip_runtime_volume else ()))
        elif arguments.command == 'status':
            _print(status(stack))
        elif arguments.command == 'native-tests':
            _print(run_native_tests(stack.resolved, out=arguments.out))
        elif arguments.command == 'down':
            _print(down(stack, volumes=arguments.volumes))
        elif arguments.command == 'scan-artifacts':
            # nightly 用 `if: always()` 调这条，可能有一步没产出它的工件（比如 pytest 没跑到）。
            # 缺文件如实说出来，不静默跳过，也不用一个 traceback 盖住真正的失败。
            present = [path for path in arguments.paths if path.exists()]
            for path in arguments.paths:
                if not path.exists():
                    print(f'{path}: 不存在，未扫描')
            leaks = leaked_secrets(stack.runtime, present)
            for name, path in leaks:
                print(f'{path}: 含 {name} 的值')
            return 1 if leaks else 0
        return 0
    except Fault as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
