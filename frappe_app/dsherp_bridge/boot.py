"""Server-derived page-context classification for the current Desk user."""
import re

import frappe

HOST = re.compile(r'^[a-z0-9.-]{1,253}$')
MAX_LINK_HOSTS = 20


def link_hosts():
    """Hosts whose absolute URLs a model answer may render as a clickable link.

    The answer is untrusted text, so this list can only come from the server: deciding in
    the browser would mean deciding with the attacker's own data. The site's own host is
    deliberately not folded in — that would widen the allowlist without anyone choosing to.
    A bad value yields an empty list and a logged error; one wrong config line must not
    take Desk down.
    """
    configured = frappe.conf.get('dsherp_link_hosts')
    if configured is None:
        return []
    if not isinstance(configured, list) or len(configured) > MAX_LINK_HOSTS:
        frappe.log_error('dsherp_link_hosts 配置无效')
        return []
    hosts = set()
    for value in configured:
        if not isinstance(value, str) or not HOST.match(value):
            frappe.log_error('dsherp_link_hosts 配置无效')
            return []
        hosts.add(value)
    return sorted(hosts)


def boot_session(bootinfo):
    """Expose context candidates, never an authorization grant."""
    user = frappe.session.user
    if user in ('Guest', 'Administrator'):
        bootinfo.dsherp_context_doctypes = []
        bootinfo.dsherp_link_hosts = []
        return
    bootinfo.dsherp_link_hosts = link_hosts()
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
