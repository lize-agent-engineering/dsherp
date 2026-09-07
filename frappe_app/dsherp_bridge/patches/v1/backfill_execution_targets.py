"""Point existing execution records at the documents they produced.

The information was always in the stored result JSON; it simply had no field of its own, so
the document side could not find the record through Connections. This reads each finished
record's own result and fills the link in - and says so honestly when the document has since
been deleted."""
import json

import frappe

from dsherp_bridge.document_links import target_of


def execute():
    if not frappe.db.exists("DocType", "DS Execution Record"):
        return
    rows = frappe.get_all("DS Execution Record", filters={"status": ["!=", "Running"]},
                          fields=["name", "result", "proposal"], limit_page_length=0)
    for row in rows:
        try:
            result = json.loads(row.result or "{}")
        except ValueError:
            continue
        payload = {}
        proposal = frappe.db.get_value("DS Operation Proposal", row.proposal, "payload") if row.proposal else None
        if proposal:
            try:
                payload = json.loads(proposal)
            except ValueError:
                payload = {}
        doctype, name = target_of(result, payload)
        if not doctype:
            continue
        state = "Present" if frappe.db.exists(doctype, name) else "Deleted"
        frappe.db.set_value("DS Execution Record", row.name,
                            {"target_doctype": doctype, "target_name": name, "target_state": state},
                            update_modified=False)
    frappe.db.commit()
