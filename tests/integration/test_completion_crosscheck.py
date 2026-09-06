"""Real-site completion-claim cross-check; no paid provider or business writes."""
import subprocess


def test_finish_run_flags_unverified_completion_without_changing_status():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as events
from dsherp_bridge.context_execution import finish_run,run_tool
from dsherp_bridge.context_permissions import run_revision
actor='dsherp-reader@example.invalid'
conversation=None;runs=[]
page_context=json.dumps({'schema_version':1,'page_type':'unknown','route':[]})

def start_run(question):
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
        'status':'Running','question':question,'page_context':page_context,'sources':'[]',
        'permission_revision':run_revision(actor,'query'),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3)}).insert(ignore_permissions=True)
    runs.append(run.name)
    return run.name,capability

try:
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Completion crosscheck'}).insert(ignore_permissions=True)
    first_id,first_cap=start_run('合成完成自述')
    frappe.set_user('Guest')
    read=run_tool(first_id,first_cap,'erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    assert read['name']=='DSHERP-TEST-ITEM',read
    sources=json.loads(frappe.db.get_value('DS Model Run',first_id,'sources') or '[]')
    assert sources,sources
    result=finish_run(first_id,first_cap,'Succeeded',answer='已创建销售订单 SO-001')
    assert result['status']=='Succeeded',result
    stored=frappe.db.get_value('DS Model Run',first_id,['status','answer_flagged'],as_dict=True)
    assert stored.status=='Succeeded' and stored.answer_flagged==1,stored
    first_events=events.list_events(first_id)
    finished=[item for item in first_events if item['kind']=='finished']
    assert finished and finished[0]['payload']['proposals']==0,finished
    assert finished[0]['payload']['executions']==0 and finished[0]['payload']['sources']==len(sources),finished
    last=first_events[-1]
    assert last['kind']=='unverified_completion_claim',last
    assert last['payload']=={'proposals':0,'executions':0},last

    frappe.set_user(actor)
    second_id,second_cap=start_run('合成查询回答')
    frappe.set_user('Guest')
    assert run_tool(second_id,second_cap,'erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'})['name']=='DSHERP-TEST-ITEM'
    result=finish_run(second_id,second_cap,'Succeeded',answer='共找到 3 条物料')
    assert result['status']=='Succeeded',result
    stored=frappe.db.get_value('DS Model Run',second_id,['status','answer_flagged'],as_dict=True)
    assert stored.status=='Succeeded' and stored.answer_flagged==0,stored
    second_events=events.list_events(second_id)
    assert second_events[-1]['kind']=='finished',second_events[-1]
    assert all(item['kind']!='unverified_completion_claim' for item in second_events),second_events
    assert second_events[-1]['payload']['proposals']==0
    assert second_events[-1]['payload']['executions']==0
    assert second_events[-1]['payload']['sources']>=1

    frappe.set_user(actor)
    public=api.get_session(conversation.name)
    assert public['messages'],public
    for message in public['messages']:
        assert 'answer_flagged' in message,message
        assert message['answer_flagged'] in (0,1,False,True),message
    by_id={message['id']:message for message in public['messages']}
    assert by_id[first_id]['answer_flagged'] in (1,True),by_id[first_id]
    assert by_id[second_id]['answer_flagged'] in (0,False),by_id[second_id]
    print('COMPLETION_CROSSCHECK_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        if frappe.db.exists('DS Model Run',name):frappe.db.delete('DS Model Run',{'name':name})
    if conversation and frappe.db.exists('DS Conversation',conversation.name):
        frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
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
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPLETION_CROSSCHECK_OK" in result.stdout
