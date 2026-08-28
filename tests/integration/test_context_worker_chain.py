"""Real service identity, business queue, containers and two separate runs."""
import json
from pathlib import Path
import uuid
import httpx
import dsherp.context_worker as worker
from dsherp.agent_worker import ROOT
from test_context_sessions import clients,created,API
from test_context_mcp_chain import CONTAINER_TEST


def test_service_worker_runs_two_messages_in_same_native_session(clients,created,tmp_path,monkeypatch):
    original=worker.docker_command
    def command(root,secret,directory,name):
        args=original(root,secret,directory,name)
        args[2:2]=['-v',f'{ROOT}/tests/conftest.py:/run/model_fixture.py:ro']
        args[-2:]=['-c',CONTAINER_TEST]
        return args
    monkeypatch.setattr(worker,'docker_command',command)
    p=json.loads(Path('.runtime/context-worker.json').read_text())
    settings={'DEEPSEEK_API_KEY':'synthetic-not-a-credential','DSH_MODEL':'deepseek-v4-flash','DEEPSEEK_BASE_URL':'http://127.0.0.1:38127/v1'}
    reader,_=clients
    with httpx.Client(base_url=p['base_url'],headers={'X-Frappe-Site-Name':p['site'],'Authorization':'token '+p['api_key']+':'+p['api_secret']},trust_env=False,timeout=25) as service:
        assert service.get('/api/method/dsherp_bridge.api.read_schema',params={'doctype':'Item'}).status_code==403
        session_id=None
        for index in range(2):
            response=reader.post(API+'send_message',json={'question':f'读取测试物料，第 {index+1} 轮','context':{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},'request_id':uuid.uuid4().hex,'session_id':session_id})
            assert response.status_code==200
            doc=response.json()['message']
            if session_id is None:
                session_id=doc['id'];created.append(session_id)
            assert worker.run_once(service,settings,tmp_path)
            saved=reader.get(API+'get_session',params={'session_id':session_id}).json()['message']
            assert len(saved['messages'])==index+1
            assert saved['messages'][-1]['status']=='Succeeded'
            assert saved['messages'][-1]['answer']=='DSHERP_OK'
            assert saved['active_run'] is None
        assert worker.run_once(service,settings,tmp_path) is False
    assert len(list(tmp_path.iterdir()))==1
    assert len(list(tmp_path.rglob('session.jsonl.zstd')))==1
