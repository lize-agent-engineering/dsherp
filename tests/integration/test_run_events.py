"""Run events are append-only, sequential, secret-free and owner-readable."""

import subprocess


def test_run_events_are_sequential_immutable_and_secret_free():
    script = r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='events-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic events','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Events'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'events','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(b'cap').hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    first=ev.record(run.name,'claimed',{'domain':'query','capability':'SECRET-CAP','apiKey':'SECRET-KEY',
        'callId':'call-1','nested':{'api_secret':'SECRET-2','accessToken':'SECRET-TOKEN','ok':'x'*3000,
        'credential':'sk-abcdefghij_12345','header':'Bearer synthetic-value','toolCallId':'tool-1'}})
    second=ev.record(run.name,'tool_call',{'tool':'erp_read_record'},error_class=None)
    assert first==run.name+'-000001' and second==run.name+'-000002',(first,second)
    stored=frappe.get_doc('DS Run Event',first)
    assert 'SECRET' not in stored.payload, stored.payload
    payload=json.loads(stored.payload)
    assert 'capability' not in payload and 'apiKey' not in payload and 'api_secret' not in payload['nested'] and 'accessToken' not in payload['nested']
    assert payload['callId']=='call-1' and payload['nested']['toolCallId']=='tool-1'
    assert payload['nested']['credential']=='[redacted]' and payload['nested']['header']=='[redacted]'
    assert payload['nested']['ok'].endswith('…[truncated]')
    try:
        stored.kind='changed';stored.save(ignore_permissions=True);raise AssertionError('event was rewritten')
    except frappe.ValidationError:pass
    try:
        frappe.delete_doc('DS Run Event',first,ignore_permissions=True);raise AssertionError('event was deleted')
    except frappe.ValidationError:pass
    try:
        ev.record(run.name,'not-a-kind',{});raise AssertionError('unknown kind accepted')
    except frappe.ValidationError:pass
    batch=ev.record_many(run.name,[{'kind':'runtime_started','payload':{},'source':'runner'},
                                   {'kind':'tool_error','payload':{'text':'x'},'source':'runner','error_class':'PermissionError'}])
    assert batch=={'recorded':2,'last_seq':4},batch
    try:
        ev.record_many(run.name,[{'kind':'turn_end','payload':{},'source':'runner'} for _ in range(201)])
        raise AssertionError('201 event batch accepted')
    except frappe.ValidationError as error:
        assert str(error)=='运行事件批次无效',error
    listed=ev.list_events(run.name)
    assert [e['seq'] for e in listed]==[1,2,3,4] and listed[3]['error_class']=='PermissionError'
    assert set(listed[0])=={'name','seq','kind','source','error_class','payload','recorded_at'}
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_oversized_tool_payload_keeps_contract_keys_within_limit():
    script = r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
try:
    result={f'field_{index}':'x'*1500 for index in range(20)}
    text=ev._serialize('tool_call',{'tool':'erp_read_record','arguments':{'doctype':'Item'},'duration_ms':7,'result':result})
    payload=json.loads(text)
    assert set(payload)=={'tool','arguments','duration_ms','result'},payload
    assert len(text.encode())<=ev.MAX_PAYLOAD,len(text.encode())
    print('OK')
finally:
    frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_server_records_the_run_lifecycle_in_order():
    script = r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as ev
from dsherp_bridge.context_execution import claim_run,run_tool,reserve_model_call,finish_run
conversation=None;actor=None;runs=[]
try:
    frappe.set_user('Administrator')
    actor='lifecycle-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic lifecycle','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.db.commit()
    frappe.set_user(actor)
    session=api.send_message('读取测试物料',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex,domain='query')
    conversation=session['id'];run_id=session['messages'][0]['id'];runs.append(run_id)
    frappe.db.commit()
    frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    claim=claim_run('a'*64)
    assert claim and claim['run_id']==run_id,claim
    cap={'run_id':run_id,'capability':claim['capability']}
    frappe.set_user('Guest')
    reserve_model_call(**cap,input_bytes=100,max_output_tokens=512,provider='deepseek-official',model='deepseek-v4-flash',purpose='conversation',runtime_revision='a'*64,domain='query',claimed_budget=claim['budget'])
    run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    finish_run(**cap,status='Succeeded',answer='完成')
    frappe.db.commit()
    kinds=[e['kind'] for e in ev.list_events(run_id)]
    assert kinds==['queued','claimed','model_call_reserved','tool_call','finished'],kinds
    reserved=ev.list_events(run_id)[2]['payload']
    assert reserved['max_output_tokens']==512,reserved
    tool=ev.list_events(run_id)[3]['payload']
    assert tool['tool']=='erp_read_record' and tool['result']['records']==1 and isinstance(tool['result']['fields'],int),tool
    assert isinstance(tool['duration_ms'],int)
    assert all('capability' not in json.dumps(e['payload']) and claim['capability'] not in json.dumps(e['payload']) for e in ev.list_events(run_id))
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_server_event_write_failure_does_not_change_business_results():
    script = r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as ev
