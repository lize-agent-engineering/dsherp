import subprocess
import pytest


@pytest.mark.parametrize('introduce_record',[False,True])
def test_preview_installs_native_workflow_and_blocks_concurrent_new_records(introduce_record):
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview,confirm_preview
from dsherp_bridge.configuration_locks import lock_key
from frappe.database import get_db
from frappe.model.workflow import apply_workflow
name='DS Preview Workflow Test';workflow='DS Preview Native Workflow'
states=['DS Preview Draft','DS Preview Approved','DS Preview Cancelled'];actions=['DS Preview Approve','DS Preview Cancel']
assert not frappe.db.exists('DocType',name) and not frappe.db.table_exists(name)
assert not frappe.db.exists('Workflow',workflow)
assert not any(frappe.db.exists('Workflow State',value) for value in states)
assert not any(frappe.db.exists('Workflow Action Master',value) for value in actions)
conversation=bundle=proposal=None;other=None
introduce_record=INTRODUCE_RECORD
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Native workflow preview'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':name,'module':'DSHERP Bridge','is_submittable':1,'fields':[
        {'fieldname':'result','label':'Result','fieldtype':'Data'},
        {'fieldname':'workflow_state','label':'Workflow State','fieldtype':'Link','options':'Workflow State'}],
        'permissions':[{'role':'System Manager','read':1,'write':1,'create':1,'submit':1,'cancel':1}]}],
        'extensions':[],'workflows':[{'workflow_name':workflow,'document_type':name,
            'states':[{'state':state,'doc_status':str(index),'allow_edit':'System Manager'} for index,state in enumerate(states)],
            'transitions':[{'state':states[index],'action':action,'next_state':states[index+1],'allowed':'System Manager','allow_self_approval':1} for index,action in enumerate(actions)]}]}
    bundle=propose_bundle(conversation.name,package);proposal=prepare_preview(bundle['id'],bundle['digest']);frappe.db.commit()
    assert '已提交' in proposal['changes'][-1]['detail'] and '邮件提醒关闭' in proposal['changes'][-1]['detail']
    assert '申请人自行审批：允许' in proposal['changes'][-1]['detail']
    if introduce_record:
        import dsherp_bridge.configuration_execution as execution_module
        original_matches=execution_module._matches
        def insert_after_schema(actual,expected):
            matched=original_matches(actual,expected)
            if isinstance(expected,dict) and expected.get('doctype')=='DocType' and expected.get('name')==name:
                frappe.get_doc({'doctype':name,'result':'Unexpected pre-workflow record'}).insert();frappe.db.commit()
            return matched
        execution_module._matches=insert_after_schema
    result=confirm_preview(proposal['id'],proposal['digest'],'native-workflow-1')
    if introduce_record:
        assert result['status']=='Partial',result
        assert result['steps'][-1]['status']=='Failed'
        assert not frappe.db.exists('Workflow',workflow)
        inserted=frappe.get_all(name,fields=['workflow_state'])
        assert len(inserted)==1 and not inserted[0].workflow_state
        assert confirm_preview(proposal['id'],proposal['digest'],'do-not-replay')['execution_id']==result['execution_id']
        raise SystemExit(0)
    assert result['status']=='Succeeded',result
    assert frappe.db.count(name)==0
    assert not frappe.db.exists('Custom Field',{'dt':name,'fieldname':'workflow_state'})
    other=get_db(host=frappe.conf.db_host,user=frappe.conf.db_name,password=frappe.conf.db_password,port=frappe.conf.db_port,cur_db_name=frappe.conf.db_name)
    assert other.sql('SELECT GET_LOCK(%s,0)',lock_key(name))[0][0]==1
    try:
        frappe.get_doc({'doctype':name,'result':'Must not be inserted'}).insert()
        raise AssertionError('record inserted while configuration lock held')
    except frappe.ValidationError as error:assert '配置' in str(error)
    other.close();other=None;frappe.db.rollback()
    record=frappe.get_doc({'doctype':name,'result':'Synthetic inspection'}).insert()
    assert record.workflow_state==states[0] and record.docstatus==0
    record=apply_workflow(record,actions[0]);assert record.docstatus==1 and record.workflow_state==states[1]
    record=apply_workflow(record,actions[1]);assert record.docstatus==2 and record.workflow_state==states[2]
    frappe.db.commit()
    assert frappe.get_doc(name,record.name).docstatus==2
    assert confirm_preview(proposal['id'],proposal['digest'],'repeat-workflow')['execution_id']==result['execution_id']
finally:
    if other:other.close()
    frappe.db.rollback();frappe.set_user('Administrator')
    if frappe.db.exists('Workflow',workflow):frappe.delete_doc('Workflow',workflow,force=True)
    if frappe.db.exists('DocType',name):
        for row in frappe.get_all('Workflow Action',filters={'reference_doctype':name},pluck='name'):frappe.delete_doc('Workflow Action',row,force=True)
        for row in frappe.get_all(name,pluck='name'):frappe.delete_doc(name,row,force=True)
        frappe.delete_doc('DocType',name,force=True)
    for value in actions:
        if frappe.db.exists('Workflow Action Master',value):frappe.delete_doc('Workflow Action Master',value,force=True)
    for value in states:
        if frappe.db.exists('Workflow State',value):frappe.delete_doc('Workflow State',value,force=True)
    if proposal:
        for row in frappe.get_all('DS Configuration Execution',filters={'confirmation':proposal['id']},pluck='name'):frappe.db.delete('DS Configuration Execution',{'name':row})
        frappe.db.delete('DS Configuration Confirmation',{'name':proposal['id']})
    if bundle:frappe.db.delete('DS Configuration Bundle',{'name':bundle['id']})
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,force=True)
    frappe.db.commit()
    if frappe.db.table_exists(name):frappe.db.sql_ddl('DROP TABLE `tabDS Preview Workflow Test`')
    frappe.destroy()
'''
    script=script.replace('INTRODUCE_RECORD',str(introduce_record))
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=100)
    assert result.returncode==0,result.stderr
