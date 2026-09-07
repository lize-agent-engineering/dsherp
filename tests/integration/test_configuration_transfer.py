import subprocess


def test_source_transfer_reauthorizes_current_owner_without_business_writes():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge import grants
from dsherp_bridge.configuration import propose_bundle,get_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer,export_transfer,TRANSFER_WINDOW_SECONDS
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
    assert 'platform_grant' not in frappe.db.get_table_columns('DS Configuration Transfer')
    assert abs((doc.expires_at-doc.creation).total_seconds()-TRANSFER_WINDOW_SECONDS)<5
    assert transfer['expires_at']==str(doc.expires_at)
    # No platform grant in this session, but the lease is written anyway: a missing entry has
    # to mean 'expired or lost', never 'never had one'.
    assert grants.of_transfer(doc.name)=={'grant':None}
    doc.payload='{}'
    try:doc.save(ignore_permissions=True);raise AssertionError('mutable transfer')
    except frappe.ValidationError:doc.reload()
    doc.expires_at=frappe.utils.add_to_date(doc.expires_at,hours=1)
    try:doc.save(ignore_permissions=True);raise AssertionError('window rewritten')
    except frappe.ValidationError:doc.reload()
    request=seal({'transfer_id':doc.name,'actor':actor,'source_site':frappe.local.site,'preview_site':'isolated-preview.localhost'},'test-pair-secret','export-request')
    frappe.set_user('Guest')
    data=open_envelope(export_transfer(request),'test-pair-secret','export-response')
    assert data['package']==package and data['actor']==actor and data['bundle_digest']==bundle['digest']
    assert not frappe.db.exists('DocType','DS Transfer Test')
    forged=seal({**request['payload'],'actor':'beta-reader@example.invalid'},'test-pair-secret','export-request')
    try:export_transfer(forged);raise AssertionError('foreign owner exported')
    except frappe.PermissionError:pass
    # The window and the lease are two separate gates, and each refuses by name.
    grants.drop_transfer(doc.name)
    try:export_transfer(request);raise AssertionError('exported without a lease')
    except frappe.ValidationError as error:assert '配置交接授权已失效' in str(error),str(error)
    grants.stash_transfer(doc.name,None,TRANSFER_WINDOW_SECONDS)
    # Age the row past its window with raw SQL, then drop the document cache: get_doc would
    # otherwise hand export_transfer the copy it already has, with the old moment on it.
    frappe.db.set_value('DS Configuration Transfer',doc.name,'expires_at',
        frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=-1),update_modified=False)
    frappe.db.commit();frappe.clear_document_cache('DS Configuration Transfer',doc.name)
    try:export_transfer(request);raise AssertionError('exported after the window')
    except frappe.ValidationError as error:assert '配置交接已过期' in str(error),str(error)
    frappe.set_user(actor)
    try:
        prepare_transfer(bundle['id'],bundle['digest'],'transfer-request-1')
        raise AssertionError('expired transfer replayed')
    except frappe.ValidationError as error:assert '配置交接已过期' in str(error),str(error)
    assert get_bundle(bundle['id'])['transfer'] is None, 'an expired transfer is not offered again'
    frappe.db.set_value('DS Configuration Transfer',doc.name,'expires_at',
        frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=TRANSFER_WINDOW_SECONDS),
        update_modified=False)
    frappe.db.commit();frappe.clear_document_cache('DS Configuration Transfer',doc.name)
    offered=get_bundle(bundle['id'])['transfer']
    assert offered['id']==doc.name and offered['expires_at']
    frappe.set_user('Guest')
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
    if 'transfer' in locals():
        grants.drop_transfer(transfer['id'])
        frappe.db.delete('DS Configuration Transfer',{'name':transfer['id']})
    if 'bundle' in locals():frappe.db.delete('DS Configuration Bundle',{'name':bundle['id']})
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
        if frappe.db.exists('DS Configuration Bundle',first['bundle']['id']):frappe.db.delete('DS Configuration Bundle',{'name':first['bundle']['id']})
        if frappe.db.exists('DS Conversation',first['session_id']):frappe.delete_doc('DS Conversation',first['session_id'],force=True)
        frappe.db.commit()
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=50)
    assert result.returncode==0,result.stderr


