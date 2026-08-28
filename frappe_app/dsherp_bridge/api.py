"""Narrow read APIs. Frappe session identity and permissions remain authoritative."""

import frappe


def _authorize(doctype):
    if frappe.session.user in ("Guest", "Administrator") or doctype not in ("Customer", "Item"):
        raise frappe.PermissionError("This read operation is not permitted")
    frappe.has_permission(doctype, "read", throw=True)


@frappe.whitelist(methods=["GET"])
def read_schema(doctype: str):
    _authorize(doctype)
    meta = frappe.get_meta(doctype)
    permitted = set(meta.get_permitted_fieldnames(user=frappe.session.user, permission_type="read"))
    return {
        "doctype": doctype,
        "modified": meta.modified,
        "fields": [
            {"fieldname": field.fieldname, "fieldtype": field.fieldtype,
             "label": field.label, "options": field.options, "reqd": field.reqd}
            for field in meta.fields if field.fieldname in permitted
        ],
    }


@frappe.whitelist(methods=["GET"])
def read_record(doctype: str, name: str):
    _authorize(doctype)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    doc.apply_fieldlevel_read_permissions()
    permitted = set(doc.meta.get_permitted_fieldnames(user=frappe.session.user, permission_type="read"))
    return {
        "doctype": doctype,
        "name": doc.name,
        "modified": doc.modified,
        "fields": {key: value for key, value in doc.as_dict().items() if key in permitted},
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
