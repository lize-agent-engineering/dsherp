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
SETTINGS={'DEEPSEEK_API_KEY':'synthetic','DEEPSEEK_BASE_URL':'http://synthetic','deployment_digest':'a1'*32}
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


def test_sigterm_drains_then_releases_the_worker_pid(tmp_path):
    """SIGTERM 请求排空而不是就地抛 SystemExit；pid 仍在循环正常退出时释放。"""
    target=tmp_path/'worker.pid'
    worker.STOPPING.clear()
    try:
        with worker.worker_pid(target):
            assert target.exists()
            worker.exit_on_signal(signal.SIGTERM,None)
            assert worker.STOPPING.is_set()
            assert worker.STOPPING.wait(0) is True
        assert not target.exists()
    finally:
        worker.STOPPING.clear()


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
        # 站点领取是并行的，顺序不做承诺；受约束的是"只联系轮转中前 capacity 个站"。
        assert coordinator.tick(now=0)==2 and sorted(claim_calls)==['a','b']
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


def test_coordinator_failed_probe_keeps_open_circuit_from_claiming(tmp_path):
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
            execute,breaker,lambda:probes.append(False) or False,tmp_path,clock=lambda:clock[0])
        assert coordinator.tick(now=0)==1;coordinator.wait_idle()
        assert breaker.state=='open' and worker.PROVIDER_CIRCUIT_OPEN._value==1
        clock[0]=60
        assert coordinator.tick(now=60)==0 and probes==[False]
        assert executions==['first'] and claims==['second']
        assert breaker.state=='open' and worker.PROVIDER_CIRCUIT_OPEN._value==1
        clock[0]=63
        assert coordinator.tick(now=63)==0 and probes==[False]
        assert executions==['first'] and claims==['second']
        assert breaker.state=='open' and worker.PROVIDER_CIRCUIT_OPEN._value==1


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


def test_coordinator_initializes_claim_metric_for_every_site(tmp_path,monkeypatch):
    from dsherp.context_worker import Coordinator

    class Counter:
        def __init__(self):self.calls=[]
        def inc(self,amount=1,**labels):self.calls.append((amount,labels))
    claims=Counter();monkeypatch.setattr(worker,'CLAIMS_TOTAL',claims)
    sites=[{'site':'alpha','client':object(),'business':{}},{'site':'daily','client':object(),'business':{}}]
    Coordinator(sites,lambda:SETTINGS,1,lambda *args:None,None,lambda:False,tmp_path)
    assert claims.calls==[(0,{'site':'alpha'}),(0,{'site':'daily'})]


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


def test_three_provider_failure_runs_emit_one_provider_circuit_open_alert(tmp_path,capsys):
    from dsherp import alerts
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    claims=['r1','r2','r3']
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
    with httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler)) as client:
        breaker=CircuitBreaker()
        notifier=alerts.Notifier()
        sites=[{'site':'a','client':client,'business':{}}]
        execute=lambda *args:{'status':'Succeeded','answer':'ok'}
        try:
            coordinator=Coordinator(sites,lambda:SETTINGS,1,execute,breaker,lambda:False,tmp_path,
                notifier=notifier)
        except TypeError as error:
            if 'notifier' not in str(error):
                raise
            coordinator=Coordinator(sites,lambda:SETTINGS,1,execute,breaker,lambda:False,tmp_path)
        for current in range(3):
            assert coordinator.tick(now=current)==1
            coordinator.wait_idle()
        assert breaker.state=='open' and coordinator.tick(now=3)==0
    records=[json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()]
    opened=[line for line in records if line.get('event')=='alert'
            and line.get('key')=='provider_circuit_open' and line.get('severity')=='critical']
    assert len(opened)==1


