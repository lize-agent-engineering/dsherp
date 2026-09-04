import json
import os
import signal
import subprocess
import threading
import time
import httpx
import pytest
from dsherp.context_mcp import BusinessRuntimeError,ToolFailure
import dsherp.context_worker as worker
from dsherp.context_worker import profile_business,run_once
SETTINGS={'DEEPSEEK_API_KEY':'synthetic','DSH_MODEL':'deepseek-v4-flash','DEEPSEEK_BASE_URL':'http://synthetic'}
NEEDS_INPUT={'status':'NeedsInput','answer':'请指定仓库'}


def _fake_container(payload):
    def fake_run(args,**kwargs):
        if list(args)[:3]==['docker','rm','-f']:
            return subprocess.CompletedProcess(args,0,'','')
        return subprocess.CompletedProcess(args,0,json.dumps(payload),'')
    return fake_run


def test_worker_claims_one_scoped_run_and_finishes_without_replay(tmp_path):
    calls=[];executed=[]
    claim={'run_id':'r','scope_id':'a'*64,'capability':'cap','native_session_id':'n','question':'q','context':{},
           'domain':'query','budget':{'run_total_seconds':300}}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    def execute(task,settings,directory,timeout):
        executed.append(directory)
        assert task['resume']=='inspect'
        assert timeout==330
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
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','capability':'c','scope_id':'b'*64,
            'domain':'query','budget':{'run_total_seconds':300}}})
        raise httpx.ReadTimeout('lost response')
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *args:{'status':'Succeeded','answer':'ok'})
    assert calls==['claim_run','record_run_event','finish_run']


def test_bad_scope_is_failed_without_opening_any_directory(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':{'run_id':'r','capability':'c','scope_id':'../other',
            'domain':'query','budget':{'run_total_seconds':300}}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        run_once(client,SETTINGS,tmp_path,execute=lambda *args:pytest.fail('invalid scope executed'))
    assert calls[-1][1]['status']=='Failed'
    assert not list(tmp_path.iterdir())


def test_worker_injects_the_selected_business_site_without_alpha_hardcoding(tmp_path):
    claim={'run_id':'r','scope_id':'c'*64,'capability':'cap','domain':'query',
           'budget':{'run_total_seconds':300}}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    def execute(task,settings,directory,timeout):
        assert task['business_url']=='http://backend:8000'
        assert task['site']=='dsherp-daily.localhost'
        assert timeout==330
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
    assert issubclass(ToolFailure,BusinessRuntimeError)
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
    assert 'ToolFailure' in diagnostic and '500' in diagnostic
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
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'d'*64,'capability':'cap',
            'domain':'query','budget':{'run_total_seconds':300}}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':9} if method=='record_run_event' else {'status':'Failed'}})
    def execute(task,settings,directory,timeout):raise RuntimeError('boom')
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
        return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'e'*64,'capability':'cap',
            'domain':'query','budget':{'run_total_seconds':300}} if method=='claim_run' else {'status':'Succeeded'}})
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
        return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'f'*64,'capability':'cap',
            'domain':'query','budget':{'run_total_seconds':300}} if method=='claim_run' else {'status':'Succeeded'}})
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


