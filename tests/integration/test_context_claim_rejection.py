import subprocess


def test_unreadable_queued_context_is_failed_instead_of_blocking_queue():
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as events,context_execution as execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
doc=api.send_message('Synthetic unreadable source',context,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
try:
    # Only this test run's context is changed; native permissions remain untouched.
    restricted={'schema_version':1,'page_type':'form','doctype':'Customer','name':'DSHERP-TEST-OTHER-CUSTOMER','route':['Form','Customer','DSHERP-TEST-OTHER-CUSTOMER']}
    frappe.db.set_value('DS Model Run',run,'page_context',json.dumps(restricted));frappe.db.commit()
    frappe.conf.dsherp_runtime_user=actor
    assert execution.claim_run('a'*64) is None
    assert frappe.db.get_value('DS Model Run',run,'status')=='Failed'
    last=events.list_events(run)[-1]
    assert last['kind']=='finished' and last['payload']['status']=='Failed',last
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    frappe.db.delete('DS Run Event',{'run':run})
    frappe.db.delete('DS Model Run',{'name':run})
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    r=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert r.returncode==0,r.stderr
