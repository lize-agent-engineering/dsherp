import subprocess
import pytest


@pytest.mark.parametrize('revoke_after_first',[False,True])
def test_confirmed_preview_uses_native_ddl_and_record_save_without_replay(revoke_after_first):
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview,confirm_preview
name='DS Preview Native Apply Test';conversation=None;bundle=None;proposal=None
revoke=REVOKE_FLAG
assert not frappe.db.exists('DocType',name)
assert not frappe.db.table_exists(name)
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Native preview apply'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':name,'module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    if revoke:
        import copy
        import dsherp_bridge.configuration_execution as execution_module
        second=copy.deepcopy(package['doctypes'][0]);second['name']='DS Preview Revoked Step Test';package['doctypes'].append(second)
        assert not frappe.db.exists('DocType',second['name']) and not frappe.db.table_exists(second['name'])
        original_matches=execution_module._matches
        def verify_then_revoke(actual,expected):
            matched=original_matches(actual,expected)
            if isinstance(expected,dict) and expected.get('doctype')=='DocType' and expected.get('name')==name:
                frappe.set_user('Administrator')
                user=frappe.get_doc('User','dsherp-preview@example.invalid');user.enabled=0;user.save();frappe.db.commit()
                frappe.set_user(user.name)
            return matched
        execution_module._matches=verify_then_revoke
    bundle=propose_bundle(conversation.name,package)
    proposal=prepare_preview(bundle['id'],bundle['digest']);frappe.db.commit()
    assert not frappe.db.exists('DocType',name)
    result=confirm_preview(proposal['id'],proposal['digest'],'preview-native-1')
    if revoke:
        assert result['status']=='Partial',result
        assert result['steps'][0]['status']=='Succeeded'
        assert result['steps'][1]['status']=='Failed'
        assert not frappe.db.exists('DocType','DS Preview Revoked Step Test')
        assert not frappe.db.table_exists('DS Preview Revoked Step Test')
        frappe.set_user('Administrator')
        user=frappe.get_doc('User','dsherp-preview@example.invalid');user.enabled=1;user.save();frappe.db.commit()
        frappe.set_user(user.name)
        repeated=confirm_preview(proposal['id'],proposal['digest'],'after-access-restored')
        assert repeated['status']=='Partial' and repeated['execution_id']==result['execution_id']
        assert not frappe.db.exists('DocType','DS Preview Revoked Step Test')
        print('PERMISSION_CHANGE_STOPPED_NEXT_NATIVE_STEP')
        raise SystemExit(0)
    assert result['status']=='Succeeded',result
    assert result['steps'][0]['status']=='Succeeded'
    assert result['steps'][0]['step_id']=='["DocType","'+name+'"]'
    assert frappe.get_doc('DocType',name).custom==1
    native=frappe.get_doc({'doctype':name,'result':'Synthetic initial'}).insert()
    native.result='Synthetic changed';native.save();frappe.db.commit()
    assert frappe.get_doc(name,native.name).result=='Synthetic changed'
    repeated=confirm_preview(proposal['id'],proposal['digest'],'different-id')
    assert repeated['execution_id']==result['execution_id']
    assert frappe.db.count('DS Configuration Execution',{'confirmation':proposal['id']})==1
    native_get_doc=frappe.get_doc
    def assert_deadlock_recovery_rejects(current_user,candidate_digest,error_type):
        frappe.set_user(current_user);deadlocked=[False]
        def deadlock_waiter(doctype,*args,**kwargs):
            if doctype=='DS Configuration Confirmation' and kwargs.get('for_update') and not deadlocked[0]:
                deadlocked[0]=True
                raise frappe.QueryDeadlockError('Synthetic configuration locked-row snapshot')
            return native_get_doc(doctype,*args,**kwargs)
        frappe.get_doc=deadlock_waiter
        try:
            try:
                confirm_preview(proposal['id'],candidate_digest,'deadlock-authorization')
                raise AssertionError('deadlock recovery bypassed confirmation authorization')
            except error_type:pass
        finally:frappe.get_doc=native_get_doc
    assert_deadlock_recovery_rejects('dsherp-preview@example.invalid','forged',frappe.ValidationError)
    assert_deadlock_recovery_rejects('beta-reader@example.invalid',proposal['digest'],frappe.PermissionError)
    frappe.set_user('dsherp-preview@example.invalid')
    from dsherp_bridge.configuration_execution import get_confirmation
    assert get_confirmation(proposal['id'])['execution']['status']=='Succeeded'
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if revoke:
        user=frappe.get_doc('User','dsherp-preview@example.invalid');user.enabled=1;user.save()
    if proposal:
        for row in frappe.get_all('DS Configuration Execution',filters={'confirmation':proposal['id']},pluck='name'):frappe.delete_doc('DS Configuration Execution',row,force=True)
        frappe.delete_doc('DS Configuration Confirmation',proposal['id'],force=True)
    if bundle:frappe.delete_doc('DS Configuration Bundle',bundle['id'],force=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,force=True)
    if frappe.db.exists('DocType',name):
        for row in frappe.get_all(name,pluck='name'):frappe.delete_doc(name,row,force=True)
        frappe.delete_doc('DocType',name,force=True)
    frappe.db.commit()
    # Frappe deliberately retains a removed DocType's empty table. This exact
    # synthetic test table is ours; cleanup is not an Agent publishing operation.
    if frappe.db.table_exists(name):frappe.db.sql_ddl('DROP TABLE `tabDS Preview Native Apply Test`')
    if revoke:
        if frappe.db.exists('DocType','DS Preview Revoked Step Test'):frappe.delete_doc('DocType','DS Preview Revoked Step Test',force=True)
        frappe.db.commit()
        if frappe.db.table_exists('DS Preview Revoked Step Test'):frappe.db.sql_ddl('DROP TABLE `tabDS Preview Revoked Step Test`')
    frappe.destroy()
