"""Real business Site DB/API execution under owner identity; no paid model."""
import subprocess


def test_capability_reads_as_owner_and_cannot_finish_without_actual_read():
    script=r'''
import os,uuid,frappe,hashlib,json
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api
from dsherp_bridge import context_execution as execution
actor='dsherp-reader@example.invalid'
frappe.set_user(actor)
payload={'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}
doc=api.send_message('Read synthetic item',payload,uuid.uuid4().hex)
frappe.db.commit()
try:
    try:execution.claim_run();raise AssertionError('ordinary user claimed')
    except frappe.PermissionError:pass
    # In-process synthetic control-plane binding, never a stored role or real key.
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run();frappe.db.commit()
    assert claim['run_id']==doc['active_run']
    assert claim['session_id']==doc['id']
    expected_scope=hashlib.sha256(json.dumps([frappe.local.site,actor,doc['id'],'query',claim['native_session_id']],separators=(',',':')).encode()).hexdigest()
    assert claim['scope_id']==expected_scope
    assert execution.claim_run() is None
    frappe.set_user('Guest')
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    assert execution.run_status(**cap)=={'run_id':claim['run_id'],'status':'Running'}
    try:execution.run_tool(**{**cap,'capability':'wrong'},tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'});raise AssertionError('bad cap allowed')
    except frappe.PermissionError:pass
    try:execution.finish_run(**cap,status='Succeeded',answer='fake');raise AssertionError('fake success allowed')
    except frappe.ValidationError:pass
    result=execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    assert result['name']=='DSHERP-TEST-ITEM'
    assert frappe.session.user=='Guest'
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Customer','name':'DSHERP-TEST-OTHER-CUSTOMER'});raise AssertionError('owner permission bypass')
    except frappe.PermissionError:pass
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM','user':'Administrator'});raise AssertionError('identity forged')
    except frappe.ValidationError:pass
    execution.finish_run(**cap,status='Succeeded',answer='read completed')
    frappe.db.commit()
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'});raise AssertionError('finished cap allowed')
    except frappe.PermissionError:pass
    frappe.set_user(actor)
    saved=api.get_session(doc['id'])
    assert saved['messages'][0]['answer']=='read completed'
    assert saved['active_run'] is None
    queued=api.send_message('Cancel synthetic run',payload,uuid.uuid4().hex,session_id=doc['id'])
    frappe.db.commit()
    claim2=execution.claim_run();frappe.db.commit()
    cap2={'run_id':claim2['run_id'],'capability':claim2['capability']}
    api.cancel_run(doc['id'],claim2['run_id'],uuid.uuid4().hex);frappe.db.commit()
    frappe.set_user('Guest')
    assert execution.run_status(**cap2)['status']=='Cancelling'
    try:execution.run_status(**{**cap2,'capability':'wrong'});raise AssertionError('bad status cap allowed')
    except frappe.PermissionError:pass
    execution.finish_run(**cap2,status='Cancelled');frappe.db.commit()
    try:execution.run_status(**cap2);raise AssertionError('terminal status cap allowed')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for run in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
        frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
