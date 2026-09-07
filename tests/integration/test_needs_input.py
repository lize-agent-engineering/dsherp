"""Real NeedsInput state, event, capability finalization, and public transcript."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def _require_resident_worker_stopped():
    path=Path('.runtime/agent-worker.pid')
    if not path.exists():return
    try:
        os.kill(int(path.read_text()),0)
    except (OSError,ValueError):
        return
    raise AssertionError('常驻 worker 正在运行；进程内集成测试必须先停止它')



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
            frappe.db.delete('DS Model Run',{'name':name})
        if frappe.db.exists('DS Conversation',doc['id']):frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1',
        '/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'NEEDS_INPUT_OK queued,claimed,needs_input,tool_call,finished' in result.stdout


def test_needs_input_run_stays_the_single_executor_until_it_hands_the_session_back():
    """capability 仍有效时 NeedsInput 必须算在飞：否则同一 native session 会被第二个执行者领走。"""
    _require_resident_worker_stopped()
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
actor='dsherp-reader@example.invalid'
payload={'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}
had_runtime_user='dsherp_runtime_user' in frappe.conf
original_runtime_user=frappe.conf.get('dsherp_runtime_user')
asking=None;waiting=None
try:
    frappe.set_user('Administrator')
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Queued','Running','Cancelling']]},pluck='name')
    assert not active,('validation site has active runs; stop the resident worker first',active)
    frappe.set_user(actor)
    asking=api.send_message('入库到哪个仓库',payload,uuid.uuid4().hex)
    frappe.db.commit()
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    frappe.set_user('Guest')
    assert execution.run_tool(**cap,tool='erp_request_input',arguments={'question':'请指定入库仓库'})=={'status':'NeedsInput'}
    frappe.db.commit()

    # 旧执行者还没退出（capability 仍在），此刻不得放第二个执行者进来。
    frappe.set_user(actor)
    waiting=api.send_message('另一个问题',payload,uuid.uuid4().hex)
    frappe.db.commit()
    assert execution.claim_run('a'*64) is None,'second executor claimed while NeedsInput still held the session'
    frappe.db.rollback()
    assert api.get_session(asking['id'])['active_run']==cap['run_id']

    # 执行者回写后才交还会话。
    frappe.set_user('Guest')
    execution.finish_run(**cap,status='NeedsInput',answer='请指定入库仓库');frappe.db.commit()
    assert not frappe.db.get_value('DS Model Run',cap['run_id'],'capability_hash')
    frappe.set_user(actor)
    assert api.get_session(asking['id'])['active_run'] is None
    released=execution.claim_run('a'*64);frappe.db.commit()
    assert released and released['run_id']==waiting['active_run'],released
    frappe.set_user('Guest')
    execution.finish_run(run_id=released['run_id'],capability=released['capability'],status='Failed',error='测试收尾')
    frappe.db.commit()
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if had_runtime_user:frappe.conf.dsherp_runtime_user=original_runtime_user
    else:frappe.conf.pop('dsherp_runtime_user',None)
    for doc in (asking,waiting):
        if not doc:continue
        for name in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':name})
            frappe.db.delete('DS Model Run',{'name':name})
        frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=60)
    assert result.returncode==0 and 'OK' in result.stdout,result.stdout+result.stderr
