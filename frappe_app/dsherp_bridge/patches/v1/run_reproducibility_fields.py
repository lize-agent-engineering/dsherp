"""A run records the prompt template version and the sampling it actually used, and the
status enum gains BudgetExceeded.

**Nothing is back-filled, on purpose.** The whole point of these two columns is
reproducibility: writing a value onto rows that predate them would turn "we did not record
this" into "we recorded this", which is the one thing the columns exist to prevent. Existing
rows keep an empty `prompt_version` and `sampling`, and that empty value is the honest answer.

`BudgetExceeded` is added to the Select options here so the six Sites take one migrate, not
two (ruling #6). No run is written into that state until the consumers land; adding an option
no row uses changes nothing for existing rows.

What G2's release comparison sees: two added columns and a widened Select. Column additions
are schema changes that are not reported per row, and no row value changes, so the
declaration below is empty of row edits and says so.
"""

EXPECTED_CHANGES = [
    {"doctype": "DS Model Run", "fields": [], "rows": "none",
     "columns_added": ["prompt_version", "sampling"]},
]


def execute():
    import frappe

    if not frappe.db.exists("DocType", "DS Model Run"):
        return
    frappe.reload_doc("dsherp_bridge", "doctype", "ds_model_run")
    columns = frappe.db.get_table_columns("DS Model Run")
    for column in ("prompt_version", "sampling"):
        if column not in columns:
            # post_model_sync: the definition must already have materialised the column.
            raise RuntimeError(f"DS Model Run 没有 {column} 列：DocType 定义未同步")
