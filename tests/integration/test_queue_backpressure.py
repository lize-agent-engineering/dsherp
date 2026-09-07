"""send_message applies queue expiry and rejects unavailable workers."""

import json
import uuid
from pathlib import Path

import httpx

from site_exec import run_site_json, run_site_script

SITE = 'dsherp-validation.localhost'
ACTOR = 'dsherp-reader@example.invalid'
# Both tests take the shared `dsherp_worker_heartbeat` key away on purpose - that absence is
# exactly what makes send_message answer 503. Each puts it back itself, but a host-side timeout
# kills the docker client and not the interpreter, so the script's `finally` may never run and
# every later test on this Site would see an unavailable worker. Registered before the deletion,
# this idempotent body seeds a fresh beat of the same shape conftest.seed_validation_worker_heartbeat
# writes, whatever state the script left the key in.
HEARTBEAT_RESTORE = r'''
from frappe.utils import now_datetime
value=now_datetime().isoformat()
frappe.cache().set_value('dsherp_worker_heartbeat',value,expires_in_sec=3600)
written=frappe.cache().get_value('dsherp_worker_heartbeat')
if written!=value:raise RuntimeError('dsherp_worker_heartbeat was not written')
print(json.dumps({'heartbeat':value}))
'''

SCRIPT = r'''
from frappe.utils import add_to_date,get_datetime,now_datetime
from dsherp_bridge import context_api as api
from dsherp_bridge.run_budget import budget
actor=ACTOR
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
    session=send(FRESH_QUESTION)
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
        if frappe.db.exists('DS Model Run',name):frappe.db.delete('DS Model Run',{'name':name})
    for name in conversations:
        if frappe.db.exists('DS Conversation',name):frappe.delete_doc('DS Conversation',name,ignore_permissions=True)
    frappe.db.commit()
'''

# The setup is its own script: the HTTP call it prepares for happens on the host, between two
# visits to the Site, so the deletion cannot live in the same interpreter as the check after it.
HTTP_SETUP = r'''
cache=frappe.cache()
previous=cache.get_value('dsherp_worker_heartbeat')
cache.delete_value('dsherp_worker_heartbeat')
if isinstance(previous,(bytes,bytearray)):previous=previous.decode()
elif previous is not None:previous=str(previous)
print(json.dumps({'previous':previous,'runs':frappe.get_all('DS Model Run',pluck='name')}))
'''

HTTP_TEARDOWN = ("cache=frappe.cache()\nprevious=%r\n"
                 "if previous is None:cache.delete_value('dsherp_worker_heartbeat')\n"
                 "else:cache.set_value('dsherp_worker_heartbeat',previous,expires_in_sec=3600)\n")


def test_send_message_rejects_unavailable_worker_and_sets_queue_expiry(residue):
    # The question is the conversation's title (context_api.send_message keeps its first 100
    # chars), so a unique tag is what lets the sweep find this run's conversation and nothing else.
    question = f'fresh heartbeat {uuid.uuid4().hex}'
    residue.restore(SITE, 'worker heartbeat', HEARTBEAT_RESTORE)
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    output = run_site_script(SITE, SCRIPT.replace('ACTOR', repr(ACTOR)).replace(
        'FRESH_QUESTION', repr(question)), timeout=90)
    assert 'QUEUE_BACKPRESSURE_OK' in output, output


def test_send_message_http_rejects_unavailable_worker_with_503(residue):
    reader = json.loads(Path('.runtime/erp-users.json').read_text())['reader']
    question = f'missing heartbeat http {uuid.uuid4().hex}'
    request_id = uuid.uuid4().hex
    residue.restore(SITE, 'worker heartbeat', HEARTBEAT_RESTORE)
    # Nothing is expected to be created - that is the assertion below. Registering the
    # conversation anyway means a regression that lets the request through is reported as a
    # failure rather than left on the shared Site.
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    state = run_site_json(SITE, HTTP_SETUP)
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
                    "question": question,
                    "context": {"schema_version": 1, "page_type": "unknown", "route": []},
                    "request_id": request_id,
                    "domain": "query",
                },
            )
        assert response.status_code == 503, response.text
        assert "助手服务暂不可用，请稍后再试" in response.text, response.text
        after = run_site_json(SITE, "print(json.dumps(frappe.get_all('DS Model Run',pluck='name')))")
        assert set(after) == set(state['runs'])
    finally:
        run_site_script(SITE, HTTP_TEARDOWN % (state['previous'],))
