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


def test_metrics_server_defaults_to_9109_and_once_mode_never_starts_it(monkeypatch):
    calls=[]
    sentinel=object()
    monkeypatch.setattr(worker.metrics,'serve',lambda registry,port:(calls.append((registry,port)),sentinel)[1])
    assert worker.start_metrics({},once=True) is None
    assert calls==[]
    assert worker.start_metrics({},once=False) is sentinel
    assert calls==[(worker.REGISTRY,9109)]
    calls.clear()
    assert worker.start_metrics({'metrics_port':9201},once=False) is sentinel
    assert calls==[(worker.REGISTRY,9201)]


def test_run_once_updates_claim_result_and_duration_metrics(tmp_path,monkeypatch):
    class Counter:
        def __init__(self):self.calls=[]
        def inc(self,amount=1,**labels):self.calls.append((amount,labels))
    class Histogram:
        def __init__(self):self.values=[]
        def observe(self,value):self.values.append(value)
    claims=Counter();runs=Counter();duration=Histogram();failures=[]
    monkeypatch.setattr(worker,'CLAIMS_TOTAL',claims)
    monkeypatch.setattr(worker,'RUNS_TOTAL',runs)
    monkeypatch.setattr(worker,'RUN_DURATION',duration)
    monkeypatch.setattr(worker,'set_consecutive_failures',lambda value:failures.append(value))
    claim={'run_id':'r','scope_id':'a'*64,'capability':'cap'}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *args:{'status':'Succeeded','answer':'answer'})
    assert claims.calls==[(1,{})]
    assert runs.calls==[(1,{'status':'Succeeded'})]
    assert len(duration.values)==1 and duration.values[0]>=0
    assert failures==[0]


def test_poll_once_counts_transient_worker_error_class(tmp_path,monkeypatch):
    class Counter:
        def __init__(self):self.calls=[]
        def inc(self,amount=1,**labels):self.calls.append((amount,labels))
    errors=Counter();monkeypatch.setattr(worker,'WORKER_ERRORS',errors)
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(lambda request:(_ for _ in ()).throw(httpx.ReadError('synthetic')))) as client:
        assert worker.poll_once(client,SETTINGS,tmp_path) is False
    assert errors.calls==[(1,{'error_class':'ReadError'})]


def test_ops_monitor_refreshes_once_per_minute_and_reports_fetch_failure(monkeypatch):
    calls=[];emitted=[];state={}
    responses=[httpx.Response(200,json={'message':{'queued':0,'queued_oldest_seconds':None,'running':0,
        'running_stuck':0,'backup_age_hours':1,'last_claim_age_seconds':10}}),httpx.ReadError('synthetic ops failure')]
    def handler(request):
        calls.append(request.url.path)
        response=responses.pop(0)
        if isinstance(response,Exception):raise response
        return response
    class Notifier:
        def emit(self,items,now):emitted.append(([item.key for item in items],now))
    monkeypatch.setattr(worker.alerts,'orphan_containers',lambda:0)
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        worker.monitor_ops(client,Notifier(),state,now=0)
        worker.monitor_ops(client,Notifier(),state,now=59)
        worker.monitor_ops(client,Notifier(),state,now=60)
    assert calls==['/api/method/dsherp_bridge.ops.ops_status']*2
    assert emitted==[([],0),(['ops_status_unavailable'],60)]


def test_fetch_ops_rejects_error_response_even_if_it_has_message():
    transport=httpx.MockTransport(lambda request:httpx.Response(503,json={'message':{'queued':0}}))
    with httpx.Client(base_url='http://local',transport=transport) as client:
        assert worker.fetch_ops(client) is None


def test_ops_monitor_counts_orphans_only_without_inflight_runs(monkeypatch):
    snapshots=[{'queued':0,'queued_oldest_seconds':None,'running':1,'running_stuck':0,
        'backup_age_hours':1,'last_claim_age_seconds':10},{'queued':0,'queued_oldest_seconds':None,'running':0,
        'running_stuck':0,'backup_age_hours':1,'last_claim_age_seconds':10}]
    orphan_calls=[]
    monkeypatch.setattr(worker,'fetch_ops',lambda client:snapshots.pop(0))
    monkeypatch.setattr(worker.alerts,'orphan_containers',lambda:orphan_calls.append(True) or 2)
    class Notifier:
        def __init__(self):self.keys=[]
        def emit(self,items,now):self.keys.append([item.key for item in items])
    notifier=Notifier();state={}
    worker.monitor_ops(object(),notifier,state,now=0)
    worker.monitor_ops(object(),notifier,state,now=60)
    assert orphan_calls==[True]
    assert notifier.keys==[[],['orphan_containers']]
