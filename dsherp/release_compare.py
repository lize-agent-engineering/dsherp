"""Compare two data snapshots of a Site: the judgement behind gate G2.

A snapshot (taken by dsherp.release_snapshot inside the bench container) is:

    {'tables': {table: {'columns': [...], 'rows': {name: {'hash': str, 'values': {col: val} | None}}}},
     'singles': {doctype: {field: value}},
     'auth': {'<doctype>|<name>|<fieldname>': hash},
     'scope': {...}}                         # informational, not compared

The comparison is field-level where the snapshot stored values and hash-level where it
stored only hashes (large tables). Differences are data drift unless a patch declared
them: an expectation names the patch, the DocType and either the fields whose values it
rewrites on existing rows, or the rows it inserts or deletes. Columns and tables that
exist only after the upgrade are schema additions and are reported as information, not
drift: a DocType JSON change materialises them and a default value in a new column is
not a change to anything that existed before. Columns or tables that vanished are drift
unless declared.

Pure Python on purpose: importable by the unit tests without Frappe and by the CLI on
the host; the same module text never runs inside the container.
"""
from dsherp.release_snapshot import FORMAT, VOLATILE_COLUMNS, row_hash  # one digest convention

# A Single is re-saved by migrate and by restore with identical values; only these move.
SINGLE_STAMPS = frozenset({'modified', 'modified_by'})
ROW_KINDS = ('existing', 'inserted', 'deleted', 'any')
DETAIL_LIMIT = 500


def _validate(expectations):
    checked = []
    for entry in expectations or ():
        if not isinstance(entry, dict) or not isinstance(entry.get('patch'), str) or not entry['patch']:
            raise ValueError('An expected change must name the patch that causes it: ' + repr(entry))
        if not isinstance(entry.get('doctype'), str) or not entry['doctype']:
            raise ValueError('An expected change must name a DocType: ' + repr(entry))
        fields = entry.get('fields', [])
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            raise ValueError('An expected change lists fields as a list of names: ' + repr(entry))
        rows = entry.get('rows', 'existing')
        if rows not in ROW_KINDS:
            raise ValueError('An expected change names rows as one of ' + '/'.join(ROW_KINDS) + ': ' + repr(entry))
        checked.append({'patch': entry['patch'], 'doctype': entry['doctype'], 'fields': fields, 'rows': rows,
                        'columns_removed': list(entry.get('columns_removed', []))})
    return checked


def _table_name(doctype):
    return doctype if doctype.startswith('tab') else 'tab' + doctype


def _declared(expectations, table, change, field=None):
    """The patch that declared this difference, or False."""
    for entry in expectations:
        if _table_name(entry['doctype']) != table and 'single:' + entry['doctype'] != table:
            continue
        # A row whose values were not stored proves nothing about which field moved, so only
        # a whole-row declaration ('*': the patch rewrites the rows) may cover it.
        if change == 'changed' and entry['rows'] in ('existing', 'any'):
            if '*' in entry['fields'] or (field is not None and field in entry['fields']):
                return entry['patch']
        if change == 'inserted' and entry['rows'] in ('inserted', 'any'):
            return entry['patch']
        if change == 'deleted' and entry['rows'] in ('deleted', 'any'):
            return entry['patch']
        if change == 'column_removed' and field in entry['columns_removed']:
            return entry['patch']
    return False


