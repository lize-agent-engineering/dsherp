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

FORMAT = 1
BUSINESS_APPS = ('erpnext', 'dsherp_bridge', 'dsherp_platform')
# Frappe-defined DocTypes whose records the tenant writes: identities, contacts, custom
# metadata, and the permissions, workflows, scripts and templates that decide what the
# tenant's users may do and see (the configuration executor creates workflows; role and
# permission rows decide access). Standard definitions shipped by apps are partitioned
# out below where the DocType mixes both.
STRICT_FRAPPE_DOCTYPES = (
    'User', 'Has Role', 'User Permission', 'Role', 'Role Profile', 'Custom DocPerm', 'File',
    'Contact', 'Address', 'Contact Email', 'Contact Phone', 'Dynamic Link',
    'OAuth Client', 'Social Login Key', 'Custom Field', 'Property Setter',
    'DocType', 'DocField', 'DocPerm', 'DocType Link', 'DocType Action', 'DocType State',
    'Workflow', 'Workflow State', 'Workflow Action Master', 'Workflow Document State', 'Workflow Transition',
    'Workflow Action', 'Notification', 'Report', 'Print Format', 'Web Form', 'Client Script', 'Server Script',
    'Letter Head', 'Assignment Rule', 'Auto Repeat', 'Email Account', 'Webhook',
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
    'tabNotification': 'is_standard = 0',
    'tabReport': "is_standard = 'No'",
    'tabPrint Format': "standard = 'No'",
    'tabWeb Form': 'is_standard = 0',
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


def row_hash(values, columns=None):
    # Stable digest of one row: sorted keys, JSON with default=str, volatile columns dropped.
    # `columns` restricts the digest to the columns the other snapshot has, so a column the
    # upgrade added does not change the digest of rows whose data did not change.
    material = {key: value for key, value in values.items()
                if key not in VOLATILE_COLUMNS and (columns is None or key in columns)}
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


def _read_table(frappe, table, keep_values, page, columns=None, budget=None):
    # `budget` is the number of rows this table may still contribute under max_rows; it is
    # enforced page by page because a count taken before reading can be stale.
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
            rows[row['name']] = {'hash': row_hash(row, columns), 'values': row if keep_values else None}
        if budget is not None and len(rows) > budget:
            raise RuntimeError('max_rows exceeded while paging ' + table)
        if len(chunk) < page:
            return rows
        last = chunk[-1]['name']


def snapshot(frappe, page=2000, detail_rows=5000, hash_columns=None, max_rows=1000000):
    # Any exception propagates: a snapshot that skipped a table would certify nothing.
    # `hash_columns` (table -> columns of the snapshot this one will be compared with) keeps
    # digests comparable across a schema that gained columns. `max_rows` is a ceiling on
    # what is held in memory, not a promise: rows are paged from the database but the
    # snapshot itself is one document.
    scope = classify(frappe)
    tables, counts, timings = {}, {}, {}
    total = 0
    for bucket in ('strict', 'log'):
        for table in scope[bucket]:
            started = time.monotonic()
            try:
                partition = ROW_PARTITION.get(table)
                count = frappe.db.sql('select count(*) from `' + table + '`' + (' where ' + partition if partition else ''))[0][0]
                if total + count > max_rows:
                    raise RuntimeError('max_rows=' + str(max_rows) + ' would be exceeded by ' + table + ' (' + str(count)
                                       + ' rows after ' + str(total) + '); raise the ceiling deliberately or narrow the scope')
                keep = bucket == 'strict' and count <= detail_rows
                columns = _columns(frappe, table)
                restrict = set(hash_columns[table]) if hash_columns and table in hash_columns else None
                hashed = [c for c in columns if c not in VOLATILE_COLUMNS and (restrict is None or c in restrict)]
                tables[table] = {'columns': columns, 'hash_columns': hashed,
                                 'rows': _read_table(frappe, table, keep, page, restrict, budget=max_rows - total)}
                counts[table] = len(tables[table]['rows'])
            except Exception as error:
                raise RuntimeError('snapshot failed on ' + table + ': ' + type(error).__name__ + ': ' + str(error)[:200]) from error
            timings[table] = round(time.monotonic() - started, 3)
            total += counts[table]
    wanted = set(scope['singles'])
    singles = {}
    for doctype, field, value in frappe.db.sql('select doctype, field, value from `tabSingles`'):
        if doctype in wanted:
            value = _jsonable(value)
            if value is not None and value != '' and SECRET_COLUMN.search(field):
                value = _digest(value)
            singles.setdefault(doctype, {})[field] = value
    patches = sorted(row[0] for row in frappe.db.sql('select patch from `tabPatch Log`'))
    auth = {}
    for doctype, name, fieldname, encrypted, password in frappe.db.sql(
            'select doctype, name, fieldname, encrypted, password from `__Auth`'):
        auth[doctype + '|' + name + '|' + fieldname] = hashlib.sha256((str(encrypted) + ':' + str(password)).encode()).hexdigest()
    return {'format': FORMAT, 'site': getattr(frappe.local, 'site', None), 'tables': tables, 'singles': singles,
            'auth': auth, 'patches': patches, 'scope': scope, 'row_counts': counts, 'timings': timings,
            'settings': {'page': page, 'detail_rows': detail_rows, 'max_rows': max_rows}}


def container_script(page=2000, detail_rows=5000, hash_columns=None):
    # The module source itself plus the call; Bench.python supplies frappe and json.
    import inspect
    source = inspect.getsource(inspect.getmodule(snapshot))
    return (source + '\nhash_columns=json.loads(' + repr(json.dumps(hash_columns)) + ')\n'
            + 'print(' + repr(MARKER) + ' + json.dumps(snapshot(frappe, page=' + str(int(page))
            + ', detail_rows=' + str(int(detail_rows)) + ', hash_columns=hash_columns), ensure_ascii=False, default=str))\n')


def parse_output(text):
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise ValueError('snapshot output carries no ' + MARKER.strip() + ' line')
