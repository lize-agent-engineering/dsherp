"""Real native creation and subsequent update under an ordinary synthetic user."""
import subprocess
import pytest


@pytest.mark.parametrize('doctype',['Item','Customer'])
def test_create_proposal_has_no_business_write_and_confirmation_is_idempotent(doctype):
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_create,propose_update,confirm,get_proposal
doctype=DOCTYPE
tag=uuid.uuid4().hex;actor='create-'+tag+'@example.invalid';label='DS-CREATE-'+tag
conversation=None;created_name=None
try:
    frappe.set_user('Administrator')
    source=frappe.get_doc(doctype,'DSHERP-TEST-ITEM' if doctype=='Item' else 'DSHERP-TEST-CUSTOMER')
    values=({'item_code':label,'item_name':label,'item_group':source.item_group,'stock_uom':source.stock_uom,'is_stock_item':0,'end_of_life':'2099-12-31'}
        if doctype=='Item' else {'customer_name':label,'customer_type':source.customer_type,'customer_group':source.customer_group,'territory':source.territory})
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic creation writer','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'},{'role':'Sales User'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Native creation'}).insert(ignore_permissions=True).name
    count=frappe.db.count(doctype)
    version=str(frappe.get_meta(doctype).modified)
    proposal=propose_create(conversation,doctype,values,version)
    assert proposal['action']=='create' and proposal['name'] is None
    assert frappe.db.count(doctype)==count
    assert all(change['before'] is None for change in proposal['changes'])
    frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Succeeded',result
    created_name=result['name']
    assert result['values']==values
    assert frappe.db.count(doctype)==count+1
    assert get_proposal(proposal['id'])['execution']['name']==created_name
    assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
    assert frappe.db.count(doctype)==count+1
    field='item_name' if doctype=='Item' else 'customer_name'
    saved=frappe.get_doc(doctype,created_name)
    change=propose_update(conversation,doctype,created_name,{field:label+' UPDATED'},str(saved.modified));frappe.db.commit()
    updated=confirm(change['id'],change['digest'],uuid.uuid4().hex)
    assert updated['status']=='Succeeded',updated
    assert frappe.db.get_value(doctype,created_name,field)==label+' UPDATED'
    assert frappe.db.get_value(doctype,created_name,'modified_by')==actor
    frappe.set_user('dsherp-reader@example.invalid')
    try:propose_create(conversation,doctype,values,version);raise AssertionError('read-only creation accepted')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal_id in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution_id in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
                # Cleanup must include any successful creation even if assertions failed.
                execution=frappe.get_doc('DS Execution Record',execution_id)
                result=json.loads(execution.result or '{}')
                if result.get('status')=='Succeeded':created_name=result.get('name',created_name)
                frappe.db.delete('DS Execution Record',{'name':execution_id})
            frappe.db.delete('DS Operation Proposal',{'name':proposal_id})
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if created_name and frappe.db.exists(doctype,created_name):frappe.delete_doc(doctype,created_name,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''.replace('DOCTYPE',repr(doctype))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
