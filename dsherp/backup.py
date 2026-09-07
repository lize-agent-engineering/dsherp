"""dsherp-admin backup: a stable window per Site, staged as one backup set (design §4.1).

A backup file and the snapshot that judges a restore must describe the same business state.
Frappe's own backup is not transactional across its four pieces, and neither maintenance mode
nor a paused scheduler ends work that already started, so the window is built explicitly:

    hold the Site for the worker  ->  hold it server-side (claim_run refuses)
    ->  wait for in-flight executors (Running/Cancelling; a frozen queue never waits)
    ->  maintenance_mode + pause_scheduler (no new HTTP writes, no new jobs)
    ->  drain the writers already inside (RQ jobs and non-idle database connections)
    ->  bench backup --with-files + the G2 snapshot
    ->  undo every step that was taken, in reverse, whatever happened.

The set is then staged into two volumes: the three data pieces and the snapshot in one, the
site_config copy in another with tighter permissions, so a sync container for one half cannot
read the other."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import secrets as tokens
import subprocess
import time
from pathlib import Path

from dsherp import admin, backup_retention, backup_sets, backup_status, deploy_env, site_holds
from dsherp.admin import Fault, ROOT, SITES

BACKUPS = '/home/frappe/backups'
BACKUP_SECRETS = '/home/frappe/backup-secrets'
LOCAL_KEEP = 3
EXECUTOR_WAIT_SECONDS = 600
EXECUTOR_POLL_SECONDS = 10
DRAIN_WAIT_SECONDS = 120
DRAIN_POLL_SECONDS = 5
DRAIN_QUIET_ROUNDS = 2
# Counts the writers that maintenance mode cannot stop: jobs already queued or running for
# this Site, and this Site's own database connections that are doing something. Each Site has
# its own database user, so processlist shows only its own threads.
WRITERS = ("from frappe.utils.background_jobs import get_jobs\n"
           "jobs=len(get_jobs(site=frappe.local.site).get(frappe.local.site,[]))\n"
           "rows=frappe.db.sql(\"select id,command from information_schema.processlist where user=%s\","
           "(frappe.conf.db_name,))\n"
           "mine=frappe.db.sql('select connection_id()')[0][0]\n"
           "busy=[r for r in rows if r[0]!=mine and (r[1] or '').lower() not in ('sleep','daemon')]\n"
           "print('DSHERP_WRITERS '+json.dumps({'jobs':jobs,'connections':len(busy)}))")
VERSION = "print(frappe.__version__)"


@contextmanager
def operations_lock(resolved, root=ROOT, who='backup'):
    """One operator action at a time on this host: backup, release, rollback, retire."""
    path = admin.runtime_dir(resolved, root) / 'operations.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path, 'a+')
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip() or '未知'
            raise Fault(f'另一项操作正在进行（{holder}）；backup/release/rollback/retire-tenant 不并行，等它结束再来')
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({'who': who, 'pid': os.getpid(), 'at': backup_status.now_iso()}))
        handle.flush()
        yield path
    finally:
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


@contextmanager
def _nothing():
    yield None


def status_path(resolved, root=ROOT):
    return admin.runtime_dir(resolved, root) / 'backups' / 'status.json'


def read_status(resolved, root=ROOT):
    """(status, state). A corrupt file is never silently replaced: the protection of the last
    complete and last verified set lives in it, so pruning stops until a human looks."""
    status, state = backup_status.read(status_path(resolved, root))
    return (status if status is not None else backup_status.empty()), state


def load_status(resolved, root=ROOT):
    return read_status(resolved, root)[0]


def save_status(resolved, status, root=ROOT):
    return backup_status.save(status_path(resolved, root), status)


def find_pieces(bench, site):
    """The newest four-piece set Frappe just wrote into the Site's private/backups."""
    listing = bench.run('sh', '-c', f'ls -1 {SITES}/{site}/private/backups', timeout=60).split()
    slug = backup_sets.slug(site)
    databases = sorted(name for name in listing if name.endswith(f'-{slug}-database.sql.gz'))
    if not databases:
        raise Fault(f'站点 {site} 备份后在 private/backups 下没有数据库转储；备份集不完整')
    prefix = databases[-1][:-len('-database.sql.gz')]
    pieces = {piece: f'{prefix}-{piece}' for piece in admin.BACKUP_PIECES}
    missing = [name for name in pieces.values() if name not in listing]
    if missing:
        raise Fault(f'站点 {site} 的备份集不完整，缺 {"、".join(missing)}')
    return pieces


def _digests(bench, paths, timeout=1800):
    out = bench.run('sh', '-c', 'sha256sum ' + ' '.join(paths), timeout=timeout)
    digests = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            digests[parts[-1].rsplit('/', 1)[-1]] = parts[0]
    return digests