def test_metrics_bind_failure_only_logs_and_worker_can_continue(monkeypatch,capsys):
    monkeypatch.setattr(worker.metrics,'serve',lambda registry,port:(_ for _ in ()).throw(OSError('synthetic occupied port')))
    assert worker.start_metrics({},once=False) is None
    lines=[json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
    assert len(lines)==1
    assert lines[0]['event']=='metrics_start_failed' and lines[0]['error_class']=='OSError'
    assert 'synthetic occupied port' not in json.dumps(lines)


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
    claim={'run_id':'r','scope_id':'a'*64,'capability':'cap','domain':'query',
           'budget':{'run_total_seconds':300}}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {'status':'Succeeded'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *args:{'status':'Succeeded','answer':'answer'})
    assert claims.calls==[(0,{'site':'legacy'}),(1,{'site':'legacy'})]
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
    responses=[httpx.Response(200,json={'message':{'snapshot':{'queued':0,'queued_oldest_seconds':None,'running':0,
        'running_stuck':0,'backup_age_hours':1,'last_claim_age_seconds':10},'age_seconds':0}}),httpx.ReadError('synthetic ops failure')]
    timeouts=[]
    def handler(request):
        calls.append(request.url.path)
        timeouts.append(request.extensions['timeout'])
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
    assert all(set(timeout.values())=={5.0} for timeout in timeouts)
    assert emitted==[([],0),(['ops_status_unavailable'],60)]


def test_fetch_ops_rejects_error_response_even_if_it_has_message():
    transport=httpx.MockTransport(lambda request:httpx.Response(503,json={'message':{'queued':0}}))
    with httpx.Client(base_url='http://local',transport=transport) as client:
        assert worker.fetch_ops(client) is None


def test_ops_monitor_counts_orphans_only_without_inflight_runs(monkeypatch):
    snapshots=[{'snapshot':{'queued':0,'queued_oldest_seconds':None,'running':1,'running_stuck':0,
        'backup_age_hours':1,'last_claim_age_seconds':10},'age_seconds':0},{'snapshot':{'queued':0,
        'queued_oldest_seconds':None,'running':0,'running_stuck':0,'backup_age_hours':1,
        'last_claim_age_seconds':10},'age_seconds':0}]
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


def test_ops_monitor_skips_snapshot_gauges_and_orphans_when_stale(monkeypatch):
    stale={'snapshot':{'queued':9,'running':0,'running_stuck':3,'backup_age_hours':99,
        'last_claim_age_seconds':999},'age_seconds':901}
    monkeypatch.setattr(worker,'fetch_ops',lambda client:stale)
    monkeypatch.setattr(worker.alerts,'orphan_containers',lambda:pytest.fail('stale snapshot probed docker'))
    writes=[]
    for name in ('QUEUE_DEPTH','RUNNING_STUCK','BACKUP_AGE','LAST_CLAIM','ORPHAN_CONTAINERS'):
        monkeypatch.setattr(getattr(worker,name),'set',lambda value,name=name:writes.append((name,value)))
    class Notifier:
        def emit(self,items,now):
            assert [item.key for item in items]==['ops_snapshot_stale']
    worker.monitor_ops(object(),Notifier(),{},now=1000)
    assert writes==[]


def test_ops_monitor_does_not_overwrite_optional_gauges_with_missing_values(monkeypatch):
    current={'snapshot':{'queued':0,'running':0,'running_stuck':0,'backup_age_hours':None,
        'last_claim_age_seconds':None},'age_seconds':0}
    monkeypatch.setattr(worker,'fetch_ops',lambda client:current)
    monkeypatch.setattr(worker.alerts,'orphan_containers',lambda:0)
    backup=[];claims=[]
    monkeypatch.setattr(worker.BACKUP_AGE,'set',backup.append)
    monkeypatch.setattr(worker.LAST_CLAIM,'set',claims.append)
    class Notifier:
        def emit(self,items,now):pass
    worker.monitor_ops(object(),Notifier(),{},now=1000)
    assert backup==[] and claims==[]


def test_ops_monitor_skips_orphan_metric_when_probe_fails(monkeypatch):
    current={'snapshot':{'queued':0,'running':0,'running_stuck':0,'backup_age_hours':1,
        'last_claim_age_seconds':10},'age_seconds':0}
    monkeypatch.setattr(worker,'fetch_ops',lambda client:current)
    monkeypatch.setattr(worker.alerts,'orphan_containers',lambda:None)
    writes=[]
    monkeypatch.setattr(worker.ORPHAN_CONTAINERS,'set',writes.append)
    class Notifier:
        def emit(self,items,now):
            assert [item.key for item in items]==[]
    worker.monitor_ops(object(),Notifier(),{},now=1000)
    assert writes==[]


def test_coordinator_round_robins_sites_and_respects_slots(tmp_path):
    from dsherp.context_worker import Coordinator

    claims={'a':['r1','r2'],'b':['r3']};finished=[];clients=[]
    def make_client(site):
        def handler(request):
            method=request.url.path.rsplit('.',1)[-1];body=json.loads(request.content)
            if method=='claim_run':
                queue=claims[site]
                return httpx.Response(200,json={'message':{'run_id':queue.pop(0),'scope_id':site*64,
                    'capability':'c','domain':'query','budget':{'run_total_seconds':300}} if queue else {}})
            if method=='finish_run':
                finished.append((site,body['run_id']))
                return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
            return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
        client=httpx.Client(base_url='http://'+site,transport=httpx.MockTransport(handler))
        clients.append(client);return client
    sites=[{'site':site,'client':make_client(site),'business':{'business_url':'http://x','site':site}}
           for site in ('a','b')]
    executed=[]
    def execute(task,settings,directory,timeout):
        executed.append((task['site'],task['run_id'],timeout));return {'status':'Succeeded','answer':'ok'}
    try:
        coordinator=Coordinator(sites,lambda:SETTINGS,slots=2,execute=execute,breaker=None,
                                probe=lambda:True,state_root=tmp_path)
        assert coordinator.tick(now=0)==2
        coordinator.wait_idle()
        assert coordinator.tick(now=1)==1 and coordinator.wait_idle() is None
        assert sorted(item[1] for item in executed)==['r1','r2','r3']
        assert all(item[2]==330 for item in executed)
        assert sorted(finished)==[('a','r1'),('a','r2'),('b','r3')]
    finally:
        for client in clients:client.close()


def test_run_claimed_never_escapes_and_records_provider_failure(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':
            return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'a'*64,'capability':'c',
                'domain':'query','budget':{'run_total_seconds':300}}})
        if method=='finish_run':return httpx.Response(403,json={'exc_type':'PermissionError'})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        breaker=CircuitBreaker()
        coordinator=Coordinator([{'site':'a','client':client,
            'business':{'business_url':'http://x','site':'a'}}],lambda:SETTINGS,slots=1,
            execute=lambda *args,**kwargs:{'status':'Succeeded','answer':'ok'},breaker=breaker,
            probe=lambda:False,state_root=tmp_path)
        assert coordinator.tick(now=0)==1;coordinator.wait_idle()
        assert breaker.state=='closed' and breaker.consecutive_failures==0
        def failing(*args,**kwargs):raise RuntimeError('boom')
        coordinator.execute=failing
        for current in range(10,13):
            coordinator.tick(now=current);coordinator.wait_idle()
        assert breaker.state=='open' and coordinator.tick(now=20)==0


