"""Residue registry for the integration suite: declare what a test is about to leave on a
Site before it leaves it, and take it away again afterwards - even when the test was killed.

Why registration-first. Most integration tests inject a script into a Site container and
commit; the script's own `finally` is its cleanup. A host-side `subprocess.run(timeout=...)`
kills the docker client, not the interpreter inside the container, so that `finally` never
runs and nothing on the host remembers what was created. So every registration is written to
`.runtime/integration-residue.json` (fsync, 0600) before the test creates anything; teardown
sweeps this test's entries in the order the controllers dictate, verifies nothing is left,
and errors the test otherwise; a ledger left behind by a killed session is swept at the next
session start, and what cannot be swept stays in the ledger until a person deals with it.

How things are removed. Audit DocTypes refuse `on_trash` unconditionally, so they go through
`frappe.db.delete` - the sanctioned test-cleanup path recorded in
docs/engineering/data-governance-evidence.md. Everything else goes through the Document API:
submitted business documents are cancelled first, users lose their `__Auth` row, a synthetic
DocType loses its rows, its DocType document and finally its table.

Hash-named records the product creates through its own API (conversations, runs, bundles)
cannot be registered by name before they exist: register the filters that will identify them
(owner + a unique tag in the title, or creation >= the Site's clock read just before). The
sweep resolves filters to names on the Site, cascades through the audit chain, and verifies
with the same filters.
"""
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:  # inside tests/integration, where pytest puts this directory on sys.path
    import site_exec
except ImportError:  # imported as tests.integration.residue by the host suite
    from tests.integration import site_exec
from dsherp import backup, backup_sets, backup_status, deploy_env

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / '.runtime' / 'integration-residue.json'
VERSION = 1

PROTECTED_PREFIXES = ('DSHERP-TEST-', 'DSHERP-HITL-', 'DSHERP-MFG-SYN-', 'DSHERP-UI-', 'DSHERP-BETA-')
PROTECTED_NAMES = frozenset({'SAL-ORD-2026-00001', 'Administrator', 'Guest'})
PROTECTED_USERS = frozenset({
    'dsherp-reader@example.invalid', 'dsherp-denied@example.invalid', 'dsherp-writer@example.invalid',
    'dsherp-preview@example.invalid', 'dsherp-context-runtime@example.invalid',
    'daily-operator@example.invalid', 'beta-reader@example.invalid',
    'member@example.invalid', 'operator@example.invalid', 'outsider@example.invalid',
})
# Our own DocTypes are never "synthetic": a test may not drop their tables.
OWN_DOCTYPES = frozenset({
    'DS Model Run', 'DS Run Event', 'DS Operation Proposal', 'DS Execution Record',
    'DS Configuration Bundle', 'DS Configuration Confirmation', 'DS Configuration Execution',
    'DS Configuration Transfer', 'DS Conversation', 'DS Doctype Policy', 'DS Doctype Policy Route',
    'DS Business Credential', 'DS Ops Snapshot', 'DS Enterprise', 'DS Membership', 'DS Agent Task',
})
SYNTHETIC_DOCTYPE = re.compile(r'DS [A-Za-z0-9][A-Za-z0-9 ]{2,60}')
CONTAINER = re.compile(r'dsherp-context-[0-9a-f]{32}')
# Audit-chain roots: registering one sweeps what hangs off it (CHAIN inside the script).
DS_CHAIN = ('DS Conversation', 'DS Model Run', 'DS Run Event', 'DS Operation Proposal',
            'DS Execution Record', 'DS Configuration Bundle', 'DS Configuration Confirmation',
            'DS Configuration Execution', 'DS Configuration Transfer')
BACKUPS, BACKUP_SECRETS = '/home/frappe/backups', '/home/frappe/backup-secrets'
MARK = 'DSHERP_RESIDUE '
# Only a set that never left the host may be forgotten; anything that reached a repository
# keeps its row, as production's own local prune does.
FORGETTABLE_STATES = ('staged',)


class ResidueError(AssertionError):
    """Something registered is still there after the sweep, or the sweep itself failed."""


# ---- protection ------------------------------------------------------------------------

def _protected_text(value):
    text = str(value)
    return (text in PROTECTED_NAMES or text in PROTECTED_USERS
            or any(prefix in text for prefix in PROTECTED_PREFIXES))


