"""send_message applies queue expiry and rejects unavailable workers."""

import json
import subprocess
import uuid
from pathlib import Path

import httpx


def test_send_message_rejects_unavailable_worker_and_sets_queue_expiry():
    script = r'''
import os,uuid,frappe
from frappe.utils import add_to_date,get_datetime,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge.run_budget import budget
actor='dsherp-reader@example.invalid'
context={'schema_version':1,'page_type':'unknown','route':[]}
conversations=[];runs=[]
cache=frappe.cache()
previous=cache.get_value('dsherp_worker_heartbeat')

def send(question):
    return api.send_message(question,context,uuid.uuid4().hex,domain='query')

def reject_without_run(question):
    before=set(frappe.get_all('DS Model Run',pluck='name'))
    before_sessions=set(frappe.get_all('DS Conversation',filters={'owner':actor},pluck='name'))
    frappe.local.response.pop('http_status_code',None)
    try:
        send(question);raise AssertionError('unavailable worker accepted')
    except frappe.ValidationError as error:
        assert str(error)=='助手服务暂不可用，请稍后再试',error
        assert frappe.local.response.http_status_code==503,frappe.local.response
    assert set(frappe.get_all('DS Model Run',pluck='name'))==before
    assert set(frappe.get_all('DS Conversation',filters={'owner':actor},pluck='name'))==before_sessions

try:
    frappe.set_user(actor)
    cache.delete_value('dsherp_worker_heartbeat')
    reject_without_run('missing heartbeat')

    cache.set_value('dsherp_worker_heartbeat',add_to_date(now_datetime(),seconds=-budget('query')['heartbeat_stale_seconds']-1).isoformat(),expires_in_sec=3600)
    reject_without_run('stale heartbeat')

    cache.set_value('dsherp_worker_heartbeat',now_datetime().isoformat(),expires_in_sec=3600)
    started=now_datetime()
    session=send('fresh heartbeat')
    conversations.append(session['id']);runs.append(session['active_run'])
    frappe.db.commit()
    assert session['active_run'] and session['messages'][-1]['status']=='Queued',session
    expires=get_datetime(frappe.db.get_value('DS Model Run',session['active_run'],'queue_expires_at'))
    remaining=(expires-started).total_seconds()
    assert 590<=remaining<=610,(expires,started,remaining)
    print('QUEUE_BACKPRESSURE_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if previous is None:cache.delete_value('dsherp_worker_heartbeat')
    else:cache.set_value('dsherp_worker_heartbeat',previous,expires_in_sec=3600)
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
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QUEUE_BACKPRESSURE_OK" in result.stdout, result.stdout


def _exec(script):
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
    return result.stdout


def test_send_message_http_rejects_unavailable_worker_with_503():
    reader = json.loads(Path(".runtime/erp-users.json").read_text())["reader"]
    request_id = uuid.uuid4().hex
    setup = _exec(
        r"""
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
cache=frappe.cache()
previous=cache.get_value('dsherp_worker_heartbeat')
cache.delete_value('dsherp_worker_heartbeat')
if isinstance(previous,(bytes,bytearray)):previous=previous.decode()
elif previous is not None:previous=str(previous)
print(json.dumps({'previous':previous,'runs':frappe.get_all('DS Model Run',pluck='name')}))
frappe.destroy()
"""
    )
    state = json.loads(setup.strip().splitlines()[-1])
    try:
        with httpx.Client(
            base_url=reader["base_url"],
            headers={
                "X-Frappe-Site-Name": reader["site"],
                "Authorization": "token " + reader["api_key"] + ":" + reader["api_secret"],
            },
            trust_env=False,
            timeout=20,
        ) as client:
            response = client.post(
                "/api/method/dsherp_bridge.context_api.send_message",
                json={
                    "question": "missing heartbeat http",
                    "context": {"schema_version": 1, "page_type": "unknown", "route": []},
                    "request_id": request_id,
                    "domain": "query",
                },
            )
        assert response.status_code == 503, response.text
        assert "助手服务暂不可用，请稍后再试" in response.text, response.text
        after = _exec(
            r"""
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
print(json.dumps(frappe.get_all('DS Model Run',pluck='name')))
frappe.destroy()
"""
        )
        assert set(json.loads(after.strip().splitlines()[-1])) == set(state["runs"])
    finally:
        _exec(
            """
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
cache=frappe.cache()
previous=%r
if previous is None:cache.delete_value('dsherp_worker_heartbeat')
else:cache.set_value('dsherp_worker_heartbeat',previous,expires_in_sec=3600)
frappe.destroy()
"""
            % (state["previous"],)
        )
