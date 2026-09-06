"""Deterministic DB interleaving: cancellation after candidate selection."""
import subprocess


def test_claim_does_not_resurrect_cancelled_candidate():
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_execution as execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=api.send_message('Cancel candidate',{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
original=frappe.get_all
def selected(*args,**kwargs):
    result=original(*args,**kwargs)
    if args==('DS Model Run',) and kwargs.get('filters')=={'status':'Queued'}:
        selected_names={row if isinstance(row,str) else row.name for row in result}
    else:
        selected_names=set()
    if run in selected_names:
        # Reproduce a cancellation that commits before the claimant gets its row lock.
        frappe.db.set_value('DS Model Run',run,'status','Cancelled');frappe.db.commit()
    return result
try:
    frappe.conf.dsherp_runtime_user=actor
    frappe.get_all=selected
    assert execution.claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',run,'status')=='Cancelled'
    assert not frappe.db.get_value('DS Model Run',run,'capability_hash')
finally:
    frappe.get_all=original
    frappe.db.rollback();frappe.set_user('Administrator')
    frappe.db.delete('DS Run Event',{'run':run})
    frappe.db.delete('DS Model Run',{'name':run})
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_queued_cancel_records_finished_terminal_event():
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as events
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=api.send_message('Cancel queued run',{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
try:
    saved=api.cancel_run(doc['id'],run,uuid.uuid4().hex)
    frappe.db.commit()
    assert saved['active_run'] is None
    assert frappe.db.get_value('DS Model Run',run,'status')=='Cancelled'
    last=events.list_events(run)[-1]
    assert last['kind']=='finished' and last['payload']['status']=='Cancelled',last
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    frappe.db.delete('DS Run Event',{'run':run})
    frappe.db.delete('DS Model Run',{'name':run})
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