def _refuse_protected(doctype, selector):
    """What is being deleted may never be a persistent fixture. Naming a fixed actor in a
    scoping field is a different thing and is how a sweep is narrowed: `{'owner': reader}`
    removes the rows that actor made, not the actor. Only the identity of the row itself -
    a bare name, a `name` filter, or a persistent fixture's prefix anywhere - is refused."""
    if doctype == 'User':
        raise ValueError('用户请用 registry.user(site, email) 登记：它还要清 __Auth，而且只认临时演员')
    if isinstance(selector, str):
        if not selector.strip():
            raise ValueError('登记的名称不能为空')
        if _protected_text(selector):
            raise ValueError(f'{doctype} {selector!r} 是持久夹具或固定演员，不能登记为残留')
        return
    if not isinstance(selector, dict) or not selector:
        raise ValueError('按条件登记必须给出非空的 filters 字典（空字典会匹配整张表）')
    for key, value in selector.items():
        for atom in (value if isinstance(value, (list, tuple)) else [value]):
            text = str(atom)
            if any(prefix in text for prefix in PROTECTED_PREFIXES):
                raise ValueError(f'{doctype} 的条件 {key}={atom!r} 会命中持久夹具，拒绝登记')
            if key == 'name' and _protected_text(text):
                raise ValueError(f'{doctype} 的条件 name={atom!r} 指向持久夹具，拒绝登记')


# ---- the plan and the script ----------------------------------------------------------

