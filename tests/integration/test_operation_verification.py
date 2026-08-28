import subprocess


def test_verification_reads_current_facts_without_replaying_unknown_execution():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_update,verify_execution
try:
    frappe.set_user('dsherp-writer@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Read-only verification'}).insert(ignore_permissions=True)
    item=frappe.get_doc('Item','DSHERP-HITL-ITEM');version=str(item.modified)
    proposal=propose_update(conversation.name,'Item',item.name,{'item_name':'Unexecuted proposal'},version)
    execution=frappe.get_doc({'doctype':'DS Execution Record','proposal':proposal['id'],'request_id':'synthetic','status':'Unknown','result':json.dumps({'status':'Unknown','error':'Synthetic lost response'})}).insert(ignore_permissions=True)
    result=verify_execution(proposal['id'])
    assert result['execution']['status']=='Unknown'
    assert result['observed']['values']=={'item_name':item.item_name}
    assert result['observed']['version']==version
    assert result['matches_proposal'] is False
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    assert frappe.get_doc('DS Execution Record',execution.name).status=='Unknown'
    assert str(frappe.get_doc('Item',item.name).modified)==version
    frappe.set_user('dsherp-reader@example.invalid')
    try:verify_execution(proposal['id']);raise AssertionError('foreign proposal read')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
