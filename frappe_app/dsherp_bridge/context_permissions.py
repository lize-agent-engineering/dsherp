"""Snapshot native authorization inputs; this does not grant any permission."""
import hashlib
import json
import frappe

DOCTYPES=['Item','Customer','Sales Order']
CONFIGURATION_DOCTYPES=['DocType','Custom Field','Workflow','Workflow State','Workflow Action Master','Module Def','Role']


def revision(user,doctypes=None):
    doctypes=DOCTYPES if doctypes is None else list(doctypes)
    schema_doctypes=sorted(set(doctypes)|{field.options for name in doctypes for field in frappe.get_meta(name).get_table_fields()})
    def rows(doctype,filters):
        return frappe.get_all(doctype,filters=filters,fields=['*'],order_by='name asc')
    state={
        'user':user,
        'enabled':frappe.db.get_value('User',user,'enabled'),
        'user_type':frappe.db.get_value('User',user,'user_type'),
        'effective_roles':sorted(frappe.get_roles(user)),
        'role_definitions':rows('Role',{'name':['in',frappe.get_roles(user)]}),
        'roles':frappe.get_all('Has Role',filters={'parent':user,'parenttype':'User'},pluck='role',order_by='role asc'),
        'user_permissions':rows('User Permission',{'user':user}),
        'doctype_permissions':rows('DocPerm',{'parent':['in',doctypes]}),
        'custom_permissions':rows('Custom DocPerm',{'parent':['in',doctypes]}),
        'doctypes':rows('DocType',{'name':['in',schema_doctypes]}),
        'fields':rows('DocField',{'parent':['in',schema_doctypes]}),
        'custom_fields':rows('Custom Field',{'dt':['in',schema_doctypes]}),
        'properties':rows('Property Setter',{'doc_type':['in',schema_doctypes]}),
        'shares':frappe.get_all('DocShare',filters={'share_doctype':['in',doctypes]},
            or_filters={'user':user,'everyone':1},fields=['*'],order_by='name asc'),
        'strict_user_permissions':frappe.db.get_single_value('System Settings','apply_strict_user_permissions'),
    }
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def run_revision(user,domain):
    if domain not in ('query','operation','configuration'):frappe.throw('未知业务领域')
    return revision(user,doctypes=CONFIGURATION_DOCTYPES if domain=='configuration' else None)


def require_revision(run):
    if not run.permission_revision or run.permission_revision!=run_revision(run.owner,run.domain):
        raise frappe.PermissionError('业务权限已改变，需要新的运行上下文')
