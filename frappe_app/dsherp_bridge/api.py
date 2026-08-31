"""Narrow read APIs. Frappe session identity and permissions remain authoritative."""

import math

import frappe


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


def _field_schema_with_children(meta, field, user):
    result = _field_schema(field)
    if field.fieldtype == 'Table' and field.permlevel in meta.get_permlevel_access('read', user=user):
        child = frappe.get_meta(field.options)
        readable = _readable_fields(child, user, parenttype=meta.name)
        result['fields'] = [_field_schema(item) for item in child.fields if item.fieldname in readable]
    return result


@frappe.whitelist(methods=["GET"])
def read_schema(doctype: str):
    _authorize(doctype)
    meta = frappe.get_meta(doctype)
    permitted = _readable_fields(meta, frappe.session.user)
    permitted.update(field.fieldname for field in meta.fields
                     if field.fieldtype == 'Table'
                     and field.permlevel in meta.get_permlevel_access('read', user=frappe.session.user))
    fields=[_field_schema_with_children(meta, field, frappe.session.user)
            for field in meta.fields if field.fieldname in permitted]
    return {
        "doctype": doctype,
        "modified": meta.modified,
        "fields": fields,
    }


def _field_schema(field):
    return {key:field.get(key) for key in ('fieldname','fieldtype','label','options','reqd','read_only','default')}


@frappe.whitelist(methods=["GET"])
def read_record(doctype: str, name: str):
    _authorize(doctype)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    doc.apply_fieldlevel_read_permissions()
    permitted = _readable_fields(doc.meta, frappe.session.user)
    permitted.update(field.fieldname for field in doc.meta.fields
                     if field.fieldtype == 'Table' and field.permlevel in doc.get_permlevel_access('read'))
    fields={}
    for key in permitted:
        definition=doc.meta.get_field(key)
        if definition and definition.fieldtype=='Table':
            if definition.permlevel not in doc.get_permlevel_access('read'):
                continue
            child=frappe.get_meta(definition.options)
            readable=_readable_fields(child, frappe.session.user, parenttype=doctype)|{'name','idx'}
            fields[key]=[{field:value for field,value in row.as_dict().items() if field in readable}
                         for row in doc.get(key)]
        else:
            fields[key]=doc.get(key)
    if doc.meta.is_submittable:
        fields['docstatus']=doc.docstatus
    return {
        "doctype": doctype,
        "name": doc.name,
        "modified": doc.modified,
        "fields": fields,
    }


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
def search_records(doctype: str, query: str = "", filters=None, fields=None):
    _authorize(doctype)
    if not isinstance(query, str) or len(query) > 140:
        frappe.throw('query 必须是最多 140 个字符的文本')
    meta = frappe.get_meta(doctype)
    permitted = _searchable_fields(meta)
    if filters is not None:
        if query:
            frappe.throw('filters 批量读取不能同时使用 query')
        restricted = _restricted_filters(filters, permitted)
        selected = _restricted_result_fields([] if fields is None else fields, permitted)
        return frappe.get_list(
            doctype, filters=restricted, fields=selected,
            order_by='name asc', page_length=100,
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
            doctype, or_filters=fuzzy, fields=['name','modified'],
            order_by='name asc', page_length=20,
        )
    return frappe.get_list(
        doctype, fields=['name','modified'], order_by='name asc', page_length=20,
    )