def test_cleanup_stale_runtime_artifacts_after_lock_removes_only_owned_leftovers(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    work=tmp_path/'work'
    work.mkdir()
    stale_a=work/'context-run-aaaa'
    stale_b=work/'context-run-bbbb'
    stale_a.mkdir();(stale_a/'run.json').write_text('{"marker":"stale-a"}')
    stale_b.mkdir();(stale_b/'run.json').write_text('{"marker":"stale-b"}')
    keeper=work/'keeper'
    keeper.mkdir();(keeper/'keep.txt').write_text('keep')
    similar=work/'context-run-notes.txt'
    similar.write_text('not-a-temp-dir')
    owned='dsherp-context-'+'ab'*16
    neighbor='dsherp-context-orphan-test'
    short='dsherp-context-abc'
    uppercase='dsherp-context-'+'AB'*16
    commands=[]
    def fake_run(args,**kwargs):
        commands.append(list(args))
        if list(args)[:2]==['docker','ps']:
            return subprocess.CompletedProcess(args,0,'\n'.join((owned,neighbor,short,uppercase))+'\n','')
        if list(args)[:3]==['docker','rm','-f']:
            return subprocess.CompletedProcess(args,0,'','')
        raise AssertionError('unexpected '+str(args))
    worker.cleanup_stale_runtime_artifacts(runner=fake_run)
    listed=next(cmd for cmd in commands if cmd[:2]==['docker','ps'])
    assert '-a' in listed or '--all' in listed
    assert 'name=dsherp-context-' in listed
    assert [cmd[3] for cmd in commands if cmd[:3]==['docker','rm','-f']]==[owned]
    assert not stale_a.exists() and not stale_b.exists()
    assert (keeper/'keep.txt').read_text()=='keep'
    assert similar.read_text()=='not-a-temp-dir'
    def fail_list(args,**kwargs):
        return subprocess.CompletedProcess(args,1,'','synthetic enumerate failed')
    with pytest.raises((subprocess.CalledProcessError,RuntimeError)):
        worker.cleanup_stale_runtime_artifacts(runner=fail_list)
    def fail_rm(args,**kwargs):
        if list(args)[:2]==['docker','ps']:
            return subprocess.CompletedProcess(args,0,owned+'\n','')
        return subprocess.CompletedProcess(args,1,'','synthetic rm failed')
    with pytest.raises((subprocess.CalledProcessError,RuntimeError)):
        worker.cleanup_stale_runtime_artifacts(runner=fail_rm)


def test_probe_recovery_risks_exactly_one_trial_run_not_a_full_slate(tmp_path):
    """探针只证明 /models 活着；恢复应当只赌一条运行，而不是同一 tick 灌满槽位。"""
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker

    clock=[0];claims=['a1','a2','a3','a4'];executions=[]
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
        coordinator=Coordinator([{'site':'a','client':client,'business':{}}],lambda:SETTINGS,3,
            execute,breaker,lambda:True,tmp_path,clock=lambda:clock[0])
        assert coordinator.tick(now=0)==1;coordinator.wait_idle()
        assert breaker.state=='open'
        clock[0]=60
        assert coordinator.tick(now=60)==1,'probe recovery must risk a single trial run'
        assert breaker.state=='half_open'
        coordinator.wait_idle()
        assert breaker.state=='open','a failed trial must reopen the circuit immediately'
        assert executions==['a1','a2']


def _site_client(name,handler):
    return httpx.Client(base_url='http://'+name,transport=httpx.MockTransport(handler))


def test_a_black_hole_site_is_skipped_and_never_starves_a_healthy_one(tmp_path):
    """故障站不得按串行 25s 超时逐个拖住健康站，并且连续失败后应被跳过。"""
    from dsherp.context_worker import Coordinator
    contacted=[];executed=[]
    def dead(request):
        contacted.append('dead');raise httpx.ReadTimeout('black hole')
    healthy=iter(['h1','h2','h3','h4','h5','h6'])
    def alive(request):
        method=request.url.path.rsplit('.',1)[-1]
        contacted.append('alive')
        if method=='claim_run':
            run_id=next(healthy,None)
            return httpx.Response(200,json={'message':None if run_id is None else {
                'run_id':run_id,'scope_id':'a'*64,'capability':'c','domain':'query',
                'budget':{'run_total_seconds':300}}})
        if method=='finish_run':return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
    with _site_client('dead',dead) as bad, _site_client('alive',alive) as good:
        sites=[{'site':'dead','client':bad,'business':{}},{'site':'alive','client':good,'business':{}}]
        coordinator=Coordinator(sites,lambda:SETTINGS,2,
            lambda task,settings,directory,timeout:executed.append(task['run_id']) or {'status':'Succeeded','answer':'ok'},
            None,lambda:True,tmp_path)
        for round_index in range(4):
            coordinator.tick(now=round_index);coordinator.wait_idle()
    assert executed==['h1','h2','h3','h4'],executed
    assert contacted.count('dead')<=3,'a repeatedly failing site must stop being contacted every tick'


def test_failed_container_removal_is_reported_and_retried(tmp_path,capsys):
    """docker rm 失败不得静默：容器仍挂着含 provider key 的 run.json。"""
    from dsherp import context_worker
    attempts=[]
    def runner(command,**kwargs):
        attempts.append(command)
        code=1 if len([c for c in attempts if c[:3]==['docker','rm','-f']])==1 else 0
        return subprocess.CompletedProcess(command,code,stdout='',stderr='no such container')
    context_worker.PENDING_REMOVALS.clear()
    assert context_worker.remove_container('dsherp-context-abc',runner=runner) is False
    assert 'dsherp-context-abc' in context_worker.PENDING_REMOVALS
    logged=capsys.readouterr().err
    assert 'container_removal_failed' in logged
    assert context_worker.retry_pending_removals(runner=runner)==1
    assert not context_worker.PENDING_REMOVALS

    # 永远删不掉的名字必须被放弃，否则重试集合与 docker 调用无界增长。
    context_worker.REMOVAL_ATTEMPTS.clear()
    always_fails=lambda command,**kwargs:subprocess.CompletedProcess(command,1,stdout='',stderr='no such container')
    for _ in range(context_worker.MAX_REMOVAL_ATTEMPTS):
        context_worker.remove_container('dsherp-context-ghost',runner=always_fails)
    assert not context_worker.PENDING_REMOVALS and not context_worker.REMOVAL_ATTEMPTS
    assert 'container_removal_abandoned' in capsys.readouterr().err


def test_observability_failure_never_stops_claiming(tmp_path,monkeypatch):
    """monitor_ops 抛出不得跳过 tick：心跳停 60s 会让所有站对真实用户返回 503。"""
    from dsherp import context_worker
    context_worker.STOPPING.clear()
    beats=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='worker_heartbeat':beats.append(1)
        return httpx.Response(200,json={'message':{}})
    monkeypatch.setattr(context_worker,'monitor_ops',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('ops shape changed')))
    with _site_client('alive',handler) as client:
        coordinator=context_worker.Coordinator([{'site':'alive','client':client,'business':{}}],lambda:SETTINGS,1,
            lambda *a,**k:{'status':'Succeeded','answer':'ok'},None,lambda:True,tmp_path)
        assert context_worker.serve_once(coordinator,[{'site':'alive','client':client}],None,{},now=0) is True
    assert beats,'heartbeat must survive an observability failure'


