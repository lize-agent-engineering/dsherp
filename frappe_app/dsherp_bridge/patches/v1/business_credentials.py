"""Give every key that already exists a window, so "short-lived" starts meaning something.

Before this, a business user's API key lived as long as the binding did. Enforcement now
refuses a key with no record, which would lock out every existing member at once - so each
non-service user that already holds a key gets one grandfathered window, marked `legacy`.
Their next platform login replaces it with a properly issued pair; if nobody logs in, the
key stops working when that window closes, which is the point."""
import frappe
from frappe.utils import now_datetime

from dsherp_bridge import business_credentials as policy


def execute():
    if not frappe.db.exists("DocType", "DS Business Credential"):
        return
    service = {"Administrator", "Guest", frappe.conf.get("dsherp_runtime_user")}
    issued_at = now_datetime()
    for row in frappe.get_all("User", filters={"enabled": 1}, fields=["name", "api_key"]):
        if not row.api_key or row.name in service:
            continue
        if frappe.db.exists("DS Business Credential", row.name):
            continue
        frappe.get_doc({
            "doctype": "DS Business Credential", "user": row.name, "api_key": row.api_key,
            "issued_at": issued_at, "expires_at": policy.expiry(issued_at), "version": 1,
            "revoked": 0, "legacy": 1, "issued_for": "migrated",
        }).insert(ignore_permissions=True)
