"""Narrow read APIs. Frappe session identity and permissions remain authoritative."""

import json
import math

import frappe

from dsherp_bridge.tool_limits import LIMITS


def _too_big(payload, cap):
    """Measured the way the model receives it: FastMCP serialises a tool result with
    `pydantic_core.to_json(result, indent=2)`, and the indentation is a real cost - two
    spaces on every line of every result."""
    try:
        from pydantic_core import to_json
        return len(to_json(payload, indent=2)) > cap
    except ImportError:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode()) * 13 // 10 > cap


def _authorize(doctype):
    if frappe.session.user in ("Guest", "Administrator"):
        raise frappe.PermissionError("This read operation is not permitted")
    from dsherp_bridge.doctype_policy import require_action
    require_action(doctype, 'read')
    frappe.has_permission(doctype, "read", throw=True)


def _readable_fields(meta, user, *, parenttype=None):
    return set(meta.get_permitted_fieldnames(
        parenttype=parenttype, user=user, permission_type="read"
    ))


def _as_list(value, label):
    """Arguments arrive as JSON text over HTTP and as real objects in-process."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            frappe.throw(f'{label} 必须是字段名列表')
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        frappe.throw(f'{label} 必须是字段名列表')
    return value


def _requested_tables(meta, requested, label):
    """The child tables the caller asked to expand, checked one by one.

    A cap on how many may be expanded at once, because expanding tables is the single biggest
    driver of a result's size (slice 0 measured -51% on a Sales Order from not expanding them).
    """
    names = _as_list(requested, label)
    if names is None:
        return []
    if len(names) > LIMITS['max_child_tables']:
        frappe.throw(f'{label} 最多 {LIMITS["max_child_tables"]} 张子表')
    access = meta.get_permlevel_access('read')
    for name in names:
        field = meta.get_field(name)
        if field is None or field.fieldtype != 'Table':
            frappe.throw(f'{name} 不是这个业务对象的子表')
        if field.permlevel not in access:
            raise frappe.PermissionError('无权读取所需子表')
    return names


def _child_columns(meta, field, user, after):
    child = frappe.get_meta(field.options)
    readable = _readable_fields(child, user, parenttype=meta.name)
    columns = [item for item in child.fields if item.fieldname in readable]
    start = 0
    if after:
        names = [item.fieldname for item in columns]
        if after not in names:
            frappe.throw('子表游标字段不存在')
        start = names.index(after) + 1
    return columns, start


def _field_schema_with_children(meta, field, user, expand, child_after=None):
    """One field, with its child columns only if the caller asked for that table."""
    result = _field_schema(field)
    if field.fieldtype != 'Table':
        return result
    # Named, but not inlined: this is what the model needs to decide whether to ask.
    result['rows_of'] = field.options
    if field.fieldname not in expand or field.permlevel not in meta.get_permlevel_access('read', user=user):
        return result
    columns, start = _child_columns(meta, field, user, (child_after or {}).get(field.fieldname))
    result['total_columns'] = len(columns)
    result['fields'] = [_field_schema(item) for item in columns[start:]]
    result['_column_offset'] = start
    return result


def _trim_columns(entry, fits):
    """Drop child columns until the page fits, and say how many were dropped.

    A wide child table does not fit in one page: Sales Order Item has 80 columns, 13,802
    bytes on its own and 16,426 once nested inside a result — the nesting alone costs 2,624,
    two spaces on every one of its lines. So the columns page like everything else, with a
    cursor of their own; nothing becomes unreachable.

    Trimmed in a loop rather than measured up-front because the `columns_truncated` marker is
    itself part of what has to fit.
    """
    offset = entry.pop('_column_offset', 0)
    total = entry.get('total_columns')
    columns = entry.get('fields')
    if columns is None:
        return entry
    kept = list(columns)
    while kept:
        candidate = dict(entry, fields=kept)
        if len(kept) < len(columns):
            candidate['columns_truncated'] = {
                'returned': len(kept), 'total': total,
                'next_after_child_fieldname': kept[-1]['fieldname']}
        if fits(candidate) or len(kept) == 1:
            return candidate
        kept = kept[:-1]
    return dict(entry, fields=[], columns_truncated={
        'returned': 0, 'total': total, 'next_after_child_fieldname': None,
    }) if offset or total else entry


@frappe.whitelist(methods=["GET"])
def read_schema(doctype: str, tables=None, after_fieldname: str = None, child_after=None):
    """The field structure of one business object, in pages.

    Child tables are named but not inlined unless asked for. Measured on the real Site
    (slice 0): with every table inlined, Sales Order was 62,674 bytes and Purchase Order
    57,183 - every DocType exceeded the 16KB cap, and the smallest still by 1.65x. Even
    without inlining, Sales Order is 23,102 bytes, so paging is the ordinary path here, not
    the exception.
    """
    _authorize(doctype)
    meta = frappe.get_meta(doctype)
    expand = set(_requested_tables(meta, tables, 'tables'))
    if isinstance(child_after, str):
        try:
            child_after = json.loads(child_after)
        except ValueError:
            frappe.throw('child_after 必须是 {子表: 字段名} 对象')
    if child_after is not None and (not isinstance(child_after, dict)
                                    or any(not isinstance(value, str) for value in child_after.values())):
        frappe.throw('child_after 必须是 {子表: 字段名} 对象')
    permitted = _readable_fields(meta, frappe.session.user)
    permitted.update(field.fieldname for field in meta.fields
                     if field.fieldtype == 'Table'
                     and field.permlevel in meta.get_permlevel_access('read', user=frappe.session.user))
    ordered = [field for field in meta.fields if field.fieldname in permitted]
    start = 0
    if after_fieldname:
        names = [field.fieldname for field in ordered]
        if after_fieldname not in names:
            frappe.throw('游标字段不存在')
        start = names.index(after_fieldname) + 1
    def shaped(fields, truncated, cursor=None):
        result = {"doctype": doctype,
                  # Follows meta.modified only. If it moved with `tables`, a propose_create
                  # made after a wide read would be refused for a version change that never
                  # happened.
                  "modified": meta.modified,
                  "total_fields": len(ordered), "returned": len(fields),
                  "truncated": truncated, "fields": fields}
        if cursor:
            result['next_after_fieldname'] = cursor
        return result

    def budget(fields):
        """Measured against the widest shape the page could end up as: truncated, and
        carrying the cursor key. Budgeting without them leaves a few dozen bytes
        unaccounted for, and the page comes back over the cap by exactly that much."""
        cursor = fields[-1]['fieldname'] if fields else None
        return not _too_big(shaped(fields, True, cursor), LIMITS['schema_max_bytes'])

    page, truncated = [], False
    for field in ordered[start:]:
        entry = _field_schema_with_children(meta, field, frappe.session.user, expand, child_after)
        entry = _trim_columns(entry, lambda probe: budget(page + [probe]))
        if budget(page + [entry]):
            page.append(entry)
            continue
        if page:
            # Start a new page rather than shrinking further: a wide table that does not fit
            # beside other fields still fits on a page of its own.
            truncated = True
            break
        # One field always makes progress, over cap or not: a page that returns nothing
        # leaves the cursor where it was and the caller loops forever.
        page.append(entry)
        truncated = True
        break
    return shaped(page, truncated, page[-1]['fieldname'] if truncated and page else None)


def _field_schema(field):
    """One field's structure, without the keys it does not have.

    `options` and `default` are null on most fields, and a Sales Order Item has about eighty
    columns: carrying those nulls is most of what pushed an expanded child table over the
    16KB cap by a dozen bytes. `reqd: 0` and `read_only: 0` are kept - those are answers, not
    absences, the same distinction `read_record` draws."""
    return {key: value for key, value in
            ((key, field.get(key)) for key in
             ('fieldname', 'fieldtype', 'label', 'options', 'reqd', 'read_only', 'default'))
            if value is not None}


def _routes_for(doc):
    """Which server-owned routes this exact record is ready for, and why not for the rest.

    Evaluated per record, not per DocType: "can I make a Purchase Receipt from this" depends
    on whether *this* order is submitted, not subcontracted, and not closed.

    This is what lets the token list leave `SKILL.md`. The refusal in `resolve_route` only
    echoes the name the model guessed wrong; it never lists what is available - so before
    this existed, a model that did not memorise the seven names had no way to find them.

    Counted in the byte total but never truncated: there are at most seven of them, and a
    half-list of available next steps is worse than none. Not recorded in `sources` and not
    replayed by `authorize_sources` - it reveals no field values, only which steps the
    server would accept.
    """
    from dsherp_bridge.doctype_policy import require_policy_schema
    from dsherp_bridge.make_adapters import _ADAPTERS, route_requirements, unmet_requirements
    require_policy_schema()
    policy = frappe.db.get_value('DS Doctype Policy',
                                 {'target_doctype': doc.doctype, 'enabled': 1}, 'name')
    if not policy:
        return []
    enabled = frappe.get_all('DS Doctype Policy Route',
                             filters={'parent': policy, 'parenttype': 'DS Doctype Policy',
                                      'parentfield': 'routes'},
                             fields=['route_name', 'method_path', 'target_doctype'],
                             order_by='idx asc,name asc')
    routes = []
    for row in enabled:
        key = (doc.doctype, row['route_name'], row['method_path'], row['target_doctype'])
        if key not in _ADAPTERS:
            # Configured but not registered in code. `resolve_route` would refuse it, so
            # offering it here would be offering something that cannot be done.
            continue
        requirement = route_requirements(*key)
        unmet = unmet_requirements(doc, requirement)
        progress = requirement['progress_field']
        routes.append({'route': row['route_name'], 'target_doctype': row['target_doctype'],
                       'ready': not unmet, 'unmet': unmet,
                       'progress_field': progress, 'progress_value': doc.get(progress)})
    return routes


def _is_empty(value):
    """Only None and the empty string are absences.

    `0`, `False`, `0.0` and `[]` are answers, and `docstatus` is the one that matters most:
    about ten integration assertions read `docstatus == 0` to know a document is still a
    draft. Dropping a zero would tell the model a field it can see is unset."""
    return value is None or value == ''


def _child_rows(doc, definition, doctype, after):
    child = frappe.get_meta(definition.options)
    readable = _readable_fields(child, frappe.session.user, parenttype=doctype) | {'name', 'idx'}
    rows = doc.get(definition.fieldname) or []
    remaining = [row for row in rows if int(row.idx or 0) > after]
    page = remaining[:LIMITS['child_rows_per_page']]
    shaped = [{field: value for field, value in row.as_dict().items() if field in readable}
              for row in page]
    more = len(remaining) > len(page)
    return shaped, len(rows), more


@frappe.whitelist(methods=["GET"])
def read_record(doctype: str, name: str, fields=None, children=None,
                include_empty: bool = False, after_idx=None):
    """One record, only as much of it as was asked for.

    Two defaults do the work, both from slice 0's measurements on the real Site: child tables
    are counted rather than expanded (-51% on a Sales Order, -52% on a Stock Entry - a
    record's size is driven by rows, not by field count), and fields with no value are left
    out (a further 15-23%). Everything held back is reachable by naming it.
    """
    _authorize(doctype)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    doc.apply_fieldlevel_read_permissions()
    permitted = _readable_fields(doc.meta, frappe.session.user)
    permitted.update(field.fieldname for field in doc.meta.fields
                     if field.fieldtype == 'Table' and field.permlevel in doc.get_permlevel_access('read'))
    expand = _requested_tables(doc.meta, children, 'children')
    wanted = _as_list(fields, 'fields')
    if wanted is not None:
        unknown = [key for key in wanted if key not in permitted and key != 'docstatus']
        if unknown:
            frappe.throw('fields 包含当前用户不可读的字段：' + ', '.join(sorted(unknown)))
    cursors = after_idx
    if isinstance(cursors, str):
        try:
            cursors = json.loads(cursors)
        except ValueError:
            frappe.throw('after_idx 必须是 {子表: idx} 对象')
    if cursors is not None and (not isinstance(cursors, dict)
                                or any(not isinstance(value, int) for value in cursors.values())):
        frappe.throw('after_idx 必须是 {子表: idx} 对象')
    cursors = cursors or {}

    values, tables, truncated, omitted = {}, {}, {}, 0
    expanded = {}
    for key in sorted(permitted):
        definition = doc.meta.get_field(key)
        if definition is not None and definition.fieldtype == 'Table':
            if definition.permlevel not in doc.get_permlevel_access('read'):
                continue
            if key not in expand:
                tables[key] = {'child_doctype': definition.options,
                               'rows': len(doc.get(key) or [])}
                continue
            cursor = int(cursors.get(key, 0) or 0)
            rows, total, more = _child_rows(doc, definition, doctype, cursor)
            values[key] = rows
            expanded[key] = {'total': total, 'cursor': cursor}
            if more:
                truncated.setdefault('tables', {})[key] = {
                    'returned': len(rows), 'total': total,
                    'next_after_idx': rows[-1]['idx'] if rows else cursor}
            continue
        value = doc.get(key)
        if wanted is not None:
            # Naming a field is asking whether it has a value, so a named field always comes
            # back - empty or not.
            if key in wanted:
                values[key] = value
            continue
        if not include_empty and _is_empty(value):
            omitted += 1
            continue
        values[key] = value
    if doc.meta.is_submittable and (wanted is None or 'docstatus' in wanted):
        values['docstatus'] = doc.docstatus
    result = {
        "doctype": doctype,
        "name": doc.name,
        "modified": doc.modified,
        "fields": values,
        "omitted_empty": omitted,
    }
    if tables:
        result['child_tables'] = tables
    routes = _routes_for(doc)
    if routes:
        result['routes'] = routes
    if truncated:
        result['truncated'] = truncated
    _fit_bytes(result, values, expanded, LIMITS['record_max_bytes'])
    return result


def _fit_bytes(result, values, expanded, limit):
    """Bring an expanded record under the byte cap by dropping child rows, and say so.

    The cap used to be a label: `truncated['bytes']` was set and **nothing was removed**. A
    Sales Order with 8 expanded lines came back at ~21KB against a 16KB cap that
    `tool_limits.describe_read_record()` promises the model in so many words, and at 20 lines
    — exactly `child_rows_per_page` — at ~52KB, three times over. Two or three such reads
    exhaust `model_max_input_bytes_per_call` and the run ends on budget. The reverse was just
    as wrong: a record that fitted after all could be labelled truncated with no cursor to
    follow, telling the model it was missing something it was not.

    Rows come off the longest table first, so one huge table cannot starve the others, and the
    cursor is rewritten each time — `next_after_idx` always names the last row actually
    returned, so the model can ask for the rest. `bytes` is set only when there is nothing
    left to drop: then the record genuinely does not fit and the model has to narrow `fields`
    rather than page.
    """
    while _too_big(result, limit):
        longest = max((key for key in expanded if values.get(key)),
                      key=lambda key: len(values[key]), default=None)
        if longest is None:
            break
        values[longest] = values[longest][:-1]
        kept = values[longest]
        result.setdefault('truncated', {}).setdefault('tables', {})[longest] = {
            'returned': len(kept), 'total': expanded[longest]['total'],
            'next_after_idx': kept[-1]['idx'] if kept else expanded[longest]['cursor']}
    if _too_big(result, limit):
        result.setdefault('truncated', {})['bytes'] = limit


def _searchable_fields(meta):
    permitted = _readable_fields(meta, frappe.session.user) | {'name', 'modified'}
    if meta.is_submittable:
        permitted.add('docstatus')
    return permitted


def _safe_filter_scalar(value):
    return (value is None or type(value) in (str, int, bool)
            or type(value) is float and math.isfinite(value))


def _restricted_filters(filters, permitted):
    if not isinstance(filters, dict) or not filters or len(filters) > 20:
        frappe.throw('filters 必须是包含 1 至 20 个字段的对象')
    if any(not isinstance(field, str) or field not in permitted for field in filters):
        frappe.throw('filters 包含当前用户不可读的字段')
    result = {}
    for field, value in filters.items():
        if _safe_filter_scalar(value):
            result[field] = value
            continue
        if not isinstance(value, list) or len(value) != 2 or value[0] not in ('=', 'in'):
            frappe.throw('filters 仅支持精确等于或 in')
        operator, operand = value
        if operator == '=':
            if not _safe_filter_scalar(operand):
                frappe.throw('等于筛选值必须是安全标量')
        elif (not isinstance(operand, list) or not operand or len(operand) > 100
              or not all(_safe_filter_scalar(item) for item in operand)):
            frappe.throw('in 筛选必须包含 1 至 100 个安全标量')
        result[field] = [operator, operand]
    return result


def _restricted_result_fields(fields, permitted):
    if not isinstance(fields, list) or len(fields) > 20:
        frappe.throw('fields 必须是最多 20 项的字段列表')
    if (any(not isinstance(field, str) or field not in permitted for field in fields)
            or len(set(fields)) != len(fields)):
        frappe.throw('fields 包含重复项或当前用户不可读的字段')
    return ['name', 'modified', *(
        field for field in fields if field not in ('name', 'modified')
    )]


def _as_filter_list(restricted):
    """The validated filter dict as Frappe's triple form, so a cursor condition can be added
    alongside it. `_restricted_filters` has already checked every field and value."""
    triples = []
    for field, value in restricted.items():
        if isinstance(value, list):
            operator, operand = value
            triples.append([field, operator, operand])
        else:
            triples.append([field, '=', value])
    return triples


def _search_match_fields(meta, permitted, query, filters):
    if filters is not None:
        return sorted(filters)
    if not query:
        return []
    result = ['name']
    if meta.title_field in permitted:
        result.append(meta.title_field)
    return result


@frappe.whitelist(methods=["GET"])
def search_records(doctype: str, query: str = "", filters=None, fields=None, after_name: str = None):
    """Rows of one business object, ordered by name.

    **Still returns a plain list.** `context_execution` builds `record_versions` and its
    `_tool_summary` from that, and seven assertions in `test_filtered_search.py` read it as
    one; wrapping it in an object would be a large refactor for no gain to the model. The
    paging hint the model actually needs - that there may be more - is added by the envelope
    on the container side, which can see the page length without changing this shape.

    `after_name` is a readable cursor over the ordering the server already applies, not a
    token: the model can see what it means and the server can check it.
    """
    _authorize(doctype)
    if not isinstance(query, str) or len(query) > LIMITS['query_max_chars']:
        frappe.throw(f'query 必须是最多 {LIMITS["query_max_chars"]} 个字符的文本')
    if after_name is not None and (not isinstance(after_name, str) or not after_name.strip()):
        frappe.throw('after_name 必须是上一页最后一个 name')
    meta = frappe.get_meta(doctype)
    permitted = _searchable_fields(meta)
    after = [['name', '>', after_name]] if after_name else []
    if filters is not None:
        if query:
            frappe.throw('filters 批量读取不能同时使用 query')
        restricted = _restricted_filters(filters, permitted)
        selected = _restricted_result_fields([] if fields is None else fields, permitted)
        return frappe.get_list(
            doctype, filters=[*_as_filter_list(restricted), *after], fields=selected,
            order_by='name asc', page_length=LIMITS['search_page_length'],
        )
    if fields is not None:
        frappe.throw('fields 仅可与 filters 一起使用')
    title = meta.title_field
    if query:
        if not title or not meta.get_field(title):
            frappe.throw('业务对象没有可搜索的标题字段')
        # Match fields are also persisted by context_execution and reauthorized.
        fuzzy = {
            field:['like','%'+query+'%']
            for field in _search_match_fields(meta, permitted, query, filters)
        }
        return frappe.get_list(
            doctype, filters=after, or_filters=fuzzy, fields=['name','modified'],
            order_by='name asc', page_length=LIMITS['search_name_page_length'],
        )
    return frappe.get_list(
        doctype, filters=after, fields=['name','modified'], order_by='name asc',
        page_length=LIMITS['search_name_page_length'],
    )