def _bytes(bench, path):
    return int(bench.run('sh', '-c', f'wc -c < {path}', timeout=60).split()[-1])


def require_staging(bench, site):
    """Prove both staging directories exist and are writable before a Site is touched."""
    probe = bench.run('sh', '-c', f'mkdir -p {BACKUPS}/sets/{site} {BACKUP_SECRETS}/{site} 2>/dev/null; '
                                  f'test -w {BACKUPS}/sets/{site} && test -w {BACKUP_SECRETS}/{site} && echo writable',
                      timeout=60)
    if probe.strip().splitlines()[-1:] != ['writable']:
        raise Fault(f'站点 {site} 的备份暂存目录不可写（{BACKUPS}、{BACKUP_SECRETS}）；'
                    '先修正 bench 的备份卷挂载与属主，站点未动')


def stage_set(bench, site, pieces, *, kind, stamp, token, window, image_tag, image_id, snapshot, frappe_version):
    """Move the four pieces into the two volumes as one set and prove the copies by digest."""
    set_id = backup_sets.new_set_id(site, stamp, token)
    data_dir = f'{BACKUPS}/sets/{site}/{set_id}'
    secret_dir = f'{BACKUP_SECRETS}/{site}/{set_id}'
    source = f'{SITES}/{site}/private/backups'
    before = _digests(bench, [f'{source}/{pieces[piece]}' for piece in backup_sets.DATA_PIECES])
    copies = ' && '.join(f'cp {source}/{pieces[piece]} {data_dir}/{piece}' for piece in backup_sets.DATA_PIECES)
    bench.run('sh', '-c', f'umask 077 && mkdir -p {data_dir} && {copies}', timeout=3600)
    config_name = pieces[backup_sets.CONFIG_PIECE]
    bench.run('sh', '-c', f'umask 077 && mkdir -p {secret_dir} && chmod 700 {BACKUP_SECRETS} {secret_dir} && '
                          f'mv {source}/{config_name} {secret_dir}/{backup_sets.CONFIG_PIECE} && '
                          f'chmod 600 {secret_dir}/{backup_sets.CONFIG_PIECE}', timeout=600)
    after = _digests(bench, [f'{data_dir}/{piece}' for piece in backup_sets.DATA_PIECES])
    for piece in backup_sets.DATA_PIECES:
        if not after.get(piece) or before.get(pieces[piece]) != after[piece]:
            raise Fault(f'站点 {site} 的 {piece} 复制到暂存目录后摘要不一致'
                        f'（原 {before.get(pieces[piece])}，暂存 {after.get(piece)}）；备份集 {set_id} 作废，先检查磁盘')
    config_digest = _digests(bench, [f'{secret_dir}/{backup_sets.CONFIG_PIECE}'], timeout=300).get(backup_sets.CONFIG_PIECE)
    snapshot_text = backup_sets.snapshot_text(snapshot)
    bench.run('sh', '-c', f'umask 077 && cat > {data_dir}/snapshot.json', stdin=snapshot_text, timeout=600)
    set_doc = backup_sets.set_manifest(
        set_id=set_id, site=site, kind=kind, stamp=stamp, window=window, image_tag=image_tag, image_id=image_id,
        frappe_version=frappe_version,
        pieces={piece: {'sha256': after[piece], 'bytes': _bytes(bench, f'{data_dir}/{piece}')}
                for piece in backup_sets.DATA_PIECES},
        snapshot_sha256=hashlib.sha256(snapshot_text.encode()).hexdigest())
    bench.run('sh', '-c', f'umask 077 && cat > {data_dir}/set.json',
              stdin=json.dumps(set_doc, sort_keys=True), timeout=60)
    bench.run('sh', '-c', f'umask 077 && cat > {secret_dir}/pair.json',
              stdin=json.dumps(backup_sets.pair_manifest(set_doc, config_sha256=config_digest), sort_keys=True), timeout=60)
    return set_doc


def _writers(bench, site):
    line = admin._last_line(bench.python(site, WRITERS, timeout=300))
    marker = 'DSHERP_WRITERS '
    if not line.startswith(marker):
        raise Fault(f'读不到站点 {site} 的在途写入者')
    counts = json.loads(line[len(marker):])
    return counts['jobs'] + counts['connections']


def _image_of(images, kind, resolved):
    row = images.get('platform-backend' if kind == 'platform' else 'backend') or {}
    return resolved['image_tag'] or 'dev', row.get('image_id') or ''


class _Window:
    """Records what has been done so it can be undone in reverse, whatever fails."""

    def __init__(self):
        self.undo = []

    def did(self, action):
        self.undo.append(action)

    def close(self):
        while self.undo:
            action = self.undo.pop()
            try:
                action()
            except Exception as error:  # a cleanup failure must not hide the original one
                print(f'清理步骤失败：{type(error).__name__}: {error}', file=__import__('sys').stderr)