def test_sigterm_requests_a_drain_instead_of_tearing_down_in_flight_runs():
    """SIGTERM 直接抛 SystemExit 会在在飞运行回写前关掉 client，答案必然丢失。"""
    from dsherp import context_worker
    context_worker.STOPPING.clear()
    try:
        context_worker.exit_on_signal(15,None)
        assert context_worker.STOPPING.is_set()
    finally:
        context_worker.STOPPING.clear()


def test_crash_leftovers_are_reclaimed_before_dependency_checks(monkeypatch):
    """依赖检查失败时若清理还没跑，残留容器会继续挂着含 provider key 的 run.json。"""
    from dsherp import context_worker
    order=[]
    def runner(command,**kwargs):
        order.append(command[1])
        if command[1]=='image':raise subprocess.CalledProcessError(1,command)
        return subprocess.CompletedProcess(command,0,stdout='',stderr='')
    with pytest.raises(subprocess.CalledProcessError):
        context_worker.prepare_host(runner=runner,cleanup=lambda runner:order.append('cleanup'))
    assert order[0]=='cleanup' and 'image' in order


def test_site_client_never_reuses_an_idle_backend_socket():
    """tick 间隔长于后端 keep-alive：池化连接到下一轮总是陈旧，复用它会丢掉心跳或领取。"""
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading as _threading
    from dsherp.context_worker import site_client
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_POST(self):
            seen.append(self.client_address)
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=_threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        item={'site':'s','base_url':f'http://127.0.0.1:{server.server_port}','api_key':'k','api_secret':'v'}
        with site_client(item) as client:
            assert client.post('/a',json={}).status_code==200
            assert client.post('/b',json={}).status_code==200
        assert len(seen)==2 and seen[0]!=seen[1],seen
    finally:
        server.shutdown();server.server_close();thread.join()


