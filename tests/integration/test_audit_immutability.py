"""Ruling #3 against a real Site: an audit record cannot be deleted, not even by Administrator
with permissions ignored, and a policy change without a reason is refused."""
import subprocess

import pytest


def _run(script, timeout=120):
    return subprocess.run(["docker", "exec", "-i", "dsherp-validation-backend-1",
                           "/home/frappe/frappe-bench/env/bin/python", "-"],
                          input=script, text=True, capture_output=True, timeout=timeout)


def test_an_audit_record_refuses_deletion_even_for_administrator_ignoring_permissions():
    script = r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
frappe.set_user('Administrator')
from dsherp_bridge import context_api as api
context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=api.send_message('Synthetic immutability probe',context,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
frappe.set_user('Administrator')
refused=[]
try:
    frappe.delete_doc('DS Model Run',run,force=True,ignore_permissions=True,delete_permanently=True)
except Exception as error:
    refused.append(type(error).__name__)
frappe.db.rollback()
assert refused,'Administrator deleted an audit record with force and ignore_permissions'
assert frappe.db.exists('DS Model Run',run),'the run must still be there'
# the conversation cannot be dropped while its runs exist
conversation_refused=[]
try:
    frappe.delete_doc('DS Conversation',doc['id'],force=True,ignore_permissions=True)
except Exception as error:
    conversation_refused.append(str(error)[:60])
frappe.db.rollback()
assert conversation_refused,'a conversation with runs was deleted'
# every audit DocType keeps its own history
for name in ('DS Model Run','DS Run Event','DS Operation Proposal','DS Execution Record'):
    assert frappe.db.get_value('DocType',name,'track_changes')==1,name
print(json.dumps({'refused':refused,'conversation':conversation_refused[:1]}))
frappe.db.rollback();frappe.set_user('Administrator')
frappe.db.delete('DS Run Event',{'run':run})
frappe.db.sql('delete from `tabDS Model Run` where name=%s',(run,))
frappe.db.sql('delete from `tabDS Conversation` where name=%s',(doc['id'],))
frappe.db.commit();frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-800:]


def test_a_policy_change_without_a_new_reason_is_refused():
    script = r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
frappe.set_user('Administrator')
name=frappe.db.get_value('DS Doctype Policy',{},'name')
if not name:
    print(json.dumps({'skipped':'no policy on this Site'}))
else:
    policy=frappe.get_doc('DS Doctype Policy',name)
    original=policy.change_reason
    policy.change_reason=''
    refused=[]
    try:
        policy.save(ignore_permissions=True)
    except Exception as error:
        refused.append(str(error)[:60])
    frappe.db.rollback()
    assert refused,'a policy was saved without a reason'
    policy=frappe.get_doc('DS Doctype Policy',name)
    policy.change_reason=original or '迁移前既有策略，原因未记录'
    repeated=[]
    try:
        policy.save(ignore_permissions=True)
    except Exception as error:
        repeated.append(str(error)[:60])
    frappe.db.rollback()
    assert repeated,'a policy was saved reusing the previous reason'
    print(json.dumps({'refused':refused[:1],'repeated':repeated[:1]}))
frappe.db.rollback();frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-800:]
