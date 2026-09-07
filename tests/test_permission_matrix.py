"""G7: the permission matrix is one file, and the DocType definitions, the controllers, the
guest-endpoint inventory and the report gate all agree with it.

The matrix says what each kind of actor may do with each of our DocTypes and why. This file
checks it against what is on disk; the behaviour on a real Site - what Frappe's permission
layer actually answers, and what the controllers refuse regardless of it - is checked by
frappe_app/*/tests/test_permission_matrix.py on the throwaway test Sites."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / 'frappe_app'
MATRIX = APPS / 'dsherp_bridge' / 'tests' / 'permission_matrix.json'
DOCTYPE_ROOTS = {'dsherp_bridge': APPS / 'dsherp_bridge/dsherp_bridge/doctype',
                 'dsherp_platform': APPS / 'dsherp_platform/platform/doctype'}
FLAGS = ('read', 'write', 'create', 'delete', 'submit', 'cancel', 'amend', 'report',
         'export', 'import', 'print', 'email', 'share', 'select')
ACTIONS = ('read', 'create', 'write', 'delete')
REFUSAL_MODES = ('always', 'while_enabled', 'while_referenced', 'never')
WHITELIST = re.compile(r'^\s*@frappe\.whitelist\((?P<args>[^)]*)\)\s*$')
DEF = re.compile(r'^\s*def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')


def matrix():
    return json.loads(MATRIX.read_text())


def _folder(row):
    return DOCTYPE_ROOTS[row['app']] / row['doctype'].lower().replace(' ', '_')


def _definition(row):
    path = _folder(row) / (_folder(row).name + '.json')
    assert path.is_file(), path
    return json.loads(path.read_text())


def _controller(row):
    path = _folder(row) / (_folder(row).name + '.py')
    assert path.is_file(), path
    return path.read_text()


def _normalise(rows):
    return sorted((row['role'], int(row.get('permlevel') or 0), tuple(f for f in FLAGS if row.get(f)))
                  for row in rows)


ROWS = matrix()['doctypes'] if MATRIX.is_file() else []
IDS = [row['doctype'] for row in ROWS]


def test_both_doctype_roots_exist_and_the_matrix_covers_every_ds_doctype_exactly_once():
    for root in DOCTYPE_ROOTS.values():
        assert root.is_dir(), root
    on_disk = {(app, path.name) for app, root in DOCTYPE_ROOTS.items()
               for path in root.iterdir() if path.is_dir() and not path.name.startswith('_')}
    listed = [(row['app'], _folder(row).name) for row in ROWS]
    assert set(listed) == on_disk
    assert len(listed) == len(set(listed)) == len(on_disk)


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_permission_rows_track_changes_and_child_status_match_the_definition(row):
    definition = _definition(row)
    assert _normalise(definition.get('permissions', [])) == _normalise(row['permissions']), row['doctype']
    assert bool(definition.get('track_changes')) is row['track_changes'], row['doctype']
    assert bool(definition.get('istable')) is bool(row.get('parent_doctype')), row['doctype']


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_the_controller_refuses_delete_exactly_where_the_matrix_says(row):
    """Permissions are bypassed by ignore_permissions and by Administrator; on_trash is not."""
    source = _controller(row)
    mode = row['controller_refuses_delete']
    assert mode in REFUSAL_MODES, row['doctype']
    if mode == 'never':
        assert 'def on_trash' not in source, row['doctype']
    else:
        assert 'def on_trash' in source, row['doctype']
        assert 'frappe.throw' in source.split('def on_trash', 1)[1], row['doctype']


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_actor_expectations_are_derived_from_the_rows_not_free_text(row):
    """Nobody hand-writes what an actor may do: it follows from the permission rows, so a row
    that changes without the expectation changing is a mismatch, not a silent re-authorisation."""
    actors = matrix()['actors']
    by_role = {}
    for role, level, flags in _normalise(row['permissions']):
        if level == 0:
            by_role.setdefault(role, set()).update(flags)
    parent = next((other for other in ROWS if other['doctype'] == row.get('parent_doctype')), None)
    for actor, expected in row['actions'].items():
        assert set(expected) == set(ACTIONS), (row['doctype'], actor)
        for action in ACTIONS:
            want = expected[action]
            if actor == 'administrator':
                if action == 'delete' and row['controller_refuses_delete'] in ('always', 'while_enabled'):
                    assert want == 'refused_by_controller', row['doctype']
                else:
                    assert want is True, (row['doctype'], action)
            elif actor == 'guest':
                assert want is False, (row['doctype'], action)
            elif parent:
                assert want is parent['actions'][actor][action], (row['doctype'], actor, action)
            else:
                derived = any(action in by_role.get(role, ()) for role in actors[actor]['roles'])
                assert want is derived, (row['doctype'], actor, action)


def test_permlevel_one_expectations_match_the_rows():
    """api_key and api_secret sit at permlevel 1 so an ordinary member never reads them."""
    for row in ROWS:
        levels = {(item['role'], int(item.get('permlevel') or 0)):
                  set(f for f in FLAGS if item.get(f)) for item in row['permissions']}
        for actor, flags in row.get('permlevel_1', {}).items():
            roles = matrix()['actors'][actor]['roles']
            derived = set().union(*(levels.get((role, 1), set()) for role in roles)) if roles else set()
            assert set(flags) == derived, (row['doctype'], actor)


def guest_endpoints_on_disk():
    found = set()
    for path in sorted(APPS.rglob('*.py')):
        if '/tests/' in path.as_posix() or '__pycache__' in path.parts:
            continue
        module = path.relative_to(APPS).with_suffix('').as_posix().replace('/', '.')
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            match = WHITELIST.match(line)
            if not match or 'allow_guest=True' not in match.group('args').replace(' ', ''):
                continue
            for following in lines[index + 1:index + 4]:
                name = DEF.match(following)
                if name:
                    found.add(module + '.' + name.group('name'))
                    break
            else:
                raise AssertionError(f'{path}:{index + 1} allow_guest 装饰器后面没有函数定义')
    return found


def test_the_guest_endpoint_inventory_is_exactly_the_declared_one():
    """Anything reachable without a session is the outer surface; a new one has to be declared
    here with the guard that stands in for the missing session."""
    declared = matrix()['guest_endpoints']
    assert guest_endpoints_on_disk() == {row['method'] for row in declared}
    assert all(row['guard'] in ('oauth_state', 'run_capability', 'hmac_envelope') for row in declared)


def test_the_audit_report_is_gated_by_role_in_its_definition():
    for report in matrix()['reports']:
        definition = json.loads((APPS / report['path']).read_text())
        assert [row['role'] for row in definition['roles']] == report['roles'], report['name']
