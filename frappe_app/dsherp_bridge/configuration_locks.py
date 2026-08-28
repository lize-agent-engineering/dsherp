"""Connection-scoped locks shared by native configuration saves and publishing.

Frappe DDL commits internally. Transaction callbacks therefore cannot delimit
these locks: HTTP releases after request completion; CLI callers release in
finally, and connection closure always releases MariaDB advisory locks.
"""
import hashlib
import frappe


def lock_key(doctype):
    return hashlib.sha256((frappe.local.site+'\0'+doctype).encode()).hexdigest()


def acquire(doctypes):
    held=getattr(frappe.local,'dsherp_configuration_locks',None)
    if held is None:
        held=set();frappe.local.dsherp_configuration_locks=held
    for doctype in sorted(set(doctypes)):
        key=lock_key(doctype)
        if key in held:continue
        if frappe.db.sql('SELECT GET_LOCK(%s,0)',key)[0][0]!=1:
            frappe.throw('此配置正在保存或发布，请稍后核实最新配置')
        held.add(key)


def release(request=None,response=None):
    held=getattr(frappe.local,'dsherp_configuration_locks',set())
    for key in tuple(held):
        frappe.db.sql('SELECT RELEASE_LOCK(%s)',key)
        held.remove(key)


def lock_native(doc,method=None,*args,**kwargs):
    target_field={'Custom Field':'dt','Property Setter':'doc_type','Workflow':'document_type'}.get(doc.doctype)
    targets=[]
    if doc.doctype=='DocType':
        targets.append(doc.name)
        if method=='before_rename':targets.extend(value for value in args[:2] if isinstance(value,str))
    elif doc.doctype in ('Workflow State','Workflow Action Master'):
        targets.append(doc.doctype)
    elif target_field:
        targets.append(doc.get(target_field))
        if not doc.is_new():targets.append(frappe.db.get_value(doc.doctype,doc.name,target_field))
    acquire([target for target in targets if target])


def check_new_custom_record(doc,method=None):
    # New app tables become visible after native DDL commits, before the next
    # workflow step. Do not let another request create rows in that window.
    if not frappe.get_meta(doc.doctype).custom:return
    owner,current=frappe.db.sql('SELECT IS_USED_LOCK(%s), CONNECTION_ID()',lock_key(doc.doctype))[0]
    if owner is not None and owner!=current:frappe.throw('新应用配置正在应用，请完成后再创建记录')
