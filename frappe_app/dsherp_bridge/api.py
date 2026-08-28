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
        "fields": {key: value for key, value in doc.as_dict().items() if key in permitted},
    }
