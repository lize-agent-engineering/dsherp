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

from dsherp import admin, backup_sets, backup_status, site_holds
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
                admin_log = getattr(admin, 'Fault', Exception)
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
        site_holds.hold(runtime, site, kind)
        window.did(lambda: site_holds.release(runtime, site))
        flags = admin._site_flags(bench, site)
        admin._set_flag(bench, site, 'dsherp_hold', 1)
        window.did(lambda: admin._set_flag(bench, site, 'dsherp_hold', flags.get('dsherp_hold', 0)))
        deadline = clock() + EXECUTOR_WAIT_SECONDS
        while flags['running']:
            if clock() >= deadline:
                return 'busy'
            sleep(EXECUTOR_POLL_SECONDS)
            flags = {**admin._site_flags(bench, site), 'maintenance_mode': flags['maintenance_mode'],
                     'pause_scheduler': flags['pause_scheduler'], 'dsherp_hold': flags.get('dsherp_hold', 0)}
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
    return report


def backup_sync(resolved, *, root=ROOT, runner=subprocess.run, clock=time.time, locked=False):
    raise Fault('异地同步尚未接入')
