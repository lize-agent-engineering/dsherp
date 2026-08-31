import subprocess


def test_child_fill_freezes_existing_rows_without_saving_order():
    script=r'''
import os,uuid,frappe,json
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_fill,confirm
conversation=None
try:
    frappe.set_user('dsherp-writer@example.invalid');doc=frappe.get_doc('Sales Order','SAL-ORD-2026-00001');version=str(doc.modified)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic child fill'}).insert(ignore_permissions=True).name
    values={'items':[{'name':row.name,'qty':5} for row in doc.items]}
    proposal=propose_fill(conversation,doc.doctype,doc.name,values,version);frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Authorized',result
    saved=frappe.get_doc(doc.doctype,doc.name);assert str(saved.modified)==version and saved.items[0].qty==2
    try:propose_fill(conversation,doc.doctype,doc.name,{'items':[{'item_code':doc.items[0].item_code,'qty':1}]},version);raise AssertionError('fill replaced row set')
    except frappe.ValidationError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal},pluck='name'):frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
            frappe.delete_doc('DS Operation Proposal',proposal,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_confirm_fill_authorizes_draft_only_and_never_saves_business_record():
    script=r'''
import os,json,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_fill,confirm
conversation=None
try:
    frappe.set_user('dsherp-writer@example.invalid')
    doc=frappe.get_doc('Item','DSHERP-HITL-ITEM');before=doc.item_name;version=str(doc.modified)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic draft fill'}).insert(ignore_permissions=True).name
    origin=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation,'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'form','route':['Form','Item',doc.name],'doctype':'Item','name':doc.name,'version':version,'unsaved':{'item_name':'Provided draft'}})}).insert(ignore_permissions=True)
    proposal=propose_fill(conversation,'Item',doc.name,{'item_name':'Suggested draft only'},version,model_run=origin.name)
    assert proposal['changes'][0]['before']==before
    assert proposal['changes'][0]['form_before']=='Provided draft'
    frappe.db.set_value('DS Model Run',origin.name,'status','Succeeded');frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Authorized',result
    assert result['target']=='browser-draft'
    assert frappe.get_doc('Item',doc.name).item_name==before
    assert str(frappe.get_doc('Item',doc.name).modified)==version
    assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal},pluck='name'):frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
            frappe.delete_doc('DS Operation Proposal',proposal,ignore_permissions=True)
        for run in frappe.get_all('DS Model Run',filters={'conversation':conversation},pluck='name'):frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_confirm_fill_records_failed_when_policy_schema_is_injected_missing():
    script=r'''
import os,uuid,frappe
from unittest.mock import patch
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_fill,confirm

conversation=None
try:
    frappe.set_user('dsherp-writer@example.invalid')
    doc=frappe.get_doc('Item','DSHERP-HITL-ITEM')
    before=doc.item_name;version=str(doc.modified)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic missing policy schema'}).insert(ignore_permissions=True).name
    proposal=propose_fill(conversation,'Item',doc.name,{'item_name':'Must remain browser draft'},version)
    frozen=frappe.db.get_value('DS Operation Proposal',proposal['id'],['payload','digest'],as_dict=True)
    frappe.db.commit()

    actual_table_exists=frappe.db.table_exists
    def injected_table_exists(table):
        return False if table=='DS Doctype Policy' else actual_table_exists(table)

    with patch.object(frappe.db,'table_exists',side_effect=injected_table_exists):
        result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)

    assert result['status']=='Failed' and result['status']!='Unknown',result
    execution=frappe.get_doc('DS Execution Record',result['execution_id'])
    assert execution.status=='Failed'
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id'],'status':'Unknown'})==0
    stored=frappe.db.get_value('DS Operation Proposal',proposal['id'],['payload','digest','status'],as_dict=True)
    assert stored.payload==frozen.payload and stored.digest==frozen.digest and stored.status=='Failed'
    saved=frappe.get_doc(doc.doctype,doc.name)
    assert saved.item_name==before and str(saved.modified)==version
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal},pluck='name'):frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
            frappe.delete_doc('DS Operation Proposal',proposal,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
