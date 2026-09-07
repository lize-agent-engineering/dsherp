"""Give every membership its first counted binding version and its uniqueness key.

The index was created by model sync a moment ago on an all-NULL column; this fills the key
for every enabled binding (unique after the pre-sync de-duplication) and starts the version
counter at 1 wherever it is unset."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "DS Membership"):
        return
    for row in frappe.get_all("DS Membership", fields=["name", "enterprise", "erp_user", "enabled",
                                                        "binding_version", "active_binding"]):
        values = {}
        if row.enabled and not row.active_binding:
            values["active_binding"] = f"{row.enterprise}\0{row.erp_user}"
        if not row.binding_version:
            values["binding_version"] = 1
        if values:
            frappe.db.set_value("DS Membership", row.name, values, update_modified=False)
