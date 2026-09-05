"""Real DSH stdio/MCP/HTTP/Frappe chain with only the model replaced."""
import json
import subprocess
import httpx
import pytest
from pathlib import Path
from dsherp.context_container import docker_command
from dsherp.runtime_revision import configuration_revision

from dsherp.context_runner import run_business
from dsherp.context_mcp import post
from test_context_sessions import created


CONTAINER_TEST=r'''
import json,runpy
from pathlib import Path
from dsherp.context_runner import run_business
fixture=runpy.run_path('/run/model_fixture.py')['model_server'].__wrapped__(38127)
settings,requests,state=next(fixture)
state['tool_call']={'name':'mcp__erp__erp_read_record','arguments':json.dumps({'doctype':'Item','name':'DSHERP-TEST-ITEM'})}
try:
    config=json.loads(Path('/run/business.json').read_text());config.update(settings)
    path=Path('/tmp/business.json');path.write_text(json.dumps(config));path.chmod(0o600)
    result=run_business(path,Path('/session'))
    assert len(requests)==2
    if config['resume'] is True:
        assert any(m['role']=='assistant' and m.get('content')=='DSHERP_OK' for m in requests[0]['messages'])
    assert {tool['function']['name'] for tool in requests[0]['tools']}=={'skill','mcp__erp__erp_read_schema','mcp__erp__erp_read_record','mcp__erp__erp_search_records','mcp__erp__erp_request_input'}
    assert 'DSHERP-TEST-ITEM' in str([m for m in requests[1]['messages'] if m['role']=='tool'])
    print(json.dumps(result))
finally:
    fixture.close()
'''


@pytest.mark.parametrize('isolated',[False,True])
def test_native_context_runtime_reads_actual_erp_through_run_capability(model_server,tmp_path,created,isolated):
    settings,requests,state=model_server
    # Host side only: the run container is handed this digest and must never compute it.
    from dsherp import deploy_env
    settings={**settings,'deployment_digest':deploy_env.deployment_digest(deploy_env.settings({'DSHERP_ENV':'dev'}))}
    if isolated:settings={**settings,'DEEPSEEK_BASE_URL':'http://127.0.0.1:38127/v1'}
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api,context_execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
doc=context_api.send_message('Read test item',{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},uuid.uuid4().hex)
frappe.db.commit();frappe.conf.dsherp_runtime_user=actor
claim=context_execution.claim_run(REVISION);frappe.db.commit()
print(json.dumps(claim));frappe.destroy()
'''.replace('REVISION',repr(configuration_revision(settings)))
    provision=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert provision.returncode==0,provision.stderr
    claim=json.loads(provision.stdout);created.append(claim['session_id'])
    secret=tmp_path/'run.json'
    secret.write_text(json.dumps({**claim,**settings,'resume':False,
        'business_url':'http://dsherp-validation-backend-1:8000' if isolated else 'http://127.0.0.1:18081','site':'dsherp-validation.localhost'}))
    secret.chmod(0o600)
    state['tool_call']={'name':'mcp__erp__erp_read_record','arguments':json.dumps({'doctype':'Item','name':'DSHERP-TEST-ITEM'})}
    try:
        with httpx.Client(base_url='http://127.0.0.1:18081',headers={'X-Frappe-Site-Name':'dsherp-validation.localhost'},trust_env=False) as client:
            cap={'run_id':claim['run_id'],'capability':claim['capability']}
            if isolated:
                import uuid
                root=Path(__file__).resolve().parents[2]
                directory=tmp_path/'native';directory.mkdir(mode=0o700)
                name='dsherp-context-test-'+uuid.uuid4().hex
                command=docker_command(root,secret,directory,name)
                command[2:2]=['-v',f'{root}/tests/conftest.py:/run/model_fixture.py:ro']
                command[-2:]=['-c',CONTAINER_TEST]
                try:
                    process=subprocess.run(command,capture_output=True,text=True,timeout=140)
                    assert process.returncode==0,process.stderr
                    result=json.loads(process.stdout)
                    restored=json.loads(secret.read_text());restored['resume']=True
                    secret.write_text(json.dumps(restored))
                    resumed=subprocess.run(command,capture_output=True,text=True,timeout=140)
                    assert resumed.returncode==0,resumed.stderr
                    assert json.loads(resumed.stdout)==result
                finally:
                    subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
                logs=list((directory/'sessions').rglob('session.jsonl.zstd'))
                assert len(logs)==1 and logs[0].stat().st_size>0
            else:
                result=run_business(secret,tmp_path/'native')
            saved=post(client,'finish_run',**cap,**result)
            assert saved['status']=='Succeeded'
            audit_script="""
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
print(frappe.db.get_value('DS Model Run',RUN,'model_calls'));frappe.destroy()
""".replace('RUN',repr(claim['run_id']))
            audit=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=audit_script,text=True,capture_output=True,timeout=20)
            assert audit.returncode==0,audit.stderr
            assert int(audit.stdout)==(4 if isolated else 2)
    finally:
        secret.unlink()
    assert result=={'status':'Succeeded','answer':'DSHERP_OK'}
    if isolated:return
    assert {t['function']['name'] for t in requests[0]['tools']}=={'skill','mcp__erp__erp_read_record','mcp__erp__erp_read_schema','mcp__erp__erp_search_records','mcp__erp__erp_request_input'}
    results=[m for m in requests[1]['messages'] if m['role']=='tool']
    assert results and 'DSHERP-TEST-ITEM' in str(results)
