"""Ruling #3 in the database: audit records keep their own history and nobody may delete them.

`bench migrate` reloads the DocType definitions, so track_changes and the permission rows
arrive on their own. What it does not do is remove a delete permission a Site already granted
by hand, or state a reason for a policy that predates the requirement - this patch does both,
so an existing Site ends up where a fresh one starts."""
import frappe

AUDIT_DOCTYPES = (
    "DS Model Run", "DS Run Event", "DS Operation Proposal", "DS Execution Record",
    "DS Configuration Bundle", "DS Configuration Confirmation", "DS Configuration Execution",
    "DS Configuration Transfer",
)


def execute():
    for doctype in AUDIT_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            continue
        frappe.db.set_value("DocType", doctype, "track_changes", 1, update_modified=False)
        # A Custom DocPerm someone added by hand outlives a definition reload.
        for table in ("DocPerm", "Custom DocPerm"):
            frappe.db.sql(
                f"update `tab{table}` set `delete` = 0, `cancel` = 0 where parent = %s",
                (doctype,),
            )
    if frappe.db.exists("DocType", "DS Doctype Policy"):
        frappe.db.set_value("DocType", "DS Doctype Policy", "track_changes", 1, update_modified=False)
        # Policies written before the rule existed keep working; their reason says so.
        frappe.db.sql(
            "update `tabDS Doctype Policy` set change_reason = %s "
            "where change_reason is null or change_reason = ''",
            ("迁移前既有策略，原因未记录",),
        )
    frappe.clear_cache()