'''
    script=script.replace('REVOKE_FLAG',str(revoke_after_first))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('fail_early',[False,True],ids=['normal','early-failure'])
def test_concurrent_preview_confirmations_create_one_execution_and_one_native_config(fail_early):
    script=r'''
import os,uuid,frappe,requests
from concurrent.futures import ThreadPoolExecutor
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview
actor='dsherp-config-concurrency@example.invalid';name='DS Concurrent Configuration Test'
fail_early=FAIL_EARLY
conversation=bundle=proposal=None
try:
    frappe.set_user('Administrator')
    user=frappe.get_doc({'doctype':'User','email':actor,'first_name':'Config concurrency','enabled':1,'send_welcome_email':0,'api_key':uuid.uuid4().hex})
    secret=uuid.uuid4().hex;user.api_secret=secret;user.insert();user.add_roles('System Manager');frappe.db.commit()
    frappe.set_user(actor)
    title='Concurrent configuration cleanup fault' if fail_early else 'Concurrent configuration'
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':title}).insert(ignore_permissions=True)
    if fail_early:
        frappe.db.commit()
        raise RuntimeError('EXPECTED_EARLY_FAILURE')
    package={'version':1,'doctypes':[{'name':name,'module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package);proposal=prepare_preview(bundle['id'],bundle['digest']);frappe.db.commit()
    headers={'X-Frappe-Site-Name':frappe.local.site,'Authorization':'token '+user.api_key+':'+secret}
    def send(_):
        with requests.Session() as client:
            client.trust_env=False
            return client.post('http://127.0.0.1:8000/api/method/dsherp_bridge.configuration_execution.confirm_preview',headers=headers,json={'proposal_id':proposal['id'],'digest':proposal['digest'],'request_id':uuid.uuid4().hex},timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(send,range(2)))
    assert all(response.status_code==200 for response in responses),[(response.status_code,response.text[:200]) for response in responses]
    assert frappe.db.count('DS Configuration Execution',{'confirmation':proposal['id']})==1
    assert frappe.db.count('DocType',{'name':name})==1
finally:
    proposal_id=proposal['id'] if proposal else None
    bundle_id=bundle['id'] if bundle else None
    conversation_id=conversation.name if conversation else None
    frappe.db.rollback();frappe.destroy()
    os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
    frappe.set_user('Administrator')
    if proposal_id:
        for row in frappe.get_all('DS Configuration Execution',filters={'confirmation':proposal_id},pluck='name'):
            frappe.delete_doc('DS Configuration Execution',row,force=True)
        if frappe.db.exists('DS Configuration Confirmation',proposal_id):frappe.delete_doc('DS Configuration Confirmation',proposal_id,force=True)
    if bundle_id and frappe.db.exists('DS Configuration Bundle',bundle_id):frappe.delete_doc('DS Configuration Bundle',bundle_id,force=True)
    if conversation_id and frappe.db.exists('DS Conversation',conversation_id):frappe.delete_doc('DS Conversation',conversation_id,force=True)
    if frappe.db.exists('DocType',name):frappe.delete_doc('DocType',name,force=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,force=True)
    frappe.db.commit()
    if frappe.db.table_exists(name):frappe.db.sql_ddl('DROP TABLE `tabDS Concurrent Configuration Test`')
    residual={
        'executions':frappe.db.count('DS Configuration Execution',{'confirmation':proposal_id}) if proposal_id else 0,
        'confirmations':frappe.db.count('DS Configuration Confirmation',{'name':proposal_id}) if proposal_id else 0,
        'bundles':frappe.db.count('DS Configuration Bundle',{'name':bundle_id}) if bundle_id else 0,
        'conversations':frappe.db.count('DS Conversation',{'name':conversation_id}) if conversation_id else 0,
    }
    assert residual=={'executions':0,'confirmations':0,'bundles':0,'conversations':0},residual
    frappe.destroy()
'''
    script=script.replace('FAIL_EARLY',str(fail_early))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=70)
    if fail_early:
        assert result.returncode!=0
        assert 'EXPECTED_EARLY_FAILURE' in result.stderr,result.stderr
        assert 'NameError' not in result.stderr,result.stderr
        assert result.stderr.rstrip().endswith('RuntimeError: EXPECTED_EARLY_FAILURE'),result.stderr
    else:
        assert result.returncode==0,result.stderr
