"""A configuration transfer has a validity window and never carries the platform token.

The transfer row used to keep the member's encrypted platform grant for as long as the row
existed - which is forever, because it is an audit record nobody may delete (ruling #3). The
grant now lives in the Site cache under the transfer's id for exactly the transfer's window,
the way a run's does (R7), and the row records the window as a visible, frozen field.

This file checks the definition and the migration contract on the host; the behaviour on a
real Site is in frappe_app/dsherp_bridge/tests/test_transfer_grant.py and
tests/integration/test_transfer_grants.py."""
import importlib
import json
from pathlib import Path

from dsherp import release_compare

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'frappe_app/dsherp_bridge'
PATCH = 'dsherp_bridge.patches.v1.expire_transfer_grant'
DEFINITION = BRIDGE / 'dsherp_bridge/doctype/ds_configuration_transfer/ds_configuration_transfer.json'


def _definition():
    return json.loads(DEFINITION.read_text())


def test_the_transfer_row_has_a_window_and_no_column_for_the_grant():
    definition = _definition()
    fields = {field['fieldname']: field for field in definition['fields']}
    assert 'platform_grant' not in fields and 'platform_grant' not in definition['field_order']
    assert fields['expires_at']['fieldtype'] == 'Datetime' and fields['expires_at'].get('reqd') == 1
    assert fields['expires_at'].get('read_only') == 1, '窗口由服务端定，不给人改'
    assert 'expires_at' in definition['field_order']


def test_the_window_is_part_of_the_frozen_binding_in_the_controller():
    source = (DEFINITION.parent / 'ds_configuration_transfer.py').read_text()
    frozen = source.split('FROZEN=', 1)[1].split('\n', 1)[0]
    assert 'expires_at' in frozen and 'platform_grant' not in frozen


def test_the_window_is_one_number_in_one_place():
    source = (BRIDGE / 'configuration_transfer.py').read_text()
    line = next(l for l in source.splitlines() if l.startswith('TRANSFER_WINDOW_SECONDS'))
    assert line.split('=')[1].strip().split('#')[0].strip() == '7200', '用户 2026-09-07 裁决 2 小时'


def test_existing_rows_are_migrated_by_a_named_patch_after_the_definition_is_synced():
    lines = [line.split('#')[0].strip() for line in (BRIDGE / 'patches.txt').read_text().splitlines()]
    assert PATCH in lines, '存量行要拿到窗口并失去令牌，只重载定义是不够的'
    assert lines.index(PATCH) > lines.index('[post_model_sync]'), '新列在模型同步之后才存在'


def _snapshot(columns, rows):
    table = {'columns': list(columns), 'hash_columns': [c for c in columns if c != 'modified'],
             'rows': {name: {'hash': release_compare.row_hash(values), 'values': dict(values)}
                      for name, values in rows.items()}}
    return {'tables': {'tabDS Configuration Transfer': table}, 'singles': {}, 'auth': {}}


def test_the_patch_declares_what_g2_will_see_and_the_declaration_makes_the_upgrade_clean():
    """The judge is the real comparison, not a description of it: dropping the grant column is
    drift until this patch declares it, and clean once it has."""
    module = importlib.import_module('frappe_app.' + PATCH)
    declared = [{'patch': PATCH, **entry} for entry in module.EXPECTED_CHANGES]
    before = _snapshot(['name', 'bundle', 'platform_grant'],
                       {'t1': {'name': 't1', 'bundle': 'b1', 'platform_grant': 'cipher-text'}})
    after = _snapshot(['name', 'bundle', 'expires_at'],
                      {'t1': {'name': 't1', 'bundle': 'b1', 'expires_at': '2026-09-07 12:00:00'}})

    undeclared = release_compare.compare(before, after)
    assert undeclared['clean'] is False
    assert undeclared['removed_columns'] == {'tabDS Configuration Transfer': ['platform_grant']}

    report = release_compare.compare(before, after, declared)
    assert report['clean'] is True, report['differences']
    assert report['differences'] and all(row['declared'] == PATCH for row in report['differences'])
    entry = next(item for item in declared if item['doctype'] == 'DS Configuration Transfer')
    assert entry['columns_removed'] == ['platform_grant']
    assert entry['fields'] == ['expires_at'] and entry['rows'] == 'existing'


def test_the_patch_can_be_read_without_a_frappe_installation():
    """admin.py collects EXPECTED_CHANGES by importing each patch inside the container, but the
    declaration has to be readable here too, so `import frappe` belongs inside execute()."""
    source = (BRIDGE / 'patches/v1/expire_transfer_grant.py').read_text()
    head = source.split('def execute(', 1)[0]
    assert 'import frappe' not in head
