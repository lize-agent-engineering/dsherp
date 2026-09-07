"""The platform authorization leaves the run table (R7).

It is held in the Site cache for the run's lifetime now. Rows written before this carry the
encrypted grant of whoever asked; a definition reload does not drop a column, so the values
are cleared and the column removed here. A run still in flight at upgrade time loses its
authorization and fails closed at its next check - upgrades run in a quiet window."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "DS Model Run"):
        return
    columns = frappe.db.get_table_columns("DS Model Run")
    if "platform_grant" not in columns:
        return
    frappe.db.sql("update `tabDS Model Run` set platform_grant = NULL where platform_grant is not NULL")
    frappe.db.commit()
    frappe.db.sql_ddl("alter table `tabDS Model Run` drop column platform_grant")
