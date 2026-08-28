import subprocess


def test_preview_confirmation_is_separate_expiring_and_immutable_without_ddl():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview,get_confirmation
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Preview confirmation test'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Preview Confirmation Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    confirmation=prepare_preview(bundle['id'],bundle['digest'])
    assert confirmation['purpose']=='preview' and confirmation['target']=='dsherp-beta.localhost'
    assert confirmation['status']=='Pending'
    assert not frappe.db.exists('DocType','DS Preview Confirmation Test')
    record=frappe.get_doc('DS Configuration Confirmation',confirmation['id'])
    frozen=json.loads(record.payload)['documents'][0]
    assert frozen['custom']==1 and frozen['autoname']=='hash'
    assert frozen['permissions'][0]['role']=='System Manager'
    assert 'System Manager' in confirmation['changes'][0]['detail']
    assert 1790<(record.expires_at-frappe.utils.now_datetime()).total_seconds()<=1800
    record.payload='{}'
    try:record.save(ignore_permissions=True);raise AssertionError('mutable confirmation')
    except frappe.ValidationError:pass
    frappe.set_user('beta-reader@example.invalid')
    try:get_confirmation(confirmation['id']);raise AssertionError('foreign confirmation')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
