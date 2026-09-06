"""Before the unique index on `active_binding` exists: no two enabled bindings may share a
business user in one enterprise, or the index cannot be created and the upgrade stops.

A duplicate is not silently dropped. The earliest binding keeps its place; later ones are
disabled and each one is written to the Error Log by name, so the operator sees exactly whom
the upgrade signed out and can decide. Runs pre-model-sync, when the column is not there yet."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "DS Membership"):
        return
    rows = frappe.db.sql(
        "select name, enterprise, erp_user, platform_user from `tabDS Membership` "
        "where enabled = 1 order by creation asc, name asc", as_dict=True)
    seen = {}
    for row in rows:
        key = (row.enterprise, row.erp_user)
        if key not in seen:
            seen[key] = row
            continue
        frappe.db.sql("update `tabDS Membership` set enabled = 0 where name = %s", (row.name,))
        frappe.log_error(
            title="DS Membership 重复绑定已停用",
            message=(f"企业 {row.enterprise} 的业务用户 {row.erp_user} 已绑定给 {seen[key].platform_user}；"
                     f"平台成员 {row.platform_user} 的绑定 {row.name} 已停用（唯一约束迁移）"))
