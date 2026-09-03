"""Site concurrency, owner fairness, queue expiry, and worker heartbeat."""

import subprocess


def test_claim_respects_site_and_owner_limits_and_expires_queued_runs():
    script = r'''
import os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as events
from dsherp_bridge.context_execution import claim_run
actors=['dsherp-reader@example.invalid','dsherp-denied@example.invalid','dsherp-writer@example.invalid']
conversations=[];runs=[]
runtime_user=frappe.conf.get('dsherp_runtime_user')
had_concurrency='dsherp_site_concurrency' in frappe.conf
original_concurrency=frappe.conf.get('dsherp_site_concurrency')

def enqueue(actor,question):
    frappe.set_user(actor)
    session=api.send_message(question,{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex,domain='query')
    conversations.append(session['id']);runs.append(session['active_run'])
    frappe.db.commit()
    return session['active_run']

try:
    frappe.set_user('Administrator')
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Queued','Running','Cancelling']]},pluck='name')
    assert not active,('validation site has active runs; stop the resident worker and clean the queue first',active)
    assert all(frappe.db.exists('User',actor) for actor in actors),actors
    frappe.conf.dsherp_site_concurrency=2

    first_owner_run=enqueue(actors[0],'first owner first run')
    same_owner_queued=enqueue(actors[0],'first owner second run')
    second_owner_run=enqueue(actors[1],'second owner run')
    third_owner_queued=enqueue(actors[2],'third owner run')

    frappe.set_user(runtime_user)
    first=claim_run('a'*64);frappe.db.commit()
    assert first and first['run_id']==first_owner_run,first
    assert first['site']=='dsherp-validation.localhost' and first['budget']['site_concurrency']==2,first
    second=claim_run('a'*64);frappe.db.commit()
    assert second and second['run_id']==second_owner_run,second
    assert claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',same_owner_queued,'status')=='Queued'
    assert frappe.db.get_value('DS Model Run',third_owner_queued,'status')=='Queued'
    assert frappe.cache().get_value('dsherp_worker_heartbeat')

    frappe.set_user('Administrator')
    for name in (first_owner_run,second_owner_run,same_owner_queued,third_owner_queued):
        frappe.db.set_value('DS Model Run',name,{'status':'Cancelled','capability_hash':''})
    frappe.db.commit()
    expired=enqueue(actors[2],'expired queued run')
    frappe.set_user('Administrator')
    frappe.db.set_value('DS Model Run',expired,'queue_expires_at',add_to_date(now_datetime(),seconds=-1))
    frappe.db.commit();frappe.set_user(runtime_user)
    assert claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',expired,'status')=='Failed'
    assert frappe.db.get_value('DS Model Run',expired,'error')=='系统繁忙，排队超时，请稍后重试'
    tail=events.list_events(expired)[-2:]
    assert [item['kind'] for item in tail]==['expired','finished'],tail
    assert tail[0]['payload']=={'reason':'queue_expired'},tail
    print('CLAIM_CONCURRENCY_OK',first_owner_run,second_owner_run,expired)
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if had_concurrency:frappe.conf.dsherp_site_concurrency=original_concurrency
    else:frappe.conf.pop('dsherp_site_concurrency',None)
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        if frappe.db.exists('DS Model Run',name):frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
    for name in conversations:
        if frappe.db.exists('DS Conversation',name):frappe.delete_doc('DS Conversation',name,ignore_permissions=True)
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
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLAIM_CONCURRENCY_OK" in result.stdout, result.stdout
