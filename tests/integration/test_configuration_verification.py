import subprocess


def test_configuration_verification_reads_native_state_without_replay_or_audit_rewrite():
    script=r'''
import os,json,hashlib,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import _documents,verify_execution
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Configuration verification'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[],'extensions':[{'doctype':'Item','fields':[{'fieldname':'ds_verification_only','label':'Verification Only','fieldtype':'Data','insert_after':'item_name'}]}],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    binding={'purpose':'preview','target':frappe.local.site,'bundle_digest':bundle['digest'],'package_digest':'p','baseline':bundle['baseline'],'documents':_documents(package)}
    confirmation=frappe.get_doc({'doctype':'DS Configuration Confirmation','bundle':bundle['id'],'payload':json.dumps(binding),'digest':hashlib.sha256(json.dumps(binding).encode()).hexdigest(),'expires_at':frappe.utils.add_to_date(frappe.utils.now_datetime(),minutes=30),'status':'Unknown'}).insert(ignore_permissions=True)
    native=frappe.get_doc(binding['documents'][0]).insert();frappe.db.commit()
    execution=frappe.get_doc({'doctype':'DS Configuration Execution','confirmation':confirmation.name,'request_id':'verification-only','status':'Unknown','steps':json.dumps([{'object':'Item.ds_verification_only','status':'Unknown','doctype':'Custom Field','name':native.name}]),'error':'Synthetic response lost'}).insert(ignore_permissions=True);frappe.db.commit()
    # A changed authorization revision must stop new execution, but must not
    # prevent the same owner from checking currently readable native state.
    frappe.db.set_value('User',frappe.session.user,'modified',frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=1))
    result=verify_execution(confirmation.name)
    assert result['execution']['status']=='Unknown' and result['observations'][0]['state']=='Matches'
    assert result['observations'][0]['name']==native.name and result['observations'][0]['version']==str(native.modified)
    assert frappe.get_doc('DS Configuration Execution',execution.name).status=='Unknown'
    assert frappe.db.count('DS Configuration Execution',{'confirmation':confirmation.name})==1
    frappe.set_user('beta-reader@example.invalid')
    try:verify_execution(confirmation.name);raise AssertionError('foreign verification allowed')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    frappe.delete_doc('DS Configuration Execution',execution.name,force=True)
    frappe.delete_doc('DS Configuration Confirmation',confirmation.name,force=True)
    frappe.delete_doc('DS Configuration Bundle',bundle['id'],force=True)
    frappe.delete_doc('DS Conversation',conversation.name,force=True)
    if frappe.db.exists('Custom Field','Item-ds_verification_only'):frappe.delete_doc('Custom Field','Item-ds_verification_only',force=True)
    frappe.db.commit()
    residual={
        'executions':frappe.db.count('DS Configuration Execution',{'name':execution.name}),
        'confirmations':frappe.db.count('DS Configuration Confirmation',{'name':confirmation.name}),
        'bundles':frappe.db.count('DS Configuration Bundle',{'name':bundle['id']}),
        'conversations':frappe.db.count('DS Conversation',{'name':conversation.name}),
    }
    assert residual=={'executions':0,'confirmations':0,'bundles':0,'conversations':0},residual
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=45)
    assert result.returncode==0,result.stderr
