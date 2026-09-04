import subprocess


def test_configuration_tools_use_native_permissions_sources_and_no_ddl():
    script=r'''
import os,json,hashlib,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
from dsherp_bridge import context_execution as execution,context_permissions
from dsherp_bridge.configuration import get_bundle
try:
    actor='dsherp-preview@example.invalid';frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Configuration tool test'}).insert(ignore_permissions=True)
    context={'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']}
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'configuration','status':'Running',
        'page_context':json.dumps(context),'sources':'[]','capability_hash':hashlib.sha256(b'test-cap').hexdigest(),
        'expires_at':frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=180),
        'runtime_revision':'a'*64,'permission_revision':context_permissions.run_revision(actor,'configuration')}).insert(ignore_permissions=True)
    cap={'run_id':run.name,'capability':'test-cap'}
    package={'version':1,'doctypes':[{'name':'DS Configuration Domain Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    frappe.set_user('Guest')
    try:execution.run_tool(**cap,tool='erp_propose_configuration',arguments={'package':package});raise AssertionError('no configuration source')
    except frappe.ValidationError:pass
    result=execution.run_tool(**cap,tool='erp_read_configuration',arguments={'doctype':'DS Configuration Domain Test'})
    assert not result['exists'] and 'DSHERP Bridge' in result['modules'] and 'System Manager' in result['roles']
    existing=execution.run_tool(**cap,tool='erp_read_configuration',arguments={'doctype':'Item'})
    assert existing['exists'] and any(row['fieldname']=='item_name' for row in existing['fields'])
    # Controlled metadata fault: the native field-level reader must not be
    # bypassed by subsequently reading the unfiltered effective Meta object.
    original_meta=frappe.get_meta
    def restricted_meta(doctype,*args,**kwargs):
        import copy
        meta=original_meta(doctype,*args,**kwargs)
        if doctype=='DocField':
            meta=copy.deepcopy(meta);meta.get_field('options').permlevel=99
        return meta
    frappe.get_meta=restricted_meta
    try:
        try:execution.run_tool(**cap,tool='erp_read_configuration',arguments={'doctype':'Item'});raise AssertionError('hidden configuration field exposed')
        except frappe.PermissionError:pass
    finally:frappe.get_meta=original_meta
    saved_sources=frappe.db.get_value('DS Model Run',run.name,'sources')
    stale=json.loads(saved_sources);stale[0]['configuration_revision']='outdated'
    frappe.db.set_value('DS Model Run',run.name,'sources',json.dumps(stale))
    try:execution.run_tool(**cap,tool='erp_propose_configuration',arguments={'package':package});raise AssertionError('stale configuration source accepted')
    except frappe.ValidationError:pass
    frappe.db.set_value('DS Model Run',run.name,'sources',saved_sources)
    for tool,args in [('erp_read_record',{'doctype':'Item','name':'DSHERP-TEST-ITEM'}),('erp_propose_configuration',{'package':package,'site':'other'})]:
        try:execution.run_tool(**cap,tool=tool,arguments=args);raise AssertionError('domain boundary bypassed')
        except (frappe.PermissionError,frappe.ValidationError):pass
    proposed=execution.run_tool(**cap,tool='erp_propose_configuration',arguments={'package':package})
    assert not proposed['execution_ready'] and not frappe.db.exists('DocType','DS Configuration Domain Test')
    assert frappe.session.user=='Guest'
    from dsherp_bridge.run_budget import budget
    assert execution.reserve_model_call(**cap,input_bytes=100,max_output_tokens=3072,provider='deepseek-official',model='deepseek-v4-flash',purpose='compaction',runtime_revision='a'*64,domain='configuration',claimed_budget=budget('configuration'))['allowed']
    execution.finish_run(**cap,status='Succeeded',answer='配置待确认')
    frappe.set_user(actor);assert get_bundle(proposed['id'])['execution_ready']
    sources=json.loads(frappe.get_doc('DS Model Run',run.name).sources)
    assert sources[0]['tool']=='erp_read_configuration'
    frappe.set_user('beta-reader@example.invalid')
    try:execution.authorize_sources(sources);raise AssertionError('configuration history exposed')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=50)
    assert result.returncode==0,result.stderr
