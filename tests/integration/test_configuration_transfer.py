import subprocess


def test_source_transfer_reauthorizes_current_owner_without_business_writes():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer,export_transfer
from dsherp_bridge.configuration_transport import seal,open_envelope
try:
    actor='dsherp-preview@example.invalid';frappe.set_user(actor)
    frappe.conf.dsherp_configuration_preview={'site':'isolated-preview.localhost','url':'http://preview-backend:8000','public_url':'http://preview.localhost:18085','secret':'test-pair-secret'}
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Source transfer test'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Transfer Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    frappe.db.commit()
    transfer=prepare_transfer(bundle['id'],bundle['digest'],'transfer-request-1')
    frappe.db.commit()
    assert prepare_transfer(bundle['id'],bundle['digest'],'transfer-request-1')['id']==transfer['id']
    doc=frappe.get_doc('DS Configuration Transfer',transfer['id'])
    assert doc.owner==actor and doc.bundle==bundle['id']
    doc.payload='{}'
    try:doc.save(ignore_permissions=True);raise AssertionError('mutable transfer')
    except frappe.ValidationError:doc.reload()
    request=seal({'transfer_id':doc.name,'actor':actor,'source_site':frappe.local.site,'preview_site':'isolated-preview.localhost'},'test-pair-secret','export-request')
    frappe.set_user('Guest')
    data=open_envelope(export_transfer(request),'test-pair-secret','export-response')
    assert data['package']==package and data['actor']==actor and data['bundle_digest']==bundle['digest']
    assert not frappe.db.exists('DocType','DS Transfer Test')
    forged=seal({**request['payload'],'actor':'beta-reader@example.invalid'},'test-pair-secret','export-request')
    try:export_transfer(forged);raise AssertionError('foreign owner exported')
    except frappe.PermissionError:pass
    frappe.set_user('Administrator');user=frappe.get_doc('User',actor);user.enabled=0;user.save()
    frappe.set_user('Guest')
    try:export_transfer(request);raise AssertionError('revoked source user exported')
    except frappe.PermissionError:pass
    frappe.set_user('Administrator');user=frappe.get_doc('User',actor);user.enabled=1;user.save()
    frappe.set_user('Guest')
    try:export_transfer(request);raise AssertionError('reenabled user reused old transfer authorization')
    except frappe.ValidationError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if 'transfer' in locals():frappe.delete_doc('DS Configuration Transfer',transfer['id'],force=True)
    if 'bundle' in locals():frappe.delete_doc('DS Configuration Bundle',bundle['id'],force=True)
    if 'conversation' in locals():frappe.delete_doc('DS Conversation',conversation.name,force=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=50)
    assert result.returncode==0,result.stderr


def test_preview_import_is_owner_bound_idempotent_and_rechecks_source():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge import configuration_transfer as transfer
from dsherp_bridge.configuration import check_authorization
from dsherp_bridge.configuration_bundle import freeze_bundle
try:
    actor='dsherp-preview@example.invalid';frappe.set_user(actor)
    frappe.conf.dsherp_configuration_source={'site':'source.localhost','url':'http://source-backend:8000','secret':'test-pair-secret'}
    package={'version':1,'doctypes':[{'name':'DS Imported Transfer Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    artifact={'transfer_id':'test-transfer','source_site':'source.localhost','preview_site':frappe.local.site,'actor':actor,
        'bundle_digest':'a'*64,'package_digest':freeze_bundle(package)['digest'],'package':package}
    calls=[]
    def source_request(peer,purpose,payload):calls.append((purpose,payload));return artifact
    transfer._request=source_request
    first=transfer.accept_transfer('test-transfer');second=transfer.accept_transfer('test-transfer')
    assert first['bundle']['id']==second['bundle']['id'] and first['session_id']==second['session_id']
    doc=frappe.get_doc('DS Configuration Bundle',first['bundle']['id'])
    assert json.loads(doc.payload)['origin']['transfer_id']=='test-transfer' and doc.source_transfer
    assert not frappe.db.exists('DocType','DS Imported Transfer Test')
    check_authorization(doc.name);assert len(calls)==3
    def denied(*args):raise frappe.PermissionError('source permission revoked')
    transfer._request=denied
    try:check_authorization(doc.name);raise AssertionError('source permission not rechecked')
    except frappe.PermissionError:pass
    transfer._request=source_request;frappe.set_user('beta-reader@example.invalid')
    try:transfer.accept_transfer('test-transfer');raise AssertionError('different actor imported')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if 'first' in locals():
        if frappe.db.exists('DS Configuration Bundle',first['bundle']['id']):frappe.delete_doc('DS Configuration Bundle',first['bundle']['id'],force=True)
        if frappe.db.exists('DS Conversation',first['session_id']):frappe.delete_doc('DS Conversation',first['session_id'],force=True)
        frappe.db.commit()
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=50)
    assert result.returncode==0,result.stderr
