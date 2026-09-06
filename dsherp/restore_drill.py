"""Restore drills and cold-start recovery (design §4.5).

A drill takes the newest set whose two halves were read back and agreed, starts an isolated
stack running the build that set records, restores into it, compares the result with the
snapshot taken inside the backup's own window, samples the encrypted fields, and removes
exactly the stack it created. Nothing else on the host is touched, and the copy never has a
route to a user, a worker, mail or a webhook.

Credentials (the database root password, the site's admin password, the encryption key from
the secret half) only ever appear on the bench interpreter's stdin: bench logs its own argv,
and a drill runs often."""
import json
import secrets as tokens
import shutil
import subprocess
import time
from pathlib import Path

from dsherp import admin, backup, backup_sets, backup_status, deploy_env, release_snapshot
from dsherp.admin import Fault, ROOT

DRILL_PROJECT = 'dsherp-restore'
DRILL_COMPOSE = 'infra/compose.restore.yml'
FETCHED = '/home/frappe/fetched'
FAILED_KEEP_DAYS = 14
HEALTH_WAIT_SECONDS = 300
HEALTH_POLL_SECONDS = 5
# Only this: the database password and the deployment keys belong to the host being restored
# onto, but the encryption key is what makes the restored rows readable at all.
CARRIED_KEYS = ('encryption_key',)
# One call does both on a stack where the Site does not exist yet: Frappe creates the site and
# loads the dump into it. `bench restore` is the same call with an existing site_config; here
# the credentials come in on stdin instead of on a command line.
RESTORE = (
    "payload=PAYLOAD\n"
    "os.chdir('/home/frappe/frappe-bench/sites')\n"
    "import frappe\n"
    "from frappe.installer import _new_site,extract_files\n"
    "existing=None\n"
    "if os.path.exists('/home/frappe/frappe-bench/sites/'+payload['site']+'/site_config.json'):\n"
    "    frappe.init(site=payload['site'],sites_path='/home/frappe/frappe-bench/sites')\n"
    "    existing=frappe.conf.db_name\n"
    "    frappe.destroy()\n"
    # `bench new-site` does exactly this before _new_site: without new_site=True the config
    # reader refuses a Site directory that does not exist yet.
    "frappe.init(payload['site'],new_site=True)\n"
    "_new_site(existing or payload['db_name'],payload['site'],db_root_username='root',"
    "db_root_password=payload['root_password'],admin_password=payload['admin_password'],verbose=False,"
    "source_sql=payload['database'],force=True,db_host='db',mariadb_user_host_login_scope='%')\n"
    "for archive in (payload['files'],payload['private_files']):\n"
    "    extract_files(payload['site'],archive)\n"
    "    frappe.destroy()\n"
    "print('DSHERP_RESTORED '+json.dumps({'site':payload['site']}))\n")
SITE_CONFIG = (
    "payload=PAYLOAD\n"
    "os.chdir('/home/frappe/frappe-bench/sites')\n"
    "import frappe\n"
    "from frappe.installer import update_site_config\n"
    "frappe.init(site=payload['site'],sites_path='/home/frappe/frappe-bench/sites')\n"
    "for key,value in payload['values'].items():update_site_config(key,value)\n"
    "frappe.destroy()\n"
    "print('DSHERP_CONFIG '+json.dumps(sorted(payload['values'])))\n")
DECRYPT_CHECK = (
    "rows=frappe.db.sql('select doctype,name,fieldname from `__Auth` where encrypted=1 limit 20')\n"
    "from frappe.utils.password import get_decrypted_password\n"
    "failed=[]\n"
    "for doctype,name,fieldname in rows:\n"
    "    try:get_decrypted_password(doctype,name,fieldname,raise_exception=True)\n"
    "    except Exception:failed.append([doctype,name,fieldname])\n"
    "print('DSHERP_DECRYPT '+json.dumps({'checked':len(rows),'failed':failed}))")


def _script(template, payload):
    return template.replace('PAYLOAD', json.dumps(payload))


def _marked(output, marker):
    line = admin._last_line(output)
    if not line.startswith(marker + ' '):
        raise Fault(f'恢复栈脚本没有输出 {marker}：{line[:200]}')
    return json.loads(line[len(marker) + 1:])


