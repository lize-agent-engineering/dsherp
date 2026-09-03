"""Run events are append-only, sequential, secret-free and owner-readable."""

import subprocess


def test_run_events_are_sequential_immutable_and_secret_free():
    script = r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='events-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic events','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Events'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'events','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(b'cap').hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    first=ev.record(run.name,'claimed',{'domain':'query','capability':'SECRET-CAP','nested':{'api_secret':'SECRET-2','ok':'x'*3000}})
    second=ev.record(run.name,'tool_call',{'tool':'erp_read_record'},error_class=None)
    assert first==run.name+'-000001' and second==run.name+'-000002',(first,second)
    stored=frappe.get_doc('DS Run Event',first)
    assert 'SECRET' not in stored.payload, stored.payload
    assert 'capability' not in json.loads(stored.payload) and 'api_secret' not in json.loads(stored.payload)['nested']
    assert json.loads(stored.payload)['nested']['ok'].endswith('…[truncated]')
    try:
        stored.kind='changed';stored.save(ignore_permissions=True);raise AssertionError('event was rewritten')
    except frappe.ValidationError:pass
    try:
        frappe.delete_doc('DS Run Event',first,ignore_permissions=True);raise AssertionError('event was deleted')
    except frappe.ValidationError:pass
    try:
        ev.record(run.name,'not-a-kind',{});raise AssertionError('unknown kind accepted')
    except frappe.ValidationError:pass
    batch=ev.record_many(run.name,[{'kind':'runtime_started','payload':{},'source':'runner'},
                                   {'kind':'tool_error','payload':{'text':'x'},'source':'runner','error_class':'PermissionError'}])
    assert batch=={'recorded':2,'last_seq':4},batch
    listed=ev.list_events(run.name)
    assert [e['seq'] for e in listed]==[1,2,3,4] and listed[3]['error_class']=='PermissionError'
    assert set(listed[0])=={'name','seq','kind','source','error_class','payload','recorded_at'}
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
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
