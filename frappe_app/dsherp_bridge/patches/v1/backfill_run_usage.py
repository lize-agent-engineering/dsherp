"""Give existing runs the usage numbers a new run gets at finish time.

The facts were always there - every model_response event carries the provider's own usage -
they were simply never summed onto the run. Reading them now costs one pass over the events
of finished runs and makes the first monthly report cover history rather than starting at
zero."""
import frappe

from dsherp_bridge.usage import storable, summarise

FINISHED = ("Succeeded", "Failed", "Cancelled")


def execute():
    if not frappe.db.exists("DocType", "DS Model Run"):
        return
    names = frappe.get_all("DS Model Run", filters={"status": ["in", FINISHED]}, pluck="name",
                           order_by="creation asc", limit_page_length=0)
    for name in names:
        events = frappe.get_all("DS Run Event", filters={"run": name},
                                fields=["kind", "payload", "recorded_at", "source"],
                                order_by="seq asc", limit_page_length=0)
        if not events:
            continue
        summary = storable(summarise(events))
        if not summary:
            continue
        # update_modified stays off: a backfill is not a change the user made.
        frappe.db.set_value("DS Model Run", name, summary, update_modified=False)
    frappe.db.commit()
