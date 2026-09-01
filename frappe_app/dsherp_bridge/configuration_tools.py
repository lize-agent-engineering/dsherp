"""Native configuration facts only; no business records or executable code."""
import hashlib
import frappe
from dsherp_bridge.context_api import _user,_json

FIELD_COLUMNS=('fieldname','label','fieldtype','options','reqd','in_list_view','permlevel')
GOVERNANCE_TARGETS=frozenset(('DS Doctype Policy','DS Doctype Policy Route'))


def _require_columns(doc,doctype,columns):
    access=doc.get_permlevel_access('read')
    meta=frappe.get_meta(doctype)
    if any((field:=meta.get_field(key)) and field.permlevel and field.permlevel not in access for key in columns):
        raise frappe.PermissionError('无权读取所需配置字段')


def read_configuration(doctype):
    _user()
    if not isinstance(doctype,str) or not doctype.strip() or len(doctype)>140:
        frappe.throw('请提供确切配置DocType名称')
    if doctype in GOVERNANCE_TARGETS:
        from dsherp_bridge.doctype_policy import require_action
        require_action(doctype, 'read')
    frappe.has_permission('DocType','read',throw=True)
    result={'doctype':doctype,'exists':bool(frappe.db.exists('DocType',doctype)),
        'modules':frappe.get_list('Module Def',pluck='name',order_by='name',limit_page_length=0),
        'roles':frappe.get_list('Role',filters={'disabled':0},pluck='name',order_by='name',limit_page_length=0)}
    if result['exists']:
        doc=frappe.get_doc('DocType',doctype);doc.check_permission('read')
        doc.apply_fieldlevel_read_permissions()
        visible=doc.as_dict()
        required={'name','modified','module','istable','is_submittable','fields'}
        _require_columns(doc,'DocType',required)
        _require_columns(doc,'DocField',FIELD_COLUMNS)
        if not required.issubset(visible):raise frappe.PermissionError('无权读取所需配置字段')
        result.update({key:visible[key] for key in required-{'fields'}})
        result['modified']=str(result['modified'])
        meta=frappe.get_meta(doctype)
        if any(field.get('is_custom_field') for field in meta.fields):
            frappe.has_permission('Custom Field','read',throw=True)
            for name in frappe.get_all('Custom Field',filters={'dt':doctype},pluck='name',order_by='name asc'):
                custom=frappe.get_doc('Custom Field',name);custom.check_permission('read')
                _require_columns(custom,'Custom Field',FIELD_COLUMNS)
        result['fields']=[{key:field.get(key) for key in FIELD_COLUMNS} for field in meta.fields]
    if len(_json(result).encode())>65536:frappe.throw('配置来源超出输入预算，请缩小配置对象')
    result['configuration_revision']=hashlib.sha256(_json(result).encode()).hexdigest()
    return result


def authorize_source(source):
    current=read_configuration(**source['arguments'])
    for key in ('modules','roles'):
        if set(source[key])-set(current[key]):raise frappe.PermissionError('历史配置来源权限已改变')
