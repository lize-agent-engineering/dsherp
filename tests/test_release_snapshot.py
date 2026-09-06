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
    ('Role', 'Core', 0, 0, 0, 0),
    ('Custom DocPerm', 'Core', 0, 0, 0, 0),
    ('Workflow', 'Workflow', 0, 0, 0, 0),
    ('Workflow Document State', 'Workflow', 1, 0, 0, 0),
    ('Workflow Transition', 'Workflow', 1, 0, 0, 0),
    ('Workflow State', 'Workflow', 0, 0, 0, 0),
    ('Workflow Action Master', 'Workflow', 0, 0, 0, 0),
    ('Notification', 'Email', 0, 0, 0, 0),
    ('Report', 'Core', 0, 0, 0, 0),
    ('Print Format', 'Printing', 0, 0, 0, 0),
    ('Server Script', 'Core', 0, 0, 0, 0),
]
MODULES = {'Stock': 'erpnext', 'Selling': 'erpnext', 'DSHERP Bridge': 'dsherp_bridge', 'Core': 'frappe',
           'Custom': 'frappe', 'Contacts': 'frappe', 'Integrations': 'frappe', 'Workflow': 'frappe',
           'Email': 'frappe', 'Printing': 'frappe'}


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
        if 'from `tabPatch Log`' in flat:
            return [(name,) for name in getattr(self, 'patches', [])]
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
            for clause in where.split(' and '):
                clause = clause.strip()
                if clause in ('', 'name > %s') or clause.startswith('parent in ('):
                    continue
                match_in = re.fullmatch(r"(\w+) in \(([^)]*)\)", clause)
                match_eq = re.fullmatch(r"(\w+) = '?([^']*)'?", clause)
                if match_in:
                    wanted = {v.strip().strip("'") for v in match_in.group(2).split(',')}
                    rows = [row for row in rows if str(row.get(match_in.group(1))) in wanted]
                elif match_eq:
                    column, value = match_eq.groups()
                    rows = [row for row in rows if str(row.get(column, 0)) == value]
                else:
                    raise AssertionError('unexpected where clause: ' + clause)
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


def test_tenant_permissions_and_workflows_are_strict_and_standard_definitions_are_partitioned_out():
    """Review R4: Role, Custom DocPerm and the Workflow family are Frappe-defined but tenant-
    written (the configuration executor creates workflows; permissions decide access)."""
    db = FakeDB({'tabRole': [{'name': 'Tenant Role', 'modified': 't'}],
                 'tabCustom DocPerm': [{'name': 'p1', 'modified': 't', 'parent': 'Item', 'role': 'Tenant Role'}],
                 'tabWorkflow': [{'name': 'Approval', 'modified': 't'}],
                 'tabWorkflow Transition': [{'name': 'wt1', 'modified': 't', 'parent': 'Approval'}],
                 'tabNotification': [{'name': 'n-std', 'modified': 't', 'is_standard': 1}, {'name': 'n-tenant', 'modified': 't', 'is_standard': 0}],
                 'tabReport': [{'name': 'r-std', 'modified': 't', 'is_standard': 'Yes'}, {'name': 'r-tenant', 'modified': 't', 'is_standard': 'No'}],
                 'tabPrint Format': [{'name': 'pf-std', 'modified': 't', 'standard': 'Yes'}, {'name': 'pf-tenant', 'modified': 't', 'standard': 'No'}]})
    snapshot = rs.snapshot(FakeFrappe(db))
    strict = set(snapshot['scope']['strict'])
    assert {'tabRole', 'tabCustom DocPerm', 'tabWorkflow', 'tabWorkflow Document State', 'tabWorkflow Transition',
            'tabWorkflow State', 'tabWorkflow Action Master', 'tabNotification', 'tabReport', 'tabPrint Format', 'tabServer Script'} <= strict
    assert list(snapshot['tables']['tabNotification']['rows']) == ['n-tenant']
    assert list(snapshot['tables']['tabReport']['rows']) == ['r-tenant']
    assert list(snapshot['tables']['tabPrint Format']['rows']) == ['pf-tenant']
    assert list(snapshot['tables']['tabRole']['rows']) == ['Tenant Role']


