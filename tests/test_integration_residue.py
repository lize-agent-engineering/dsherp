"""Say what you are about to leave behind before you leave it; the suite takes it away in
the order the controllers dictate and refuses to touch the persistent fixtures."""
import json
import stat
import subprocess

import pytest

from tests.integration import residue

SITE = 'dsherp-validation.localhost'


class Fake:
    """Records sweep scripts and docker calls; answers as a clean Site unless told otherwise."""

    def __init__(self, leftovers=None, listing=('20260901_020000-dsherp-platform_localhost-aaaaaa',)):
        self.scripts, self.commands = [], []
        self.leftovers = leftovers or []
        self.listing = list(listing)

    def run_script(self, site, body, timeout=120, **kwargs):
        self.scripts.append((site, body, timeout))
        return ('noise\n' + residue.MARK
                + json.dumps({'removed': {'DS Conversation': 1}, 'leftovers': self.leftovers}) + '\n')

    def run(self, command, **kwargs):
        self.commands.append(list(command))
        text = ' '.join(command)
        out = ''
        if 'ls -1 /home/frappe/backups/sets/' in text:
            out = '\n'.join(self.listing) + '\n'
        elif 'rm -rf' in text:
            doomed = text.split('rm -rf ', 1)[1].split()[0].rsplit('/', 1)[-1]
            self.listing = [name for name in self.listing if name != doomed]
        return subprocess.CompletedProcess(command, 0, out, '')


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / 'runtime' / 'integration-residue.json'


def registry(ledger, fake=None, scope='function', owner='tests/integration/test_x.py::test_one'):
    fake = fake or Fake()
    return residue.Registry(owner, scope, ledger=ledger, run_script=fake.run_script, run=fake.run), fake


def test_a_registration_is_on_disk_private_and_readable_back_before_the_test_creates_anything(ledger):
    reg, _ = registry(ledger)
    entry_id = reg.doc(SITE, 'DS Conversation', {'owner': 'dsherp-reader@example.invalid', 'title': 'probe abc'})
    assert ledger.is_file() and stat.S_IMODE(ledger.stat().st_mode) == 0o600
    payload = residue.read_ledger(ledger)
    assert payload['version'] == residue.VERSION and [e['id'] for e in payload['entries']] == [entry_id]
    entry = payload['entries'][0]
    assert entry['kind'] == 'doc' and entry['site'] == SITE and entry['doctype'] == 'DS Conversation'
    assert entry['selector'] == {'owner': 'dsherp-reader@example.invalid', 'title': 'probe abc'}
    assert entry['owner'] == 'tests/integration/test_x.py::test_one' and entry['scope'] == 'function'


@pytest.mark.parametrize('call', [
    lambda r: r.doc(SITE, 'Item', 'DSHERP-TEST-ITEM'),
    lambda r: r.doc(SITE, 'Item', 'DSHERP-MFG-SYN-RM'),
    lambda r: r.doc(SITE, 'Sales Order', 'SAL-ORD-2026-00001'),
    lambda r: r.doc(SITE, 'Item', {'item_code': ['like', 'DSHERP-HITL-%']}),
    lambda r: r.doc(SITE, 'Item', {}),
    lambda r: r.doc(SITE, 'Item', ''),
    lambda r: r.doc('dsherp-validation-backend-1', 'Item', 'X'),
    lambda r: r.doc(SITE, 'Item', {'name': 'DSHERP-TEST-ITEM'}),
    lambda r: r.doc(SITE, 'User', {'name': ['like', 'probe-%']}),
    lambda r: r.user(SITE, 'dsherp-reader@example.invalid'),
    lambda r: r.user(SITE, 'member@example.invalid'),
    lambda r: r.user(SITE, 'someone@example.com'),
    lambda r: r.doctype(SITE, 'DS Model Run'),
    lambda r: r.doctype(SITE, 'Item'),
    lambda r: r.doctype(SITE, 'DS Bad`Name'),
    lambda r: r.container('dsherp-validation-backend-1'),
    lambda r: r.container('dsherp-context-' + 'g' * 32),
    lambda r: r.restore(SITE, 'x', '   '),
    lambda r: r.backup_set('dsherp-platform.localhost', '../evil'),
])
def test_persistent_fixtures_fixed_actors_and_malformed_names_are_refused(ledger, call):
    reg, _ = registry(ledger)
    with pytest.raises(ValueError):
        call(reg)
    assert not ledger.exists()


