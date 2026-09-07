"""Ruling #3 against a real Site: an audit record cannot be deleted, not even by Administrator
with permissions ignored, and a policy change without a reason is refused."""
import json
import subprocess



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


def test_a_finished_run_refuses_rewriting_on_the_document_path_while_the_server_and_cancellation_still_work():
    """G7 on a real Site, three ways: Administrator rewriting a finished answer through the
    Document API is refused; the execution module's own database transition still lands;
    the one document-path transition a person may make (cancel) still works."""
    script = r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=api.send_message('Synthetic rewrite probe',context,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={}
# the trusted path: the execution module moves the run to its terminal state at the database level
frappe.set_user('Administrator')
frappe.db.set_value('DS Model Run',run,{'status':'Succeeded','answer':'原始回答','capability_hash':''});frappe.db.commit()
# an ordinary System User with the record's roles, then Administrator ignoring permissions
for who,kwargs in ((actor,{}),('Administrator',{'ignore_permissions':True})):
    frappe.set_user(who)
    document=frappe.get_doc('DS Model Run',run)
    document.answer='被改写的回答'
    try:
        document.save(**kwargs);out[who]='saved'
    except Exception as error:
        out[who]=type(error).__name__+': '+str(error)[:40]
    frappe.db.rollback()
frappe.set_user('Administrator')
out['answer_after']=frappe.db.get_value('DS Model Run',run,'answer')
# the person's own transition on a live run still works
frappe.set_user(actor)
live=api.send_message('Synthetic cancel probe',context,uuid.uuid4().hex)
frappe.db.commit()
cancelled=api.cancel_run(live['id'],live['active_run'],uuid.uuid4().hex)
frappe.db.commit()
out['cancel']=frappe.db.get_value('DS Model Run',live['active_run'],'status')
# but nobody may use that path to change anything else on a live run - Administrator with
# permissions ignored gets past the permission check and meets the state machine
frappe.set_user('Administrator')
document=frappe.get_doc('DS Model Run',live['active_run'])
document.question='改过的问题'
try:
    document.save(ignore_permissions=True);out['question_rewrite']='saved'
except Exception as error:
    out['question_rewrite']=type(error).__name__
frappe.db.rollback()
print(json.dumps(out,ensure_ascii=False))
frappe.set_user('Administrator')
for name in (run,live['active_run']):
    frappe.db.delete('DS Run Event',{'run':name})
    frappe.db.sql('delete from `tabDS Model Run` where name=%s',(name,))
for conversation in (doc['id'],live['id']):
    frappe.db.sql('delete from `tabDS Conversation` where name=%s',(conversation,))
frappe.db.commit();frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1200:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    # the ordinary user is stopped by permissions before the state machine is reached;
    # Administrator ignoring permissions is stopped by the state machine itself
    assert out['dsherp-reader@example.invalid'] != 'saved', out
    assert out['Administrator'].startswith('ValidationError'), out
    assert out['answer_after'] == '原始回答', out
    assert out['cancel'] == 'Cancelled', out
    assert out['question_rewrite'] == 'ValidationError', out