class DrillStack:
    """The isolated compose project a drill runs in."""

    def __init__(self, resolved, root, runner, *, image_tag, secrets_dir, project=DRILL_PROJECT):
        self.resolved, self.root, self.runner = resolved, Path(root), runner
        self.image_tag, self.secrets_dir, self.project = image_tag, Path(secrets_dir), project
        # A development set records the base image the dev bench runs; a release set records
        # this project's image for its tag.
        image = (resolved['base_image'] if image_tag == 'dev'
                 else deploy_env.image_name(resolved['registry'], 'frappe', image_tag))
        self.image = image
        self.environment = {
            'DSHERP_RESTORE_IMAGE': image,
            'DSHERP_IMAGE_TAG': image_tag,
            'DSHERP_IMAGE_REGISTRY': resolved['registry'] or 'local',
            'DSHERP_SECRETS_DIR': str(secrets_dir),
            'DSHERP_RESTORE_PROJECT': project,
            'DSHERP_BACKUP_REPOSITORY': resolved.get('backup_repository', ''),
            'DSHERP_BACKUP_SECRETS_REPOSITORY': resolved.get('backup_secrets_repository', ''),
            'DSHERP_AGENT_UID': str(resolved.get('agent_uid') or 1000),
            'DSHERP_AGENT_GID': str(resolved.get('agent_gid') or 1000),
        }

    def _compose(self, *arguments, profile=None):
        command = ['docker', 'compose', '-p', self.project, '-f', str(self.root / DRILL_COMPOSE)]
        if profile:
            command += ['--profile', profile]
        return command + list(arguments)

    def _run(self, command, timeout=900):
        import os
        result = self.runner(command, text=True, capture_output=True, timeout=timeout,
                             stdin=subprocess.DEVNULL, env={**os.environ, **self.environment})
        if result.returncode:
            tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-6:])
            raise Fault(f'恢复栈命令失败（{" ".join(command[-3:])}）：\n{tail}')
        return result.stdout

    def volumes_exist(self):
        """Whether a previous drill left restored data behind. restic's own cache is not
        evidence of anything and is not counted: it survives an ordinary teardown and would
        otherwise block every drill after the first."""
        result = self.runner(['docker', 'volume', 'ls', '-q', '--filter', f'name={self.project}_'],
                             text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        names = [name for name in (result.stdout or '').split() if not name.endswith('restore-cache')]
        return bool(names)

    def up(self, clock=time.time, sleep=time.sleep):
        self._run(self._compose('up', '-d', 'db', 'redis-cache', 'redis-queue', 'backend'), timeout=1800)
        deadline = clock() + HEALTH_WAIT_SECONDS
        while True:
            rows = [json.loads(line) for line in self._run(self._compose('ps', '--format', 'json'), timeout=120).splitlines()
                    if line.strip()]
            healthy = {row.get('Service') for row in rows if row.get('Health') in ('healthy', '')}
            if {'db', 'redis-cache', 'redis-queue', 'backend'} <= healthy:
                return
            if clock() >= deadline:
                raise Fault('恢复栈在 300 秒内没有全部健康：' + '、'.join(sorted(healthy)) or '无')
            sleep(HEALTH_POLL_SECONDS)

    def image_id(self):
        listed = self._run(self._compose('ps', '-q', 'backend'), timeout=120).strip().splitlines()
        if not listed:
            raise Fault('恢复栈的 backend 没有容器')
        inspected = self._run(['docker', 'inspect', '-f', '{{.Config.Image}} {{.Image}}', listed[0]], timeout=120)
        parts = inspected.split()
        if len(parts) < 2:
            raise Fault('读不到恢复栈 backend 的镜像身份')
        return parts[1]

    def fetch(self, side, snapshot):
        # The same private certificate authority the sync containers use, when one is installed.
        ca = self.secrets_dir / 'backup_storage_ca.pem'
        extra = (['-v', f'{ca}:/run/secrets/backup_storage_ca.pem:ro',
                  '-e', 'RESTIC_CACERT=/run/secrets/backup_storage_ca.pem'] if ca.exists() else [])
        # Each fetch container has its own volume mounted at /fetched, so the target is that
        # root; the bench sees the two of them side by side under /home/frappe/fetched/.
        self._run(self._compose('run', '--rm', '-T', *extra, f'restore-fetch-{side}', 'restore', snapshot,
                                '--target', '/fetched'), timeout=3600)

    def bench(self):
        return admin.Bench(self.resolved, 'tenant', root=self.root, runner=self.runner, project=self.project,
                           compose_file=self.root / DRILL_COMPOSE, environment=self.environment, service='backend')

    def down(self, volumes):
        # The fetch services live behind a profile; without naming it their volumes stay and
        # the next drill would see a phantom leftover.
        self._run(self._compose('down', *(['-v'] if volumes else []), '--remove-orphans', profile='fetch'), timeout=900)


def newest_complete(status, site):
    rows = [row for row in backup_status.sets_of(status, site) if row.get('state') in ('complete', 'verified')]
    if not rows:
        raise Fault(f'站点 {site} 没有任何两侧核对通过的异地备份集，无法演练恢复')
    return rows[-1]


def _fetched_root(bench, side, site, set_id, base=FETCHED):
    """restic restores the absolute path it saved, under --target."""
    pattern = (f'{base}/{side}/backups/*/sets/{site}/{set_id}' if side == 'data'
               else f'{base}/{side}/backups/*/{site}/{set_id}')
    listed = bench.run('sh', '-c', f'ls -d {pattern}', timeout=120).strip().splitlines()
    if not listed:
        raise Fault(f'取回的 {side} 侧里没有 {set_id}')
    return listed[-1]


def verify_fetched(bench, site, set_id, base=FETCHED):
    """Prove the two fetched halves describe one set before anything is restored from them."""
    data = _fetched_root(bench, 'data', site, set_id, base=base)
    secrets_root = _fetched_root(bench, 'secrets', site, set_id, base=base)
    set_doc = json.loads(bench.run('sh', '-c', f'cat {data}/set.json', timeout=120))
    pair = json.loads(bench.run('sh', '-c', f'cat {secrets_root}/pair.json', timeout=120))
    if set_doc.get('set_id') != set_id or not backup_sets.pair_matches(set_doc, pair):
        raise Fault(f'取回的备份集 {set_id} 两侧不配对')
    paths = [f'{data}/{piece}' for piece in backup_sets.DATA_PIECES]
    paths += [f'{data}/snapshot.json', f'{secrets_root}/{backup_sets.CONFIG_PIECE}']
    out = bench.run('sh', '-c', 'sha256sum ' + ' '.join(paths), timeout=1800)
    digests = {line.split()[-1].rsplit('/', 1)[-1]: line.split()[0] for line in out.splitlines() if line.strip()}
    for piece in backup_sets.DATA_PIECES:
        if digests.get(piece) != set_doc['pieces'][piece]['sha256']:
            raise Fault(f'取回的 {piece} 摘要与 set.json 不符（{digests.get(piece)}）')
    if digests.get('snapshot.json') != set_doc['snapshot_sha256']:
        raise Fault('取回的 snapshot.json 摘要与 set.json 不符')
    if digests.get(backup_sets.CONFIG_PIECE) != pair['config_sha256']:
        raise Fault('取回的 site_config 摘要与 pair.json 不符')
    snapshot = json.loads(bench.run('sh', '-c', f'cat {data}/snapshot.json', timeout=1800))
    config = json.loads(bench.run('sh', '-c', f'cat {secrets_root}/{backup_sets.CONFIG_PIECE}', timeout=300))
    return set_doc, data, snapshot, config


def restore_into(bench, site, data_root, config, *, root_password, admin_password):
    bench.script(_script(RESTORE, {'site': site, 'root_password': root_password, 'admin_password': admin_password,
                                   'db_name': 'restore_' + tokens.token_hex(6),
                                   'database': f'{data_root}/database.sql.gz', 'files': f'{data_root}/files.tar',
                                   'private_files': f'{data_root}/private-files.tar'}),
                 timeout=3600, secrets=(root_password, admin_password))
    carried = {key: config[key] for key in CARRIED_KEYS if key in config}
    if not carried:
        raise Fault(f'{site} 的密钥副本里没有 encryption_key，恢复后的密码字段无法解密')
    bench.script(_script(SITE_CONFIG, {'site': site, 'values': {**carried, 'maintenance_mode': 0,
                                                               'pause_scheduler': 1, 'mute_emails': 1}}),
                 timeout=300, secrets=tuple(carried.values()))
    return carried


def decrypt_check(bench, site):
    result = _marked(bench.python(site, DECRYPT_CHECK, timeout=600), 'DSHERP_DECRYPT')
    if result['failed']:
        raise Fault(f'{site} 恢复后有 {len(result["failed"])} 个加密字段无法解密（例如 {result["failed"][0]}）')
    return result


def restore_drill(resolved, sites=None, *, root=ROOT, runner=subprocess.run, stack_bench_factory=None,
                  clock=time.time, sleep=time.sleep, discard_failed=False):
    """Verify every Site's newest complete set by restoring it. Sets recorded under different
    builds are restored in separate stacks, each running the build its own set names."""
    backup.require_repositories(resolved)
    status, state = backup.read_status(resolved, root)
    if state == 'corrupt':
        raise Fault('备份状态文件损坏：先修复 backups/status.json 再演练，否则无从知道哪一份副本是最新的')
    targets = list(sites) if sites else [row['site'] for row in admin.load_tenants(resolved, root)] + [resolved['platform_site']]
    drill_id = time.strftime('%Y%m%d_%H%M%S', time.gmtime(clock())) + '-' + tokens.token_hex(3)
    report = {'ok': True, 'drill_id': drill_id, 'sites': {}}
    bundle = admin.runtime_dir(resolved, root) / 'backups' / 'drills' / drill_id
    with backup.operations_lock(resolved, root, 'restore-drill'):
        chosen, groups = {}, {}
        for site in targets:
            try:
                chosen[site] = newest_complete(status, site)
            except Fault as error:
                report['ok'] = False
                report['sites'][site] = {'error': str(error)}
                continue
            groups.setdefault(chosen[site].get('image_tag') or resolved['image_tag'], []).append(site)
        secrets_dir = bundle / 'secrets'
        root_password, admin_password = tokens.token_urlsafe(24), tokens.token_urlsafe(24)
        admin._write_private(secrets_dir / 'db_root_password', root_password + '\n')
        for name in ('backup_repository_password', 'backup_secrets_repository_password',
                     'backup_storage_credentials', 'backup_secrets_storage_credentials', 'backup_storage_ca.pem'):
            source = admin.secrets_dir(resolved, root) / name
            if source.exists():
                admin._write_private(secrets_dir / name, source.read_text())
        for image_tag, sites_of_tag in groups.items():
            stack = DrillStack(resolved, root, runner, image_tag=image_tag, secrets_dir=secrets_dir)
            if stack.volumes_exist():
                if not discard_failed:
                    raise Fault('上一次演练留下的恢复栈还在（诊断资料保留 14 天）；先看 backups/drills/ 下的报告，'
                                '确认后用 --discard-failed 清掉再演练')
                stack.down(volumes=True)
            failed_here = False
            try:
                stack.up(clock=clock, sleep=sleep)
                bench = stack_bench_factory(stack) if stack_bench_factory else stack.bench()
                for site in sites_of_tag:
                    row = chosen[site]
                    started = time.monotonic()
                    try:
                        running = stack.image_id()
                        if row.get('image_id') and running != row['image_id']:
                            raise Fault(f'恢复栈运行的镜像 id 是 {running}，备份集记录的是 {row["image_id"]}；'
                                        '按备份记录的那次构建恢复，不要用别的镜像')
                        stack.fetch('data', row['data_snapshot'])
                        stack.fetch('secrets', row['secrets_snapshot'])
                        set_doc, data_root, expected, config = verify_fetched(bench, site, row['set_id'])
                        restore_into(bench, site, data_root, config,
                                     root_password=root_password, admin_password=admin_password)
                        restored = admin.take_snapshot(resolved, site, bench=bench,
                                                       hash_columns=admin._hash_columns_of(expected))
                        comparison = admin.compare_snapshots(expected, restored, admin.RESTORE_EXPECTATIONS)
                        if not comparison['clean']:
                            raise Fault(f'{site} 恢复后与备份窗口内的快照有 {comparison["summary"]["undeclared"]} 处未声明差异')
                        decrypted = decrypt_check(bench, site)
                        report['sites'][site] = {'set_id': row['set_id'], 'image_tag': image_tag,
                                                 'seconds': round(time.monotonic() - started, 1),
                                                 'summary': comparison['summary'], 'decrypt_checked': decrypted['checked']}
                        backup_status.record_site(status, site, 'verified', at=backup_status.now_iso(clock), ok=True,
                                                  set_id=row['set_id'], image_tag=image_tag)
                        backup_status.record_set(status, {**status['sets'][row['set_id']], 'set_id': row['set_id']}, 'verified')
                    except Exception as error:
                        failed_here = True
                        report['ok'] = False
                        report['sites'][site] = {'set_id': row['set_id'], 'error': str(error)[:800]}
                        backup_status.record_site(status, site, 'verified', at=backup_status.now_iso(clock), ok=False,
                                                  error=str(error)[:500])
                    backup.save_status(resolved, status, root)
            except Exception as error:
                failed_here = True
                report['ok'] = False
                for site in sites_of_tag:
                    report['sites'].setdefault(site, {'error': str(error)[:800]})
            finally:
                admin._write_json(bundle / 'report.json', report)
                try:
                    stack.down(volumes=not failed_here)
                except Fault as error:
                    report['ok'] = False
                    report['sites'].setdefault('_teardown', {'error': str(error)[:400]})
        backup_status.record_run(status, 'drill', at=backup_status.now_iso(clock), ok=report['ok'],
                                 error=None if report['ok'] else
                                 '；'.join(f'{site}: {row["error"]}' for site, row in report['sites'].items() if 'error' in row))
        backup.save_status(resolved, status, root)
        admin._write_json(bundle / 'report.json', report)
        if report['ok']:
            shutil.rmtree(secrets_dir, ignore_errors=True)
    return report


def restore_site(resolved, site, *, set_id=None, root=ROOT, runner=subprocess.run, bench_factory=None,
                 clock=time.time, provision=None):
    """Cold-start recovery on a freshly built host (gate G3, runbook §12).

    The contract, in order: the Site must not already exist here; the set's metadata and
    artefacts are read back and checked before anything is created; this stack must be running
    the build the set records; the Site is provisioned by the normal path, restored, given back
    its encryption key, compared with the snapshot taken inside the backup's window, and
    provisioned once more so host-specific configuration is derived for THIS host; only a clean
    comparison reopens it."""
    backup.require_repositories(resolved)
    status, state = backup.read_status(resolved, root)
    factory = bench_factory or (lambda kind: admin.Bench(resolved, kind, root=root, runner=runner))
    kind = backup.bench_kind_of(resolved, site)
    bench = factory(kind)
    if bench.site_exists(site):
        raise Fault(f'站点 {site} 在这台主机上已经存在；恢复不会覆盖它，先确认这是不是要恢复的那台机器')
    if set_id:
        row = dict(status['sets'].get(set_id) or {}, set_id=set_id)
        if not row.get('site'):
            raise Fault(f'本机记录里没有备份集 {set_id}；先 backup-sync 让它认识异地已有的集，或换一个 --set')
    else:
        if state == 'corrupt':
            raise Fault('备份状态文件损坏：无法判断哪一份副本最新；先修复 backups/status.json 或用 --set 指名')
        row = newest_complete(status, site)
    started = time.monotonic()
    with backup.operations_lock(resolved, root, 'restore-site'):
        # 1. the artefacts, before anything is created
        for side in ('data', 'secrets'):
            snapshot = row.get(f'{side}_snapshot')
            if not snapshot:
                raise Fault(f'备份集 {row["set_id"]} 的 {side} 侧没有快照 id；它不是一个完整配对，不恢复')
        images = admin._running_images(resolved, runner, root)
        service = admin.bench_service_of(resolved, site)
        running = (images.get(service) or {}).get('image_id')
        if row.get('image_id') and running != row['image_id']:
            raise Fault(f'本机 {service} 运行的镜像 id 是 {running}，备份集记录的是 {row["image_id"]}；'
                        f'先把 prod.env 的 tag 改成 {row.get("image_tag")} 并 compose up -d，再恢复')
        target = f'{backup.BACKUPS}/incoming/{row["set_id"]}'
        volume = f'{resolved["project"]}_{"platform" if kind == "platform" else "tenant"}-backups'
        for side in ('data', 'secrets'):
            arguments = ['restore', row[f'{side}_snapshot'], '--target', f'/incoming/{side}']
            backup.restic(resolved, side, arguments, root=root, runner=runner, timeout=3600,
                          mounts=[f'{volume}:/incoming'])
        data_root = _fetched_root(bench, 'data', site, row['set_id'], base=f'{backup.BACKUPS}/incoming')
        set_doc, data_root, expected, config = verify_fetched(bench, site, row['set_id'],
                                                              base=f'{backup.BACKUPS}/incoming')
        # 2. the Site, by the normal path, so this host's own configuration is derived here
        provision = provision or (lambda: admin.provision_tenant(resolved, site.split('.')[0], root=root, runner=runner)
                                  if kind == 'tenant' else admin.provision_platform(resolved, root=root, runner=runner))
        provision()
        root_password = admin.read_secret(resolved, 'db_root_password', root)
        admin_password = admin.read_secret(resolved, 'tenant_admin_password' if kind == 'tenant'
                                           else 'platform_admin_password', root)
        restore_into(bench, site, data_root, config, root_password=root_password, admin_password=admin_password)
        restored = admin.take_snapshot(resolved, site, bench=bench, hash_columns=admin._hash_columns_of(expected))
        comparison = admin.compare_snapshots(expected, restored, admin.RESTORE_EXPECTATIONS)
        report = {'site': site, 'set_id': row['set_id'], 'image_tag': set_doc['image_tag'],
                  'comparison': comparison['summary'], 'clean': comparison['clean'],
                  'seconds': round(time.monotonic() - started, 1)}
        if not comparison['clean']:
            admin._set_flag(bench, site, 'maintenance_mode', 1)
            report['maintenance'] = 'kept'
            admin._write_json(admin.runtime_dir(resolved, root) / 'backups' / f'restore-{site}.json', report)
            raise Fault(f'{site} 恢复后与备份窗口内的快照有 {comparison["summary"]["undeclared"]} 处未声明差异；'
                        '站点保持维护模式，先看报告再决定')
        decrypted = decrypt_check(bench, site)
        report['decrypt_checked'] = decrypted['checked']
        # 3. host-specific configuration again, now that the data is in place
        provision()
        admin._set_flag(bench, site, 'maintenance_mode', 0)
        report['maintenance'] = 'released'
        backup_status.record_site(status, site, 'verified', at=backup_status.now_iso(clock), ok=True,
                                  set_id=row['set_id'], image_tag=set_doc['image_tag'])
        backup.save_status(resolved, status, root)
        admin._write_json(admin.runtime_dir(resolved, root) / 'backups' / f'restore-{site}.json', report)
    return report


def migrate_drill(resolved, tag, sites=None, *, root=ROOT, runner=subprocess.run, stack_bench_factory=None,
                  clock=time.time, sleep=time.sleep, discard_failed=False):
    """The automated form of G2 (D4): restore a set taken on the build being upgraded away
    from into an isolated stack running the NEW build, migrate it, and judge the result the
    way a release judges it - only differences a patch declared are allowed.

    A restore drill refuses a stack whose image is not the one the set records; this one
    requires the opposite, which is the whole point: it answers "will this release's migration
    keep the data intact" before the release touches a Site."""
    backup.require_repositories(resolved)
    if not deploy_env.TAG.fullmatch(tag or ''):
        raise Fault('迁移演练要给出目标 tag')
    status, state = backup.read_status(resolved, root)
    if state == 'corrupt':
        raise Fault('备份状态文件损坏：先修复 backups/status.json 再做迁移演练')
    targets = list(sites) if sites else [row['site'] for row in admin.load_tenants(resolved, root)] + [resolved['platform_site']]
    drill_id = time.strftime('%Y%m%d_%H%M%S', time.gmtime(clock())) + '-' + tokens.token_hex(3)
    report = {'ok': True, 'drill_id': drill_id, 'tag': tag, 'sites': {}}
    bundle = admin.runtime_dir(resolved, root) / 'backups' / 'drills' / drill_id
    with backup.operations_lock(resolved, root, 'migrate-drill'):
        chosen = {}
        for site in targets:
            try:
                chosen[site] = newest_complete(status, site)
            except Fault as error:
                report['ok'] = False
                report['sites'][site] = {'error': str(error)}
        secrets_dir = bundle / 'secrets'
        root_password, admin_password = tokens.token_urlsafe(24), tokens.token_urlsafe(24)
        admin._write_private(secrets_dir / 'db_root_password', root_password + '\n')
        for name in ('backup_repository_password', 'backup_secrets_repository_password',
                     'backup_storage_credentials', 'backup_secrets_storage_credentials', 'backup_storage_ca.pem'):
            source = admin.secrets_dir(resolved, root) / name
            if source.exists():
                admin._write_private(secrets_dir / name, source.read_text())
        stack = DrillStack(resolved, root, runner, image_tag=tag, secrets_dir=secrets_dir)
        if stack.volumes_exist():
            if not discard_failed:
                raise Fault('上一次演练留下的恢复栈还在；先看 backups/drills/ 下的报告，确认后用 --discard-failed')
            stack.down(volumes=True)
        failed = False
        try:
            stack.up(clock=clock, sleep=sleep)
            bench = stack_bench_factory(stack) if stack_bench_factory else stack.bench()
            for site, row in chosen.items():
                started = time.monotonic()
                try:
                    stack.fetch('data', row['data_snapshot'])
                    stack.fetch('secrets', row['secrets_snapshot'])
                    set_doc, data_root, before, config = verify_fetched(bench, site, row['set_id'])
                    restore_into(bench, site, data_root, config,
                                 root_password=root_password, admin_password=admin_password)
                    migrated = bench.run('bench', '--site', site, 'migrate', timeout=3600)
                    after = admin.take_snapshot(resolved, site, bench=bench,
                                                hash_columns=admin._hash_columns_of(before))
                    declared = admin._expected_changes(bench, site)
                    executed = sorted(set(after.get('patches', [])) - set(before.get('patches', [])))
                    applicable = [entry for entry in declared if entry['patch'] in executed]
                    comparison = admin.compare_snapshots(before, after, applicable)
                    if not comparison['clean']:
                        raise Fault(f'{site} 从 {set_doc["image_tag"]} 迁移到 {tag} 后有 '
                                    f'{comparison["summary"]["undeclared"]} 处未声明差异；'
                                    '要么补 patch 的 EXPECTED_CHANGES 声明，要么这次迁移会改坏数据')
                    report['sites'][site] = {'set_id': row['set_id'], 'from_tag': set_doc['image_tag'], 'to_tag': tag,
                                             'seconds': round(time.monotonic() - started, 1),
                                             'summary': comparison['summary'], 'patches_executed': executed,
                                             'expectations_ignored': [entry for entry in declared
                                                                      if entry['patch'] not in executed],
                                             'migrate_tail': migrated.strip().splitlines()[-10:]}
                except Exception as error:
                    failed = True
                    report['ok'] = False
                    report['sites'][site] = {'set_id': row.get('set_id'), 'error': str(error)[:800]}
        except Exception as error:
            failed = True
            report['ok'] = False
            for site in chosen:
                report['sites'].setdefault(site, {'error': str(error)[:800]})
        finally:
            admin._write_json(bundle / 'report.json', report)
            try:
                stack.down(volumes=not failed)
            except Fault as error:
                report['ok'] = False
                report['sites'].setdefault('_teardown', {'error': str(error)[:400]})
        if report['ok']:
            shutil.rmtree(secrets_dir, ignore_errors=True)
        admin._write_json(bundle / 'report.json', report)
    return report
