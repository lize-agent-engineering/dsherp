"""G2: the comparison that decides whether an upgrade or a rollback left the data as it was.

Pure Python over two snapshots (see dsherp/release_snapshot.py for how they are taken):
{'tables': {table: {'columns': [...], 'rows': {name: {'hash': str, 'values': {col: val} | None}}}},
 'singles': {doctype: {field: value}}, 'auth': {key: hash}, 'scope': {...}}
"""
import pytest

from dsherp import release_compare as rc


def snap(tables=None, singles=None, auth=None):
    built = {}
    for table, (columns, rows) in (tables or {}).items():
        built[table] = {'columns': list(columns), 'rows': {
            name: {'hash': rc.row_hash(values), 'values': dict(values)} for name, values in rows.items()}}
    return {'tables': built, 'singles': dict(singles or {}), 'auth': dict(auth or {})}


SO = ['name', 'customer', 'grand_total', 'modified']


def test_identical_snapshots_are_clean_and_report_nothing():
    before = snap({'tabSales Order': (SO, {'SO-1': {'name': 'SO-1', 'customer': 'C', 'grand_total': 10, 'modified': 't1'}})},
                  singles={'Stock Settings': {'valuation_method': 'FIFO'}}, auth={'User|a|api_secret': 'h1'})
    report = rc.compare(before, before)
    assert report['clean'] is True
    assert report['differences'] == [] and report['added_tables'] == [] and report['removed_tables'] == []
    assert report['added_columns'] == {} and report['removed_columns'] == {}
    assert report['summary'] == {'tables': 1, 'rows': 1, 'singles': 1, 'auth': 1, 'differences': 0, 'undeclared': 0}


def test_a_changed_field_is_named_with_both_values_and_makes_the_report_not_clean():
    before = snap({'tabSales Order Item': (['name', 'parent', 'qty'], {'r1': {'name': 'r1', 'parent': 'SO-1', 'qty': 2}})})
    after = snap({'tabSales Order Item': (['name', 'parent', 'qty'], {'r1': {'name': 'r1', 'parent': 'SO-1', 'qty': 3}})})
    report = rc.compare(before, after)
    assert report['clean'] is False
    assert report['differences'] == [{'table': 'tabSales Order Item', 'name': 'r1', 'change': 'changed',
                                      'field': 'qty', 'before': 2, 'after': 3, 'declared': False}]


def test_inserted_and_deleted_rows_and_changed_singles_and_auth_are_differences():
    before = snap({'tabItem': (['name', 'item_name'], {'A': {'name': 'A', 'item_name': 'a'}, 'B': {'name': 'B', 'item_name': 'b'}})},
                  singles={'Stock Settings': {'valuation_method': 'FIFO'}}, auth={'User|u|api_secret': 'h1'})
    after = snap({'tabItem': (['name', 'item_name'], {'A': {'name': 'A', 'item_name': 'a'}, 'C': {'name': 'C', 'item_name': 'c'}})},
                 singles={'Stock Settings': {'valuation_method': 'Moving Average'}}, auth={'User|u|api_secret': 'h2'})
    report = rc.compare(before, after)
    kinds = {(d['table'], d['name'], d['change']) for d in report['differences']}
    assert kinds == {('tabItem', 'B', 'deleted'), ('tabItem', 'C', 'inserted'),
                     ('single:Stock Settings', 'valuation_method', 'changed'), ('__Auth', 'User|u|api_secret', 'changed')}
    assert report['clean'] is False and report['summary']['undeclared'] == 4


def test_columns_added_by_the_new_schema_are_informational_but_removed_columns_are_not():
    before = snap({'tabDS Model Run': (['name', 'status'], {'r': {'name': 'r', 'status': 'Succeeded'}})})
    after = snap({'tabDS Model Run': (['name', 'status', 'provider_failures'],
                                      {'r': {'name': 'r', 'status': 'Succeeded', 'provider_failures': 0}})})
    report = rc.compare(before, after)
    assert report['clean'] is True and report['added_columns'] == {'tabDS Model Run': ['provider_failures']}
    assert report['differences'] == []  # a default materialised into a new column is not data drift
    shrunk = rc.compare(after, before)
    assert shrunk['clean'] is False and shrunk['removed_columns'] == {'tabDS Model Run': ['provider_failures']}
    assert shrunk['differences'] == [{'table': 'tabDS Model Run', 'name': '*', 'change': 'column_removed',
                                      'field': 'provider_failures', 'before': None, 'after': None, 'declared': False}]


def test_new_tables_are_informational_and_a_vanished_table_is_a_difference():
    before = snap({'tabItem': (['name'], {'A': {'name': 'A'}})})
    after = snap({'tabItem': (['name'], {'A': {'name': 'A'}}), 'tabDS Run Event': (['name'], {})})
    assert rc.compare(before, after) == {**rc.compare(before, after), 'clean': True, 'added_tables': ['tabDS Run Event']}
    gone = rc.compare(after, before)
    assert gone['clean'] is False and gone['removed_tables'] == ['tabDS Run Event']
    assert gone['differences'][0]['change'] == 'table_removed'