def test_publish_requires_a_signed_current_preview_receipt():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge import configuration_transfer as transfer
from dsherp_bridge.configuration_transport import seal,open_envelope
try:
    actor='dsherp-preview@example.invalid';frappe.set_user(actor)
    frappe.conf.dsherp_configuration_source={'site':'source.localhost','url':'http://source-backend:8000','secret':'test-pair-secret'}
    package={'version':1,'doctypes':[],'extensions':[{'doctype':'Item','fields':[{'fieldname':'ds_receipt_test','label':'Receipt Test','fieldtype':'Data','insert_after':'item_name'}]}],'workflows':[]}
    origin={'source_site':'source.localhost','preview_site':frappe.local.site,'actor':actor,'transfer_id':'receipt-transfer','bundle_digest':'a'*64,'package_digest':__import__('dsherp_bridge.configuration_bundle',fromlist=['freeze_bundle']).freeze_bundle(package)['digest']}
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Receipt test'}).insert(ignore_permissions=True)
    identity=__import__('hashlib').sha256(json.dumps(['source.localhost','receipt-transfer'],ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()).hexdigest()
    bundle=__import__('dsherp_bridge.configuration',fromlist=['_propose_bundle'])._propose_bundle(conversation.name,package,origin=origin,source_transfer=identity)
    confirmation=frappe.get_doc({'doctype':'DS Configuration Confirmation','bundle':bundle['id'],'payload':json.dumps({'purpose':'preview','target':frappe.local.site}),'digest':'b'*64,'expires_at':frappe.utils.add_to_date(frappe.utils.now_datetime(),minutes=30),'status':'Succeeded'}).insert(ignore_permissions=True)
    execution=frappe.get_doc({'doctype':'DS Configuration Execution','confirmation':confirmation.name,'request_id':'receipt-run','status':'Succeeded','steps':json.dumps([{'object':'Item.ds_receipt_test','status':'Succeeded','doctype':'Custom Field','name':'Item-ds_receipt_test','version':'v1'}])}).insert(ignore_permissions=True)
    frappe.db.commit()
    transfer.check_authorization=lambda bundle_id:bundle
    transfer._verify_execution_current=lambda bundle_id,record: ([{'doctype':'Custom Field','name':'Item-ds_receipt_test','version':'v1'}] if record.status=='Succeeded' else frappe.throw('preview incomplete'))
    request={'transfer_id':'receipt-transfer','source_site':'source.localhost','preview_site':frappe.local.site,'actor':actor,'bundle_digest':'a'*64,'package_digest':origin['package_digest']}
    frappe.set_user('Guest')
    receipt=open_envelope(transfer.receipt_transfer(seal(request,'test-pair-secret','receipt-request')),'test-pair-secret','receipt-response')
    assert receipt['transfer_id']=='receipt-transfer' and receipt['preview_execution']==execution.name
    assert receipt['preview_confirmation']==confirmation.name and receipt['verified_documents'][0]['name']=='Item-ds_receipt_test'
    frappe.db.set_value('DS Configuration Execution',execution.name,'status','Partial',update_modified=False);frappe.db.commit()
    try:transfer.receipt_transfer(seal(request,'test-pair-secret','receipt-request'));raise AssertionError('partial preview accepted')
    except frappe.ValidationError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for doctype,name in [('DS Configuration Execution',locals().get('execution') and execution.name),('DS Configuration Confirmation',locals().get('confirmation') and confirmation.name),('DS Configuration Bundle',locals().get('bundle') and bundle['id']),('DS Conversation',locals().get('conversation') and conversation.name)]:
        # audit records (ruling #3) go through the database path; the conversation through the API
        if name and frappe.db.exists(doctype,name):
            if doctype=='DS Conversation':frappe.delete_doc(doctype,name,force=True)
            else:frappe.db.delete(doctype,{'name':name})
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=50)
    assert result.returncode==0,result.stderr
