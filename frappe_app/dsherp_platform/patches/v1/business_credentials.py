"""Existing memberships hold a credential with no stated end; give them one window.

Reads now refuse a membership whose credential has expired. A binding written before that
rule has no expiry at all, which would read as expired and break every existing member on
the spot. Each one gets the same grandfathered window the business Site gives its keys; the
member's next login through SSO replaces it with an issued pair.

The row also has to say which business user its credential was provisioned for. That is what
the row already means today, and stating it lets a login on an expired credential tell an
unchanged binding from one that has been edited to name somebody else - without it, no
existing member could log in at all once their grandfathered window closed."""
import frappe
from frappe.utils import now_datetime

from dsherp_platform import business_credentials as policy


def execute():
    if not frappe.db.exists("DocType", "DS Membership"):
        return
    issued_at = now_datetime()
    for row in frappe.get_all("DS Membership", filters={"enabled": 1},
                              fields=["name", "erp_user", "api_key", "credential_expires_at",
                                      "credential_erp_user"]):
        values = {}
        if not row.credential_expires_at:
            values.update({"credential_issued_at": issued_at,
                           "credential_expires_at": policy.expiry(issued_at),
                           "credential_version": 0})
        if not row.credential_erp_user and row.api_key:
            values["credential_erp_user"] = row.erp_user
        if values:
            frappe.db.set_value("DS Membership", row.name, values, update_modified=False)
