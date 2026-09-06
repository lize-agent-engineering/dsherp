"""G2: the reader that snapshots a Site's data inside the bench container.

Run here against a fake `frappe` so the scope rules, the paged reads, the fail-fast and
the redaction are pinned without a database; the same module text is what the CLI sends
into the container.
"""
import json
import re

import pytest

from dsherp import release_snapshot as rs


DOCTYPES = [
    # name, module, istable, issingle, custom, is_virtual
    ('Item', 'Stock', 0, 0, 0, 0),
    ('Sales Order', 'Selling', 0, 0, 0, 0),
    ('Sales Order Item', 'Selling', 1, 0, 0, 0),
    ('Stock Settings', 'Stock', 0, 1, 0, 0),
    ('DS Model Run', 'DSHERP Bridge', 0, 0, 0, 0),
    ('DS Run Event', 'DSHERP Bridge', 0, 0, 0, 0),
    ('DS Custom Thing', 'DSHERP Custom', 0, 0, 1, 0),
    ('Comment', 'Core', 0, 0, 0, 0),
    ('Error Log', 'Core', 0, 0, 0, 0),
    ('Patch Log', 'Core', 0, 0, 0, 0),
    ('Custom Field', 'Custom', 0, 0, 0, 0),
    ('User', 'Core', 0, 0, 0, 0),
    ('Has Role', 'Core', 1, 0, 0, 0),
    ('Contact', 'Contacts', 0, 0, 0, 0),
    ('System Settings', 'Core', 0, 1, 0, 0),
    ('Navbar Settings', 'Core', 0, 1, 0, 0),
    ('OAuth Client', 'Integrations', 0, 0, 0, 0),
    ('Virtual Thing', 'Core', 0, 0, 0, 1),
]
MODULES = {'Stock': 'erpnext', 'Selling': 'erpnext', 'DSHERP Bridge': 'dsherp_bridge', 'Core': 'frappe',
           'Custom': 'frappe', 'Contacts': 'frappe', 'Integrations': 'frappe'}


class FakeDB:
    """Answers exactly the queries the reader is allowed to issue."""

    def __init__(self, tables, singles=None, auth=None, fail_on=None):
        self.tables = tables          # table -> list of row dicts (ordered by name)
        self.singles = singles or {}  # doctype -> {field: value}
        self.auth = auth or []
        self.fail_on = fail_on
        self.queries = []

    def sql(self, query, values=None, as_dict=False):
        self.queries.append((query, values))
        flat = ' '.join(query.split())
        if 'tabModule Def' in flat:
            return [{'name': m, 'app_name': a} for m, a in MODULES.items()]
        if 'from `tabDocType`' in flat:
            return [dict(zip(('name', 'module', 'istable', 'issingle', 'custom', 'is_virtual'), row)) for row in DOCTYPES]
        if 'information_schema.columns' in flat:
            table = values[0] if isinstance(values, (list, tuple)) else values
            rows = self.tables.get(table, [])
            columns = list(rows[0].keys()) if rows else ['name']
            return [(column,) for column in columns]
        if 'from `tabSingles`' in flat:
            return [(doctype, field, value) for doctype, fields in self.singles.items() for field, value in fields.items()]
        if 'from `__Auth`' in flat:
            return [tuple(row) for row in self.auth]
        match = re.search(r'select count\(\*\) from `([^`]+)`', flat)
        if match:
            return [(len(self.tables.get(match.group(1), [])),)]
        match = re.search(r'select \* from `([^`]+)`(?: where (.*?))? order by name limit (\d+)', flat)
        if match:
            table, where, limit = match.group(1), match.group(2) or '', int(match.group(3))
            if self.fail_on == table:
                raise RuntimeError('synthetic read failure on ' + table)
            rows = self.tables.get(table, [])
            if 'name > %s' in where:
                after = values[-1] if isinstance(values, (list, tuple)) else values
                rows = [row for row in rows if row['name'] > after]
            if "parenttype in ('User', 'Role Profile')" in where:
                rows = [row for row in rows if row.get('parenttype') in ('User', 'Role Profile')]
            if 'is_system_generated = 0' in where:
                rows = [row for row in rows if not row.get('is_system_generated')]
            if 'custom = 1' in where:
                rows = [row for row in rows if row.get('custom')]
            return [dict(row) for row in rows[:limit]]
        raise AssertionError('unexpected query: ' + flat)


class FakeFrappe:
    def __init__(self, db):
        self.db = db
        self.local = type('Local', (), {'site': 'g1.localhost'})()


def rows(prefix, count, **extra):
    return [{'name': f'{prefix}{index:05d}', 'modified': '2026-09-06 10:00:00', **extra} for index in range(count)]


