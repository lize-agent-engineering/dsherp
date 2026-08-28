"""Native synthetic role change invalidates a run and rotates its successor."""
import subprocess

def test_context_permission_revision_tracks_native_child_schema():
    script="""
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_permissions
original=frappe.get_all
tracked=[]
def observe(doctype,*args,**kwargs):
    if doctype=='DocField':tracked.extend(kwargs['filters']['parent'][1])
    return original(doctype,*args,**kwargs)
frappe.get_all=observe
context_permissions.revision('dsherp-reader@example.invalid')
assert 'UOM Conversion Detail' in tracked,tracked
frappe.destroy()
"""
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_native_permission_change_invalidates_and_rotates_runtime():
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_execution as execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
doc=api.send_message('Permission version test',context,uuid.uuid4().hex);frappe.db.commit()
role='DSHERP-REV-'+uuid.uuid4().hex[:12]
try:
    frappe.conf.dsherp_runtime_user=actor
    before=execution.claim_run('a'*64);frappe.db.commit()
    cap={'run_id':before['run_id'],'capability':before['capability']}
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'Role','role_name':role}).insert()
    frappe.get_doc('User',actor).add_roles(role);frappe.db.commit()
    frappe.set_user('Guest')
    try:execution.run_status(**cap);raise AssertionError('old authorization remained usable')
    except frappe.PermissionError:pass
    try:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'});raise AssertionError('old tool context allowed')
    except frappe.PermissionError:pass
    try:execution.reserve_model_call(**cap,input_bytes=100,max_output_tokens=2048,provider='deepseek-official',model='deepseek-v4-flash',purpose='compaction',runtime_revision='a'*64);raise AssertionError('old summary context allowed')
    except frappe.PermissionError:pass
    assert frappe.db.get_value('DS Model Run',before['run_id'],'model_calls')==0
    execution.finish_run(**cap,status='Failed',error='Permission changed');frappe.db.commit()
    frappe.set_user(actor)
    api.send_message('New authorized context',context,uuid.uuid4().hex,session_id=doc['id']);frappe.db.commit()
    after=execution.claim_run('a'*64);frappe.db.commit()
    assert after['native_session_id']!=before['native_session_id']
    assert after['scope_id']!=before['scope_id']
    assert after['permission_revision']!=before['permission_revision']
    execution.finish_run(run_id=after['run_id'],capability=after['capability'],status='Failed',error='End synthetic run');frappe.db.commit()
    api.send_message('Changed runtime config',context,uuid.uuid4().hex,session_id=doc['id']);frappe.db.commit()
    changed=execution.claim_run('b'*64);frappe.db.commit()
    assert changed['native_session_id']!=after['native_session_id']
    assert changed['permission_revision']==after['permission_revision']
    assert changed['runtime_revision']=='b'*64
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    user=frappe.get_doc('User',actor)
    user.set('roles',[row for row in user.roles if row.role!=role]);user.save()
    if frappe.db.exists('Role',role):frappe.delete_doc('Role',role)
    for run in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):
        frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
