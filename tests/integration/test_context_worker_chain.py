"""Real service identity, business queue, containers and two separate runs."""
import json
import os
from pathlib import Path
import uuid
import httpx
import pytest
import dsherp.context_worker as worker
from dsherp.context_mcp import post
from dsherp.runtime_host import ROOT
from test_context_sessions import clients,created,API  # noqa: F401  # clients/created are pytest fixtures
from test_context_mcp_chain import CONTAINER_TEST


def _require_stopped_agent_worker():
    pid_file=Path('.runtime/agent-worker.pid')
    if not pid_file.exists():return
    try:
        pid=int(pid_file.read_text())
        os.kill(pid,0)
    except ProcessLookupError:
        return
    except (OSError,ValueError):
        pytest.fail('常驻 worker PID 状态无效；先停止它再跑')
    pytest.fail('常驻 worker 正在运行，会用真实 provider 抢先领取测试运行；先停止它再跑')


def test_service_worker_runs_two_messages_in_same_native_session(clients,created,tmp_path,monkeypatch):
    _require_stopped_agent_worker()
    original=worker.docker_command
    def command(root,secret,directory,name):
        args=original(root,secret,directory,name)
        args[2:2]=['-v',f'{ROOT}/tests/conftest.py:/run/model_fixture.py:ro']
        args[-2:]=['-c',CONTAINER_TEST]
        return args
    monkeypatch.setattr(worker,'docker_command',command)
    p=json.loads(Path('.runtime/context-worker.json').read_text())
    from dsherp import deploy_env
    settings={'DEEPSEEK_API_KEY':'synthetic-not-a-credential','DEEPSEEK_BASE_URL':'http://127.0.0.1:38127/v1',
              'deployment_digest':deploy_env.deployment_digest(deploy_env.settings({'DSHERP_ENV':'dev'}))}
    reader,_=clients
    event_sequences=[]
    with httpx.Client(base_url=p['base_url'],headers={'X-Frappe-Site-Name':p['site'],'Authorization':'token '+p['api_key']+':'+p['api_secret']},trust_env=False,timeout=25) as service:
        assert service.get('/api/method/dsherp_bridge.api.read_schema',params={'doctype':'Item'}).status_code==403
        session_id=None
        for index in range(2):
            post(service,'worker_heartbeat')
            response=reader.post(API+'send_message',json={'question':f'读取测试物料，第 {index+1} 轮','context':{'schema_version':1,'page_type':'unknown','route':['Workspaces','Home']},'request_id':uuid.uuid4().hex,'session_id':session_id})
            assert response.status_code==200
            doc=response.json()['message']
            run_id=doc['active_run']
            if session_id is None:
                session_id=doc['id'];created.append(session_id)
            assert worker.run_once(service,settings,tmp_path,business={'business_url':p['business_url'],'site':p['site']})
            saved=reader.get(API+'get_session',params={'session_id':session_id}).json()['message']
            assert len(saved['messages'])==index+1
            assert saved['messages'][-1]['status']=='Succeeded'
            assert saved['messages'][-1]['answer']=='DSHERP_OK'
            assert saved['active_run'] is None
            events_response=reader.get(API+'list_run_events',params={'run_id':run_id})
            assert events_response.status_code==200,events_response.text
            events=events_response.json()['message']['events']
            assert [event['seq'] for event in events]==sorted(event['seq'] for event in events)
            kinds=[event['kind'] for event in events]
            required={'queued','claimed','runtime_started','model_call_reserved','runtime_tool_call','tool_call',
                      'model_response','turn_end','container_finished','finished'}
            assert required<=set(kinds),kinds
            responses=[event for event in events if event['kind']=='model_response']
            assert responses and all(set(event['payload'])>={'usage','chunk_keys'} for event in responses)
            event_sequences.append({'run_id':run_id,'kinds':kinds,'model_responses':[event['payload'] for event in responses]})
        assert worker.run_once(service,settings,tmp_path,business={'business_url':p['business_url'],'site':p['site']}) is False
    print('C2_EVENT_EVIDENCE='+json.dumps(event_sequences,ensure_ascii=False,separators=(',',':')))
    assert len(list(tmp_path.iterdir()))==1
    assert len(list(tmp_path.rglob('session.jsonl.zstd')))==1
