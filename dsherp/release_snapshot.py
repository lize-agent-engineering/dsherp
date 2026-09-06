# Snapshot a Site's data for gate G2 (dsherp/release_compare.py judges two of them).
#
# This module runs in two places with the same bytes: imported by the unit tests with a
# fake `frappe`, and sent by the CLI into the bench container as a stdin script (see
# container_script). Hence: no `import frappe` at module level, no triple-quoted strings
# (Bench.python re-indents every line), and only the queries the fake below can answer.
#
# Scope is decided from the Site's own metadata at run time and every DocType lands in
# exactly one bucket, so a new DocType can never fall through the cracks:
#   strict   - business documents and masters (every DocType of erpnext and of the two
#              dsherp Apps, custom DocTypes, contacts and identities): compared row by row
#              and field by field (values kept while the table is small enough);
#   log      - append-only records users read (comments, versions, deletions,
#              communications, activity): hashes only, so a lost or altered row shows;
#   excluded - Frappe's own metadata, caches, settings machinery and technical logs,
#              rewritten by every migrate and owned by code, not by the tenant.
# Metadata that tenants do write is partitioned by row instead of by DocType (custom
# DocTypes and their fields, non-system Custom Fields and Property Setters).
import hashlib
import json
import re
import time

BUSINESS_APPS = ('erpnext', 'dsherp_bridge', 'dsherp_platform')
STRICT_FRAPPE_DOCTYPES = (
    'User', 'Has Role', 'User Permission', 'Role Profile', 'File',
    'Contact', 'Address', 'Contact Email', 'Contact Phone', 'Dynamic Link',
    'OAuth Client', 'Social Login Key', 'Custom Field', 'Property Setter',
    'DocType', 'DocField', 'DocPerm', 'DocType Link', 'DocType Action', 'DocType State',
)
STRICT_SINGLES = ('System Settings',)
LOG_DOCTYPES = ('Comment', 'Version', 'Deleted Document', 'Communication', 'Activity Log')
# Rows of these metadata tables that belong to the tenant, not to the code.
ROW_PARTITION = {
    # Has Role also hangs off Reports, Pages and Dashboard Charts that every migrate re-imports.
    'tabHas Role': "parenttype in ('User', 'Role Profile')",
    'tabCustom Field': 'is_system_generated = 0',
    'tabProperty Setter': 'is_system_generated = 0',
    'tabDocType': 'custom = 1',
    'tabDocField': 'parent in (select name from `tabDocType` where custom = 1)',
    'tabDocPerm': 'parent in (select name from `tabDocType` where custom = 1)',
    'tabDocType Link': 'parent in (select name from `tabDocType` where custom = 1)',
    'tabDocType Action': 'parent in (select name from `tabDocType` where custom = 1)',
    'tabDocType State': 'parent in (select name from `tabDocType` where custom = 1)',
}
VOLATILE_COLUMNS = ('_comments', '_assign', '_liked_by', '_user_tags', '_seen')
SECRET_COLUMN = re.compile('(secret|password|token)$')
MARKER = 'DSHERP_SNAPSHOT '


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)


def _row_hash(values):
    material = {key: value for key, value in values.items() if key not in VOLATILE_COLUMNS}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _digest(value):
    return 'sha256:' + hashlib.sha256(str(value).encode()).hexdigest()


def classify(frappe):
    apps = {row['name']: row['app_name'] for row in frappe.db.sql('select name, app_name from `tabModule Def`', as_dict=True)}
    doctypes = frappe.db.sql('select name, module, istable, issingle, custom, is_virtual from `tabDocType`', as_dict=True)
    scope = {'strict': [], 'log': [], 'excluded': [], 'singles': [], 'excluded_singles': [], 'skipped_virtual': []}
    for row in doctypes:
        name = row['name']
        if row.get('is_virtual'):
            scope['skipped_virtual'].append(name)
            continue
        app = apps.get(row.get('module'))
        business = app in BUSINESS_APPS or bool(row.get('custom')) or app is None
        if row.get('issingle'):
            if business or name in STRICT_SINGLES:
                scope['singles'].append(name)
            else:
                scope['excluded_singles'].append(name)
            continue
        table = 'tab' + name
        if name in LOG_DOCTYPES:
            scope['log'].append(table)
        elif business or name in STRICT_FRAPPE_DOCTYPES or row.get('module') == 'Contacts':
            scope['strict'].append(table)
        else:
            scope['excluded'].append(table)
    for key in scope:
        scope[key].sort()
    return scope