def test_profile_normalization_supports_legacy_and_multisite_shapes():
    from dsherp.context_worker import normalize_profile

    legacy={'site':'alpha.localhost','base_url':'http://alpha','business_url':'http://backend:8000',
            'api_key':'key','api_secret':'secret'}
    normalized=normalize_profile(legacy)
    assert normalized['slots']==3 and normalized['metrics_port']==9109
    assert normalized['sites']==[legacy]
    multisite=normalize_profile({'slots':2,'metrics_port':9200,'alert_webhook':None,
        'sites':[legacy,{**legacy,'site':'daily.localhost'}]})
    assert multisite['slots']==2 and [item['site'] for item in multisite['sites']]==[
        'alpha.localhost','daily.localhost']
    for invalid in ({'sites':[]},{'slots':0,'sites':[legacy]},
                    {'sites':[legacy,dict(legacy)]},{'sites':[{}]}):
        with pytest.raises(ValueError):normalize_profile(invalid)


def test_coordinator_never_claims_beyond_free_slots(tmp_path):
    from dsherp.context_worker import Coordinator

    release=threading.Event();started=[];claim_calls=[];clients=[]
    def make_client(site):
        available=[site+'-run']
        def handler(request):
            method=request.url.path.rsplit('.',1)[-1]
            if method=='claim_run':
                claim_calls.append(site)
                run_id=available.pop(0) if available else None
                return httpx.Response(200,json={'message':None if run_id is None else {
                    'run_id':run_id,'scope_id':site*64,'capability':'c','domain':'query',
                    'budget':{'run_total_seconds':300}}})
            return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
        client=httpx.Client(base_url='http://'+site,transport=httpx.MockTransport(handler))
        clients.append(client);return client
    def execute(task,settings,directory,timeout):
        started.append(task['run_id']);release.wait(5)
        return {'status':'Succeeded','answer':'ok'}
    sites=[{'site':site,'client':make_client(site),'business':{'site':site,'business_url':'http://x'}}
           for site in ('a','b','c')]
    try:
        coordinator=Coordinator(sites,lambda:SETTINGS,2,execute,None,lambda:True,tmp_path)
        assert coordinator.tick(now=0)==2 and claim_calls==['a','b']
        assert coordinator.tick(now=1)==0 and len(started)==2
        release.set();coordinator.wait_idle()
        assert coordinator.tick(now=2)==1
        coordinator.wait_idle()
        assert sorted(started)==['a-run','b-run','c-run']
    finally:
        release.set()
        for client in clients:client.close()