def backup_window(resolved, bench, site, kind_of_bench, *, root=ROOT, kind='scheduled', clock=time.time,
                  sleep=time.sleep, token=None, images=None):
    """One stable window. Returns the set, or a string reason when the Site was left alone."""
    runtime = admin.runtime_dir(resolved, root)
    image_tag, image_id = _image_of(images or {}, kind_of_bench, resolved)
    if not image_id:
        raise Fault(f'读不到站点 {site} 所在 bench 的运行镜像 id；没有可核验的制品身份就不做备份')
    require_staging(bench, site)
    window = _Window()
    try:
        site_holds.hold(runtime, site, kind, ttl_seconds=site_holds.DEFAULT_TTL_SECONDS)
        window.did(lambda: site_holds.release(runtime, site))
        flags = admin._site_flags(bench, site)
        # The server-side gate carries its own end as well: a window whose command was
        # killed must not keep the Site from claiming for ever.
        admin._set_flag(bench, site, 'dsherp_hold_until', int(time.time()) + site_holds.DEFAULT_TTL_SECONDS)
        window.did(lambda: admin._set_flag(bench, site, 'dsherp_hold_until', flags.get('dsherp_hold_until', 0)))
        deadline = clock() + EXECUTOR_WAIT_SECONDS
        while flags['running']:
            if clock() >= deadline:
                return 'busy'
            sleep(EXECUTOR_POLL_SECONDS)
            flags = {**admin._site_flags(bench, site), 'maintenance_mode': flags['maintenance_mode'],
                     'pause_scheduler': flags['pause_scheduler'], 'dsherp_hold_until': flags.get('dsherp_hold_until', 0)}
        admin._set_flag(bench, site, 'maintenance_mode', 1)
        window.did(lambda: admin._set_flag(bench, site, 'maintenance_mode', flags.get('maintenance_mode', 0)))
        admin._set_flag(bench, site, 'pause_scheduler', 1)
        window.did(lambda: admin._set_flag(bench, site, 'pause_scheduler', flags.get('pause_scheduler', 0)))
        drain_deadline = clock() + DRAIN_WAIT_SECONDS
        quiet = 0
        while quiet < DRAIN_QUIET_ROUNDS:
            if _writers(bench, site) == 0:
                quiet += 1
            else:
                quiet = 0
                if clock() >= drain_deadline:
                    return 'draining'
                sleep(DRAIN_POLL_SECONDS)
        started = backup_status.now_iso(clock)
        stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime(clock()))
        bench.run('bench', '--site', site, 'backup', '--with-files', timeout=3600)
        snapshot = admin.take_snapshot(resolved, site, bench=bench)
        finished = backup_status.now_iso(clock)
        pieces = find_pieces(bench, site)
        version = admin._last_line(bench.python(site, VERSION, timeout=120)).strip()
    finally:
        window.close()
    return stage_set(bench, site, pieces, kind=kind, stamp=stamp, token=token or tokens.token_hex(3),
                     window={'started': started, 'finished': finished}, image_tag=image_tag, image_id=image_id,
                     snapshot=snapshot, frappe_version=version)


def prune_local(bench, site, protect=()):
    listing = bench.run('sh', '-c', f'ls -1 {BACKUPS}/sets/{site} 2>/dev/null || true', timeout=60).split()
    doomed = backup_sets.local_prune(listing, keep=LOCAL_KEEP, protect=protect)
    for set_id in doomed:
        bench.run('sh', '-c', f'rm -rf {BACKUPS}/sets/{site}/{set_id} {BACKUP_SECRETS}/{site}/{set_id}', timeout=600)
    return doomed