def test_scoping_a_sweep_by_a_fixed_actor_is_allowed_because_it_removes_rows_not_the_actor(ledger):
    """`{'owner': reader}` is how a test says "the conversations I made as the reader"; the
    reader themself is only ever removable through registry.user, which refuses fixed actors."""
    reg, _ = registry(ledger)
    entry_id = reg.doc(SITE, 'DS Conversation', {'owner': 'dsherp-reader@example.invalid', 'title': 'probe abc'})
    assert residue.read_ledger(ledger)['entries'][0]['id'] == entry_id
    reg.doc(SITE, 'Stock Entry', {'owner': 'dsherp-writer@example.invalid', 'docstatus': 0})
    assert len(residue.read_ledger(ledger)['entries']) == 2


def test_the_plan_follows_the_guard_order_and_the_script_carries_the_auth_and_ddl_lines(ledger):
    reg, _ = registry(ledger)
    reg.user(SITE, 'unknown-impact-abc@example.invalid')
    reg.doctype(SITE, 'DS Unknown Impact abc')
    reg.doc(SITE, 'DS Unknown Impact abc', {'subject': 'Must remain draft'})
    reg.doc(SITE, 'Stock Entry', {'owner': 'impact-permission-abc@example.invalid', 'docstatus': 0})
    reg.doc(SITE, 'DS Conversation', {'title': 'Unknown stock impact abc'})
    reg.doc(SITE, 'DS Model Run', 'a' * 64)
    plan = residue.sweep_plan(reg.entries)
    assert [step['op'] for step in plan] == ['ds', 'ds', 'document', 'document', 'user', 'doctype', 'verify']
    assert [step['doctype'] for step in plan[:2]] == ['DS Conversation', 'DS Model Run']
    assert plan[-1]['entries'][0]['kind'] == 'user'
    script = residue.sweep_script(plan)
    assert "delete from `__Auth` where doctype='User' and name=%s" in script
    assert "frappe.db.sql_ddl('DROP TABLE `tab' + name + '`')" in script
    assert "frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)" in script
    assert "frappe.db.delete(doctype, {'name': name})" in script
    assert 'doc.cancel()' in script and residue.MARK.strip() in script
    chain = script.index("'DS Conversation': [('DS Model Run', 'conversation')")
    assert chain < script.index("'DS Model Run': [('DS Run Event', 'run')")
    compile(script, '<sweep>', 'exec')
    assert residue.sweep_plan(reg.entries) == plan  # deterministic


def test_sweeping_runs_one_script_per_site_then_containers_then_restores_and_forgets_only_its_own(ledger):
    fake = Fake()
    reg, _ = registry(ledger, fake)
    other, _ = registry(ledger, fake, scope='module', owner='tests/integration/test_y.py')
    other.doc('dsherp-beta.localhost', 'DS Doctype Policy', 'BOM')
    reg.doc(SITE, 'DS Conversation', {'title': 'probe abc'})
    reg.doc('dsherp-platform.localhost', 'DS Enterprise', 'matrix-abc')
    reg.container('dsherp-context-' + 'b' * 32)
    reg.restore(SITE, 'heartbeat', "frappe.cache().set_value('dsherp_worker_heartbeat','x')")
    report = reg.sweep()
    sites = [site for site, _, _ in fake.scripts]
    assert sites == [SITE, 'dsherp-platform.localhost', SITE]  # two sweeps, then the restore
    assert residue.MARK.strip() in fake.scripts[0][1] and fake.scripts[2][1].startswith('frappe.cache()')
    assert fake.commands == [['docker', 'rm', '-f', 'dsherp-context-' + 'b' * 32]]
    assert report['restored'] == ['heartbeat'] and report['leftovers'] == [] and report['failed'] == []
    remaining = residue.read_ledger(ledger)['entries']
    assert [e['doctype'] for e in remaining] == ['DS Doctype Policy']  # the module's entry survives
    assert reg.entries == []
    reg.sweep()
    assert len(fake.scripts) == 3  # nothing left: no second script


def test_leftovers_fail_the_sweep_and_stay_in_the_ledger(ledger):
    fake = Fake(leftovers=[{'id': 'x', 'doctype': 'DS Conversation', 'names': ['abc']}])
    reg, _ = registry(ledger, fake)
    entry_id = reg.doc(SITE, 'DS Conversation', 'abc')
    fake.leftovers[0]['id'] = entry_id
    with pytest.raises(residue.ResidueError) as caught:
        reg.sweep()
    assert 'abc' in str(caught.value)
    assert [e['id'] for e in residue.read_ledger(ledger)['entries']] == [entry_id]


def test_a_failing_sweep_script_is_a_failure_not_a_pass(ledger):
    class Broken(Fake):
        def run_script(self, site, body, timeout=120, **kwargs):
            raise AssertionError('站点脚本失败')

    reg, _ = registry(ledger, Broken())
    reg.user(SITE, 'sso-machine-abc@example.invalid')
    with pytest.raises(residue.ResidueError):
        reg.sweep()
    assert len(residue.read_ledger(ledger)['entries']) == 1, 'nothing is known to be gone'