def test_coordinator_probe_resets_open_circuit_after_sixty_seconds(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    clock=[0];claims=['first','second'];probes=[];executions=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':
            run_id=claims.pop(0) if claims else None
            return httpx.Response(200,json={'message':None if run_id is None else {
                'run_id':run_id,'scope_id':'a'*64,'capability':'c','domain':'query',
                'budget':{'run_total_seconds':300}}})
        if method=='finish_run':
            return httpx.Response(200,json={'message':{'status':'Failed','provider_failures':1}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
    def execute(task,settings,directory,timeout):
        executions.append(task['run_id']);raise RuntimeError('synthetic provider failure')
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        breaker=CircuitBreaker(threshold=1,open_seconds=60)
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,1,
            execute,breaker,lambda:probes.append(True) or True,tmp_path,clock=lambda:clock[0])
        assert coordinator.tick(now=0)==1;coordinator.wait_idle()
        assert breaker.state=='open' and coordinator.tick(now=59)==0 and probes==[]
        clock[0]=60
        assert coordinator.tick(now=60)==1 and probes==[True]
        coordinator.wait_idle()
        assert executions==['first','second']


def test_circuit_open_period_starts_when_slow_run_finishes(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    clock=[0];claims=['first','second'];executed=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':
            run_id=claims.pop(0) if claims else None
            return httpx.Response(200,json={'message':None if run_id is None else {
                'run_id':run_id,'scope_id':'a'*64,'capability':'c','domain':'query',
                'budget':{'run_total_seconds':300}}})
        if method=='finish_run':return httpx.Response(200,json={'message':{'status':'Failed','provider_failures':1}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
    def execute(task,settings,directory,timeout):
        executed.append(task['run_id']);clock[0]=120
        raise RuntimeError('synthetic slow provider failure')
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,1,execute,
            CircuitBreaker(threshold=1,open_seconds=60),lambda:False,tmp_path,clock=lambda:clock[0])
        assert coordinator.tick(now=0)==1;coordinator.wait_idle()
        clock[0]=121
        assert coordinator.tick(now=121)==0 and executed==['first']
        clock[0]=179
        assert coordinator.tick(now=179)==0 and executed==['first']


def test_half_open_tick_claims_only_one_trial_across_sites(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    clients=[];claimed=[]
    def make_client(site):
        def handler(request):
            method=request.url.path.rsplit('.',1)[-1]
            if method=='claim_run':
                claimed.append(site)
                return httpx.Response(200,json={'message':{'run_id':site,'scope_id':site*64,'capability':'c',
                    'domain':'query','budget':{'run_total_seconds':300}}})
            return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
        client=httpx.Client(base_url='http://'+site,transport=httpx.MockTransport(handler));clients.append(client)
        return client
    breaker=CircuitBreaker(threshold=1,open_seconds=60);breaker.record('provider_failure',now=0)
    sites=[{'site':site,'client':make_client(site),'business':{}} for site in ('a','b','c')]
    try:
        coordinator=Coordinator(sites,lambda:SETTINGS,3,
            lambda *args:{'status':'Succeeded','answer':'ok'},breaker,lambda:False,tmp_path,clock=lambda:60)
        assert coordinator.tick(now=60)==1 and len(claimed)==1
        coordinator.wait_idle()
    finally:
        for client in clients:client.close()


def test_coordinator_initializes_claim_metric_for_every_site(tmp_path,monkeypatch):
    from dsherp.context_worker import Coordinator

    class Counter:
        def __init__(self):self.calls=[]
        def inc(self,amount=1,**labels):self.calls.append((amount,labels))
    claims=Counter();monkeypatch.setattr(worker,'CLAIMS_TOTAL',claims)
    sites=[{'site':'alpha','client':object(),'business':{}},{'site':'daily','client':object(),'business':{}}]
    Coordinator(sites,lambda:SETTINGS,1,lambda *args:None,None,lambda:False,tmp_path)
    assert claims.calls==[(0,{'site':'alpha'}),(0,{'site':'daily'})]


def test_half_open_empty_poll_releases_trial_for_next_tick(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    queue=[];executed=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':
            run_id=queue.pop(0) if queue else None
            return httpx.Response(200,json={'message':None if run_id is None else {
                'run_id':run_id,'scope_id':'a'*64,'capability':'c','domain':'query',
                'budget':{'run_total_seconds':300}}})
        return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
    breaker=CircuitBreaker(threshold=1,open_seconds=60);breaker.record('provider_failure',now=0)
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,1,
            lambda task,*args:executed.append(task['run_id']) or {'status':'Succeeded','answer':'ok'},
            breaker,lambda:False,tmp_path,clock=lambda:61)
        assert coordinator.tick(now=60)==0 and breaker.state=='half_open'
        queue.append('trial')
        assert coordinator.tick(now=61)==1
        coordinator.wait_idle()
        assert executed==['trial'] and breaker.state=='closed'


def test_tick_refreshes_site_heartbeats_while_circuit_open(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    calls=[]
    def handler(request):
        calls.append(request.url.path.rsplit('.',1)[-1])
        return httpx.Response(200,json={'message':{'heartbeat':'synthetic'}})
    breaker=CircuitBreaker(threshold=1,open_seconds=60);breaker.record('provider_failure',now=0)
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,1,
            lambda *args:pytest.fail('open circuit executed'),breaker,lambda:False,tmp_path)
        assert coordinator.tick(now=1)==0
    assert calls==['worker_heartbeat']


def test_tick_refreshes_site_heartbeats_while_slots_are_full(tmp_path):
    from dsherp.context_worker import Coordinator

    release=threading.Event();calls=[];queue=['running']
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append(method)
        if method=='claim_run':
            run_id=queue.pop(0) if queue else None
            return httpx.Response(200,json={'message':None if run_id is None else {
                'run_id':run_id,'scope_id':'a'*64,'capability':'c','domain':'query',
                'budget':{'run_total_seconds':300}}})
        return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
    def execute(*args):
        release.wait(5);return {'status':'Succeeded','answer':'ok'}
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,1,
            execute,None,lambda:False,tmp_path)
        try:
            assert coordinator.tick(now=0)==1
            assert coordinator.tick(now=1)==0
            assert calls.count('worker_heartbeat')==2
        finally:
            release.set();coordinator.wait_idle()


def test_run_container_accepts_needs_input_result(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    (tmp_path/'work').mkdir()
    monkeypatch.setattr(worker.subprocess,'run',_fake_container(NEEDS_INPUT))
    assert worker.run_container({'run_id':'r'},SETTINGS,tmp_path/'session')==NEEDS_INPUT


def test_run_container_rejects_invalid_status_and_shape(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    (tmp_path/'work').mkdir()
    for payload in ({'status':'Failed','answer':''},{'status':'Running','answer':'x'},
                    {'status':'NeedsInput','answer':'请指定仓库','extra':1},{'status':'NeedsInput'}):
        monkeypatch.setattr(worker.subprocess,'run',_fake_container(payload))
        with pytest.raises(RuntimeError,match='Invalid business runtime result'):
            worker.run_container({'run_id':'r'},SETTINGS,tmp_path/'session')


def test_run_once_finishes_needs_input_and_clears_consecutive_failures(tmp_path,monkeypatch):
    failures=[]
    monkeypatch.setattr(worker,'set_consecutive_failures',lambda value:failures.append(value))
    calls=[]
    claim={'run_id':'r','scope_id':'a'*64,'capability':'cap','domain':'query',
           'budget':{'run_total_seconds':300}}
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        return httpx.Response(200,json={'message':claim if method=='claim_run' else {
            'status':'NeedsInput','provider_failures':0}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *args:NEEDS_INPUT)
    assert [m for m,_ in calls]==['claim_run','record_run_event','finish_run']
    assert calls[-1][1]['status']=='NeedsInput' and calls[-1][1]['answer']=='请指定仓库'
    assert failures==[0]


def test_run_claimed_contains_malformed_claim_errors(tmp_path,monkeypatch):
    from dsherp.context_worker import Coordinator

    class Counter:
        def __init__(self):self.calls=[]
        def inc(self,amount=1,**labels):self.calls.append((amount,labels))
    errors=Counter();monkeypatch.setattr(worker,'WORKER_ERRORS',errors)
    coordinator=Coordinator([{'site':'a','client':object(),'business':{}}],lambda:SETTINGS,1,
        lambda *args:pytest.fail('malformed claim executed'),None,lambda:False,tmp_path)
    coordinator.run_claimed(coordinator.sites[0],{'run_id':'missing-capability'},SETTINGS)
    assert errors.calls==[(1,{'error_class':'KeyError'})]
