"""Site concurrency, owner fairness, queue expiry, and worker heartbeat."""

import subprocess


def run_in_site(script, timeout=120):
    return subprocess.run(
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
        timeout=timeout,
    )


def test_worker_heartbeat_requires_runtime_identity_and_refreshes_cache():
    script = r'''
import os,frappe
from frappe.utils import get_datetime,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import worker_heartbeat
try:
    frappe.cache().delete_value('dsherp_worker_heartbeat')
    frappe.set_user('dsherp-reader@example.invalid')
    try:worker_heartbeat();raise AssertionError('ordinary user refreshed worker heartbeat')
    except frappe.PermissionError:pass
    frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    response=worker_heartbeat()
    stored=frappe.cache().get_value('dsherp_worker_heartbeat')
    assert response['heartbeat']==stored,response
    assert (now_datetime()-get_datetime(stored)).total_seconds()<5,stored
    print('WORKER_HEARTBEAT_OK')
finally:
    frappe.destroy()
'''
    result = run_in_site(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WORKER_HEARTBEAT_OK" in result.stdout, result.stdout


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
    frappe.set_user('Administrator')
    for name in runs:
        frappe.db.set_value('DS Model Run',name,'queue_expires_at',add_to_date(now_datetime(),seconds=600))
    frappe.db.commit()

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
    result = run_in_site(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLAIM_CONCURRENCY_OK" in result.stdout, result.stdout


def test_claim_expires_every_eligible_row_and_never_overwrites_cancelled():
    script = r'''
import os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import claim_run
conversation=None;runs=[];original_get_all=frappe.get_all
page_context='{"schema_version":1,"page_type":"unknown","route":[]}'
try:
    frappe.set_user('dsherp-reader@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Bulk queue expiry'}).insert(ignore_permissions=True)
    for index in range(21):
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
            'status':'Queued','question':f'expired {index}','page_context':page_context,
            'queue_expires_at':add_to_date(now_datetime(),seconds=-1)}).insert(ignore_permissions=True)
        runs.append(run.name)
    frappe.db.commit();frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    assert claim_run('a'*64) is None
    assert all(frappe.db.get_value('DS Model Run',name,'status')=='Failed' for name in runs)

    frappe.set_user('Administrator')
    cancelled=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
        'status':'Queued','question':'cancel during expiry','page_context':page_context,
        'queue_expires_at':add_to_date(now_datetime(),seconds=-1)}).insert(ignore_permissions=True)
    runs.append(cancelled.name);frappe.db.commit()
    def cancel_after_scan(*args,**kwargs):
        result=original_get_all(*args,**kwargs)
        filters=kwargs.get('filters')
        if (args==('DS Model Run',) and isinstance(filters,list)
            and ['queue_expires_at','is','set'] in filters and cancelled.name in result):
            frappe.db.set_value('DS Model Run',cancelled.name,'status','Cancelled');frappe.db.commit()
        return result
    frappe.get_all=cancel_after_scan;frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    assert claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',cancelled.name,'status')=='Cancelled'
    print('QUEUE_EXPIRY_RACES_OK')
finally:
    frappe.get_all=original_get_all;frappe.db.rollback();frappe.set_user('Administrator')
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        if frappe.db.exists('DS Model Run',name):frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation.name):
        frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = run_in_site(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QUEUE_EXPIRY_RACES_OK" in result.stdout, result.stdout


def test_claim_finds_owner_after_fifty_busy_rows():
    script = r'''
import os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import claim_run
conversations=[];runs=[]
page_context='{"schema_version":1,"page_type":"unknown","route":[]}'
had_concurrency='dsherp_site_concurrency' in frappe.conf
original_concurrency=frappe.conf.get('dsherp_site_concurrency')
try:
    frappe.conf.dsherp_site_concurrency=2
    frappe.set_user('dsherp-reader@example.invalid')
    busy=frappe.get_doc({'doctype':'DS Conversation','title':'Busy owner queue'}).insert(ignore_permissions=True)
    conversations.append(busy.name)
    active=frappe.get_doc({'doctype':'DS Model Run','conversation':busy.name,'domain':'query','status':'Running',
        'question':'active','page_context':page_context,'expires_at':add_to_date(now_datetime(),seconds=600)}).insert(ignore_permissions=True)
    runs.append(active.name)
    for index in range(50):
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':busy.name,'domain':'query','status':'Queued',
            'question':f'busy {index}','page_context':page_context,'queue_expires_at':add_to_date(now_datetime(),seconds=600)}).insert(ignore_permissions=True)
        runs.append(run.name)
    frappe.set_user('dsherp-denied@example.invalid')
    free=frappe.get_doc({'doctype':'DS Conversation','title':'Free owner queue'}).insert(ignore_permissions=True)
    conversations.append(free.name)
    target=frappe.get_doc({'doctype':'DS Model Run','conversation':free.name,'domain':'query','status':'Queued',
        'question':'free','page_context':page_context,'queue_expires_at':add_to_date(now_datetime(),seconds=600)}).insert(ignore_permissions=True)
    runs.append(target.name);frappe.db.commit();frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    claimed=claim_run('a'*64);frappe.db.commit()
    assert claimed and claimed['run_id']==target.name,claimed
    print('OWNER_WINDOW_FAIRNESS_OK')
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
    result = run_in_site(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER_WINDOW_FAIRNESS_OK" in result.stdout, result.stdout


def test_claim_counts_all_active_rows_before_using_capacity():
    script = r'''
import os,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import claim_run
conversations=[];runs=[]
page_context='{"schema_version":1,"page_type":"unknown","route":[]}'
had_concurrency='dsherp_site_concurrency' in frappe.conf
original_concurrency=frappe.conf.get('dsherp_site_concurrency')
try:
    frappe.conf.dsherp_site_concurrency=21
    frappe.set_user('dsherp-reader@example.invalid')
    busy=frappe.get_doc({'doctype':'DS Conversation','title':'Active pagination'}).insert(ignore_permissions=True)
    conversations.append(busy.name)
    for index in range(21):
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':busy.name,'domain':'query','status':'Running',
            'question':f'active {index}','page_context':page_context,'expires_at':add_to_date(now_datetime(),seconds=600)}).insert(ignore_permissions=True)
        runs.append(run.name)
    frappe.set_user('dsherp-denied@example.invalid')
    free=frappe.get_doc({'doctype':'DS Conversation','title':'Capacity target'}).insert(ignore_permissions=True)
    conversations.append(free.name)
    target=frappe.get_doc({'doctype':'DS Model Run','conversation':free.name,'domain':'query','status':'Queued',
        'question':'must remain queued','page_context':page_context,'queue_expires_at':add_to_date(now_datetime(),seconds=600)}).insert(ignore_permissions=True)
    runs.append(target.name);frappe.db.commit();frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    assert claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',target.name,'status')=='Queued'
    print('ACTIVE_PAGINATION_OK')
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
    result = run_in_site(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ACTIVE_PAGINATION_OK" in result.stdout, result.stdout