def test_the_host_circuit_probe_uses_the_direct_provider_address_not_the_container_proxy(tmp_path):
    """Plan-2 re-audit: 3f79ac0 switched the probe to agent_settings(), whose base URL is the
    egress proxy's compose name. That name resolves only inside the agent network, so on the
    host the probe always failed and an opened circuit never closed until a restart."""
    from dsherp import context_worker,runtime_host
    env=tmp_path/'provider.env'
    env.write_text('DEEPSEEK_API_KEY=sk-test-key\nDEEPSEEK_BASE_URL=https://provider.example.test/v1\n')
    probed=[]
    def fake_probe(base_url,api_key):
        probed.append((base_url,api_key));return True
    assert context_worker.host_probe(env,probe=fake_probe)() is True
    assert probed==[('https://provider.example.test/v1','sk-test-key')]
    container=runtime_host.agent_settings(env)
    assert container['DEEPSEEK_BASE_URL']!=probed[0][0] and 'agent-egress' in container['DEEPSEEK_BASE_URL']
    # main() must hand the coordinator this probe, not one built from the container settings.
    source=(context_worker.ROOT/'dsherp/context_worker.py').read_text()
    assert 'host_probe(args.provider_env)' in source
    assert "probe_models(current['DEEPSEEK_BASE_URL']" not in source


PRODUCTION=None
def _production():
    from dsherp import deploy_env
    return deploy_env.settings({'DSHERP_ENV':'prod','DSHERP_PROJECT':'dsherp',
        'DSHERP_BASE_DOMAIN':'tenant.example.com','DSHERP_PLATFORM_SLUG':'platform',
        'DSHERP_IMAGE_TAG':'v0.3.0','DSHERP_IMAGE_REGISTRY':'registry.example.com/dsherp',
        'DSHERP_AGENT_UID':'1000','DSHERP_AGENT_GID':'1000'})


def _host_runner(network_id='db512087a978abcdef0123456789',verdict='blocked',calls=None):
    """Fake docker: network inspects answer the id and gateway; `docker run` is the probe."""
    calls=calls if calls is not None else []
    def runner(command,**kwargs):
        calls.append(command)
        if command[1:3]==['network','inspect'] and '--format' in command:
            template=command[command.index('--format')+1]
            out=network_id if 'Id' in template else '172.20.0.1'
            return subprocess.CompletedProcess(command,0,stdout=out+'\n',stderr='')
        if command[:2]==['docker','run']:
            if verdict is None:
                return subprocess.CompletedProcess(command,125,stdout='',stderr='docker: no such image')
            return subprocess.CompletedProcess(command,0,stdout='DSHERP_ISOLATION '+json.dumps({'gateway':'172.20.0.1','verdict':verdict})+'\n',stderr='')
        return subprocess.CompletedProcess(command,0,stdout='',stderr='')
    runner.calls=calls
    return runner


def test_production_refuses_to_serve_without_the_firewall_record_or_with_a_stale_bridge(tmp_path):
    """Reviewer finding: a missing record was only logged and a present one only compared the
    bridge name. Missing means the unit never ran; stale means a recreated network."""
    from dsherp import context_worker
    production=_production()
    state=tmp_path/'dsherp_agent'
    with pytest.raises(RuntimeError,match='dsherp-agent-firewall'):
        context_worker.prepare_host(runner=_host_runner(),cleanup=lambda runner:None,resolved=production,firewall_state=tmp_path)
    state.write_text('br-000000000000\n')
    with pytest.raises(RuntimeError,match='br-db512087a978'):
        context_worker.prepare_host(runner=_host_runner(),cleanup=lambda runner:None,resolved=production,firewall_state=tmp_path)
    state.write_text('br-db512087a978\n')
    context_worker.prepare_host(runner=_host_runner(),cleanup=lambda runner:None,resolved=production,firewall_state=tmp_path)


