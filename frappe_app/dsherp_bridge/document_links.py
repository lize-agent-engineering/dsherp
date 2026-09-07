"""What became of the document an execution produced (T3).

An execution record points at its document with a Dynamic Link, so the document's own
Connections show what the assistant did to it. The link is listed in `ignore_links_on_delete`
on purpose: the business document belongs to the user, who may cancel or delete it, and an
audit record must never be the reason they cannot. When that happens the record is marked -
the fact that the assistant created that document stays true either way."""
import frappe

STATES = {"on_cancel": "Cancelled", "on_trash": "Deleted"}


def target_of(result, payload=None):
    """(doctype, name) of the document a finished execution produced, or (None, None).

    A failed or rolled-back execution produced nothing; a browser draft ('fill') was authorized
    but never written by us, so it is not claimed as ours either."""
    if not isinstance(result, dict) or result.get("status") not in ("Succeeded", "Authorized"):
        return None, None
    if (payload or {}).get("action") == "fill" or result.get("target") == "browser-draft":
        return None, None
    doctype, name = result.get("doctype"), result.get("name")
    if isinstance(doctype, str) and doctype and isinstance(name, str) and name:
        return doctype, name
    return None, None


def mark(doc, method=None):
    """Mark every execution record that produced this document. Never raises: a user cancelling
    a document must not be stopped by our bookkeeping."""
    state = STATES.get(method)
    if not state or not doc.get("doctype") or not doc.get("name"):
        return
    try:
        names = frappe.get_all("DS Execution Record",
                               filters={"target_doctype": doc.doctype, "target_name": doc.name},
                               pluck="name", limit_page_length=0)
        for name in names:
            frappe.db.set_value("DS Execution Record", name, "target_state", state, update_modified=False)
    except Exception as error:
        frappe.log_error(f"标注执行记录失败：{type(error).__name__}", "dsherp document link")
