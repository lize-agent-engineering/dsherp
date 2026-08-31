"""Server-owned DocType action policy; native ERP permissions remain final."""
import frappe


COMPATIBILITY_DOCTYPES = frozenset({'Item', 'Customer', 'Sales Order'})
ACTION_FIELDS = {
    'read': 'allow_read',
    'create': 'allow_create',
    'update': 'allow_update',
    'submit': 'allow_submit',
    'cancel': 'allow_cancel',
    'fill': 'allow_fill',
}
POLICY_FIELDS = (
    'target_doctype', 'enabled', 'allow_read', 'allow_create', 'allow_update',
    'allow_submit', 'allow_cancel', 'allow_fill', 'company_scope',
)


def require_action(target_doctype, action):
    """Require an explicit action when a policy exists; only the old trio may lack one."""
    field = ACTION_FIELDS.get(action)
    if not field:
        frappe.throw('未知 DocType 策略动作：' + str(action))
    policy = frappe.db.get_value(
        'DS Doctype Policy', {'target_doctype': target_doctype}, ['name', 'enabled', field], as_dict=True,
    )
    if not policy:
        if target_doctype in COMPATIBILITY_DOCTYPES:
            return
        raise frappe.PermissionError('缺少 DS DocType 策略：' + target_doctype)
    if not policy.enabled:
        raise frappe.PermissionError('DS DocType 策略未启用：' + target_doctype)
    if not policy.get(field):
        raise frappe.PermissionError('DS DocType 策略未允许 ' + target_doctype + ' 的 ' + action + ' 操作')


def policy_revision_material():
    """Return every policy identity/state and full enabled-policy material."""
    policies = frappe.get_all(
        'DS Doctype Policy', fields=['name', *POLICY_FIELDS],
        order_by='target_doctype asc',
    )
    names = [row['name'] for row in policies if row['enabled']]
    routes = frappe.get_all(
        'DS Doctype Policy Route', filters={'parent': ['in', names]},
        fields=['parent', 'idx', 'route_name', 'method_path', 'target_doctype'],
        order_by='parent asc, idx asc, name asc',
    ) if names else []
    by_parent = {name: [] for name in names}
    for route in routes:
        by_parent[route.pop('parent')].append(route)
    material = []
    for policy in policies:
        row = {'target_doctype': policy['target_doctype'], 'enabled': policy['enabled']}
        if policy['enabled']:
            row.update({field: policy[field] for field in POLICY_FIELDS})
            row['routes'] = by_parent[policy['name']]
        material.append(row)
    return material
