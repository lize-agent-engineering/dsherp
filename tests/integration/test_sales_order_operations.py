"""Sales Order submit/cancel use real native methods and ordinary permissions."""
import subprocess


def test_native_sales_order_submit_cancel_are_separate_confirmed_actions():
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_action,propose_create,propose_update,confirm
actor='so-'+uuid.uuid4().hex+'@example.invalid';order_name=None;conversation=None
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic Sales Order actor','enabled':1,'send_welcome_email':0,'roles':[{'role':'Sales User'}]}).insert()
    source=frappe.get_doc('Sales Order','SAL-ORD-2026-00001')
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Sales Order actions'}).insert(ignore_permissions=True).name
    values={f.fieldname:source.get(f.fieldname) for f in source.meta.fields if f.reqd and not f.read_only and f.fieldtype!='Table'}
    values['delivery_date']=str(source.delivery_date)
    values['items']=[{key:row.get(key) for key in ('item_code','qty','rate','uom','delivery_date')} for row in source.items]
    values=json.loads(json.dumps(values,default=str))
    count=frappe.db.count('Sales Order')
    proposal=propose_create(conversation,'Sales Order',values,str(source.meta.modified));frappe.db.commit()
    assert frappe.db.count('Sales Order')==count
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Succeeded',result
    order_name=result['name'];assert frappe.db.get_value('Sales Order',order_name,'docstatus')==0
    draft=frappe.get_doc('Sales Order',order_name)
    for invalid in ([{'name':source.items[0].name,'qty':3}], [{'name':draft.items[0].name,'amount':1}], [{'name':draft.items[0].name,'qty':3},{'name':draft.items[0].name,'qty':4}]):
        try:propose_update(conversation,'Sales Order',order_name,{'items':invalid},str(draft.modified));raise AssertionError('invalid child edit accepted')
        except (frappe.ValidationError,frappe.PermissionError):pass
    changes={'items':[{'name':draft.items[0].name,'qty':3}]}
    proposal=propose_update(conversation,'Sales Order',order_name,changes,str(draft.modified));frappe.db.commit()
    assert proposal['changes'][0]['before'][0]['item_code']==source.items[0].item_code
    assert proposal['changes'][0]['columns']['qty']==frappe.get_meta('Sales Order Item').get_field('qty').label
    assert frappe.get_doc('Sales Order',order_name).items[0].qty==2
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Succeeded',result
    assert frappe.get_doc('Sales Order',order_name).items[0].qty==3
    assert frappe.get_doc('Sales Order',order_name).items[0].item_code==source.items[0].item_code
    draft=frappe.get_doc('Sales Order',order_name)
    new_row={**values['items'][0],'qty':1}
    proposal=propose_update(conversation,'Sales Order',order_name,{'items':[{'name':draft.items[0].name,'qty':3},new_row]},str(draft.modified));frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex);assert result['status']=='Succeeded',result
    draft=frappe.get_doc('Sales Order',order_name);assert len(draft.items)==2
    remaining=draft.items[1].name
    proposal=propose_update(conversation,'Sales Order',order_name,{'items':[{'name':remaining,'qty':1}]},str(draft.modified));frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex);assert result['status']=='Succeeded',result
    draft=frappe.get_doc('Sales Order',order_name);assert len(draft.items)==1 and draft.items[0].name==remaining
    for action,before,after in [('submit',0,1),('cancel',1,2)]:
        doc=frappe.get_doc('Sales Order',order_name)
        proposal=propose_action(conversation,'Sales Order',order_name,action,str(doc.modified));frappe.db.commit()
        assert frappe.db.get_value('Sales Order',order_name,'docstatus')==before
        result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
        assert result['status']=='Succeeded',result
        assert result['values']['docstatus']==after
        assert frappe.db.get_value('Sales Order',order_name,'docstatus')==after
        assert frappe.db.get_value('Sales Order',order_name,'modified_by')==actor
        assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
    assert frappe.db.get_value('Sales Order',order_name,'status')=='Cancelled'
    try:propose_action(conversation,'Sales Order',order_name,'submit',str(frappe.get_doc('Sales Order',order_name).modified));raise AssertionError('cancelled order resubmitted')
    except frappe.ValidationError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal},pluck='name'):
                outcome=json.loads(frappe.get_doc('DS Execution Record',execution).result or '{}')
                if outcome.get('status')=='Succeeded':order_name=outcome.get('name',order_name)
                frappe.db.delete('DS Execution Record',{'name':execution})
            frappe.db.delete('DS Operation Proposal',{'name':proposal})
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if order_name and frappe.db.exists('Sales Order',order_name):
        doc=frappe.get_doc('Sales Order',order_name)
        if doc.docstatus==1:doc.cancel()
        frappe.delete_doc('Sales Order',order_name,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