def _columns(frappe, table):
    rows = frappe.db.sql('select column_name from information_schema.columns '
                         'where table_schema = database() and table_name = %s order by ordinal_position', (table,))
    return [row[0] for row in rows]


def _read_table(frappe, table, keep_values, page):
    partition = ROW_PARTITION.get(table)
    rows, last = {}, None
    while True:
        clauses = []
        values = []
        if partition:
            clauses.append(partition)
        if last is not None:
            clauses.append('name > %s')
            values.append(last)
        where = (' where ' + ' and '.join(clauses)) if clauses else ''
        chunk = frappe.db.sql('select * from `' + table + '`' + where + ' order by name limit ' + str(page),
                              tuple(values), as_dict=True)
        for raw in chunk:
            row = {}
            for column, value in raw.items():
                value = _jsonable(value)
                if value is not None and value != '' and SECRET_COLUMN.search(column):
                    value = _digest(value)
                row[column] = value
            rows[row['name']] = {'hash': _row_hash(row), 'values': row if keep_values else None}
        if len(chunk) < page:
            return rows
        last = chunk[-1]['name']


def snapshot(frappe, page=2000, detail_rows=5000):
    # Any exception propagates: a snapshot that skipped a table would certify nothing.
    scope = classify(frappe)
    tables, counts, timings = {}, {}, {}
    for bucket in ('strict', 'log'):
        for table in scope[bucket]:
            started = time.monotonic()
            try:
                partition = ROW_PARTITION.get(table)
                count = frappe.db.sql('select count(*) from `' + table + '`' + (' where ' + partition if partition else ''))[0][0]
                keep = bucket == 'strict' and count <= detail_rows
                tables[table] = {'columns': _columns(frappe, table), 'rows': _read_table(frappe, table, keep, page)}
                counts[table] = len(tables[table]['rows'])
            except Exception as error:
                raise RuntimeError('snapshot failed on ' + table + ': ' + type(error).__name__ + ': ' + str(error)[:200]) from error
            timings[table] = round(time.monotonic() - started, 3)
    wanted = set(scope['singles'])
    singles = {}
    for doctype, field, value in frappe.db.sql('select doctype, field, value from `tabSingles`'):
        if doctype in wanted:
            singles.setdefault(doctype, {})[field] = _jsonable(value)
    auth = {}
    for doctype, name, fieldname, encrypted, password in frappe.db.sql(
            'select doctype, name, fieldname, encrypted, password from `__Auth`'):
        auth[doctype + '|' + name + '|' + fieldname] = hashlib.sha256((str(encrypted) + ':' + str(password)).encode()).hexdigest()
    return {'site': getattr(frappe.local, 'site', None), 'tables': tables, 'singles': singles, 'auth': auth,
            'scope': scope, 'row_counts': counts, 'timings': timings,
            'settings': {'page': page, 'detail_rows': detail_rows}}


def container_script(page=2000, detail_rows=5000):
    # The module source itself plus the call; Bench.python supplies frappe and json.
    import inspect
    source = inspect.getsource(inspect.getmodule(snapshot))
    return source + '\nprint(' + repr(MARKER) + ' + json.dumps(snapshot(frappe, page=' + str(int(page)) \
        + ', detail_rows=' + str(int(detail_rows)) + '), ensure_ascii=False, default=str))\n'


def parse_output(text):
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise ValueError('snapshot output carries no ' + MARKER.strip() + ' line')
