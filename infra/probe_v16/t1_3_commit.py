"""Prove that v16 disables transaction control only inside registered doc_events."""
import os
import warnings
import frappe


SITE = "dsherp-v16probe.localhost"
MARKER = "commit-noop-marker"


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")

from dsherp_bridge import configuration_locks

original = configuration_locks.check_new_custom_record


def committing_handler(doc, method=None):
    frappe.db.sql(
        "INSERT INTO `tabDS V16 Probe Record` (name, title) VALUES (%s, %s)",
        (MARKER, MARKER),
    )
    frappe.db.commit()


try:
    configuration_locks.check_new_custom_record = committing_handler
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frappe.get_doc({"doctype": "ToDo", "description": "v16 commit hook probe"}).insert()
    if not any(
        "transaction control is disabled" in str(item.message).lower()
        or "commit/rollback are disabled" in str(item.message).lower()
        for item in caught
    ):
        raise AssertionError([str(item.message) for item in caught])
    frappe.db.rollback()
    if frappe.db.exists("DS V16 Probe Record", MARKER):
        raise AssertionError("doc_events commit persisted despite transaction-control disablement")

    if frappe.db._disable_transaction_control != 0:
        raise AssertionError("transaction-control flag leaked outside hook dispatch")
    with warnings.catch_warnings(record=True) as outside:
        warnings.simplefilter("always")
        frappe.db.commit()
    if outside:
        raise AssertionError([str(item.message) for item in outside])

    print("C1-R1 PASS: doc_events commit warned/no-op; transaction control restored outside hook")
finally:
    configuration_locks.check_new_custom_record = original
    frappe.db.rollback()
    frappe.destroy()