from dsherp_bridge.context_execution import finish_run,run_tool
from dsherp_bridge.context_permissions import run_revision
conversation=None;run_id=None;actor=None;created_logs=[];original_insert=ev._insert;original_form_dict=frappe.local.form_dict;original_request=getattr(frappe.local,'request',None)
title='dsherp run event write failed'
try:
    frappe.set_user('Administrator')
    actor='event-failure-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic event failure','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.db.commit()
    existing=set(frappe.get_all('Error Log',filters={'method':title},pluck='name'))
    def fail_insert(*args,**kwargs):
        raise RuntimeError('synthetic event insert failure')
    ev._insert=fail_insert
    frappe.set_user(actor)
    session=api.send_message('事件失败不影响发送',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex,domain='query')
    conversation=session['id'];run_id=session['messages'][0]['id']
    assert frappe.db.exists('DS Model Run',run_id),run_id
    capability=uuid.uuid4().hex
    frappe.db.set_value('DS Model Run',run_id,{
        'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'permission_revision':run_revision(actor,'query')})
    frappe.set_user('Guest')
    ev._insert=original_insert
    assert run_tool(run_id,capability,'erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'})['name']=='DSHERP-TEST-ITEM'
    ev._insert=fail_insert
    frappe.local.form_dict=frappe._dict({'run_id':run_id,'capability':capability})
    frappe.local.request=frappe._dict({'method':'POST','path':'/api/method/finish_run','referrer':''})
    assert frappe.local.form_dict['capability']==capability
    assert ev.record_safely(run_id,'finished',{'status':'Succeeded'}) is None
    assert finish_run(run_id,capability,'Succeeded',answer='事件写入失败但业务完成')=={
        'run_id':run_id,'status':'Succeeded','provider_failures':0}
    frappe.db.commit()
    assert frappe.db.get_value('DS Model Run',run_id,'status')=='Succeeded'
    created_logs=[name for name in frappe.get_all('Error Log',filters={'method':title},pluck='name') if name not in existing]
    assert created_logs,frappe.get_all('Error Log',filters={'method':title},fields=['name','method'])
    log_text=''.join((frappe.db.get_value('Error Log',name,'error') or '')+(frappe.db.get_value('Error Log',name,'metadata') or '') for name in created_logs)
    assert capability not in log_text
    print('OK')
finally:
    ev._insert=original_insert;frappe.local.form_dict=original_form_dict;frappe.local.request=original_request
    frappe.db.rollback();frappe.set_user('Administrator')
    if run_id:
        frappe.db.delete('DS Run Event',{'run':run_id})
        if frappe.db.exists('DS Model Run',run_id):frappe.delete_doc('DS Model Run',run_id,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    for name in created_logs:frappe.db.delete('Error Log',{'name':name})
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_runner_batch_endpoint_requires_live_capability_and_runner_kinds():
    script = r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
from dsherp_bridge.context_execution import record_run_event
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='batch-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic batch','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Batch'}).insert(ignore_permissions=True)
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'batch','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    frappe.set_user('Guest')
    out=record_run_event(run.name,capability,[{'kind':'runtime_started','payload':{'api_key':'SECRET'},'source':'runner'},
                                              {'kind':'tool_error','payload':{'text':'拒绝'},'source':'runner','error_class':'PermissionError'}])
    assert out=={'recorded':2,'last_seq':2},out
    for bad in ([{'kind':'claimed','payload':{},'source':'runner'}],[{'kind':'runtime_started','payload':{},'source':'server'}],[]):
        try:record_run_event(run.name,capability,bad);raise AssertionError('accepted %r'%bad)
        except (frappe.ValidationError,frappe.PermissionError):pass
    try:record_run_event(run.name,'wrong',[{'kind':'runtime_started','payload':{},'source':'runner'}]);raise AssertionError('bad capability accepted')
    except frappe.PermissionError:pass
    assert 'SECRET' not in ''.join(json.dumps(e['payload']) for e in ev.list_events(run.name))
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name});frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_run_events_are_readable_by_owner_but_not_another_user():
    script = r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as ev
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None;other=None
try:
    frappe.set_user('Administrator')
    actor='events-owner-'+uuid.uuid4().hex+'@example.invalid'
    other='events-other-'+uuid.uuid4().hex+'@example.invalid'
    for email in (actor,other):
        frappe.get_doc({'doctype':'User','email':email,'first_name':'Synthetic event reader','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Readable events'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'read events','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(b'cap').hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    ev.record(run.name,'claimed',{'capability':'SECRET-CAP','domain':'query'})
    ev.record(run.name,'finished',{'status':'Failed'})
    frappe.set_user(actor);page=api.list_run_events(run.name)
    assert page['run_id']==run.name and [e['seq'] for e in page['events']]==[1,2] and page['has_more'] is False
    assert 'SECRET-CAP' not in json.dumps(page)
    second=api.list_run_events(run.name,page=2)
    assert second['events']==[] and second['has_more'] is False
    for seq in range(3,202):
        ev.record(run.name,'finished',{'status':'Failed','ordinal':seq})
    first=api.list_run_events(run.name)
    assert [e['seq'] for e in first['events']]==list(range(1,201)) and first['has_more'] is True
    second=api.list_run_events(run.name,page=2)
    assert [e['seq'] for e in second['events']]==[201] and second['has_more'] is False
    frappe.set_user(other)
    errors=[]
    for candidate in (run.name,'missing-'+uuid.uuid4().hex):
        try:api.list_run_events(candidate);raise AssertionError('other user read events')
        except frappe.PermissionError as exc:errors.append(str(exc))
    assert errors==['运行不属于当前用户','运行不属于当前用户'],errors
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name});frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    for email in (actor,other):
        if email and frappe.db.exists('User',email):frappe.delete_doc('User',email,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


def test_finish_run_reports_and_persists_provider_failure_count():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as events
from dsherp_bridge.context_execution import finish_run
conversation=None;run=None;capability=uuid.uuid4().hex
try:
    actor='dsherp-reader@example.invalid';frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Provider failure count'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'provider failures','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3)}).insert(ignore_permissions=True)
    for attempt,error_class in enumerate((
        'TRANSPORT','TIMEOUT','SERVER','ProviderError',
        'CONTEXT_WINDOW_EXCEEDED','EMPTY_RESPONSE','INVALID_REQUEST','AUTH','RATE_LIMIT','QUOTA_EXCEEDED','PI_AI_ERROR',
        # 已发布 runtime 实际发出的码（复核 批评 1）：余额不足是 QUOTA，密钥格式错误是
        # INVALID_CREDENTIAL，未归类的 4xx 是 HTTP_<status>；UNKNOWN 说明不了 provider 是否可用。
        'QUOTA','INVALID_CREDENTIAL','HTTP_402','HTTP_404','UNKNOWN','HTTPX_ERROR',
    ),start=1):
        events.record(run.name,'model_error',{'attempt':attempt},source='runner',error_class=error_class)
    frappe.set_user('Guest')
    result=finish_run(run.name,capability,'Failed',error='provider unavailable')
    # provider 不可用的类都要计数：六个原有类 + QUOTA、INVALID_CREDENTIAL、两个 HTTP_ 前缀 = 10。
    # 只有本轮输入造成的失败（上下文超长、请求非法、空响应）与说明不了可用性的 UNKNOWN/ProviderError 不计。
    assert result=={'run_id':run.name,'status':'Failed','provider_failures':10},result
    assert frappe.db.get_value('DS Model Run',run.name,'provider_failures')==10
    print('PROVIDER_FAILURE_COUNT_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        if frappe.db.exists('DS Model Run',run.name):frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation.name):
        frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ["docker","exec","-i","dsherp-validation-backend-1",
         "/home/frappe/frappe-bench/env/bin/python","-"],
        input=script,text=True,capture_output=True,timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROVIDER_FAILURE_COUNT_OK" in result.stdout