def test_production_probes_the_real_isolation_from_a_container_and_refuses_when_the_host_answers(tmp_path):
    """The record proves the unit ran; only a container on the agent network proves the rules
    work. A refused connection is the host's kernel answering, i.e. no DROP rule in the way."""
    from dsherp import context_worker
    production=_production()
    (tmp_path/'dsherp_agent').write_text('br-db512087a978\n')
    for verdict in ('refused','connected','reset','error:OSError'):
        with pytest.raises(RuntimeError,match='网关'):
            context_worker.HostIsolation(production,runner=_host_runner(verdict=verdict),state_dir=tmp_path).verify()
    with pytest.raises(RuntimeError):  # the probe itself could not run: refuse rather than assume
        context_worker.HostIsolation(production,runner=_host_runner(verdict=None),state_dir=tmp_path).verify()
    runner=_host_runner(verdict='blocked')
    isolation=context_worker.HostIsolation(production,runner=runner,state_dir=tmp_path)
    assert isolation.verify()=='db512087a978abcdef0123456789'
    probe=[command for command in runner.calls if command[:2]==['docker','run']][0]
    assert '--network' in probe and probe[probe.index('--network')+1]=='dsherp_agent'
    assert probe[probe.index('--user')+1]=='1000:1000' and '--cap-drop=ALL' in probe and '--read-only' in probe
    assert 'registry.example.com/dsherp/dsherp-worker:v0.3.0' in probe
    assert 'DSHERP_PROBE_GATEWAY=172.20.0.1' in probe


def test_isolation_is_rechecked_every_tick_and_reprobed_when_the_network_changes(tmp_path):
    """A network recreated while the worker runs gets a new bridge; the gate must notice
    without a restart, and claims stop until the unit is restarted."""
    from dsherp import context_worker
    production=_production()
    (tmp_path/'dsherp_agent').write_text('br-db512087a978\n')
    runner=_host_runner()
    isolation=context_worker.HostIsolation(production,runner=runner,state_dir=tmp_path)
    isolation.verify()
    probes=lambda:sum(1 for command in runner.calls if command[:2]==['docker','run'])
    assert isolation.allows() is True and probes()==1  # unchanged network: no second probe
    runner.calls.clear()
    changed=_host_runner(network_id='6e15112f7d1cabcdef0123456789',verdict='blocked',calls=runner.calls)
    isolation.runner=changed
    assert isolation.allows() is False  # record still names the old bridge
    (tmp_path/'dsherp_agent').write_text('br-6e15112f7d1c\n')  # unit restarted by the operator
    assert isolation.allows() is True and probes()==1  # re-probed exactly once for the new network
    assert isolation.allows() is True and probes()==1


def test_the_isolation_probe_calls_only_a_timeout_isolation(capsys,monkeypatch):
    """Reviewer finding: every OSError, a connection reset included, fell through as 'blocked'.
    Only a timeout proves the SYN was dropped; a refusal or reset is the host answering, and
    anything else cannot prove isolation."""
    import errno,socket
    from dsherp import context_worker
    monkeypatch.setenv('DSHERP_PROBE_GATEWAY','172.20.0.1')
    def run(outcomes):
        def fake(address,timeout):
            outcome=outcomes[address[1]]
            if isinstance(outcome,BaseException):raise outcome
            class Sock:
                def close(self):pass
            return Sock()
        monkeypatch.setattr(socket,'create_connection',fake)
        exec(context_worker.ISOLATION_PROBE,{'__name__':'probe'})
        line=[l for l in capsys.readouterr().out.splitlines() if l.startswith('DSHERP_ISOLATION ')][-1]
        return json.loads(line.split(' ',1)[1])['verdict']
    assert run({22:TimeoutError(),9:TimeoutError()})=='blocked'
    assert run({22:ConnectionRefusedError(),9:TimeoutError()})=='refused'
    assert run({22:ConnectionResetError(),9:TimeoutError()})=='reset'
    assert run({22:None,9:TimeoutError()})=='connected'
    assert run({22:OSError(errno.EHOSTUNREACH,'no route'),9:TimeoutError()})=='error:OSError'
    assert run({22:TimeoutError(),9:socket.timeout()})=='blocked'