def sweep_plan(entries):
    """Ordered steps for one Site's doc/user/doctype entries (pure).

    Audit-chain roots first (they cascade), other documents, users, synthetic DocTypes,
    then the verification of every entry. Steps are data; the script interprets them."""
    plan = []
    for entry in entries:
        if entry['kind'] == 'doc' and entry['doctype'] in DS_CHAIN:
            plan.append({'op': 'ds', 'doctype': entry['doctype'], 'selector': entry['selector'], 'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'doc' and entry['doctype'] not in DS_CHAIN:
            plan.append({'op': 'document', 'doctype': entry['doctype'], 'selector': entry['selector'],
                         'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'user':
            plan.append({'op': 'user', 'name': entry['name'], 'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'doctype':
            plan.append({'op': 'doctype', 'name': entry['name'], 'id': entry['id']})
    plan.append({'op': 'verify', 'entries': [
        {'id': entry['id'], 'kind': entry['kind'], 'doctype': entry.get('doctype'),
         'selector': entry.get('selector', entry.get('name'))} for entry in entries]})
    return plan


SWEEP_TEMPLATE = r'''
PLAN = json.loads(PLAN_JSON)
CHAIN = {
    'DS Conversation': [('DS Model Run', 'conversation'), ('DS Operation Proposal', 'conversation'),
                        ('DS Configuration Bundle', 'conversation')],
    'DS Model Run': [('DS Run Event', 'run'), ('DS Operation Proposal', 'model_run')],
    'DS Operation Proposal': [('DS Execution Record', 'proposal')],
    'DS Configuration Bundle': [('DS Configuration Confirmation', 'bundle'),
                                ('DS Configuration Transfer', 'bundle')],
    'DS Configuration Confirmation': [('DS Configuration Execution', 'confirmation')],
}
removed = {}
resolved = {}


def count(key, n=1):
    removed[key] = removed.get(key, 0) + n


def names_of(doctype, selector):
    if isinstance(selector, str):
        return [selector] if frappe.db.exists(doctype, selector) else []
    return frappe.get_all(doctype, filters=selector, pluck='name')


def sweep_ds(doctype, names):
    for name in names:
        for child, link in CHAIN.get(doctype, []):
            sweep_ds(child, frappe.get_all(child, filters={link: name}, pluck='name'))
        if doctype == 'DS Conversation':
            frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)
        else:
            frappe.db.delete(doctype, {'name': name})
        count(doctype)


def sweep_document(doctype, names):
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
        count(doctype)


def sweep_user(email):
    frappe.db.delete('DS Business Credential', {'user': email})
    if frappe.db.exists('User', email):
        frappe.delete_doc('User', email, force=True, ignore_permissions=True)
        count('User')
    frappe.db.sql("delete from `__Auth` where doctype='User' and name=%s", (email,))


def sweep_doctype(name):
    frappe.db.delete('DS Doctype Policy', {'target_doctype': name})
    if frappe.db.exists('DocType', name):
        if frappe.db.table_exists(name, cached=False):
            sweep_document(name, frappe.get_all(name, pluck='name'))
        frappe.delete_doc('DocType', name, force=True, ignore_permissions=True)
        count('DocType')
    frappe.db.commit()
    if frappe.db.table_exists(name, cached=False):
        frappe.db.sql_ddl('DROP TABLE `tab' + name + '`')
    frappe.clear_cache(doctype=name)


for step in PLAN:
    if step['op'] in ('ds', 'document'):
        resolved[step['id']] = names_of(step['doctype'], step['selector'])
for step in PLAN:
    if step['op'] == 'ds':
        sweep_ds(step['doctype'], resolved[step['id']])
    elif step['op'] == 'document':
        sweep_document(step['doctype'], resolved[step['id']])
    elif step['op'] == 'user':
        sweep_user(step['name'])
    elif step['op'] == 'doctype':
        sweep_doctype(step['name'])
frappe.db.commit()

leftovers = []
for item in PLAN[-1]['entries']:
    if item['kind'] == 'doc':
        left = names_of(item['doctype'], item['selector'])
        for name in resolved.get(item['id'], []):
            if frappe.db.exists(item['doctype'], name):
                left.append(name)
            for child, link in CHAIN.get(item['doctype'], []):
                left += [child + ':' + row for row in frappe.get_all(child, filters={link: name}, pluck='name')]
        if left:
            leftovers.append({'id': item['id'], 'doctype': item['doctype'], 'names': sorted(set(left))})
    elif item['kind'] == 'user':
        auth = frappe.db.sql("select count(*) from `__Auth` where doctype='User' and name=%s",
                             (item['selector'],))[0][0]
        if frappe.db.exists('User', item['selector']) or auth:
            leftovers.append({'id': item['id'], 'doctype': 'User', 'names': [item['selector']]})
    elif item['kind'] == 'doctype':
        if (frappe.db.exists('DocType', item['selector'])
                or frappe.db.table_exists(item['selector'], cached=False)
                or frappe.db.exists('DS Doctype Policy', {'target_doctype': item['selector']})):
            leftovers.append({'id': item['id'], 'doctype': 'DocType', 'names': [item['selector']]})
print(MARKER + json.dumps({'removed': removed, 'leftovers': leftovers}, ensure_ascii=False))
'''


def sweep_script(plan):
    """The body run_site_script executes on one Site for one plan."""
    return (SWEEP_TEMPLATE
            .replace('PLAN_JSON', repr(json.dumps(plan)), 1)
            .replace('MARKER', repr(MARK), 1))


def _marked(output):
    lines = [line for line in output.splitlines() if line.startswith(MARK)]
    if not lines:
        raise ResidueError('清扫脚本没有输出 ' + MARK.strip() + ' 行：' + output[-300:])
    return json.loads(lines[-1][len(MARK):])


# ---- ledger --------------------------------------------------------------------------------

def _write_ledger(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_ledger(path=LEDGER):
    path = Path(path)
    if not path.exists():
        return {'version': VERSION, 'entries': []}
    payload = json.loads(path.read_text())
    if payload.get('version') != VERSION or not isinstance(payload.get('entries'), list):
        raise ResidueError(f'残留台账 {path} 不是版本 {VERSION} 的台账；先人工检查再处理它')
    return payload


# ---- backup sets (platform bench volumes + host status file) ------------------------------

def _list_sets(run, site):
    result = run([*site_exec.COMPOSE, 'exec', '-T', 'platform-backend', 'sh', '-c',
                  f'ls -1 {BACKUPS}/sets/{site} 2>/dev/null || true'],
                 cwd=ROOT, text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise ResidueError('读不到平台备份集目录：' + (result.stderr or '')[-300:])
    return sorted(name for name in result.stdout.split() if backup_sets.parse_set_id(name))


def _status_file():
    return backup.status_path(deploy_env.settings({'DSHERP_ENV': 'dev'}))


def _forget_staged(doomed):
    path = _status_file()
    status = backup_status.load(path)
    if not status:
        return
    for set_id in doomed:
        row = status['sets'].get(set_id)
        if row and row.get('state') in FORGETTABLE_STATES:
            del status['sets'][set_id]
    backup_status.save(path, status)


def _prune_sets(entry, run):
    site = entry['site']
    listing = _list_sets(run, site)
    if entry['kind'] == 'backup_sets_after':
        doomed = [set_id for set_id in listing if set_id not in entry['known']]
    else:
        doomed = [set_id for set_id in entry['set_ids'] if set_id in listing]
    for set_id in doomed:
        if not backup_sets.parse_set_id(set_id):
            raise ResidueError(f'{set_id!r} 不是备份集 id，拒绝删除')
        result = run([*site_exec.COMPOSE, 'exec', '-T', 'platform-backend', 'sh', '-c',
                      f'rm -rf {BACKUPS}/sets/{site}/{set_id} {BACKUP_SECRETS}/{site}/{set_id}'],
                     cwd=ROOT, text=True, capture_output=True, timeout=600)
        if result.returncode:
            raise ResidueError(f'删除备份集 {set_id} 失败：' + (result.stderr or '')[-300:])
    remaining = set(_list_sets(run, site)) & set(doomed)
    if remaining:
        raise ResidueError('备份集删除后仍在：' + '、'.join(sorted(remaining)))
    _forget_staged(doomed)
    return doomed


# ---- sweeping ----------------------------------------------------------------------------

def sweep_entries(entries, *, run_script=site_exec.run_site_script, run=subprocess.run):
    """Docs/users/doctypes per Site, then containers and backup sets, then restore scripts.
    `clean` lists the ids that are known to be gone; everything else stays in the ledger."""
    report = {'removed': {}, 'leftovers': [], 'failed': [], 'restored': [], 'containers': [],
              'backup_sets': [], 'clean': []}
    by_site = {}
    for entry in entries:
        if entry['kind'] in ('doc', 'user', 'doctype'):
            by_site.setdefault(entry['site'], []).append(entry)
    for site, rows in by_site.items():
        try:
            outcome = _marked(run_script(site, sweep_script(sweep_plan(rows)), timeout=300))
        except Exception as error:  # the script itself failed: nothing here is known to be gone
            report['failed'].append({'site': site, 'error': str(error)[-500:],
                                     'ids': [row['id'] for row in rows]})
            continue
        report['removed'][site] = outcome['removed']
        bad = {item['id'] for item in outcome['leftovers']}
        report['leftovers'] += [{'site': site, **item} for item in outcome['leftovers']]
        report['clean'] += [row['id'] for row in rows if row['id'] not in bad]
    for entry in entries:
        if entry['kind'] == 'container':
            result = run(['docker', 'rm', '-f', entry['name']], text=True, capture_output=True, timeout=60)
            gone = result.returncode == 0 or 'No such container' in (result.stderr or '')
            if gone:
                report['containers'].append(entry['name'])
                report['clean'].append(entry['id'])
            else:
                report['failed'].append({'container': entry['name'], 'error': (result.stderr or '')[-200:]})
        elif entry['kind'] in ('backup_set', 'backup_sets_after'):
            try:
                report['backup_sets'] += _prune_sets(entry, run)
                report['clean'].append(entry['id'])
            except ResidueError as error:
                report['failed'].append({'site': entry['site'], 'error': str(error)})
    for entry in entries:
        if entry['kind'] == 'restore':
            try:
                run_script(entry['site'], entry['script'], timeout=120)
                report['restored'].append(entry['label'])
                report['clean'].append(entry['id'])
            except Exception as error:
                report['failed'].append({'site': entry['site'], 'label': entry['label'],
                                         'error': str(error)[-500:]})
    return report


def sweep_previous(ledger=LEDGER, *, run_script=site_exec.run_site_script, run=subprocess.run):
    """Sweep what a killed session left in the ledger; keep what could not be swept."""
    payload = read_ledger(ledger)
    if not payload['entries']:
        return {'entries': 0}
    report = sweep_entries(payload['entries'], run_script=run_script, run=run)
    clean = set(report['clean'])
    payload['entries'] = [entry for entry in payload['entries'] if entry['id'] not in clean]
    _write_ledger(ledger, payload)
    report['entries'] = len(clean) + len(payload['entries'])
    report['kept'] = len(payload['entries'])
    if report['leftovers'] or report['failed']:
        raise ResidueError('上一会话的残留清扫不完整；人工处理后修正或删除台账再运行：'
                           + json.dumps(report, ensure_ascii=False))
    return report


class Registry:
    """What one test (or one module) is about to leave behind, and how to take it away."""

    def __init__(self, owner, scope='function', *, ledger=LEDGER, run_script=site_exec.run_site_script,
                 run=subprocess.run, clock=time.time):
        self.owner, self.scope = owner, scope
        self.ledger = Path(ledger)
        self.run_script, self.run, self.clock = run_script, run, clock
        self.entries = []
        self.interrupted = []

    # -- registration: durable before it returns --------------------------------------
    def _add(self, **fields):
        entry = {'id': uuid.uuid4().hex, 'owner': self.owner, 'scope': self.scope,
                 'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock())), **fields}
        ledger = read_ledger(self.ledger)
        ledger['entries'].append(entry)
        _write_ledger(self.ledger, ledger)
        self.entries.append(entry)
        return entry['id']

    def doc(self, site, doctype, name_or_filters, *, label=None):
        site_exec.service_of(site)
        _refuse_protected(doctype, name_or_filters)
        return self._add(kind='doc', site=site, doctype=doctype, selector=name_or_filters, label=label)

    def user(self, site, email):
        site_exec.service_of(site)
        if email in PROTECTED_USERS or not email.endswith('@example.invalid'):
            raise ValueError(f'{email!r} 不是可清扫的临时用户（固定演员或不在 example.invalid）')
        return self._add(kind='user', site=site, name=email)

    def doctype(self, site, name):
        site_exec.service_of(site)
        if name in OWN_DOCTYPES or not SYNTHETIC_DOCTYPE.fullmatch(name):
            raise ValueError(f'{name!r} 不是可丢弃的合成 DocType（须形如 "DS Xxx"，且不是本产品的 DocType）')
        return self._add(kind='doctype', site=site, name=name)

    def container(self, name):
        if not CONTAINER.fullmatch(name):
            raise ValueError(f'{name!r} 不是运行容器名（dsherp-context-<32hex>）')
        return self._add(kind='container', name=name)

    def restore(self, site, label, script):
        """A body re-run at teardown to put shared state back; it must be idempotent."""
        site_exec.service_of(site)
        if not script.strip():
            raise ValueError('恢复脚本不能为空')
        return self._add(kind='restore', site=site, label=label, script=script)

    def backup_set(self, site, set_id):
        if not backup_sets.parse_set_id(set_id):
            raise ValueError(f'{set_id!r} 不是备份集 id')
        return self._add(kind='backup_set', site=site, set_ids=[set_id])

    def backup_sets_after(self, site):
        """Every set that appears under this Site's set directory from now on."""
        return self._add(kind='backup_sets_after', site=site, known=_list_sets(self.run, site))

    # -- timeouts ----------------------------------------------------------------------
    def on_timeout(self, site, body):
        self.interrupted.append({'site': site, 'body': body.strip()[:160]})

    @contextmanager
    def active(self):
        site_exec.ON_TIMEOUT.append(self.on_timeout)
        try:
            yield self
        finally:
            site_exec.ON_TIMEOUT.remove(self.on_timeout)

    # -- teardown -----------------------------------------------------------------------
    def sweep(self):
        """Remove everything this registry holds; forget what is gone; raise on the rest."""
        if not self.entries:
            return {'removed': {}, 'leftovers': [], 'failed': [], 'restored': [], 'containers': [],
                    'backup_sets': [], 'clean': [], 'interrupted': list(self.interrupted)}
        report = sweep_entries(self.entries, run_script=self.run_script, run=self.run)
        report['interrupted'] = list(self.interrupted)
        clean = set(report['clean'])
        ledger = read_ledger(self.ledger)
        ledger['entries'] = [entry for entry in ledger['entries'] if entry['id'] not in clean]
        _write_ledger(self.ledger, ledger)
        self.entries = [entry for entry in self.entries if entry['id'] not in clean]
        if report['leftovers'] or report['failed']:
            raise ResidueError('清扫后仍有残留或清扫失败（条目保留在台账里）：'
                               + json.dumps(report, ensure_ascii=False))
        return report
