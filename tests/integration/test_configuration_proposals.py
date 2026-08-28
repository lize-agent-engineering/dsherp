import subprocess


def test_native_configuration_proposal_is_immutable_permission_checked_and_has_no_ddl():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle,get_bundle,check_bundle
try:
    user='dsherp-config-test@example.invalid'
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':user,'first_name':'Synthetic Config','send_welcome_email':0,'roles':[{'role':'System Manager'}]}).insert()
    frappe.set_user(user)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Configuration zero DDL'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Synthetic Inspection','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'Sales User','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    result=propose_bundle(conversation.name,package)
    assert result['site']=='dsherp-validation.localhost'
    assert not frappe.db.exists('DocType','DS Synthetic Inspection')
    assert not frappe.db.table_exists('DS Synthetic Inspection')
    assert get_bundle(result['id'])['digest']==result['digest']
    assert check_bundle(result['id'],result['digest'])['id']==result['id']
    extension={'version':1,'doctypes':[],'extensions':[{'doctype':'Item','fields':[{'fieldname':'custom_ds_pending_note','label':'Pending note','fieldtype':'Data'}]}],'workflows':[]}
    extended=propose_bundle(conversation.name,extension)
    assert not frappe.get_meta('Item').has_field('custom_ds_pending_note')
    frappe.get_doc({'doctype':'Property Setter','doctype_or_field':'DocField','doc_type':'Item','field_name':'item_name','property':'label','property_type':'Data','value':'Synthetic concurrent label'}).insert()
    try:check_bundle(extended['id'],extended['digest']);raise AssertionError('stale config baseline')
    except frappe.ValidationError as error:assert '基线已变化' in str(error)
    doc=frappe.get_doc('DS Configuration Bundle',result['id']);doc.payload='{}'
    try:doc.save(ignore_permissions=True);raise AssertionError('mutable bundle')
    except frappe.ValidationError:pass
    frappe.set_user('dsherp-writer@example.invalid')
    own=frappe.get_doc({'doctype':'DS Conversation','title':'Denied config'}).insert(ignore_permissions=True)
    try:propose_bundle(own.name,package);raise AssertionError('writer configured native schema')
    except frappe.PermissionError:pass
    try:get_bundle(result['id']);raise AssertionError('cross-owner bundle')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.clear_cache(doctype='Item');frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
