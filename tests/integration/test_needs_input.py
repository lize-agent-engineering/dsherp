"""Real NeedsInput state, event, capability finalization, and public transcript."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def _require_stopped_agent_worker():
    pid_file=Path('.runtime/agent-worker.pid')
    if not pid_file.exists():return
    try:
        os.kill(int(pid_file.read_text()),0)
    except ProcessLookupError:return
    except (OSError,ValueError):pytest.fail('常驻 worker PID 状态无效；先停止它再跑')
    pytest.fail('常驻 worker 正在运行，会抢先领取 NeedsInput 测试运行；先停止它再跑')


def test_model_can_request_input_and_finish_without_active_run():
    _require_stopped_agent_worker()
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as events
from dsherp_bridge import context_execution as execution
actor='dsherp-reader@example.invalid';doc=None
payload={'schema_version':1,'page_type':'unknown','route':[]}
try:
    frappe.set_user(actor)
    doc=api.send_message('需要仓库的查询',payload,uuid.uuid4().hex)
    frappe.db.commit()
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    assert claim and claim['run_id']==doc['active_run'],claim
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    frappe.set_user('Guest')
    result=execution.run_tool(**cap,tool='erp_request_input',arguments={'question':'请指定仓库'})
    assert result=={'status':'NeedsInput'},result
    stored=frappe.db.get_value('DS Model Run',claim['run_id'],['status','needs_input','capability_hash'],as_dict=True)
    assert stored.status=='NeedsInput' and stored.needs_input=='请指定仓库' and stored.capability_hash,stored
    status=execution.run_status(**cap)
    assert status['status']=='NeedsInput' and status['needs_input']=='请指定仓库',status
    finished=execution.finish_run(**cap,status='NeedsInput',answer='请指定仓库')
    assert finished['status']=='NeedsInput',finished
    frappe.db.commit()
    final=frappe.db.get_value('DS Model Run',claim['run_id'],
        ['status','answer','needs_input','capability_hash'],as_dict=True)
    assert final.status=='NeedsInput' and not final.answer and final.needs_input=='请指定仓库',final
    assert not final.capability_hash,final
    try:execution.run_status(**cap);raise AssertionError('NeedsInput capability remained active')
    except frappe.PermissionError:pass
    frappe.set_user(actor)
    public=api.get_session(doc['id'])
    assert public['active_run'] is None,public
    message=next(item for item in public['messages'] if item['id']==claim['run_id'])
    assert message['status']=='NeedsInput' and message['answer']=='请指定仓库',message
    kinds=[item['kind'] for item in events.list_events(claim['run_id'])]
    assert kinds==['queued','claimed','needs_input','tool_call','finished'],kinds
    print('NEEDS_INPUT_OK '+','.join(kinds))
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if doc:
        for name in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':name})
            frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
        if frappe.db.exists('DS Conversation',doc['id']):frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1',
        '/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'NEEDS_INPUT_OK queued,claimed,needs_input,tool_call,finished' in result.stdout