def test_a_sweep_script_that_says_nothing_is_a_failure_not_an_empty_success(ledger):
    class Silent(Fake):
        def run_script(self, site, body, timeout=120, **kwargs):
            return 'no marker here\n'

    reg, _ = registry(ledger, Silent())
    reg.doc(SITE, 'DS Conversation', 'abc')
    with pytest.raises(residue.ResidueError):
        reg.sweep()


def test_a_timeout_is_noted_in_the_report(ledger):
    from tests.integration import site_exec

    reg, _ = registry(ledger)
    reg.doc(SITE, 'DS Conversation', 'abc')
    with reg.active():
        assert reg.on_timeout in site_exec.ON_TIMEOUT
        reg.on_timeout(SITE, 'frappe.db.commit()')
    assert reg.on_timeout not in site_exec.ON_TIMEOUT
    assert reg.sweep()['interrupted'] == [{'site': SITE, 'body': 'frappe.db.commit()'}]


def test_a_ledger_left_by_a_killed_session_is_swept_at_the_next_start(ledger):
    fake = Fake()
    dead, _ = registry(ledger, fake, owner='tests/integration/test_dead.py::test_killed')
    dead.doc(SITE, 'DS Conversation', {'title': 'killed abc'})
    dead.container('dsherp-context-' + 'c' * 32)
    dead.restore('dsherp-platform.localhost', 'membership', 'print(1)')
    fake.scripts.clear()
    fake.commands.clear()
    report = residue.sweep_previous(ledger, run_script=fake.run_script, run=fake.run)
    assert report['entries'] == 3 and report['kept'] == 0
    assert [site for site, _, _ in fake.scripts] == [SITE, 'dsherp-platform.localhost']
    assert fake.commands == [['docker', 'rm', '-f', 'dsherp-context-' + 'c' * 32]]
    assert residue.read_ledger(ledger)['entries'] == []
    assert residue.sweep_previous(ledger, run_script=fake.run_script, run=fake.run) == {'entries': 0}


def test_backup_sets_that_appeared_after_registration_are_removed_both_halves_and_forgotten(
        ledger, tmp_path, monkeypatch):
    from dsherp import backup_status

    site = 'dsherp-platform.localhost'
    old = '20260901_020000-dsherp-platform_localhost-aaaaaa'
    new = '20260907_030000-dsherp-platform_localhost-bbbbbb'
    fake = Fake(listing=[old])
    reg, _ = registry(ledger, fake)
    status_file = tmp_path / 'status.json'
    status = backup_status.empty()
    status['sets'] = {
        old: {'site': site, 'state': 'complete', 'stamp': '20260901_020000'},
        new: {'site': site, 'state': 'staged', 'stamp': '20260907_030000'}}
    backup_status.save(status_file, status)
    monkeypatch.setattr(residue, '_status_file', lambda: status_file)
    reg.backup_sets_after(site)
    fake.listing.append(new)
    fake.listing.append('junk-not-a-set-id')
    report = reg.sweep()
    assert report['backup_sets'] == [new]
    removals = [c for c in fake.commands if 'rm -rf' in ' '.join(c)]
    assert len(removals) == 1
    assert f'/home/frappe/backups/sets/{site}/{new}' in removals[0][-1]
    assert f'/home/frappe/backup-secrets/{site}/{new}' in removals[0][-1]
    left = backup_status.load(status_file)['sets']
    assert new not in left and old in left, 'a set that reached a repository keeps its row'


def test_a_backup_set_that_was_already_synced_is_never_forgotten_from_the_status_file(ledger, tmp_path, monkeypatch):
    from dsherp import backup_status

    site = 'dsherp-platform.localhost'
    synced = '20260907_040000-dsherp-platform_localhost-cccccc'
    fake = Fake(listing=[])
    reg, _ = registry(ledger, fake)
    status_file = tmp_path / 'status.json'
    status = backup_status.empty()
    status['sets'] = {synced: {'site': site, 'state': 'complete', 'stamp': '20260907_040000'}}
    backup_status.save(status_file, status)
    monkeypatch.setattr(residue, '_status_file', lambda: status_file)
    reg.backup_sets_after(site)
    fake.listing.append(synced)
    reg.sweep()
    assert synced in backup_status.load(status_file)['sets']


def test_the_session_sweep_runs_before_queue_hygiene():
    """Declared order in conftest, checked as AST rather than as text."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / 'tests/integration/conftest.py').read_text())
    hygiene = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'validation_queue_hygiene')
    assert [arg.arg for arg in hygiene.args.args] == ['residue_ledger_swept']
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {'residue', 'module_residue', 'residue_ledger_swept'} <= names
