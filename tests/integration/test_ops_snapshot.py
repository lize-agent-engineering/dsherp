"""Operational snapshots expose bounded, read-only runtime health."""

import os
from pathlib import Path
import subprocess

import pytest


def fail_if_resident_worker_running():
    pid_file = Path(".runtime/agent-worker.pid")
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except (OSError, ValueError):
        pytest.fail("常驻 worker PID 状态无效；先停止它再跑")
    pytest.fail("常驻 worker 正在运行，会用真实 provider 抢先领取测试运行；先停止它再跑")


def test_collect_snapshot_and_authorized_status():
    fail_if_resident_worker_running()
    script = r'''
import json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import ops
from dsherp_bridge import context_events as events
conversation=None;actor=None;runs=[];snapshots=[];proposals=[]
try:
    frappe.set_user('Administrator')
    actor='ops-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic ops','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Ops snapshot'}).insert(ignore_permissions=True)
    for status,expires_at in (
        ('Running',add_to_date(now_datetime(),minutes=-1)),
        ('Queued',add_to_date(now_datetime(),minutes=3)),
    ):
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
            'status':status,'question':'snapshot','page_context':'{}','sources':'[]','expires_at':expires_at}).insert(ignore_permissions=True)
        runs.append(run.name)
    old=frappe.get_doc({'doctype':'DS Ops Snapshot','collected_at':add_to_date(now_datetime(),days=-8),'payload':'{}'}).insert(ignore_permissions=True)
    recent=frappe.get_doc({'doctype':'DS Ops Snapshot','collected_at':add_to_date(now_datetime(),days=-1),'payload':'{}'}).insert(ignore_permissions=True)
    snapshots.extend([old.name,recent.name])
    frappe.db.commit()
    since=add_to_date(now_datetime(),hours=-24)
    baseline_needs=frappe.db.count('DS Model Run',{'status':'NeedsInput'})
    baseline_queue=sum(1 for row in frappe.get_all('DS Run Event',filters={'kind':'expired','recorded_at':['>=',since]},fields=['payload']) if json.loads(row.payload or '{}').get('reason')=='queue_expired')
    baseline_proposals=frappe.db.count('DS Operation Proposal',{'status':'Expired','modified':['>=',since]})
    need=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
        'status':'NeedsInput','question':'need input','page_context':'{}','sources':'[]','needs_input':'请指定仓库'}).insert(ignore_permissions=True)
    runs.append(need.name)
    expired_run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query',
        'status':'Failed','question':'expired','page_context':'{}','sources':'[]'}).insert(ignore_permissions=True)
    runs.append(expired_run.name)
    events.record(expired_run.name,'expired',{'reason':'queue_expired'})
    events.record(expired_run.name,'expired',{'reason':'lease_expired'})
    proposal=frappe.get_doc({'doctype':'DS Operation Proposal','conversation':conversation.name,
        'payload':'{}','digest':'ops-'+uuid.uuid4().hex,'expires_at':add_to_date(now_datetime(),minutes=-1),
        'status':'Expired'}).insert(ignore_permissions=True)
    proposals.append(proposal.name)
    frappe.db.commit()
    snapshot=ops.collect_snapshot()
    snapshots.extend(frappe.get_all('DS Ops Snapshot',filters={'collected_at':snapshot['collected_at']},pluck='name'))
    assert set(snapshot)=={'queued','queued_oldest_seconds','running','running_stuck',
        'pending_proposals_expired','last_claim_age_seconds','runs_24h','backup_age_hours','site','collected_at',
        'needs_input','queue_expired_24h','proposals_expired_24h'},snapshot
    assert snapshot['needs_input']==baseline_needs+1,snapshot
    assert snapshot['queue_expired_24h']==baseline_queue+1,snapshot
    assert snapshot['proposals_expired_24h']==baseline_proposals+1,snapshot
    assert snapshot['running_stuck']==1,snapshot
    assert snapshot['queued']>=1 and snapshot['queued_oldest_seconds']>=0,snapshot
    assert snapshot['runs_24h'].get('Running',0)>=1,snapshot
    assert snapshot['runs_24h'].get('Queued',0)>=1,snapshot
    assert 'backup_age_hours' in snapshot
    serialized=json.dumps(snapshot,ensure_ascii=False).lower()
    assert all(secret not in serialized for secret in ('capability','api_secret','deepseek')),serialized
    assert not frappe.db.exists('DS Ops Snapshot',old.name) and frappe.db.exists('DS Ops Snapshot',recent.name)
    frappe.set_user('Administrator')
    status=ops.ops_status()
    assert set(status)=={'snapshot','age_seconds'} and status['snapshot']==snapshot,status
    assert isinstance(status['age_seconds'],int) and status['age_seconds']>=0,status
    frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    status=ops.ops_status()
    assert set(status)=={'snapshot','age_seconds'} and status['snapshot']==snapshot,status
    frappe.set_user(actor)
    try:
        ops.ops_status();raise AssertionError('ordinary user read ops status')
    except frappe.PermissionError:pass
    frappe.set_user('Administrator')
    snapshot_count=frappe.db.count('DS Ops Snapshot')
    original_get_all=frappe.get_all
    frappe.get_all=lambda doctype,*args,**kwargs:[] if doctype=='DS Ops Snapshot' else original_get_all(doctype,*args,**kwargs)
    try:
        assert ops.ops_status()=={'snapshot':None,'age_seconds':None}
    finally:
        frappe.get_all=original_get_all
    assert frappe.db.count('DS Ops Snapshot')==snapshot_count
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in snapshots:frappe.db.delete('DS Ops Snapshot',{'name':name})
    for name in proposals:
        if frappe.db.exists('DS Operation Proposal',name):frappe.db.delete('DS Operation Proposal',{'name':name})
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        if frappe.db.exists('DS Model Run',name):frappe.db.delete('DS Model Run',{'name':name})
    if conversation and frappe.db.exists('DS Conversation',conversation.name):frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
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