def backup(resolved, *, root=ROOT, runner=subprocess.run, bench_factory=None, sync=False, clock=time.time,
           sleep=time.sleep, locked=False, sites=None):
    factory = bench_factory or (lambda kind: admin.Bench(resolved, kind, root=root, runner=runner))
    report = {'ok': True, 'sites': {}, 'sets': {}, 'failed': {}, 'deferred': {}, 'pruned': {}, 'warnings': []}
    with (operations_lock(resolved, root, 'backup') if not locked else _nothing()):
        images = admin._running_images(resolved, runner, root)
        status, state = read_status(resolved, root)
        report['status_state'] = state
        if state == 'corrupt':
            report['warnings'].append('备份状态文件损坏，本次不修剪本地备份集，也不做异地淘汰；先检查 backups/status.json')
        targets = [(bench, site) for bench, site in admin._targets(resolved, root, factory)
                   if sites is None or site in sites]
        if not targets:
            raise Fault('没有匹配的站点；--site 只能给出当前部署里的站')
        for bench, site in targets:
            started = time.monotonic()
            kind_of_bench = 'platform' if site == resolved['platform_site'] else 'tenant'
            try:
                outcome = backup_window(resolved, bench, site, kind_of_bench, root=root, clock=clock, sleep=sleep,
                                        images=images)
            except Exception as error:
                report['failed'][site] = str(error)
                report['ok'] = False
                backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=False,
                                          error=str(error)[:500])
                save_status(resolved, status, root)
                continue
            if isinstance(outcome, str):
                report['deferred'][site] = outcome
                report['ok'] = False
                backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=False,
                                          error=None, deferred=outcome)
                save_status(resolved, status, root)
                continue
            report['sets'][site] = outcome
            backup_status.record_set(status, outcome, 'staged')
            backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=True,
                                      set_id=outcome['set_id'], stamp=outcome['stamp'])
            if state == 'ok':
                protect = backup_status.protected_sets(status, site) | {outcome['set_id']}
                report['pruned'][site] = prune_local(bench, site, protect=protect)
            report['sites'][site] = {'seconds': round(time.monotonic() - started, 1)}
            save_status(resolved, status, root)
        backup_status.record_run(status, 'backup', at=backup_status.now_iso(clock), ok=report['ok'],
                                 error=None if report['ok'] else
                                 '有站点失败或推迟：' + '、'.join([*report['failed'], *report['deferred']]))
        save_status(resolved, status, root)
        if sync:
            report['sync'] = backup_sync(resolved, root=root, runner=runner, clock=clock, locked=True)
            if not report['sync'].get('ok', False):
                # One command, one verdict: the systemd unit runs `backup --sync`, and its
                # OnFailure notification only fires on a non-zero exit. A local set that never
                # left the host is not a finished backup.
                report['ok'] = False
                report['warnings'].append('异地同步失败：' + '；'.join(report['sync'].get('errors') or ['见 sync']))
    return report


def require_repositories(resolved):
    """Production never performs a destructive step it cannot back out of: a retired tenant's
    final state, and a release's pre-upgrade set, have to be able to leave this host."""
    if resolved.get('backup_repository') and resolved.get('backup_secrets_repository'):
        return True
    if resolved['env'] != 'prod':
        return False
    raise Fault('没有配置异地备份仓库：在 infra/env/prod.env 写 DSHERP_BACKUP_REPOSITORY 与 '
                'DSHERP_BACKUP_SECRETS_REPOSITORY，放好两侧口令与凭据（doctor 会检查），再执行发布或下线')


def site_version(bench, site):
    return admin._last_line(bench.python(site, VERSION, timeout=120)).strip()


def send_set(resolved, set_doc, *, root=ROOT, runner=subprocess.run, fatal=False, clock=time.time):
    """Put one set into both repositories now (a release's pre-upgrade set, a retire's final
    set). `fatal` marks the caller that must not proceed without it."""
    if not require_repositories(resolved):
        return {'ok': False, 'reason': 'not configured (development)'}
    try:
        outcome = upload_sets(resolved, [set_doc], root=root, runner=runner, clock=clock)
    except Fault as error:
        if fatal:
            raise Fault(f'备份集 {set_doc["set_id"]} 没能完整到达异地：{error}；不进行破坏性步骤') from error
        return {'ok': False, 'reason': str(error)}
    ok = set_doc['set_id'] in outcome['complete']
    if fatal and not ok:
        raise Fault(f'备份集 {set_doc["set_id"]} 没能完整到达异地：'
                    + (outcome['failed'].get(set_doc['set_id']) or '两侧未配对') + '；不进行破坏性步骤')
    return {'ok': ok, 'reason': outcome['failed'].get(set_doc['set_id'])}


def _compose_run(resolved, root, service, arguments, ca=None, mounts=()):
    file = Path(root) / admin.COMPOSE[resolved['env']]
    command = ['docker', 'compose', '-p', resolved['project']]
    env_file = deploy_env.env_file(resolved['env'], root)
    if env_file.exists():
        command += ['--env-file', str(env_file)]
    command += ['-f', str(file), 'run', '--rm', '-T']
    if ca is not None:
        command += ['-v', f'{ca}:/run/secrets/backup_storage_ca.pem:ro',
                    '-e', 'RESTIC_CACERT=/run/secrets/backup_storage_ca.pem']
    for mount in mounts:
        command += ['-v', mount]
    return command + [service, *arguments]


def _redactions(resolved, root):
    values = []
    for name in ('backup_repository_password', 'backup_secrets_repository_password',
                 'backup_storage_credentials', 'backup_secrets_storage_credentials'):
        path = admin.secrets_dir(resolved, root) / name
        if path.exists():
            for line in path.read_text().splitlines():
                value = line.split('=', 1)[-1].strip()
                if value:
                    values.append(value)
    return values