def compare(before, after, expectations=None, *, detail_limit=DETAIL_LIMIT, allow_inserts_in=()):
    """Return the report; `clean` is True only when every difference was declared.

    `allow_inserts_in` names tables (or 'tab'-less DocTypes) whose new rows are not
    drift: append-only logs the upgrade itself writes to.
    """
    formats = {before.get('format', FORMAT), after.get('format', FORMAT)}
    if len(formats) != 1:
        raise ValueError('snapshot format mismatch: ' + repr(sorted(formats)))
    expectations = _validate(expectations)
    inserts_ok = {_table_name(name) for name in allow_inserts_in}
    differences = []
    added_tables, removed_tables = [], []
    added_columns, removed_columns = {}, {}

    def note(table, name, change, field=None, before_value=None, after_value=None):
        declared = _declared(expectations, table, change, field)
        if not declared and change == 'inserted' and table in inserts_ok:
            declared = 'log-append'
        differences.append({'table': table, 'name': name, 'change': change, 'field': field,
                            'before': before_value, 'after': after_value, 'declared': declared})

    tables_before, tables_after = before.get('tables', {}), after.get('tables', {})
    for table in sorted(set(tables_before) | set(tables_after)):
        if table not in tables_before:
            added_tables.append(table)
            continue
        if table not in tables_after:
            removed_tables.append(table)
            note(table, '*', 'table_removed')
            continue
        first, second = tables_before[table], tables_after[table]
        columns_before, columns_after = set(first.get('columns', [])), set(second.get('columns', []))
        if columns_after - columns_before:
            added_columns[table] = sorted(columns_after - columns_before)
        if columns_before - columns_after:
            removed_columns[table] = sorted(columns_before - columns_after)
            for column in removed_columns[table]:
                note(table, '*', 'column_removed', column)
        rows_before, rows_after = first.get('rows', {}), second.get('rows', {})
        for name in sorted(set(rows_before) | set(rows_after)):
            if name not in rows_before:
                note(table, name, 'inserted')
                continue
            if name not in rows_after:
                note(table, name, 'deleted')
                continue
            old, new = rows_before[name], rows_after[name]
            old_values, new_values = old.get('values'), new.get('values')
            if old_values is not None and new_values is not None:
                # Field level: a column that only exists after the upgrade is a schema addition.
                for field in sorted(set(old_values) | set(new_values)):
                    if field in VOLATILE_COLUMNS or field not in old_values:
                        continue
                    if field not in new_values:
                        continue  # reported once above as column_removed
                    if old_values[field] != new_values[field]:
                        note(table, name, 'changed', field, old_values[field], new_values[field])
            elif old.get('hash') != new.get('hash'):
                note(table, name, 'changed')

    singles_before, singles_after = before.get('singles', {}), after.get('singles', {})
    for doctype in sorted(set(singles_before) | set(singles_after)):
        table = 'single:' + doctype
        old, new = singles_before.get(doctype, {}), singles_after.get(doctype, {})
        for field in sorted(set(old) | set(new)):
            if field in SINGLE_STAMPS:
                continue  # a re-save stamp carries no setting
            if field not in old:
                # tabSingles holds a row only once a setting was saved: a first stored value
                # is a write, not a schema addition.
                note(table, field, 'changed', field, None, new[field])
            elif field not in new:
                note(table, field, 'deleted', None, old[field], None)
            elif old[field] != new[field]:
                note(table, field, 'changed', field, old[field], new[field])

    auth_before, auth_after = before.get('auth', {}), after.get('auth', {})
    for key in sorted(set(auth_before) | set(auth_after)):
        if key not in auth_before:
            note('__Auth', key, 'inserted')
        elif key not in auth_after:
            note('__Auth', key, 'deleted')
        elif auth_before[key] != auth_after[key]:
            note('__Auth', key, 'changed')

    undeclared = sum(1 for row in differences if not row['declared'])
    rows_seen = sum(len(table.get('rows', {})) for table in tables_before.values())
    summary = {'tables': len(tables_before), 'rows': rows_seen, 'singles': len(singles_before),
               'auth': len(auth_before), 'differences': len(differences), 'undeclared': undeclared}
    report = {'clean': undeclared == 0, 'summary': summary,
              'added_tables': added_tables, 'removed_tables': removed_tables,
              'added_columns': added_columns, 'removed_columns': removed_columns,
              'expectations': expectations,
              'differences': differences[:detail_limit], 'truncated': max(0, len(differences) - detail_limit)}
    return report
