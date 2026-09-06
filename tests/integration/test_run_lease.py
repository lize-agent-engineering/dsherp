"""Run status is a cheap capability check and renews leases near expiry."""

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


def test_run_status_renews_only_near_expiry_without_locking_or_revision_reads():
    script = r'''
import hashlib,inspect,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as events
from dsherp_bridge.context_execution import run_status
from dsherp_bridge.context_permissions import run_revision
actor='dsherp-reader@example.invalid';conversation=None;run=None;capability=uuid.uuid4().hex
try:
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Lease renewal'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'lease','page_context':'{"schema_version":1,"page_type":"unknown","route":[]}',
        'permission_revision':run_revision(actor,'query'),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),seconds=60)}).insert(ignore_permissions=True)
    frappe.db.commit();frappe.set_user('Guest')
    renewed=run_status(run.name,capability)
    assert renewed['run_id']==run.name and renewed['status']=='Running',renewed
    assert 175<=renewed['lease_remaining_seconds']<=180,renewed
    last=events.list_events(run.name)[-1]
    assert last['kind']=='lease_renewed' and last['payload'].get('expires_at'),last

    frappe.set_user('Administrator')
    frappe.db.set_value('DS Model Run',run.name,'expires_at',add_to_date(now_datetime(),seconds=150))
    before=frappe.db.count('DS Run Event',{'run':run.name});frappe.db.commit();frappe.set_user('Guest')
    unchanged=run_status(run.name,capability)
    assert 140<=unchanged['lease_remaining_seconds']<=150,unchanged
    assert frappe.db.count('DS Run Event',{'run':run.name})==before
    for run_id,provided in ((run.name,'wrong'),('missing-run',capability)):
        try:run_status(run_id,provided);raise AssertionError('invalid capability accepted')
        except frappe.PermissionError:pass
    source=inspect.getsource(run_status)
    assert 'for_update=True' not in source,source
    print('RUN_LEASE_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        if frappe.db.exists('DS Model Run',run.name):frappe.db.delete('DS Model Run',{'name':run.name})
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
    assert "RUN_LEASE_OK" in result.stdout, result.stdout


def test_a_claim_the_worker_never_received_is_failed_as_unstarted_not_as_a_timeout():
    """claim 的 HTTP 超时后服务端仍提交 Running：这条运行没有执行者，必须尽快释放并说清原因。"""
    _require_resident_worker_stopped()
    script=r'''
import os,uuid,json,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
from dsherp_bridge import context_events as events
from dsherp_bridge.run_budget import budget
actor='dsherp-reader@example.invalid'
payload={'schema_version':1,'page_type':'unknown','route':[]}
had_runtime_user='dsherp_runtime_user' in frappe.conf
original_runtime_user=frappe.conf.get('dsherp_runtime_user')
ghost=None;started=None
try:
    frappe.set_user('Administrator')
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Queued','Running','Cancelling']]},pluck='name')
    assert not active,('validation site has active runs; stop the resident worker first',active)
    plan=budget('query')
    assert plan['claim_ack_seconds']<plan['lease_seconds'],plan

    frappe.set_user(actor)
    ghost=api.send_message('worker 收不到的领取',payload,uuid.uuid4().hex);frappe.db.commit()
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    # 领取只给一个短确认租约，执行者第一次 run_status 才升到完整租约。
    remaining=(frappe.utils.get_datetime(frappe.db.get_value('DS Model Run',claim['run_id'],'expires_at'))-now_datetime()).total_seconds()
    assert remaining<=plan['claim_ack_seconds'],remaining
    frappe.db.set_value('DS Model Run',claim['run_id'],'expires_at',add_to_date(now_datetime(),seconds=-1));frappe.db.commit()
    assert execution.claim_run('a'*64) is None
    frappe.db.commit()
    saved=frappe.db.get_value('DS Model Run',claim['run_id'],['status','error'],as_dict=True)
    assert saved.status=='Failed' and saved.error=='助手未能启动本次运行，请重试',saved
    assert [e['payload'].get('reason') for e in events.list_events(claim['run_id']) if e['kind']=='expired']==['claim_unacked']

    # 执行者启动过的运行仍按原有租约口径处理。
    frappe.set_user(actor)
    started=api.send_message('执行者启动过',payload,uuid.uuid4().hex);frappe.db.commit()
    second=execution.claim_run('a'*64);frappe.db.commit()
    frappe.set_user('Guest')
    execution.record_run_event(second['run_id'],second['capability'],
        [{'kind':'runtime_started','source':'runner','payload':{'session_id':'s'}}])
    frappe.db.commit()
    frappe.set_user('Administrator')
    frappe.db.set_value('DS Model Run',second['run_id'],'expires_at',add_to_date(now_datetime(),seconds=-1));frappe.db.commit()
    frappe.set_user(actor)
    execution.claim_run('a'*64);frappe.db.commit()
    late=frappe.db.get_value('DS Model Run',second['run_id'],['status','error'],as_dict=True)
    assert late.status=='Failed' and late.error=='运行已过期，未自动重试',late
    assert 'lease_expired' in [e['payload'].get('reason') for e in events.list_events(second['run_id']) if e['kind']=='expired']
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if had_runtime_user:frappe.conf.dsherp_runtime_user=original_runtime_user
    else:frappe.conf.pop('dsherp_runtime_user',None)
    for doc in (ghost,started):
        if not doc:continue
        for name in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':name})
            frappe.db.delete('DS Model Run',{'name':name})
        frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=60)
    assert result.returncode==0 and 'OK' in result.stdout,result.stdout+result.stderr