def _verb(arguments):
    return next((word for word in arguments if not word.startswith('-')), arguments[0] if arguments else '?')


def _remove_run_containers(resolved, side, *, runner=subprocess.run):
    listed = runner(['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={resolved["project"]}',
                     '--filter', f'label=com.docker.compose.service=backup-sync-{side}'],
                    text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    containers = (listed.stdout or '').split()
    if containers:
        runner(['docker', 'rm', '-f', *containers], text=True, capture_output=True, timeout=120,
               stdin=subprocess.DEVNULL)
    return containers


def restic(resolved, side, arguments, *, root=ROOT, runner=subprocess.run, timeout=3600, mounts=()):
    """One restic call against one repository, through its own one-shot compose service. The
    repository URL, its password and its storage identity come from compose, never from argv."""
    if side not in ('data', 'secrets'):
        raise ValueError('Unknown repository side: ' + repr(side))
    ca = admin.secrets_dir(resolved, root) / 'backup_storage_ca.pem'
    command = _compose_run(resolved, root, f'backup-sync-{side}', arguments, ca=ca if ca.exists() else None,
                           mounts=mounts)
    try:
        result = runner(command, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        # `compose run` only kills its own client; the container keeps retrying against a
        # storage endpoint that is not answering, so it is removed here by its compose labels.
        _remove_run_containers(resolved, side, runner=runner)
        raise Fault(f'restic（{side} 仓库）{_verb(arguments)} 超过 {timeout} 秒没有返回；'
                    '对象存储可能不可达，本次不改动任何副本，已清掉该次运行的容器') from error
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-6:])
        for value in _redactions(resolved, root):
            tail = tail.replace(value, '[redacted]')
        raise Fault(f'restic（{side} 仓库）{_verb(arguments)} 失败：\n{tail}')
    return result.stdout


def notify_failure(resolved, unit, *, root=ROOT, profile_path=None, client=None):
    """What systemd runs when a backup unit fails. The worker is the only other thing on this
    host that can raise an alert, and it may be the thing that is down, so this writes the
    journal line itself and posts the webhook itself - and only reports `posted` when the
    delivery actually succeeded."""
    import httpx
    profile = Path(profile_path) if profile_path else Path(root) / '.runtime' / 'context-worker-sites.json'
    webhook = None
    try:
        webhook = json.loads(profile.read_text()).get('alert_webhook')
    except (OSError, ValueError):
        webhook = None
    alert = {'key': 'backup_unit_failed', 'severity': 'critical',
             'message': f'systemd 单元 {unit} 失败；看 journalctl -u {unit} 与 backups/status.json'}
    print(json.dumps({'event': 'alert', 'unit': unit, **alert}, ensure_ascii=False))
    if not webhook:
        return {'unit': unit, 'webhook': 'not configured'}
    try:
        response = (client or httpx).post(webhook, json=alert, timeout=10)
        response.raise_for_status()
    except Exception as error:
        outcome = {'unit': unit, 'webhook': 'failed', 'error_class': type(error).__name__}
        print(json.dumps({'event': 'alert_webhook_failed', **outcome}, ensure_ascii=False))
        return outcome
    return {'unit': unit, 'webhook': 'posted'}


REACH_TIMEOUT_SECONDS = 60
# Listing and reading a manifest are small calls; anything slower means the endpoint is sick.
LIST_TIMEOUT_SECONDS = 300


def reachable(resolved, *, root=ROOT, runner=subprocess.run):
    """Ask both repositories for their config before doing anything slow. restic retries a
    dead endpoint for a quarter of an hour per call; an operator (and a timer) needs to hear
    'unreachable' in seconds."""
    errors = []
    for side in ('data', 'secrets'):
        try:
            # --no-cache on purpose: restic answers `cat config` out of its local cache, so a
            # cached repository would look reachable while the endpoint is down.
            restic(resolved, side, ['--no-cache', 'cat', 'config'], root=root, runner=runner,
                   timeout=REACH_TIMEOUT_SECONDS)
        except Fault as error:
            errors.append(f'{side}: {error}')
    return errors


def backup_init(resolved, *, root=ROOT, runner=subprocess.run):
    require_repositories(resolved) or _require_configured(resolved)
    outcome = {}
    for side in ('data', 'secrets'):
        try:
            restic(resolved, side, ['cat', 'config'], root=root, runner=runner, timeout=300)
            outcome[side] = 'kept'
        except Fault:
            restic(resolved, side, ['init'], root=root, runner=runner, timeout=600)
            outcome[side] = 'created'
    return outcome


def _require_configured(resolved):
    raise Fault('没有配置异地备份仓库：先在环境文件里写 DSHERP_BACKUP_REPOSITORY 与 DSHERP_BACKUP_SECRETS_REPOSITORY')


def bench_kind_of(resolved, site):
    return 'platform' if site == resolved['platform_site'] else 'tenant'


