import subprocess
import pytest


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
    transfer=frappe.get_doc({'doctype':'DS Configuration Transfer','request_id':'c'*64,'bundle':bundle['id'],'payload':json.dumps({'source_site':frappe.local.site,'preview_site':'dsherp-beta.localhost','actor':actor,'bundle_digest':bundle['digest'],'package_digest':__import__('dsherp_bridge.configuration_bundle',fromlist=['freeze_bundle']).freeze_bundle(package)['digest']}),'expires_at':frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=7200)}).insert(ignore_permissions=True)
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


@pytest.mark.parametrize('fail_early',[False,True],ids=['normal','early-failure'])
def test_expired_configuration_confirmation_has_zero_ddl_and_can_be_reprepared(fail_early):
    script=r'''
import os,frappe
from datetime import timedelta
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge import configuration_execution as execution
fail_early=FAIL_EARLY
conversation=bundle=expired=fresh=None
original_now_datetime=None
try:
    frappe.set_user('dsherp-preview@example.invalid')
    title='Expired configuration cleanup fault' if fail_early else 'Expired configuration'
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':title}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Expired Configuration Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    original_now_datetime=execution.now_datetime;execution.now_datetime=lambda:original_now_datetime()-timedelta(minutes=31)
    if fail_early:
        frappe.db.commit()
        raise RuntimeError('EXPECTED_EARLY_FAILURE')
    expired=execution.prepare_preview(bundle['id'],bundle['digest']);execution.now_datetime=original_now_datetime;frappe.db.commit()
    try:execution.confirm_preview(expired['id'],expired['digest'],'expired-request');raise AssertionError('expired confirmation executed')
    except frappe.ValidationError:pass
    assert not frappe.db.exists('DocType','DS Expired Configuration Test')
    assert frappe.db.count('DS Configuration Execution',{'confirmation':expired['id']})==0
    fresh=execution.prepare_preview(bundle['id'],bundle['digest'])
    assert fresh['id']!=expired['id'] and fresh['status']=='Pending'
    assert not frappe.db.exists('DocType','DS Expired Configuration Test')
finally:
    if original_now_datetime is not None:execution.now_datetime=original_now_datetime
    frappe.db.rollback();frappe.set_user('Administrator')
    confirmation_ids=[row['id'] for row in (expired,fresh) if row]
    if confirmation_ids:
        for row in frappe.get_all('DS Configuration Execution',filters={'confirmation':['in',confirmation_ids]},pluck='name'):
            frappe.db.delete('DS Configuration Execution',{'name':row})
    for row in confirmation_ids:
        if frappe.db.exists('DS Configuration Confirmation',row):frappe.db.delete('DS Configuration Confirmation',{'name':row})
    bundle_id=bundle['id'] if bundle else None
    conversation_id=conversation.name if conversation else None
    if bundle_id and frappe.db.exists('DS Configuration Bundle',bundle_id):frappe.db.delete('DS Configuration Bundle',{'name':bundle_id})
    if conversation_id and frappe.db.exists('DS Conversation',conversation_id):frappe.delete_doc('DS Conversation',conversation_id,force=True)
    frappe.db.commit()
    residual={
        'executions':frappe.db.count('DS Configuration Execution',{'confirmation':['in',confirmation_ids]}) if confirmation_ids else 0,
        'confirmations':frappe.db.count('DS Configuration Confirmation',{'name':['in',confirmation_ids]}) if confirmation_ids else 0,
        'bundles':frappe.db.count('DS Configuration Bundle',{'name':bundle_id}) if bundle_id else 0,
        'conversations':frappe.db.count('DS Conversation',{'name':conversation_id}) if conversation_id else 0,
    }
    assert residual=={'executions':0,'confirmations':0,'bundles':0,'conversations':0},residual
    if original_now_datetime is not None:assert execution.now_datetime is original_now_datetime
    frappe.destroy()
'''
    script=script.replace('FAIL_EARLY',str(fail_early))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    if fail_early:
        assert result.returncode!=0
        assert 'EXPECTED_EARLY_FAILURE' in result.stderr,result.stderr
        assert 'NameError' not in result.stderr,result.stderr
        assert result.stderr.rstrip().endswith('RuntimeError: EXPECTED_EARLY_FAILURE'),result.stderr
    else:
        assert result.returncode==0,result.stderr