def test_a_patch_may_declare_the_fields_and_rows_it_changes_and_only_those_count_as_declared():
    before = snap({'tabDS Model Run': (['name', 'status', 'model'], {'r1': {'name': 'r1', 'status': 'Succeeded', 'model': None},
                                                                    'r2': {'name': 'r2', 'status': 'Failed', 'model': None}}),
                   'tabItem': (['name', 'item_name'], {'A': {'name': 'A', 'item_name': 'a'}})})
    after = snap({'tabDS Model Run': (['name', 'status', 'model'], {'r1': {'name': 'r1', 'status': 'Succeeded', 'model': 'deepseek-chat'},
                                                                   'r2': {'name': 'r2', 'status': 'Failed', 'model': 'deepseek-chat'},
                                                                   'r3': {'name': 'r3', 'status': 'Queued', 'model': None}}),
                  'tabItem': (['name', 'item_name'], {'A': {'name': 'A', 'item_name': 'renamed'}})})
    declared = [{'patch': 'dsherp_bridge.patches.backfill_model', 'doctype': 'DS Model Run', 'fields': ['model'], 'rows': 'existing'}]
    report = rc.compare(before, after, expectations=declared)
    by_key = {(d['table'], d['name'], d.get('field')): d for d in report['differences']}
    assert by_key[('tabDS Model Run', 'r1', 'model')]['declared'] == 'dsherp_bridge.patches.backfill_model'
    assert by_key[('tabDS Model Run', 'r2', 'model')]['declared'] == 'dsherp_bridge.patches.backfill_model'
    assert by_key[('tabDS Model Run', 'r3', None)]['change'] == 'inserted' and by_key[('tabDS Model Run', 'r3', None)]['declared'] is False
    assert by_key[('tabItem', 'A', 'item_name')]['declared'] is False
    assert report['clean'] is False and report['summary'] == {**report['summary'], 'differences': 4, 'undeclared': 2}
    inserted_ok = rc.compare(before, after, expectations=declared + [{'patch': 'p2', 'doctype': 'DS Model Run', 'rows': 'inserted'}])
    assert inserted_ok['summary']['undeclared'] == 1


def test_an_expectation_must_name_a_patch_and_a_doctype_and_valid_row_kinds():
    for bad in ({'doctype': 'X'}, {'patch': 'p'}, {'patch': 'p', 'doctype': 'X', 'rows': 'sometimes'},
                {'patch': 'p', 'doctype': 'X', 'fields': 'model'}):
        with pytest.raises(ValueError):
            rc.compare(snap(), snap(), expectations=[bad])


def test_rows_without_stored_values_still_detect_a_change_by_hash_without_field_detail():
    before = {'tables': {'tabDS Run Event': {'columns': ['name', 'payload'], 'rows': {'e1': {'hash': 'h1', 'values': None}}}},
              'singles': {}, 'auth': {}}
    after = {'tables': {'tabDS Run Event': {'columns': ['name', 'payload'], 'rows': {'e1': {'hash': 'h2', 'values': None}}}},
             'singles': {}, 'auth': {}}
    report = rc.compare(before, after)
    assert report['differences'] == [{'table': 'tabDS Run Event', 'name': 'e1', 'change': 'changed', 'field': None,
                                      'before': None, 'after': None, 'declared': False}]


def test_the_detail_list_is_capped_but_the_counts_are_not():
    rows_before = {f'r{i}': {'name': f'r{i}', 'v': 0} for i in range(1200)}
    rows_after = {f'r{i}': {'name': f'r{i}', 'v': 1} for i in range(1200)}
    report = rc.compare(snap({'tabX': (['name', 'v'], rows_before)}), snap({'tabX': (['name', 'v'], rows_after)}), detail_limit=100)
    assert len(report['differences']) == 100 and report['summary']['differences'] == 1200
    assert report['truncated'] == 1100 and report['clean'] is False


def test_a_singles_modified_stamp_alone_is_not_a_difference_but_its_values_are():
    """Real migrate on the drill: System Settings was re-saved with identical values; only
    `modified`/`modified_by` moved. Those stamps carry no setting."""
    before = snap(singles={'System Settings': {'enable_scheduler': '1', 'modified': 't1', 'modified_by': 'Administrator'}})
    after = snap(singles={'System Settings': {'enable_scheduler': '1', 'modified': 't2', 'modified_by': 'migrate'}})
    assert rc.compare(before, after)['clean'] is True
    changed = snap(singles={'System Settings': {'enable_scheduler': '0', 'modified': 't2', 'modified_by': 'migrate'}})
    report = rc.compare(before, changed)
    assert report['clean'] is False and [d['field'] for d in report['differences']] == ['enable_scheduler']


def test_row_hash_is_stable_across_key_order_and_independent_of_volatile_columns():
    assert rc.row_hash({'a': 1, 'b': 'x'}) == rc.row_hash({'b': 'x', 'a': 1})
    assert rc.row_hash({'a': 1, '_comments': '[]'}) == rc.row_hash({'a': 1, '_comments': '[{"x":1}]'})
    assert rc.row_hash({'a': 1}) != rc.row_hash({'a': 2})