def test_rules_lost_on_the_same_bridge_are_caught_within_the_probe_interval(tmp_path,capsys):
    """Reviewer finding: with an unchanged network id only the record was re-read, so rules
    flushed from the kernel were never noticed. The probe repeats on a fixed interval while
    healthy and on every check while failing."""
    from dsherp import context_worker
    production=_production()
    (tmp_path/'dsherp_agent').write_text('br-db512087a978\n')
    clock=[0.0];state={'verdict':'blocked'};calls=[]
    def runner(command,**kwargs):
        return _host_runner(verdict=state['verdict'],calls=calls)(command,**kwargs)
    isolation=context_worker.HostIsolation(production,runner=runner,state_dir=tmp_path,clock=lambda:clock[0])
    probes=lambda:sum(1 for command in calls if command[:2]==['docker','run'])
    isolation.verify();assert probes()==1
    state['verdict']='refused'  # rules flushed from the kernel; bridge and record unchanged
    clock[0]=1.0;assert isolation.allows() is True and probes()==1   # inside the interval: not yet noticed
    clock[0]=context_worker.ISOLATION_PROBE_INTERVAL+0.5
    assert isolation.allows() is False and probes()==2               # interval elapsed: probed and refused
    clock[0]+=1;assert isolation.allows() is False and probes()==3    # failing: probed on every check
    state['verdict']='blocked'                                       # operator restarted the unit
    clock[0]+=1;assert isolation.allows() is True and probes()==4
    clock[0]+=1;assert isolation.allows() is True and probes()==4    # healthy again: back to the interval
    err=capsys.readouterr().err
    assert 'host_isolation_failed' in err and 'host_isolation_restored' in err
    assert context_worker.ISOLATION_PROBE_INTERVAL<=60


def test_the_coordinator_claims_nothing_while_host_isolation_fails(tmp_path):
    from dsherp.context_worker import Coordinator
    claim_calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':
            claim_calls.append(1)
            return httpx.Response(200,json={'message':None})
        return httpx.Response(200,json={'message':{'ok':True}})
    client=httpx.Client(transport=httpx.MockTransport(handler),base_url='http://alpha')
    sites=[{'site':'alpha','client':client,'business':{}}]
    gate=[False]
    coordinator=Coordinator(sites,lambda:SETTINGS,2,lambda *a,**k:None,None,lambda:False,tmp_path,isolation=lambda:gate[0])
    assert coordinator.tick(0)==0 and not claim_calls
    gate[0]=True
    coordinator.tick(1)
    assert claim_calls


def test_production_checks_the_release_image_and_the_agent_network_not_a_prepared_volume(tmp_path):
    """生产没有 Runtime 卷：镜像自带运行时，出网只有代理，两者缺一就不该开工。"""
    from dsherp import context_worker,deploy_env
    production=deploy_env.settings({'DSHERP_ENV':'prod','DSHERP_PROJECT':'dsherp',
        'DSHERP_BASE_DOMAIN':'tenant.example.com','DSHERP_PLATFORM_SLUG':'platform',
        'DSHERP_IMAGE_TAG':'v0.3.0','DSHERP_IMAGE_REGISTRY':'registry.example.com/dsherp',
        'DSHERP_AGENT_UID':'1000','DSHERP_AGENT_GID':'1000'})
    checked=[]
    host=_host_runner()
    def runner(command,**kwargs):
        checked.append(tuple(command[1:3])+(command[3],))
        return host(command,**kwargs)
    (tmp_path/'dsherp_agent').write_text('br-db512087a978\n')
    context_worker.prepare_host(runner=runner,cleanup=lambda runner:None,resolved=production,firewall_state=tmp_path)
    assert ('image','inspect','registry.example.com/dsherp/dsherp-worker:v0.3.0') in checked
    assert ('network','inspect','dsherp_agent') in checked
    assert not [row for row in checked if row[0]=='volume']

    development=deploy_env.settings({'DSHERP_ENV':'dev'})
    checked.clear()
    context_worker.prepare_host(runner=runner,cleanup=lambda runner:None,resolved=development)
    assert ('volume','inspect','dsherp-v16-agent-runtime') in checked
    assert ('image','inspect',deploy_env.BASE_IMAGE) in checked
    assert ('network','inspect','dsherp-validation_agent') in checked


