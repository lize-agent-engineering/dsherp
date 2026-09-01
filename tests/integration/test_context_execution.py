"""Real business Site DB/API execution under owner identity; no paid model."""
import subprocess


def test_capability_reads_as_owner_and_cannot_finish_without_actual_read():
    script=r'''
import os,uuid,frappe,hashlib,json
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
actor='dsherp-reader@example.invalid'
frappe.set_user(actor)
payload={'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}
doc=api.send_message('Read synthetic item',payload,uuid.uuid4().hex)
frappe.db.commit()
try:
    try:execution.claim_run('a'*64);raise AssertionError('ordinary user claimed')
    except frappe.PermissionError:pass
    # In-process synthetic control-plane binding, never a stored role or real key.
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    assert claim['run_id']==doc['active_run']
    assert claim['session_id']==doc['id']
    expected_scope=hashlib.sha256(json.dumps([frappe.local.site,actor,doc['id'],'query',claim['native_session_id']],separators=(',',':')).encode()).hexdigest()
    assert claim['scope_id']==expected_scope
    assert execution.claim_run('a'*64) is None
    frappe.set_user('Guest')
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    assert execution.run_status(**cap)=={'run_id':claim['run_id'],'status':'Running'}
    model_call={'input_bytes':100,'max_output_tokens':2048,'provider':'deepseek-official','model':'deepseek-v4-flash','purpose':'conversation','runtime_revision':'a'*64}
    try:execution.reserve_model_call(**cap,**{**model_call,'runtime_revision':'b'*64});raise AssertionError('unbound model config allowed')
    except frappe.PermissionError:pass
    for invalid in ({'input_bytes':-1},{'input_bytes':99999999},{'model':'unauthorized'},{'max_output_tokens':None}):
        try:execution.reserve_model_call(**cap,**{**model_call,**invalid});raise AssertionError('invalid model call allowed')
        except frappe.ValidationError:pass
    for index in range(8):
        assert execution.reserve_model_call(**cap,**{**model_call,'purpose':'compaction' if index==1 else 'conversation'})['allowed']
    assert frappe.db.get_value('DS Model Run',claim['run_id'],'model_calls')==8
    try:execution.reserve_model_call(**cap,**model_call);raise AssertionError('model budget exceeded')
    except frappe.ValidationError as error:
        assert str(error)=='本轮模型调用预算已用尽',error
    try:execution.run_tool(**{**cap,'capability':'wrong'},tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'});raise AssertionError('bad cap allowed')
    except frappe.PermissionError:pass
    try:execution.finish_run(**cap,status='Succeeded',answer='fake');raise AssertionError('fake success allowed')
    except frappe.ValidationError:pass
    result=execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    assert result['name']=='DSHERP-TEST-ITEM'
    sources=json.loads(frappe.db.get_value('DS Model Run',claim['run_id'],'sources'))
    assert sources[-1]['record_versions']=={'DSHERP-TEST-ITEM':str(result['modified'])}
    schema=execution.run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Item'})
    sources=json.loads(frappe.db.get_value('DS Model Run',claim['run_id'],'sources'))
    assert sources[-1]['schema_version']==str(schema['modified'])
    records=execution.run_tool(**cap,tool='erp_search_records',arguments={'doctype':'Item','query':'DSHERP-TEST'})
    sources=json.loads(frappe.db.get_value('DS Model Run',claim['run_id'],'sources'))
    assert sources[-1]['record_versions']=={r['name']:str(r['modified']) for r in records}
    assert frappe.session.user=='Guest'
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Customer','name':'DSHERP-TEST-OTHER-CUSTOMER'});raise AssertionError('owner permission bypass')
    except frappe.PermissionError:pass
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM','user':'Administrator'});raise AssertionError('identity forged')
    except frappe.ValidationError:pass
    execution.finish_run(**cap,status='Succeeded',answer='read completed')
    frappe.db.commit()
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'});raise AssertionError('finished cap allowed')
    except frappe.PermissionError:pass
    frappe.set_user(actor)
    saved=api.get_session(doc['id'])
    assert saved['messages'][0]['answer']=='read completed'
    assert saved['active_run'] is None
    operation=api.send_message(
        'Synthetic operation budget',payload,uuid.uuid4().hex,
        session_id=doc['id'],domain='operation',
    )
    frappe.db.commit()
    operation_claim=execution.claim_run('a'*64);frappe.db.commit()
    operation_cap={'run_id':operation_claim['run_id'],'capability':operation_claim['capability']}
    frappe.set_user('Guest')
    operation_call={**model_call,'max_output_tokens':3072,'domain':'operation'}
    for maximum,purpose in (
        (3072,'conversation'),(3072,'conversation'),(3072,'conversation'),
        (2048,'compaction'),(3072,'conversation'),(3072,'conversation'),
        (3072,'conversation'),
    ):
        assert execution.reserve_model_call(
            **operation_cap,**{**operation_call,'max_output_tokens':maximum,'purpose':purpose}
        )['allowed']
    assert frappe.db.get_value(
        'DS Model Run',operation_claim['run_id'],'model_output_tokens_reserved'
    )==20480
    assert execution.reserve_model_call(
        **operation_cap,**{**operation_call,'max_output_tokens':2048,'purpose':'compaction'}
    )['allowed']
    assert frappe.db.get_value(
        'DS Model Run',operation_claim['run_id'],'model_output_tokens_reserved'
    )==22528
    assert execution.reserve_model_call(
        **operation_cap,**{**operation_call,'max_output_tokens':3072,'purpose':'conversation'}
    )['allowed']
    assert frappe.db.get_value(
        'DS Model Run',operation_claim['run_id'],'model_output_tokens_reserved'
    )==25600
    try:
        execution.reserve_model_call(
            **operation_cap,**{**operation_call,'max_output_tokens':2048,'purpose':'compaction'}
        )
        raise AssertionError('operation output budget exceeded')
    except frappe.ValidationError as error:
        assert str(error)=='本轮模型调用预算已用尽',error
    execution.finish_run(
        **operation_cap,status='Failed',error='End synthetic operation budget'
    )
    frappe.db.commit();frappe.set_user(actor)
    queued=api.send_message('Cancel synthetic run',payload,uuid.uuid4().hex,session_id=doc['id'])
    frappe.db.commit()
    claim2=execution.claim_run('a'*64);frappe.db.commit()
    cap2={'run_id':claim2['run_id'],'capability':claim2['capability']}
    api.cancel_run(doc['id'],claim2['run_id'],uuid.uuid4().hex);frappe.db.commit()
    frappe.set_user('Guest')
    assert execution.run_status(**cap2)['status']=='Cancelling'
    try:execution.run_status(**{**cap2,'capability':'wrong'});raise AssertionError('bad status cap allowed')
    except frappe.PermissionError:pass
    execution.finish_run(**cap2,status='Cancelled');frappe.db.commit()
    try:execution.run_status(**cap2);raise AssertionError('terminal status cap allowed')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for run in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
        frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