def set_paths(resolved, site, set_id):
    """Where the two halves of a set live inside the two sync containers."""
    kind = bench_kind_of(resolved, site)
    return {'data': f'/backups/{kind}/sets/{site}/{set_id}', 'secrets': f'/backups/{kind}/{site}/{set_id}'}


CANDIDATES = 5


def _snapshot_ids(resolved, side, set_id, *, root, runner):
    """Every snapshot tagged with this set, newest first. Full ids only: a short id is a
    display convenience, and `dump` must address exactly one snapshot. There can be more than
    one - a retried upload leaves the earlier attempt behind - so the caller tries them in
    turn rather than trusting the newest to be the good one."""
    rows = json.loads(restic(resolved, side, ['snapshots', '--json', '--tag', f'set={set_id}'],
                             root=root, runner=runner, timeout=LIST_TIMEOUT_SECONDS) or '[]')
    matching = [row for row in rows if f'set={set_id}' in (row.get('tags') or []) and row.get('id')]
    return [row['id'] for row in sorted(matching, key=lambda row: row.get('time') or '', reverse=True)][:CANDIDATES]


def _read_back(resolved, side, snapshot, path, *, root, runner):
    text = restic(resolved, side, ['dump', snapshot, path], root=root, runner=runner, timeout=600)
    try:
        return json.loads(text)
    except ValueError as error:
        raise Fault(f'{side} 仓库快照 {snapshot[:12]} 里的 {path} 不是 JSON（{error}）') from error


def verify_pair(resolved, set_id, ids, *, root=ROOT, runner=subprocess.run, site=None, expected=None):
    """Read both manifests back out of the two repositories and prove they describe the same
    set. Two snapshot ids are not proof: they only say something was uploaded."""
    if not ids.get('data') or not ids.get('secrets'):
        raise Fault(f'备份集 {set_id} 只有一侧在异地')
    paths = set_paths(resolved, site or (expected or {}).get('site'), set_id)
    set_doc = _read_back(resolved, 'data', ids['data'], f'{paths["data"]}/set.json', root=root, runner=runner)
    pair = _read_back(resolved, 'secrets', ids['secrets'], f'{paths["secrets"]}/pair.json', root=root, runner=runner)
    if set_doc.get('set_id') != set_id:
        raise Fault(f'数据仓库里 {set_id} 的 set.json 记录的是 {set_doc.get("set_id")}')
    if not backup_sets.pair_matches(set_doc, pair):
        raise Fault(f'备份集 {set_id} 的两侧不配对：pair.json 与 set.json 的摘要不一致')
    if expected is not None and backup_sets.set_sha256(set_doc) != backup_sets.set_sha256(expected):
        raise Fault(f'异地的 {set_id} 与本机暂存的内容不同')
    return set_doc


