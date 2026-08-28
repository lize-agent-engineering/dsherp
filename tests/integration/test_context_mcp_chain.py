"""Real DSH stdio/MCP/HTTP/Frappe chain with only the model replaced."""
import json
import subprocess

from dsherp.session_runtime import open_runtime
from test_context_sessions import created


def test_native_context_runtime_reads_actual_erp_through_run_capability(model_server,tmp_path,created):
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api,context_execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=context_api.send_message('Read test item',{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},uuid.uuid4().hex)
frappe.db.commit();frappe.conf.dsherp_runtime_user=actor
claim=context_execution.claim_run();frappe.db.commit()
print(json.dumps(claim));frappe.destroy()
'''
    provision=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert provision.returncode==0,provision.stderr
    claim=json.loads(provision.stdout);created.append(claim['session_id'])
    secret=tmp_path/'run.json'
    secret.write_text(json.dumps({'run_id':claim['run_id'],'capability':claim['capability'],
        'business_url':'http://127.0.0.1:18081','site':'dsherp-validation.localhost'}))
    secret.chmod(0o600)
    settings,requests,state=model_server
    state['tool_call']={'name':'mcp__erp__erp_read_record','arguments':json.dumps({'doctype':'Item','name':'DSHERP-TEST-ITEM'})}
    try:
        with open_runtime(settings,tmp_path/'native',claim['native_session_id'],resume=False,run_config=secret) as runtime:
            result=runtime.run('Read the item using the ERP tool',session_id=claim['native_session_id'])
    finally:
        secret.unlink()
    assert result.finish_reason=='completed'
    assert {t['function']['name'] for t in requests[0]['tools']}=={'mcp__erp__erp_read_record','mcp__erp__erp_read_schema','mcp__erp__erp_search_records'}
    results=[m for m in requests[1]['messages'] if m['role']=='tool']
    assert results and 'DSHERP-TEST-ITEM' in str(results)
    assert any(e['type']=='tool/result' and not e['data'].get('isError') for e in result.events)
