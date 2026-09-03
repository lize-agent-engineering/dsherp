import json
import os
import signal
import time
import httpx
import pytest
from dsherp.context_mcp import BusinessRuntimeError
import dsherp.context_worker as worker
from dsherp.context_worker import profile_business,run_once
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
    assert [m for m,_ in calls]==['claim_run','record_run_event','finish_run']
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
    assert calls==['claim_run','record_run_event','finish_run']


def test_bad_scope_is_failed_without_opening_any_directory(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':{'run_id':'r','capability':'c','scope_id':'../other'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        run_once(client,SETTINGS,tmp_path,execute=lambda *args:pytest.fail('invalid scope executed'))
    assert calls[-1][1]['status']=='Failed'
    assert not list(tmp_path.iterdir())


def test_worker_injects_the_selected_business_site_without_alpha_hardcoding(tmp_path):
    claim={'run_id':'r','scope_id':'c'*64,'capability':'cap'}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    def execute(task,settings,directory):
        assert task['business_url']=='http://backend:8000'
        assert task['site']=='dsherp-daily.localhost'
        return {'status':'Succeeded','answer':'daily answer'}
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        run_once(client,SETTINGS,tmp_path,business={'business_url':'http://backend:8000','site':'dsherp-daily.localhost'},execute=execute)


def test_business_profile_requires_explicit_urls_and_site():
    assert profile_business({'base_url':'http://127.0.0.1:18086','business_url':'http://backend:8000','site':'dsherp-daily.localhost'}) == {
        'business_url':'http://backend:8000','site':'dsherp-daily.localhost'}
    for profile in ({'base_url':'http://127.0.0.1:18086','site':'dsherp-daily.localhost'},
                    {'base_url':'http://127.0.0.1:18086','business_url':'backend','site':'dsherp-daily.localhost'}):
        with pytest.raises(ValueError):profile_business(profile)


def test_worker_poll_survives_transient_business_transport_and_server_failures(tmp_path, capsys):
    attempts=[]
    def handler(request):
        attempts.append(request.url.path)
        if len(attempts)==1:raise httpx.ReadError('synthetic connection reset')
        if len(attempts)==2:return httpx.Response(500,json={'exc_type':'SyntheticFailure'})
        return httpx.Response(200,json={})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert worker.poll_once(client,SETTINGS,tmp_path) is False
        assert worker.poll_once(client,SETTINGS,tmp_path) is False
        assert worker.poll_once(client,SETTINGS,tmp_path) is False
    assert len(attempts)==3
    diagnostic=capsys.readouterr().err
    assert 'ReadError' in diagnostic
    assert 'BusinessRuntimeError' in diagnostic and '500' in diagnostic
    assert 'synthetic connection reset' not in diagnostic


def test_worker_poll_fast_fails_non_transient_business_rejection(tmp_path):
    def handler(request):
        return httpx.Response(417,json={'exc_type':'SyntheticRejected'})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BusinessRuntimeError) as caught:
            worker.poll_once(client,SETTINGS,tmp_path)
    assert caught.value.status_code==417


def test_worker_pid_file_replaces_stale_value_and_is_removed_on_exit(tmp_path):
    target=tmp_path/'worker.pid'
    target.write_text('stale')
    with worker.worker_pid(target):
        assert target.read_text()==str(os.getpid())
        assert target.stat().st_mode & 0o777==0o600
    assert not target.exists()


def test_sigterm_exits_through_worker_pid_cleanup(tmp_path):
    target=tmp_path/'worker.pid'
    with pytest.raises(SystemExit) as caught:
        with worker.worker_pid(target):
            assert target.exists()
            worker.exit_on_signal(signal.SIGTERM,None)
    assert caught.value.code==0
    assert not target.exists()


def test_worker_records_container_outcome_before_finishing(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'d'*64,'capability':'cap'}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':9} if method=='record_run_event' else {'status':'Failed'}})
    def execute(task,settings,directory):raise RuntimeError('boom')
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=execute)
    assert [m for m,_ in calls]==['claim_run','record_run_event','finish_run']
    event=calls[1][1]['events'][0]
    assert event['kind']=='runtime_failed' and event['error_class']=='RuntimeError' and event['source']=='worker'
    assert 'boom' not in json.dumps(calls)
    assert calls[-1][1]['status']=='Failed'


def test_event_writeback_failure_does_not_change_the_run_result(tmp_path,capsys):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append(method)
        if method=='record_run_event':return httpx.Response(503)
        return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'e'*64,'capability':'cap'} if method=='claim_run' else {'status':'Succeeded'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *a:{'status':'Succeeded','answer':'ok'})
    assert calls==['claim_run','record_run_event','finish_run']
    assert 'event_writeback_failed' in capsys.readouterr().err


def test_event_writeback_timeout_does_not_change_the_run_result(tmp_path,capsys):
    calls=[];timeouts=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append(method)
        if method=='record_run_event':
            timeouts.append(request.extensions['timeout'])
            raise httpx.TimeoutException('synthetic slow event sink',request=request)
        return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'f'*64,'capability':'cap'} if method=='claim_run' else {'status':'Succeeded'}})
    started=time.monotonic()
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler),timeout=30) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *a:{'status':'Succeeded','answer':'ok'})
    assert calls==['claim_run','record_run_event','finish_run']
    assert timeouts and set(timeouts[0].values())=={5.0}
    assert time.monotonic()-started<10
    assert 'event_writeback_failed' in capsys.readouterr().err
