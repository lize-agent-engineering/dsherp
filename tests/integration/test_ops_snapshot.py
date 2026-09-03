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
conversation=None;actor=None;runs=[];snapshots=[]
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
    snapshot=ops.collect_snapshot()
    snapshots.extend(frappe.get_all('DS Ops Snapshot',filters={'collected_at':snapshot['collected_at']},pluck='name'))
    assert set(snapshot)=={'queued','queued_oldest_seconds','running','running_stuck',
        'pending_proposals_expired','last_claim_age_seconds','runs_24h','backup_age_hours','site','collected_at'},snapshot
    assert snapshot['running_stuck']==1,snapshot
    assert snapshot['queued']>=1 and snapshot['queued_oldest_seconds']>=0,snapshot
    assert 'backup_age_hours' in snapshot
    assert not frappe.db.exists('DS Ops Snapshot',old.name) and frappe.db.exists('DS Ops Snapshot',recent.name)
    frappe.set_user('Administrator')
    assert set(ops.ops_status())==set(snapshot)
    frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    status=ops.ops_status()
    assert set(status)==set(snapshot),status
    frappe.set_user(actor)
    try:
        ops.ops_status();raise AssertionError('ordinary user read ops status')
    except frappe.PermissionError:pass
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in snapshots:frappe.db.delete('DS Ops Snapshot',{'name':name})
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        if frappe.db.exists('DS Model Run',name):frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
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
