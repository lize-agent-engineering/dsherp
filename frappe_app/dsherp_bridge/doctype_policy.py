"""Server-owned DocType action policy; native ERP permissions remain final."""
import re

import frappe


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


def require_policy_schema():
    """Fail explicitly when this Site has not migrated the policy schema."""
    if not frappe.db.table_exists('DS Doctype Policy'):
        raise frappe.PermissionError('DS DocType 策略表未迁移至本站点')


def require_action(target_doctype, action):
    """Require one enabled policy row that explicitly allows the action."""
    require_policy_schema()
    field = ACTION_FIELDS.get(action)
    if not field:
        frappe.throw('未知 DocType 策略动作：' + str(action))
    policy = frappe.db.get_value(
        'DS Doctype Policy', {'target_doctype': target_doctype}, ['name', 'enabled', field], as_dict=True,
    )
    if not policy:
        raise frappe.PermissionError('缺少 DS DocType 策略：' + target_doctype)
    if not policy.enabled:
        raise frappe.PermissionError('DS DocType 策略未启用：' + target_doctype)
    if not policy.get(field):
        raise frappe.PermissionError('DS DocType 策略未允许 ' + target_doctype + ' 的 ' + action + ' 操作')


def require_enabled(target_doctype):
    """Require a policy row without granting any specific direct action."""
    require_policy_schema()
    policy = frappe.db.get_value(
        'DS Doctype Policy', {'target_doctype': target_doctype}, ['name', 'enabled'], as_dict=True,
    )
    if not policy:
        raise frappe.PermissionError('缺少 DS DocType 策略：' + target_doctype)
    if not policy.enabled:
        raise frappe.PermissionError('DS DocType 策略未启用：' + target_doctype)


def resolve_route(source_doctype, route_name):
    """Resolve one enabled, complete server-owned mapped-document route."""
    require_policy_schema()
    if not isinstance(source_doctype,str) or not source_doctype or not isinstance(route_name,str) or not route_name:
        frappe.throw('make 路由参数无效')
    policies=frappe.get_all('DS Doctype Policy',filters={'target_doctype':source_doctype},
                            fields=['name','enabled'],limit_page_length=2)
    if not policies:
        raise frappe.PermissionError('缺少 DS DocType 策略：'+source_doctype)
    if len(policies)!=1:
        frappe.throw('DS DocType 策略配置存在歧义：'+source_doctype)
    policy=policies[0]
    if not policy.enabled:
        raise frappe.PermissionError('DS DocType 策略未启用：'+source_doctype)
    matches=frappe.get_all('DS Doctype Policy Route',filters={
        'parent':policy.name,'parenttype':'DS Doctype Policy','parentfield':'routes','route_name':route_name,
    },fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc')
    if not matches:
        frappe.throw('未配置启用的 make 路由：'+source_doctype+' / '+route_name)
    if len(matches)!=1:
        frappe.throw('make 路由配置重复或存在歧义：'+source_doctype+' / '+route_name)
    resolved=dict(matches[0])
    if (any(not isinstance(resolved.get(key),str) or not resolved[key].strip()
            for key in ('route_name','method_path','target_doctype'))
        or resolved['route_name']!=route_name
        or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+',resolved['method_path'])
        or not frappe.db.exists('DocType',resolved['target_doctype'])):
        frappe.throw('make 路由配置无效：'+source_doctype+' / '+route_name)
    from dsherp_bridge.make_adapters import get_make_adapter
    get_make_adapter(source_doctype,resolved['route_name'],resolved['method_path'],resolved['target_doctype'])
    resolved['source_doctype']=source_doctype
    return resolved


def policy_revision_material():
    """Return every policy identity/state and full enabled-policy material."""
    require_policy_schema()
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
