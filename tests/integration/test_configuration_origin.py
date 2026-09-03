import subprocess


def test_configuration_origin_must_succeed_before_any_execution():
    script=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge.configuration import propose_bundle,get_bundle
from dsherp_bridge.configuration_execution import prepare_preview,confirm_preview,get_confirmation
conversation=other=run=bundle=confirmation=None
assert not frappe.db.exists('DocType','DS Configuration Origin Test')
assert not frappe.db.table_exists('DS Configuration Origin Test')
try:
    frappe.set_user('dsherp-preview@example.invalid')
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Configuration origin test'}).insert(ignore_permissions=True)
    assert 'configuration' in frappe.get_meta('DS Model Run').get_field('domain').options.splitlines()
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'configuration','status':'Running','page_context':'{}'}).insert(ignore_permissions=True)
    package={'version':1,'doctypes':[{'name':'DS Configuration Origin Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    bundle=propose_bundle(conversation.name,package,model_run=run.name)
    assert not bundle['execution_ready']
    assert json.loads(frappe.get_doc('DS Configuration Bundle',bundle['id']).payload)['model_run']==run.name
    confirmation=prepare_preview(bundle['id'],bundle['digest'])
    assert not get_confirmation(confirmation['id'])['execution_ready']
    frappe.db.commit()
    for status in ['Running','Cancelling','Cancelled','Failed']:
        frappe.db.set_value('DS Model Run',run.name,'status',status)
        frappe.db.commit()
        try:confirm_preview(confirmation['id'],confirmation['digest'],'origin-'+status);raise AssertionError('unfinished origin executed')
        except frappe.ValidationError as error:assert '来源运行' in str(error)
        assert not frappe.db.exists('DocType','DS Configuration Origin Test')
        assert not frappe.db.exists('DS Configuration Execution',{'confirmation':confirmation['id']})
    frappe.db.set_value('DS Model Run',run.name,'status','Succeeded')
    assert get_confirmation(confirmation['id'])['execution_ready']
    other=frappe.get_doc({'doctype':'DS Conversation','title':'Other conversation'}).insert(ignore_permissions=True)
    frappe.db.set_value('DS Model Run',run.name,'status','Running')
    try:propose_bundle(other.name,package,model_run=run.name);raise AssertionError('foreign conversation origin')
    except frappe.PermissionError:pass
    frappe.db.set_value('DS Model Run',run.name,'domain','operation')
    try:propose_bundle(conversation.name,package,model_run=run.name);raise AssertionError('wrong domain origin')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if frappe.db.exists('DocType','DS Configuration Origin Test'):
        frappe.delete_doc('DocType','DS Configuration Origin Test',force=True)
    if confirmation:
        for name in frappe.get_all('DS Configuration Execution',filters={'confirmation':confirmation['id']},pluck='name'):
            frappe.delete_doc('DS Configuration Execution',name,force=True)
        frappe.delete_doc('DS Configuration Confirmation',confirmation['id'],force=True)
    if bundle:frappe.delete_doc('DS Configuration Bundle',bundle['id'],force=True)
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        frappe.delete_doc('DS Model Run',run.name,force=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,force=True)
    if other and frappe.db.exists('DS Conversation',other.name):frappe.delete_doc('DS Conversation',other.name,force=True)
    frappe.db.commit()
    if frappe.db.table_exists('DS Configuration Origin Test'):frappe.db.sql_ddl('DROP TABLE `tabDS Configuration Origin Test`')
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
