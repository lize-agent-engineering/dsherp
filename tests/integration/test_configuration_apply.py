import subprocess


def test_confirmed_preview_uses_native_ddl_and_record_save_without_replay():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_execution import prepare_preview,confirm_preview
name='DS Preview Native Apply Test';conversation=None;bundle=None;proposal=None
assert not frappe.db.exists('DocType',name)
assert not frappe.db.table_exists(name)
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Native preview apply'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':name,'module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package)
    proposal=prepare_preview(bundle['id'],bundle['digest']);frappe.db.commit()
    assert not frappe.db.exists('DocType',name)
    result=confirm_preview(proposal['id'],proposal['digest'],'preview-native-1')
    assert result['status']=='Succeeded',result
    assert result['steps'][0]['status']=='Succeeded'
    assert frappe.get_doc('DocType',name).custom==1
    native=frappe.get_doc({'doctype':name,'result':'Synthetic initial'}).insert()
    native.result='Synthetic changed';native.save();frappe.db.commit()
    assert frappe.get_doc(name,native.name).result=='Synthetic changed'
    repeated=confirm_preview(proposal['id'],proposal['digest'],'different-id')
    assert repeated['execution_id']==result['execution_id']
    assert frappe.db.count('DS Configuration Execution',{'confirmation':proposal['id']})==1
    from dsherp_bridge.configuration_execution import get_confirmation
    assert get_confirmation(proposal['id'])['execution']['status']=='Succeeded'
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if proposal:
        for row in frappe.get_all('DS Configuration Execution',filters={'confirmation':proposal['id']},pluck='name'):frappe.delete_doc('DS Configuration Execution',row,force=True)
        frappe.delete_doc('DS Configuration Confirmation',proposal['id'],force=True)
    if bundle:frappe.delete_doc('DS Configuration Bundle',bundle['id'],force=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,force=True)
    if frappe.db.exists('DocType',name):
        for row in frappe.get_all(name,pluck='name'):frappe.delete_doc(name,row,force=True)
        frappe.delete_doc('DocType',name,force=True)
    frappe.db.commit()
    # Frappe deliberately retains a removed DocType's empty table. This exact
    # synthetic test table is ours; cleanup is not an Agent publishing operation.
    if frappe.db.table_exists(name):frappe.db.sql_ddl('DROP TABLE `tabDS Preview Native Apply Test`')
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stderr