def test_the_after_snapshot_hashes_over_the_columns_the_before_snapshot_had_so_added_columns_do_not_change_hashes():
    """Review R3: a DDL that adds a column changes every whole-row hash of a hash-only table."""
    before_db = FakeDB({'tabDS Run Event': rows('E', 3, payload='{}')})
    before = rs.snapshot(FakeFrappe(before_db), detail_rows=1)
    after_db = FakeDB({'tabDS Run Event': rows('E', 3, payload='{}', new_column=None)})
    naive = rs.snapshot(FakeFrappe(after_db), detail_rows=1)
    assert naive['tables']['tabDS Run Event']['rows']['E00001']['hash'] != before['tables']['tabDS Run Event']['rows']['E00001']['hash']
    aligned = rs.snapshot(FakeFrappe(after_db), detail_rows=1, hash_columns={t: v['columns'] for t, v in before['tables'].items()})
    assert aligned['tables']['tabDS Run Event']['rows']['E00001']['hash'] == before['tables']['tabDS Run Event']['rows']['E00001']['hash']
    assert aligned['tables']['tabDS Run Event']['columns'] == ['name', 'modified', 'payload', 'new_column']
    assert 'hash_columns' in rs.container_script(hash_columns={'tabDS Run Event': ['name']})


def test_single_secrets_are_digested_patches_are_listed_and_the_format_is_versioned():
    db = FakeDB({}, singles={'Stock Settings': {'api_token': 'plain-token', 'valuation_method': 'FIFO'}})
    db.patches = ['frappe.patches.v16.one', 'dsherp_bridge.patches.two']
    snapshot = rs.snapshot(FakeFrappe(db))
    assert snapshot['singles']['Stock Settings']['api_token'].startswith('sha256:') and 'plain-token' not in json.dumps(snapshot)
    assert snapshot['patches'] == ['dsherp_bridge.patches.two', 'frappe.patches.v16.one']
    assert snapshot['format'] == rs.FORMAT


def test_a_site_beyond_the_row_ceiling_aborts_instead_of_growing_without_bound():
    db = FakeDB({'tabItem': rows('I', 30)})
    with pytest.raises(RuntimeError, match='max_rows'):
        rs.snapshot(FakeFrappe(db), max_rows=20)


def test_the_row_ceiling_is_enforced_before_reading_and_while_paging_not_after():
    """Review P2: the ceiling was checked after a whole table had been accumulated."""
    db = FakeDB({'tabItem': rows('I', 30)})
    with pytest.raises(RuntimeError, match='max_rows'):
        rs.snapshot(FakeFrappe(db), max_rows=20)
    assert not [q for q, _ in db.queries if 'select * from `tabItem`' in ' '.join(q.split())]  # refused on the count
    # a count that lies (rows appear while paging) is still caught page by page
    class GrowingDB(FakeDB):
        def sql(self, query, values=None, as_dict=False):
            if 'select count(*)' in ' '.join(query.split()):
                return [(5,)]
            return super().sql(query, values, as_dict)
    growing = GrowingDB({'tabItem': rows('I', 30)})
    with pytest.raises(RuntimeError, match='max_rows'):
        rs.snapshot(FakeFrappe(growing), page=10, max_rows=20)
    pages = [q for q, _ in growing.queries if 'select * from `tabItem`' in ' '.join(q.split())]
    assert len(pages) <= 3


def test_every_snapshot_records_the_exact_columns_each_hash_covers():
    db = FakeDB({'tabItem': rows('I', 2, item_name='x'), 'tabDS Run Event': rows('E', 2, payload='{}')})
    plain = rs.snapshot(FakeFrappe(db))
    assert plain['tables']['tabItem']['hash_columns'] == ['name', 'modified', 'item_name']
    aligned = rs.snapshot(FakeFrappe(db), hash_columns={'tabDS Run Event': ['name', 'payload']})
    assert aligned['tables']['tabDS Run Event']['hash_columns'] == ['name', 'payload']
    assert aligned['tables']['tabItem']['hash_columns'] == ['name', 'modified', 'item_name']
    assert 'hash_columns' not in aligned['settings']  # the per-table record replaces the boolean