def test_every_doctype_lands_in_one_bucket_and_metadata_is_excluded_by_rule_not_by_list():
    db = FakeDB({'tabItem': rows('I', 2, item_name='x'), 'tabDS Model Run': rows('R', 1, status='Succeeded')})
    snapshot = rs.snapshot(FakeFrappe(db))
    scope = snapshot['scope']
    assert set(scope['strict']) >= {'tabItem', 'tabSales Order', 'tabSales Order Item', 'tabDS Model Run', 'tabDS Run Event',
                                    'tabDS Custom Thing', 'tabUser', 'tabContact', 'tabCustom Field', 'tabOAuth Client'}
    assert 'tabComment' in scope['log']
    assert set(scope['excluded']) >= {'tabError Log', 'tabPatch Log'}
    assert 'Virtual Thing' in scope['skipped_virtual']
    assert set(scope['singles']) == {'Stock Settings', 'System Settings'} and 'Navbar Settings' not in scope['singles']
    named = set(scope['strict']) | set(scope['log']) | set(scope['excluded'])
    assert len(named) == len(DOCTYPES) - 4  # every non-single, non-virtual DocType is accounted for exactly once (3 singles, 1 virtual)


def test_has_role_rows_are_identity_data_only_under_users_and_role_profiles():
    """Real migrate on the drill: re-imported standard Reports gained Has Role rows; only rows
    under a User or Role Profile are the tenant's data."""
    db = FakeDB({'tabHas Role': [{'name': 'a', 'modified': 't', 'parenttype': 'User', 'parent': 'u', 'role': 'x'},
                                 {'name': 'b', 'modified': 't', 'parenttype': 'Report', 'parent': 'r', 'role': 'x'}]})
    snapshot = rs.snapshot(FakeFrappe(db))
    assert list(snapshot['tables']['tabHas Role']['rows']) == ['a']


def test_rows_are_read_in_keyset_pages_hashed_and_kept_with_values_up_to_the_detail_limit():
    db = FakeDB({'tabItem': rows('I', 4500, item_name='x')})
    snapshot = rs.snapshot(FakeFrappe(db), page=2000, detail_rows=5000)
    table = snapshot['tables']['tabItem']
    assert len(table['rows']) == 4500 and table['columns'] == ['name', 'modified', 'item_name']
    assert table['rows']['I00007']['values'] == {'name': 'I00007', 'modified': '2026-09-06 10:00:00', 'item_name': 'x'}
    assert re.fullmatch('[0-9a-f]{64}', table['rows']['I00007']['hash'])
    pages = [q for q, _ in db.queries if 'from `tabItem` ' in ' '.join(q.split()) and 'limit' in q]
    assert len(pages) == 3 and 'name > %s' in ' '.join(pages[1].split())
    assert snapshot['row_counts']['tabItem'] == 4500


def test_large_tables_keep_hashes_only_and_log_tables_never_keep_values():
    db = FakeDB({'tabDS Run Event': rows('E', 6000, payload='{}'), 'tabComment': rows('C', 3, content='hi')})
    snapshot = rs.snapshot(FakeFrappe(db), page=2000, detail_rows=5000)
    assert all(row['values'] is None for row in snapshot['tables']['tabDS Run Event']['rows'].values())
    assert all(row['values'] is None for row in snapshot['tables']['tabComment']['rows'].values())
    assert snapshot['tables']['tabComment']['rows']['C00001']['hash']


def test_a_read_failure_aborts_and_names_the_table_instead_of_skipping_it():
    db = FakeDB({'tabItem': rows('I', 3), 'tabSales Order': rows('S', 2)}, fail_on='tabSales Order')
    with pytest.raises(RuntimeError, match='tabSales Order'):
        rs.snapshot(FakeFrappe(db))


def test_secret_columns_are_stored_as_digests_and_singles_and_auth_are_captured():
    db = FakeDB({'tabOAuth Client': [{'name': 'c1', 'modified': 't', 'client_secret': 'plain-secret', 'app_name': 'Desk'}]},
                singles={'Stock Settings': {'valuation_method': 'FIFO', 'modified': 't'}, 'Navbar Settings': {'x': 1}},
                auth=[('User', 'u@example.invalid', 'api_secret', 1, 'gAAAA-token')])
    snapshot = rs.snapshot(FakeFrappe(db))
    values = snapshot['tables']['tabOAuth Client']['rows']['c1']['values']
    assert values['client_secret'].startswith('sha256:') and 'plain-secret' not in json.dumps(snapshot)
    assert snapshot['singles'] == {'Stock Settings': {'valuation_method': 'FIFO', 'modified': 't'}}
    assert list(snapshot['auth']) == ['User|u@example.invalid|api_secret'] and 'gAAAA' not in json.dumps(snapshot)


def test_custom_field_and_property_setter_rows_are_partitioned_by_is_system_generated():
    db = FakeDB({'tabCustom Field': [{'name': 'a', 'modified': 't', 'is_system_generated': 1},
                                     {'name': 'b', 'modified': 't', 'is_system_generated': 0}]})
    snapshot = rs.snapshot(FakeFrappe(db))
    assert list(snapshot['tables']['tabCustom Field']['rows']) == ['b']


def test_the_container_script_is_the_module_source_plus_a_call_and_prints_one_marked_line():
    script = rs.container_script(page=1000, detail_rows=2000)
    assert '"""' not in script and "'''" not in script  # Bench.python re-indents every line
    assert 'DSHERP_SNAPSHOT ' in script and 'snapshot(frappe' in script and 'page=1000' in script
    assert rs.parse_output('noise\nDSHERP_SNAPSHOT {"tables": {}}\n') == {'tables': {}}
    with pytest.raises(ValueError):
        rs.parse_output('no marker here\n')
