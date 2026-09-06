import subprocess
import pytest


@pytest.mark.parametrize('fail_early',[False,True],ids=['normal','early-failure'])
def test_configuration_verification_reads_native_state_without_replay_or_audit_rewrite(fail_early):
    script=r'''
import os,json,hashlib,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import _documents,verify_execution
fail_early=FAIL_EARLY
conversation=bundle=confirmation=execution=native=None
try:
    frappe.set_user('dsherp-preview@example.invalid')
    title='Configuration verification cleanup fault' if fail_early else 'Configuration verification'
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':title}).insert(ignore_permissions=True)
    if fail_early:
        frappe.db.commit()
        raise RuntimeError('EXPECTED_EARLY_FAILURE')
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
    execution_id=execution.name if execution else None
    confirmation_id=confirmation.name if confirmation else None
    bundle_id=bundle['id'] if bundle else None
    conversation_id=conversation.name if conversation else None
    if execution_id and frappe.db.exists('DS Configuration Execution',execution_id):frappe.db.delete('DS Configuration Execution',{'name':execution_id})
    if confirmation_id and frappe.db.exists('DS Configuration Confirmation',confirmation_id):frappe.db.delete('DS Configuration Confirmation',{'name':confirmation_id})
    if bundle_id and frappe.db.exists('DS Configuration Bundle',bundle_id):frappe.db.delete('DS Configuration Bundle',{'name':bundle_id})
    if conversation_id and frappe.db.exists('DS Conversation',conversation_id):frappe.delete_doc('DS Conversation',conversation_id,force=True)
    if frappe.db.exists('Custom Field','Item-ds_verification_only'):frappe.delete_doc('Custom Field','Item-ds_verification_only',force=True)
    frappe.db.commit()
    residual={
        'executions':frappe.db.count('DS Configuration Execution',{'name':execution_id}) if execution_id else 0,
        'confirmations':frappe.db.count('DS Configuration Confirmation',{'name':confirmation_id}) if confirmation_id else 0,
        'bundles':frappe.db.count('DS Configuration Bundle',{'name':bundle_id}) if bundle_id else 0,
        'conversations':frappe.db.count('DS Conversation',{'name':conversation_id}) if conversation_id else 0,
    }
    assert residual=={'executions':0,'confirmations':0,'bundles':0,'conversations':0},residual
    frappe.destroy()
'''
    script=script.replace('FAIL_EARLY',str(fail_early))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=45)
    if fail_early:
        assert result.returncode!=0
        assert 'EXPECTED_EARLY_FAILURE' in result.stderr,result.stderr
        assert 'NameError' not in result.stderr,result.stderr
        assert result.stderr.rstrip().endswith('RuntimeError: EXPECTED_EARLY_FAILURE'),result.stderr
    else:
        assert result.returncode==0,result.stderr
