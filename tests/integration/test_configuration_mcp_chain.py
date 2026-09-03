"""Fixed native Runtime, real MCP/HTTP/Frappe, synthetic model only."""
import json
import subprocess
import httpx
from dsherp.context_runner import run_business
from dsherp.context_mcp import post
from dsherp.runtime_revision import configuration_revision


def test_configuration_native_runtime_reads_then_proposes_without_ddl(model_server,tmp_path):
    settings,requests,state=model_server
    root="import os,frappe,json\nos.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()\n"
    def execute(script):
        result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=root+script,text=True,capture_output=True,timeout=40)
        assert result.returncode==0,result.stderr
        return result.stdout
    claim=json.loads(execute("""
import uuid
from dsherp_bridge import context_api,context_execution
actor='dsherp-preview@example.invalid';frappe.set_user(actor)
assert not frappe.db.exists('DS Model Run',{'status':['in',['Queued','Running','Cancelling']]})
doc=context_api.send_message('Propose a synthetic inspection app',{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},uuid.uuid4().hex,domain='configuration')
frappe.db.commit();frappe.conf.dsherp_runtime_user=actor
claim=context_execution.claim_run(REVISION);frappe.db.commit();print(json.dumps(claim));frappe.destroy()
""".replace('REVISION',repr(configuration_revision(settings)))))
    package={'version':1,'doctypes':[{'name':'DS Runtime Configuration Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
    state['tool_calls']=[{'name':'mcp__erp__erp_read_configuration','arguments':json.dumps({'doctype':'DS Runtime Configuration Test'})},
                         {'name':'mcp__erp__erp_propose_configuration','arguments':json.dumps({'package':package})}]
    secret=tmp_path/'run.json';secret.write_text(json.dumps({**claim,**settings,'resume':False,'business_url':'http://preview.localhost:18085','site':'dsherp-beta.localhost'}));secret.chmod(0o600)
    try:
        result=run_business(secret,tmp_path/'native')
        assert result['status']=='Succeeded'
        assert len(requests)==3
        assert {tool['function']['name'] for tool in requests[0]['tools']}=={'skill','mcp__erp__erp_read_configuration','mcp__erp__erp_propose_configuration'}
        assert 'erp-configuration' in str(requests[0])
        assert 'execution_ready' in str(requests[2]['messages'])
        with httpx.Client(base_url='http://preview.localhost:18085',headers={'X-Frappe-Site-Name':'dsherp-beta.localhost'},trust_env=False,timeout=20) as client:
            assert post(client,'finish_run',run_id=claim['run_id'],capability=claim['capability'],**result)['status']=='Succeeded'
        execute("""
from dsherp_bridge.configuration import get_bundle
frappe.set_user('dsherp-preview@example.invalid')
bundles=frappe.get_all('DS Configuration Bundle',filters={'conversation':SESSION},pluck='name')
assert len(bundles)==1 and get_bundle(bundles[0])['execution_ready']
assert not frappe.db.exists('DocType','DS Runtime Configuration Test')
assert frappe.db.get_value('DS Model Run',RUN,'model_calls')==3
frappe.destroy()
""".replace('SESSION',repr(claim['session_id'])).replace('RUN',repr(claim['run_id'])))
    finally:
        secret.unlink()
        execute("""
frappe.set_user('Administrator')
for name in frappe.get_all('DS Configuration Bundle',filters={'conversation':SESSION},pluck='name'):frappe.delete_doc('DS Configuration Bundle',name,force=True)
frappe.db.delete('DS Run Event',{'run':RUN});frappe.delete_doc('DS Model Run',RUN,force=True);frappe.delete_doc('DS Conversation',SESSION,force=True)
frappe.db.commit();frappe.destroy()
""".replace('SESSION',repr(claim['session_id'])).replace('RUN',repr(claim['run_id'])))
