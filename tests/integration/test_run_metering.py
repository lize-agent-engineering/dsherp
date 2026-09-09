"""Every finished run settles its own metering, on a real Site, with no paid provider call.

G8's third clause is "each run records the model, the prompt and the skill versions". The
numbers may only come from what the server itself wrote down for that run — the reserved
calls, the provider's own usage in model_response, and the finished event — never from a
summary the runner hands back.
"""
import subprocess

SCRIPT = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as events
from dsherp_bridge.context_execution import finish_run,run_tool
from dsherp_bridge.context_permissions import run_revision
actor='dsherp-reader@example.invalid'
conversation=None
page_context=json.dumps({'schema_version':1,'page_type':'unknown','route':[]})
SKILLS={'erp-query':'1.4.0','erp-operation':'2.3.0','erp-configuration':'1.1.0'}

def start_run(question):
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
        'status':'Running','question':question,'page_context':page_context,'sources':'[]',
        'permission_revision':run_revision(actor,'query'),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3)}).insert(ignore_permissions=True)
    return run.name,capability

def read_back(run_id):
    return frappe.db.get_value('DS Model Run',run_id,
        ['status','model','provider_request_ids','actual_input_tokens','actual_output_tokens',
         'duration_ms','skill_versions','usage_unknown_calls'],as_dict=True)

try:
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Run metering'}).insert(ignore_permissions=True)

    # 1. A fully accounted run: two calls, both answered with a usage the provider reported.
    run_id,capability=start_run('合成计量运行')
    frappe.set_user('Guest')
    events.record(run_id,'runtime_started',{'skill_versions':SKILLS},source='runner')
    for index in (1,2):
        events.record(run_id,'model_call_reserved',{'purpose':'business'})
        events.record(run_id,'model_response',{'model':'deepseek-v4-flash','request_id':f'req-{index}',
            'usage':{'input_tokens':600,'output_tokens':170}})
    read=run_tool(run_id,capability,'erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    assert read['name']=='DSHERP-TEST-ITEM',read
    result=finish_run(run_id,capability,'Succeeded',answer='读到了合成物料')
    assert result['status']=='Succeeded',result
    row=read_back(run_id)
    assert row.model=='deepseek-v4-flash',row
    assert row.actual_input_tokens==1200 and row.actual_output_tokens==340,row
    assert json.loads(row.skill_versions)==SKILLS,row
    assert row.duration_ms is not None and row.duration_ms>=0,row
    assert row.usage_unknown_calls==0,row
    assert json.loads(row.provider_request_ids)==['req-1','req-2'],row

    # 2. A call the provider never accounted for is unknown, not zero; the known half still adds.
    frappe.set_user(actor)
    partial_id,partial_cap=start_run('合成部分计量')
    frappe.set_user('Guest')
    events.record(partial_id,'model_call_reserved',{'purpose':'business'})
    events.record(partial_id,'model_response',{'model':'deepseek-v4-flash','usage':{'input_tokens':500}})
    events.record(partial_id,'model_call_reserved',{'purpose':'business'})
    read=run_tool(partial_id,partial_cap,'erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    finish_run(partial_id,partial_cap,'Succeeded',answer='读到了合成物料')
    row=read_back(partial_id)
    assert row.usage_unknown_calls==2,row          # one partial usage, one call never answered
    assert row.actual_input_tokens==500,row        # the known half is still counted
    assert row.actual_output_tokens==0,row

    # 3. A failed run settles too - the tokens it burnt are just as billable.
    frappe.set_user(actor)
    failed_id,failed_cap=start_run('合成失败运行')
    frappe.set_user('Guest')
    events.record(failed_id,'model_call_reserved',{'purpose':'business'})
    events.record(failed_id,'model_response',{'model':'deepseek-v4-flash','usage':{'input_tokens':70,'output_tokens':9}})
    finish_run(failed_id,failed_cap,'Failed',error='业务运行失败：RuntimeError')
    row=read_back(failed_id)
    assert row.status=='Failed' and row.actual_input_tokens==70 and row.actual_output_tokens==9,row

    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation is not None:
        try:frappe.delete_doc('DS Conversation',conversation.name,force=True,ignore_permissions=True);frappe.db.commit()
        except Exception:pass
    frappe.destroy()
'''


def test_a_finished_run_carries_the_usage_its_events_reported():
    done = subprocess.run(
        ['docker', 'exec', '-i', 'dsherp-validation-backend-1',
         '/home/frappe/frappe-bench/env/bin/python', '-'],
        input=SCRIPT, text=True, capture_output=True, timeout=180)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == 'OK', done.stdout
