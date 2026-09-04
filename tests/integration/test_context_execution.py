"""Real business Site DB/API execution under owner identity; no paid model."""
import os
from pathlib import Path
import subprocess


def _require_resident_worker_stopped():
    path=Path('.runtime/agent-worker.pid')
    if not path.exists():return
    try:
        os.kill(int(path.read_text()),0)
    except (OSError,ValueError):
        return
    raise AssertionError('常驻 worker 正在运行；进程内集成测试必须先停止它')


def test_capability_reads_as_owner_and_cannot_finish_without_actual_read():
    _require_resident_worker_stopped()
    script=r'''
import os,uuid,frappe,hashlib,json
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
actor='dsherp-reader@example.invalid'
payload={'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}
had_budget='dsherp_run_budget' in frappe.conf
original_budget=frappe.conf.get('dsherp_run_budget')
had_policy='dsherp_model_policy' in frappe.conf
original_policy=frappe.conf.get('dsherp_model_policy')
had_runtime_user='dsherp_runtime_user' in frappe.conf
original_runtime_user=frappe.conf.get('dsherp_runtime_user')
frappe.conf.pop('dsherp_run_budget',None)
frappe.conf.pop('dsherp_model_policy',None)

def restore_conf():
    if had_budget:frappe.conf.dsherp_run_budget=original_budget
    else:frappe.conf.pop('dsherp_run_budget',None)
    if had_policy:frappe.conf.dsherp_model_policy=original_policy
    else:frappe.conf.pop('dsherp_model_policy',None)
    if had_runtime_user:frappe.conf.dsherp_runtime_user=original_runtime_user
    else:frappe.conf.pop('dsherp_runtime_user',None)

frappe.set_user('Administrator')
active=frappe.get_all('DS Model Run',filters={'status':['in',['Queued','Running','Cancelling']]},pluck='name')
assert not active,('validation site has active runs; stop the resident worker and clean the queue first',active)
frappe.set_user(actor)
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
    assert claim['budget']['model_max_calls']==8,claim['budget']
    assert claim['budget']['model_max_input_bytes_per_call']==131072,claim['budget']
    assert claim['budget']['model_max_input_bytes_total']==524288,claim['budget']
    assert claim['budget']['model_max_output_tokens_per_call']==2048,claim['budget']
    assert claim['budget']['model_max_output_tokens_total']==16384,claim['budget']
    expected_scope=hashlib.sha256(json.dumps([frappe.local.site,actor,doc['id'],'query',claim['native_session_id']],separators=(',',':')).encode()).hexdigest()
    assert claim['scope_id']==expected_scope
    assert execution.claim_run('a'*64) is None
    frappe.set_user('Guest')
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    status=execution.run_status(**cap)
    assert status['run_id']==claim['run_id'] and status['status']=='Running',status
    assert 0<status['lease_remaining_seconds']<=claim['budget']['lease_seconds'],status
    model_call={'input_bytes':100,'max_output_tokens':2048,'provider':'deepseek-official','model':'deepseek-v4-flash','purpose':'conversation','runtime_revision':'a'*64,'claimed_budget':claim['budget']}
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
    frappe.conf.dsherp_model_policy={'provider':'deepseek-official','model':'synthetic-model'}
    frappe.conf.dsherp_run_budget={'model_max_calls':2,
        'model_max_input_bytes_per_call':100,'model_max_input_bytes_total':150,
        'model_max_output_tokens_per_call':1024,'model_max_output_tokens_total':1536}
    try:
        limited=api.send_message('Synthetic limited model budget',payload,uuid.uuid4().hex,session_id=doc['id'])
        frappe.db.commit()
        limited_claim=execution.claim_run('a'*64);frappe.db.commit()
        assert limited_claim['run_id']==limited['active_run'],limited_claim
        assert limited_claim['budget']['provider']=='deepseek-official',limited_claim['budget']
        assert limited_claim['budget']['model']=='synthetic-model',limited_claim['budget']
        assert limited_claim['native_session_id']!=claim['native_session_id'],limited_claim
        claimed_payload=json.loads(frappe.db.get_value('DS Run Event',
            {'run':limited_claim['run_id'],'kind':'claimed'},'payload'))
        assert claimed_payload['provider']=='deepseek-official',claimed_payload
        assert claimed_payload['model']=='synthetic-model',claimed_payload
        assert limited_claim['budget']['model_max_calls']==2,limited_claim['budget']
        assert limited_claim['budget']['model_max_input_bytes_per_call']==100,limited_claim['budget']
        assert limited_claim['budget']['model_max_input_bytes_total']==150,limited_claim['budget']
        assert limited_claim['budget']['model_max_output_tokens_per_call']==1024,limited_claim['budget']
        assert limited_claim['budget']['model_max_output_tokens_total']==1536,limited_claim['budget']
        limited_cap={'run_id':limited_claim['run_id'],'capability':limited_claim['capability']}
        frappe.set_user('Guest')
        limited_call={'input_bytes':100,'max_output_tokens':1024,'provider':'deepseek-official','model':'synthetic-model','purpose':'conversation','runtime_revision':'a'*64,'claimed_budget':limited_claim['budget']}
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'model':'deepseek-v4-flash'});raise AssertionError('default model allowed')
        except frappe.ValidationError:pass
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'input_bytes':101});raise AssertionError('default input budget allowed')
        except frappe.ValidationError:pass
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'max_output_tokens':2048});raise AssertionError('default output budget allowed')
        except frappe.ValidationError:pass
        frappe.conf.dsherp_model_policy={'provider':'deepseek-official','model':'deepseek-v4-flash'}
        try:execution.reserve_model_call(**limited_cap,**limited_call);raise AssertionError('changed live model policy allowed')
        except frappe.ValidationError:pass
        frappe.conf.dsherp_model_policy={'provider':'deepseek-official','model':'synthetic-model'}
        old_limited_budget=dict(frappe.conf.dsherp_run_budget)
        frappe.conf.dsherp_run_budget={**old_limited_budget,'model_max_calls':3}
        try:execution.reserve_model_call(**limited_cap,**limited_call);raise AssertionError('changed live numeric budget allowed')
        except frappe.ValidationError:pass
        frappe.conf.dsherp_run_budget=old_limited_budget
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'claimed_budget':{**limited_claim['budget'],'site_concurrency':True}});raise AssertionError('boolean site_concurrency allowed')
        except frappe.ValidationError:pass
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'claimed_budget':{**limited_claim['budget'],'model_request_timeout_seconds':90.0}});raise AssertionError('float model_request_timeout_seconds allowed')
        except frappe.ValidationError:pass
        assert (frappe.db.get_value('DS Model Run',limited_claim['run_id'],'model_calls') or 0)==0
        assert execution.reserve_model_call(**limited_cap,**limited_call)['allowed']
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'input_bytes':51,'max_output_tokens':512});raise AssertionError('cumulative input budget exceeded')
        except frappe.ValidationError as error:
            assert str(error)=='本轮模型调用预算已用尽',error
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'input_bytes':50,'max_output_tokens':513});raise AssertionError('cumulative output budget exceeded')
        except frappe.ValidationError as error:
            assert str(error)=='本轮模型调用预算已用尽',error
        assert execution.reserve_model_call(**limited_cap,**{**limited_call,'input_bytes':50,'max_output_tokens':512})['allowed']
        assert frappe.db.get_value('DS Model Run',limited_claim['run_id'],'model_calls')==2
        assert frappe.db.get_value('DS Model Run',limited_claim['run_id'],'model_input_bytes')==150
        assert frappe.db.get_value('DS Model Run',limited_claim['run_id'],'model_output_tokens_reserved')==1536
        try:execution.reserve_model_call(**limited_cap,**{**limited_call,'input_bytes':1,'max_output_tokens':1});raise AssertionError('limited model call count exceeded')
        except frappe.ValidationError as error:
            assert str(error)=='本轮模型调用预算已用尽',error
        execution.finish_run(**limited_cap,status='Failed',error='End limited model budget')
        frappe.db.commit();frappe.set_user(actor)
    finally:
        frappe.conf.pop('dsherp_run_budget',None)
        frappe.conf.pop('dsherp_model_policy',None)
    operation=api.send_message(
        'Synthetic operation budget',payload,uuid.uuid4().hex,
        session_id=doc['id'],domain='operation',
    )
    frappe.db.commit()
    operation_claim=execution.claim_run('a'*64);frappe.db.commit()
    assert operation_claim['budget']['model_max_calls']==10,operation_claim['budget']
    assert operation_claim['budget']['model_max_input_bytes_per_call']==131072,operation_claim['budget']
    assert operation_claim['budget']['model_max_input_bytes_total']==524288,operation_claim['budget']
    assert operation_claim['budget']['model_max_output_tokens_per_call']==3072,operation_claim['budget']
    assert operation_claim['budget']['model_max_output_tokens_total']==30720,operation_claim['budget']
    operation_cap={'run_id':operation_claim['run_id'],'capability':operation_claim['capability']}
    frappe.set_user('Guest')
    operation_call={**model_call,'max_output_tokens':3072,'domain':'operation','claimed_budget':operation_claim['budget']}
    for index in range(10):
        assert execution.reserve_model_call(
            **operation_cap,**{
                **operation_call,
                'purpose':'compaction' if index in (3,8) else 'conversation',
            }
        )['allowed']
    assert frappe.db.get_value(
        'DS Model Run',operation_claim['run_id'],'model_output_tokens_reserved'
    )==30720
    try:
        execution.reserve_model_call(
            **operation_cap,**operation_call
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
    restore_conf()
    frappe.db.rollback();frappe.set_user('Administrator')
    for run in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
        frappe.db.delete('DS Run Event',{'run':run})
        frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr


def test_cancelling_run_finish_succeeded_persists_cancelled_answer():
    _require_resident_worker_stopped()
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
from dsherp_bridge import context_events as events
actor='dsherp-reader@example.invalid'
payload={'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}
had_runtime_user='dsherp_runtime_user' in frappe.conf
original_runtime_user=frappe.conf.get('dsherp_runtime_user')
doc=None
try:
    frappe.set_user('Administrator')
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Queued','Running','Cancelling']]},pluck='name')
    assert not active,('validation site has active runs; stop the resident worker and clean the queue first',active)
    frappe.set_user(actor)
    doc=api.send_message('Cancel after sourced answer',payload,uuid.uuid4().hex)
    frappe.db.commit()
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    assert claim['run_id']==doc['active_run']
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    frappe.set_user('Guest')
    read=execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    assert read['name']=='DSHERP-TEST-ITEM'
    sources=json.loads(frappe.db.get_value('DS Model Run',claim['run_id'],'sources'))
    assert sources and sources[-1]['record_versions']=={'DSHERP-TEST-ITEM':str(read['modified'])}
    frappe.db.commit()
    frappe.set_user(actor)
    api.cancel_run(doc['id'],claim['run_id'],uuid.uuid4().hex);frappe.db.commit()
    frappe.set_user('Guest')
    assert execution.run_status(**cap)['status']=='Cancelling'
    finished=execution.finish_run(**cap,status='Succeeded',answer='read completed')
    frappe.db.commit()
    assert finished['status']=='Cancelled',finished
    stored=frappe.db.get_value('DS Model Run',claim['run_id'],['status','answer','capability_hash'],as_dict=True)
    assert stored.status=='Cancelled' and stored.answer=='read completed' and not stored.capability_hash,stored
    last=[item for item in events.list_events(claim['run_id']) if item['kind']=='finished']
    assert last and last[-1]['payload']['status']=='Cancelled',last
finally:
    if had_runtime_user:frappe.conf.dsherp_runtime_user=original_runtime_user
    else:frappe.conf.pop('dsherp_runtime_user',None)
    frappe.db.rollback();frappe.set_user('Administrator')
    if doc:
        for run in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':run})
            frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