def upload_sets(resolved, set_docs, *, root=ROOT, runner=subprocess.run, clock=time.time, status=None):
    """Send whichever half is missing, then read both manifests back and compare them. A set
    is complete only after that; a half-sent set stays pending for the next run."""
    if not require_repositories(resolved):
        _require_configured(resolved)
    own = status is None
    status = load_status(resolved, root) if own else status
    outcome = {'complete': [], 'pending': [], 'failed': {}}
    for doc in set_docs:
        set_id, site, kind = doc['set_id'], doc['site'], doc.get('kind', 'scheduled')
        paths = set_paths(resolved, site, set_id)
        known = status['sets'].get(set_id, {})
        ids = {'data': known.get('data_snapshot'), 'secrets': known.get('secrets_snapshot')}
        candidates = {'data': [], 'secrets': []}
        errors, last = [], '两侧未配对'
        for side in ('data', 'secrets'):
            try:
                if not ids[side]:
                    restic(resolved, side, ['backup', '--host', resolved['project'], '--tag', f'site={site}',
                                            '--tag', f'set={set_id}', '--tag', f'kind={kind}', paths[side]],
                           root=root, runner=runner, timeout=3600)
                # Always ask the repository, even for an id we recorded: the copy may be gone.
                candidates[side] = _snapshot_ids(resolved, side, set_id, root=root, runner=runner)
                ids[side] = candidates[side][0] if candidates[side] else None
                if not ids[side]:
                    errors.append(f'{side} 仓库里没有 set={set_id} 的快照')
            except Fault as error:
                ids[side], candidates[side] = None, []
                errors.append(str(error))
        complete = False
        verified = None
        for data_id in candidates['data']:
            for secrets_id in candidates['secrets']:
                try:
                    verified = verify_pair(resolved, set_id, {'data': data_id, 'secrets': secrets_id}, root=root,
                                           runner=runner, site=site, expected=doc if 'pieces' in doc else None)
                except Fault as error:
                    last = str(error)
                    continue
                ids['data'], ids['secrets'], complete = data_id, secrets_id, True
                break
            if complete:
                break
        if not complete and candidates['data'] and candidates['secrets']:
            # Both halves are over there but no pair of them agrees: the copies do not describe
            # this set. While the staging area still holds it, send it again and judge the new
            # snapshots; otherwise say so and stay pending.
            errors.append(last)
            try:
                for side in ('data', 'secrets'):
                    restic(resolved, side, ['backup', '--host', resolved['project'], '--tag', f'site={site}',
                                            '--tag', f'set={set_id}', '--tag', f'kind={kind}', paths[side]],
                           root=root, runner=runner, timeout=3600)
                    candidates[side] = _snapshot_ids(resolved, side, set_id, root=root, runner=runner)
                verify_pair(resolved, set_id, {'data': candidates['data'][0], 'secrets': candidates['secrets'][0]},
                            root=root, runner=runner, site=site, expected=doc if 'pieces' in doc else None)
                ids['data'], ids['secrets'], complete = candidates['data'][0], candidates['secrets'][0], True
                errors.append(f'备份集 {set_id} 的异地副本与本机不一致，已按本机内容重新上传')
            except (Fault, IndexError) as error:
                errors.append(f'重新上传 {set_id} 也没能配对：{error}')
        state = ('complete' if complete else
                 'data_uploaded' if ids['data'] else 'secrets_uploaded' if ids['secrets'] else 'staged')
        record = {**doc, 'kind': kind}
        if verified:
            # A set adopted from the repositories after this host lost its record knows only
            # its id; the manifest just read back and paired is what says which build made
            # it. Without that a restore would have nothing to check the running image against.
            for key in ('image_tag', 'image_id', 'stamp', 'site'):
                if verified.get(key):
                    record[key] = verified[key]
        backup_status.record_set(status, record, state, data_snapshot=ids['data'], secrets_snapshot=ids['secrets'])
        at = backup_status.now_iso(clock)
        if complete:
            outcome['complete'].append(set_id)
            backup_status.record_site(status, site, 'offsite', at=at, ok=True, set_id=set_id, stamp=doc['stamp'])
        else:
            outcome['pending'].append(set_id)
            outcome['failed'][set_id] = '；'.join(errors) or '两侧未配对'
            backup_status.record_site(status, site, 'offsite', at=at, ok=False,
                                      error=outcome['failed'][set_id][:500])
    if own:
        save_status(resolved, status, root)
    return outcome


def _remote_sets(resolved, status, *, root, runner):
    """Every set either repository holds, with the state the status file knows for it. A set
    the record does not know is not assumed complete: it is offered to the pairing check."""
    held = {}
    for side in ('data', 'secrets'):
        rows = json.loads(restic(resolved, side, ['snapshots', '--json'], root=root, runner=runner, timeout=LIST_TIMEOUT_SECONDS) or '[]')
        for row in rows:
            tags = dict(tag.split('=', 1) for tag in (row.get('tags') or []) if '=' in tag)
            set_id = tags.get('set')
            if not set_id or not backup_sets.parse_set_id(set_id) or not row.get('id'):
                continue
            entry = held.setdefault(set_id, {'site': tags.get('site'), 'kind': tags.get('kind', 'scheduled'),
                                              'data_ids': [], 'secrets_ids': []})
            # A retried upload can leave more than one snapshot under the same tag; the newest
            # is the one to read back, and expiry has to take them all.
            entry[f'{side}_ids'].append((row.get('time') or '', row['id']))
            entry['site'] = entry['site'] or tags.get('site')
    sets = []
    for set_id, row in held.items():
        known = status['sets'].get(set_id, {})
        site = known.get('site') or row.get('site')
        if not site:
            continue
        newest = {side: (sorted(row[f'{side}_ids'])[-1][1] if row[f'{side}_ids'] else None) for side in ('data', 'secrets')}
        sets.append({'set_id': set_id, 'site': site, 'kind': known.get('kind') or row['kind'],
                     'stamp': backup_sets.parse_set_id(set_id)['stamp'],
                     'state': known.get('state', 'staged'), 'data': newest['data'], 'secrets': newest['secrets'],
                     'data_ids': [item[1] for item in row['data_ids']],
                     'secrets_ids': [item[1] for item in row['secrets_ids']]})
    return sets


