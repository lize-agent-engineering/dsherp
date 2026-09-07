"""A configuration transfer gets a validity window and its platform authorization leaves the row.

The transfer is an audit record (ruling #3): nobody may delete it, so whatever it stores is
stored for good. It used to store the member's encrypted platform OAuth token so that the
preview Site could later ask this Site to export the package under that member's authority.
That authority is now held in the Site cache under the transfer's id for the transfer's window
(dsherp_bridge.grants.stash_transfer), the way a run's is (R7), and the row records the window
as `expires_at`.

Existing rows: `expires_at` is back-filled as creation + window. For every row written before
this patch that moment has long passed, so they are simply expired - a transfer that was in
flight at upgrade time has to be started again, which is a minutes-long operation. Their grant
values are cleared and the column dropped; reloading the definition alone does not drop a column.

What G2 sees, and why the declaration below is only about the dropped column: a snapshot keeps
per-row values for a table this small, and a column that exists only after the upgrade is a
schema addition that is not reported per row. So the one difference the comparison reports is
the removed column. `fields` records what the patch writes, for the reader.

`frappe` is imported inside execute() on purpose: admin.py collects EXPECTED_CHANGES by
importing each patch, and the host-side test reads this declaration without a Frappe install.
"""

EXPECTED_CHANGES = [
    {"doctype": "DS Configuration Transfer", "fields": ["expires_at"], "rows": "existing",
     "columns_removed": ["platform_grant"]},
]


def execute():
    import frappe

    from dsherp_bridge.configuration_transfer import TRANSFER_WINDOW_SECONDS

    if not frappe.db.exists("DocType", "DS Configuration Transfer"):
        return
    columns = frappe.db.get_table_columns("DS Configuration Transfer")
    if "expires_at" not in columns:
        # post_model_sync: the definition must already have materialised the column.
        raise RuntimeError("DS Configuration Transfer 没有 expires_at 列：DocType 定义未同步，不回填")
    frappe.db.sql(
        "update `tabDS Configuration Transfer` set expires_at = date_add(creation, interval %s second) "
        "where expires_at is null",
        (int(TRANSFER_WINDOW_SECONDS),),
    )
    if "platform_grant" in columns:
        frappe.db.sql("update `tabDS Configuration Transfer` set platform_grant = NULL "
                      "where platform_grant is not NULL")
        frappe.db.commit()
        frappe.db.sql_ddl("alter table `tabDS Configuration Transfer` drop column platform_grant")
    frappe.db.commit()
