import subprocess


def test_preview_confirmation_is_separate_expiring_and_immutable_without_ddl():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview,get_confirmation
from dsherp_bridge.context_api import get_session
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Preview confirmation test'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Preview Confirmation Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    assert bundle['preview_available'] and 'Result' in bundle['changes'][0]['detail']
    assert not bundle['preview_transfer_available']
    assert get_session(conversation.name)['configuration_bundles'][0]['id']==bundle['id']
    confirmation=prepare_preview(bundle['id'],bundle['digest'])
    assert confirmation['bundle_id']==bundle['id']
    assert confirmation['purpose']=='preview' and confirmation['target']=='dsherp-beta.localhost'
    assert confirmation['status']=='Pending'
    assert get_session(conversation.name)['configuration_confirmations'][0]['id']==confirmation['id']
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


def test_publish_confirmation_binds_successful_preview_receipt_without_ddl():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge import configuration_execution as execution
try:
    actor='dsherp-preview@example.invalid';frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Publish confirmation test'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[],'extensions':[{'doctype':'Item','fields':[{'fieldname':'ds_publish_test','label':'Publish Test','fieldtype':'Data','insert_after':'item_name'}]}],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    transfer=frappe.get_doc({'doctype':'DS Configuration Transfer','request_id':'c'*64,'bundle':bundle['id'],'payload':json.dumps({'source_site':frappe.local.site,'preview_site':'dsherp-beta.localhost','actor':actor,'bundle_digest':bundle['digest'],'package_digest':__import__('dsherp_bridge.configuration_bundle',fromlist=['freeze_bundle']).freeze_bundle(package)['digest']})}).insert(ignore_permissions=True)
    receipt={'transfer_id':transfer.name,'source_site':frappe.local.site,'preview_site':'dsherp-beta.localhost','actor':actor,'bundle_digest':bundle['digest'],'package_digest':json.loads(transfer.payload)['package_digest'],'preview_bundle':'PB1','preview_confirmation':'PC1','preview_execution':'PE1','verified_documents':[{'doctype':'Custom Field','name':'Item-ds_publish_test','version':'v1'}]}
    execution.read_receipt=lambda record:receipt
    proposal=execution.prepare_publish(transfer.name,bundle['digest'])
    assert proposal['purpose']=='publish' and proposal['target']==frappe.local.site and proposal['status']=='Pending'
    binding=json.loads(frappe.get_doc('DS Configuration Confirmation',proposal['id']).payload)
    assert binding['preview_receipt']==receipt and binding['documents'][0]['fieldname']=='ds_publish_test'
    assert not frappe.db.exists('Custom Field','Item-ds_publish_test')
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
