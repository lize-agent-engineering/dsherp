"""Server-derived page-context classification for the current Desk user."""
import frappe


def boot_session(bootinfo):
    """Expose context candidates, never an authorization grant."""
    user = frappe.session.user
    if user in ('Guest', 'Administrator'):
        bootinfo.dsherp_context_doctypes = []
        return
    from dsherp_bridge.doctype_policy import require_policy_schema
    require_policy_schema()
    doctypes = frappe.get_all(
        'DS Doctype Policy',
        filters={'enabled': 1, 'allow_read': 1},
        pluck='target_doctype',
        order_by='target_doctype asc',
    )
    bootinfo.dsherp_context_doctypes = [
        doctype for doctype in doctypes if frappe.has_permission(doctype, 'read', user=user)
    ]
