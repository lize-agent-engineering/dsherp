import json
import httpx
import pytest
from dsherp.context_worker import run_once
SETTINGS={'DEEPSEEK_API_KEY':'synthetic','DSH_MODEL':'deepseek-v4-flash','DEEPSEEK_BASE_URL':'http://synthetic'}


def test_worker_claims_one_scoped_run_and_finishes_without_replay(tmp_path):
    calls=[];executed=[]
    claim={'run_id':'r','scope_id':'a'*64,'capability':'cap','native_session_id':'n','question':'q','context':{}}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    def execute(task,settings,directory):
        executed.append(directory)
        assert task['resume']=='inspect'
        return {'status':'Succeeded','answer':'answer'}
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=execute)
    assert executed==[tmp_path/('a'*64)]
    assert [m for m,_ in calls]==['claim_run','finish_run']
    assert len(calls[0][1]['runtime_revision'])==64
    assert calls[-1][1]['answer']=='answer'


def test_unknown_finish_response_does_not_overwrite_or_execute_again(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append(method)
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','capability':'c','scope_id':'b'*64}})
        raise httpx.ReadTimeout('lost response')
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            run_once(client,SETTINGS,tmp_path,execute=lambda *args:{'status':'Succeeded','answer':'ok'})
    assert calls==['claim_run','finish_run']


def test_bad_scope_is_failed_without_opening_any_directory(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':{'run_id':'r','capability':'c','scope_id':'../other'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        run_once(client,SETTINGS,tmp_path,execute=lambda *args:pytest.fail('invalid scope executed'))
    assert calls[-1][1]['status']=='Failed'
    assert not list(tmp_path.iterdir())