def test_a_rejected_success_write_back_is_followed_by_a_failed_one_so_the_run_cannot_hang(tmp_path,capsys):
    """Plan-2 re-audit item 1: when finish_run(Succeeded) is refused the worker logged and
    walked away, leaving the run Running until the lease sweep labelled it '运行已过期'."""
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];body=json.loads(request.content);calls.append((method,body))
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'a'*64,'capability':'cap',
            'domain':'query','budget':{'run_total_seconds':300}}})
        if method=='finish_run' and body['status']=='Succeeded':
            return httpx.Response(417,json={'exception':'frappe.exceptions.ValidationError: 成功结果必须包含实际读取或服务端记录的工具失败'})
        return httpx.Response(200,json={'message':{'status':'Failed'} if method=='finish_run' else {'recorded':1,'last_seq':1}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *a:{'status':'Succeeded','answer':'我无权读取该单据'})
    finishes=[body for method,body in calls if method=='finish_run']
    assert [body['status'] for body in finishes]==['Succeeded','Failed'],finishes
    assert '回写' in finishes[1]['error'] and finishes[1]['answer']==''
    err=capsys.readouterr().err
    assert 'finish_rejected' in err


def test_runtime_diagnostics_are_forwarded_even_when_the_container_exits_cleanly(tmp_path,capsys):
    """An EventFlushFailed diagnostic from a run that still exited 0 was silently dropped."""
    from dsherp import context_worker
    def fake_run(args,**kwargs):
        if list(args)[:3]==['docker','rm','-f']:return subprocess.CompletedProcess(args,0,'','')
        return subprocess.CompletedProcess(args,0,json.dumps({'status':'Succeeded','answer':'ok'}),
            'DSHERP_DIAGNOSTIC {"type":"EventFlushFailed","error":"ReadTimeout"}\nother noise\n')
    original=context_worker.subprocess.run
    context_worker.subprocess.run=fake_run
    try:
        result=context_worker.run_container({'run_id':'r','capability':'c','question':'q','context':{},'domain':'query',
            'resume':False,'runtime_revision':'x','budget':{'run_total_seconds':300}},SETTINGS,tmp_path/'s')
    finally:
        context_worker.subprocess.run=original
    assert result=={'status':'Succeeded','answer':'ok'}
    err=capsys.readouterr().err
    assert 'runtime_diagnostic' in err and 'EventFlushFailed' in err and 'other noise' not in err


def test_a_site_whose_claims_time_out_is_skipped_even_while_its_heartbeats_succeed(tmp_path,capsys):
    """Plan-2 re-audit item 5: one counter fed by both calls meant a heartbeat success (Redis
    only) erased every claim timeout (MariaDB), so a DB-stalled site was never skipped."""
    from dsherp.context_worker import Coordinator
    contacted=[]
    def stalled(request):
        method=request.url.path.rsplit('.',1)[-1];contacted.append(method)
        if method=='claim_run':raise httpx.ReadTimeout('database stalled')
        return httpx.Response(200,json={'message':{'heartbeat':'now'}})
    with _site_client('stalled',stalled) as client:
        sites=[{'site':'stalled','client':client,'business':{}}]
        coordinator=Coordinator(sites,lambda:SETTINGS,1,lambda *a,**k:None,None,lambda:False,tmp_path)
        for round_index in range(6):
            coordinator.tick(now=round_index)
    assert contacted.count('claim_run')<=3,contacted
    assert 'site_skipped' in capsys.readouterr().err
