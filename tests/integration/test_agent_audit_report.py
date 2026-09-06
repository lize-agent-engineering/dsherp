"""DS Agent Audit report exposes bounded aggregates only to operators."""

import os
import subprocess
from pathlib import Path

import pytest


def _fail_if_resident_worker_is_running():
    pid_file = Path(".runtime/agent-worker.pid")
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return
    try:
        os.kill(pid, 0)
    except OSError:
        return
    pytest.fail("常驻 worker 正在运行，会用真实 provider 抢先领取测试运行；先停止它再跑")


def test_agent_audit_report_filters_aggregates_and_requires_operator_role():
    _fail_if_resident_worker_is_running()
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.desk import query_report
from frappe.utils import add_to_date,now_datetime,today
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api,context_events
actors=[];conversations=[];runs=[];proposal=None;execution=None;operator=None
try:
    frappe.set_user('Administrator')
    for suffix in ('failed','succeeded'):
        frappe.set_user('Administrator')
        actor='audit-report-'+suffix+'-'+uuid.uuid4().hex+'@example.invalid';actors.append(actor)
        frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic audit report','enabled':1,
            'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
        frappe.set_user(actor)
        conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Audit report '+suffix}).insert(ignore_permissions=True)
        conversations.append(conversation)
        status='Failed' if suffix=='failed' else 'Succeeded'
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
            'status':status,'question':'synthetic audit '+suffix,'page_context':'{}','sources':'[]',
            'error':'E'*100 if status=='Failed' else '', 'model_calls':2 if status=='Failed' else 1}).insert(ignore_permissions=True)
        runs.append(run)
    frappe.set_user('Administrator')
    operator='audit-system-manager-'+uuid.uuid4().hex+'@example.invalid';actors.append(operator)
    frappe.get_doc({'doctype':'User','email':operator,'first_name':'Synthetic audit operator','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'System Manager'}]}).insert()
    run=runs[0];actor=actors[0];conversation=conversations[0]
    context_events.record(run.name,'queued',{'domain':'operation'})
    proposal=frappe.get_doc({'doctype':'DS Operation Proposal','model_run':run.name,
        'conversation':conversation.name,'payload':'{}','digest':hashlib.sha256(b'audit').hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=5),'status':'Succeeded'}).insert(ignore_permissions=True)
    execution=frappe.get_doc({'doctype':'DS Execution Record','proposal':proposal.name,
        'request_id':uuid.uuid4().hex,'status':'Succeeded','result':'{}'}).insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.set_user('Administrator')
    report=query_report.run('DS Agent Audit',filters={'from_date':today(),'to_date':today()})
    rows=report['result']
    selected={row['name']:row for row in rows if row['name'] in {item.name for item in runs}}
    assert set(selected)=={item.name for item in runs},rows
    row=selected[run.name]
    assert row['name']==run.name and row['owner']==actor and row['domain']=='operation' and row['status']=='Failed',row
    assert row['model_calls']==2 and row['proposals']==1 and row['executions']==1 and row['events']==1,row
    assert row['error']=='E'*80,row
    filtered=query_report.run('DS Agent Audit',filters={
        'from_date':today(),'to_date':today(),'user':actor,'status':'Failed'})['result']
    assert [item['name'] for item in filtered]==[run.name],filtered

    frappe.set_user(operator)
    operator_rows=query_report.run(
        'DS Agent Audit',filters={'from_date':today(),'to_date':today()})['result']
    expected={item.name for item in runs}
    assert {item['name'] for item in operator_rows if item['name'] in expected}==expected,operator_rows
    other_events=context_api.list_run_events(run.name)
    assert other_events['run_id']==run.name and [item['kind'] for item in other_events['events']]==['queued'],other_events

    frappe.set_user(actor)
    try:
        query_report.run('DS Agent Audit',filters={'from_date':today(),'to_date':today()})
        raise AssertionError('ordinary user accessed operator audit report')
    except frappe.PermissionError:
        pass
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if execution and frappe.db.exists('DS Execution Record',execution.name):
        frappe.db.delete('DS Execution Record',{'name':execution.name})
    if proposal and frappe.db.exists('DS Operation Proposal',proposal.name):
        frappe.db.delete('DS Operation Proposal',{'name':proposal.name})
    for run in runs:
        frappe.db.delete('DS Run Event',{'run':run.name})
        if frappe.db.exists('DS Model Run',run.name):frappe.db.delete('DS Model Run',{'name':run.name})
    for conversation in conversations:
        if frappe.db.exists('DS Conversation',conversation.name):
            frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    for actor in actors:
        if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ["docker", "exec", "-i", "dsherp-validation-backend-1", "/home/frappe/frappe-bench/env/bin/python", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
