import subprocess


def test_a_held_site_refuses_to_hand_out_a_claim_even_to_the_runtime_identity():
    """A stable backup window must close the gap between the worker's hold check and its
    claim landing: the server itself refuses while dsherp_hold is set."""
    script = r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_execution as execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
doc=api.send_message('Synthetic hold gate probe',context,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
held=None
try:
    frappe.conf.dsherp_runtime_user=actor
    frappe.conf.dsherp_hold=1
    held=execution.claim_run('a'*64)
    assert held is None,'a held Site handed out a claim: '+json.dumps(held)
    assert frappe.db.get_value('DS Model Run',run,'status')=='Queued','the queued run must survive the window'
    frappe.conf.dsherp_hold=0
    task=execution.claim_run('a'*64)
    assert task and task['run_id']==run,'claiming must resume once the hold is lifted'
finally:
    frappe.db.rollback();frappe.conf.dsherp_hold=0;frappe.set_user('Administrator')
    frappe.db.delete('DS Run Event',{'run':run})
    frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    r = subprocess.run(['docker', 'exec', '-i', 'dsherp-validation-backend-1',
                        '/home/frappe/frappe-bench/env/bin/python', '-'],
                       input=script, text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
