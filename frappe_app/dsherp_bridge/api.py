"""Narrow read APIs. Frappe session identity and permissions remain authoritative."""

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


@frappe.whitelist(methods=["GET"])
def search_records(doctype: str, query: str = ""):
    _authorize(doctype)
    if not isinstance(query, str) or len(query) > 140:
        frappe.throw("Search query must be at most 140 characters")
    meta = frappe.get_meta(doctype)
    title = meta.title_field
    if not title or not meta.get_field(title):
        frappe.throw("Business object has no valid title field")
    permitted = set(meta.get_permitted_fieldnames(user=frappe.session.user, permission_type="read"))
    filters = {"name": ["like", "%" + query + "%"]}
    # Filtering unreadable fields would disclose their contents through matches.
    if title in permitted:
        filters[title] = ["like", "%" + query + "%"]
    return frappe.get_list(doctype, or_filters=filters,
                           fields=["name", "modified"], order_by="name asc", page_length=20)
