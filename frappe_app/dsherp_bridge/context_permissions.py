"""Snapshot native authorization inputs; this does not grant any permission."""
import hashlib
import json
import frappe

DOCTYPES=['Item','Customer']


def revision(user):
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
        'doctype_permissions':rows('DocPerm',{'parent':['in',DOCTYPES]}),
        'custom_permissions':rows('Custom DocPerm',{'parent':['in',DOCTYPES]}),
        'doctypes':rows('DocType',{'name':['in',DOCTYPES]}),
        'fields':rows('DocField',{'parent':['in',DOCTYPES]}),
        'custom_fields':rows('Custom Field',{'dt':['in',DOCTYPES]}),
        'properties':rows('Property Setter',{'doc_type':['in',DOCTYPES]}),
        'shares':frappe.get_all('DocShare',filters={'share_doctype':['in',DOCTYPES]},
            or_filters={'user':user,'everyone':1},fields=['*'],order_by='name asc'),
        'strict_user_permissions':frappe.db.get_single_value('System Settings','apply_strict_user_permissions'),
    }
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def require_revision(run):
    if not run.permission_revision or run.permission_revision!=revision(run.owner):
        raise frappe.PermissionError('业务权限已改变，需要新的运行上下文')