def backup_sync(resolved, *, root=ROOT, runner=subprocess.run, clock=time.time, locked=False):
    """Send what is pending, prove each pair, expire what the policy no longer keeps, and read
    a slice of the data back. Nothing is expired unless the host record is intact."""
    if not require_repositories(resolved):
        _require_configured(resolved)
    report = {'ok': True, 'complete': [], 'pending': [], 'forgotten': [], 'errors': [], 'warnings': [], 'check_ok': True}
    with (operations_lock(resolved, root, 'backup-sync') if not locked else _nothing()):
        status, state = read_status(resolved, root)
        if state == 'corrupt':
            report['ok'] = False
            report['warnings'].append('备份状态文件损坏：本次不做异地淘汰（保护"最后一份已验证副本"的依据在里面）；'
                                      '先检查 backups/status.json')
        # Before anything that talks to the repositories: restic retries a dead endpoint for a
        # quarter of an hour per call, and a run has to fit inside its own schedule.
        unreachable = reachable(resolved, root=root, runner=runner)
        if unreachable:
            report['ok'] = False
            report['errors'] += unreachable
            report['check_ok'] = False
            backup_status.record_run(status, 'sync', at=backup_status.now_iso(clock), ok=False,
                                     error='；'.join(unreachable)[:500])
            save_status(resolved, status, root)
            return report
        # The record is not evidence: ask both repositories what they actually hold, and
        # demote any set whose copy is gone before deciding anything else with it.
        listing = {}
        try:
            listing = {row['set_id']: row for row in _remote_sets(resolved, status, root=root, runner=runner)}
        except Fault as error:
            report['ok'] = False
            report['errors'].append(str(error))
        # A set the repositories hold but the record does not know (a record rebuilt, an
        # upload whose verification failed and left a snapshot behind) is adopted as pending,
        # so it is read back, understood, and can then take part in expiry like any other.
        for set_id, held in listing.items():
            if set_id in status['sets']:
                continue
            parsed = backup_sets.parse_set_id(set_id)
            backup_status.record_set(status, {'set_id': set_id, 'site': held['site'], 'kind': held.get('kind', 'scheduled'),
                                              'stamp': parsed['stamp']}, 'staged',
                                     data_snapshot=held.get('data'), secrets_snapshot=held.get('secrets'))
            report['warnings'].append(f'异地有记录里没有的备份集 {set_id}：已收养并按待核对处理')
        for set_id, row in status['sets'].items():
            held = listing.get(set_id, {})
            if row.get('state') not in ('complete', 'verified'):
                continue
            if held.get('data') and held.get('secrets'):
                continue
            row['state'] = 'data_uploaded' if held.get('data') else 'secrets_uploaded' if held.get('secrets') else 'staged'
            row['data_snapshot'], row['secrets_snapshot'] = held.get('data'), held.get('secrets')
            report['ok'] = False
            report['errors'].append(f'备份集 {set_id} 在异地不再完整（{row["state"]}）：已退回待补齐')
        pending = [dict(row, set_id=set_id) for set_id, row in status['sets'].items()
                   if row.get('state') not in ('complete', 'verified')]
        try:
            outcome = upload_sets(resolved, pending, root=root, runner=runner, clock=clock, status=status)
            report['complete'], report['pending'] = outcome['complete'], outcome['pending']
            if outcome['failed']:
                report['ok'] = False
                report['errors'] += list(outcome['failed'].values())
        except Fault as error:
            report['ok'] = False
            report['errors'].append(str(error))
        if state == 'ok':
            try:
                remote = _remote_sets(resolved, status, root=root, runner=runner)
                decision = backup_retention.select(remote)
                doomed = [row for row in remote
                          if decision.get(row['set_id']) == 'drop' and row.get('data') and row.get('secrets')]
                for side in ('data', 'secrets'):
                    ids = [snapshot for row in doomed for snapshot in row.get(f'{side}_ids', [row[side]])]
                    if ids:
                        restic(resolved, side, ['forget', *ids], root=root, runner=runner, timeout=1800)
                        restic(resolved, side, ['prune'], root=root, runner=runner, timeout=1800)
                report['forgotten'] = [row['set_id'] for row in doomed]
                for row in doomed:
                    status['sets'].pop(row['set_id'], None)
            except Fault as error:
                report['ok'] = False
                report['errors'].append(str(error))
        backup_status.record_run(status, 'sync', at=backup_status.now_iso(clock), ok=report['ok'],
                                 error='；'.join(report['errors']) or None)
        # Structure alone proves nothing about the data: a rotating seventh of it is read back
        # every run, so a week of runs reads all of it.
        subset = f'{int(time.strftime("%j", time.gmtime(clock()))) % 7 + 1}/7'
        errors = []
        for side in ('data', 'secrets'):
            try:
                restic(resolved, side, ['check', f'--read-data-subset={subset}'], root=root, runner=runner, timeout=3600)
            except Fault as error:
                errors.append(str(error))
        report['check_ok'] = not errors
        report['ok'] = report['ok'] and report['check_ok']
        backup_status.record_run(status, 'check', at=backup_status.now_iso(clock), ok=report['check_ok'],
                                 error='；'.join(errors) or None)
        report['errors'] += errors
        save_status(resolved, status, root)
    return report
