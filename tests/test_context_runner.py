import json
import threading
import time

import httpx
import pytest
from dsherp import context_runner
from dsherp.context_runner import monitored_run
from dsherp.session_runtime import open_runtime


def test_status_transport_uses_fresh_connections_without_retries():
    from dsherp.context_runner import business_client
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_GET(self):
            seen.append(self.client_address)
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with business_client(f'http://127.0.0.1:{server.server_port}','synthetic') as client:
            assert client.get('/status').status_code==200
            assert client.get('/status').status_code==200
        assert len(seen)==2 and seen[0]!=seen[1]
    finally:server.shutdown();server.server_close();thread.join()


def test_failure_diagnostic_excludes_exception_text_locals_and_credentials():
    from dsherp.context_runner import failure_diagnostic
    import json
    try:
        private_value='synthetic-credential-do-not-log'
        raise RuntimeError(private_value)
    except RuntimeError as error:
        result=failure_diagnostic(error)
    assert result['type']=='RuntimeError'
    assert result['frames'][-1]['function']=='test_failure_diagnostic_excludes_exception_text_locals_and_credentials'
    assert private_value not in json.dumps(result)
    assert all(set(frame)=={'file','function','line'} for frame in result['frames'])


def test_monitored_run_records_tool_and_turn_events_before_returning(model_server,tmp_path):
    import json
    settings,requests,state=model_server
    state['tool_call']={'name':'skill','arguments':json.dumps({'name':'erp-query'})}
    recorded=[]
    with open_runtime(settings,tmp_path,'events-run',resume=False) as runtime:
        result=monitored_run(runtime,'hello','events-run',lambda:{'status':'Running'},record=recorded.append)
    assert result['status']=='Succeeded'
    kinds=[item['kind'] for batch in recorded for item in batch]
    assert 'runtime_tool_call' in kinds and kinds[-1]=='turn_end',kinds
    assert settings['DEEPSEEK_API_KEY'] not in json.dumps(recorded)


def test_monitored_run_records_runtime_failed_when_model_never_completes(model_server,tmp_path):
    settings,requests,state=model_server
    state['finish_reason']='length';state['content']=''
    recorded=[]
    with open_runtime(settings,tmp_path,'failed-run',resume=False) as runtime:
        with pytest.raises(RuntimeError):
            monitored_run(runtime,'hello','failed-run',lambda:{'status':'Running'},record=recorded.append)
    items=[item for batch in recorded for item in batch]
    assert items[-1]['kind']=='runtime_failed' and items[-1]['error_class']=='RuntimeError'
    # 栈帧只走宿主 stderr；事件流对业务用户可见，payload 不带 type/frames。
    assert set(items[-1]['payload'])=={'reason'} and items[-1]['payload']['reason']=='runtime_error'


def test_event_writeback_timeout_does_not_change_successful_run(model_server,tmp_path):
    settings,_,_=model_server
    attempts=[];outcomes=[]
    def handler(request):
        attempts.append(request.extensions['timeout'])
        raise httpx.TimeoutException('synthetic slow event sink',request=request)
    started=time.monotonic()
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler),timeout=30) as client:
        def record(items):
            outcomes.append(context_runner.flush_run_events(client,'RUN','CAP',items))
        with open_runtime(settings,tmp_path,'event-timeout',resume=False) as runtime:
            result=monitored_run(runtime,'hello','event-timeout',lambda:{'status':'Running'},record=record)
    assert result=={'status':'Succeeded','answer':'DSHERP_OK'}
    assert outcomes and all(outcome['error']=='TimeoutException' for outcome in outcomes)
    assert attempts and all(set(timeout.values())=={5.0} for timeout in attempts)
    assert time.monotonic()-started<10


def test_event_mapping_failure_does_not_change_successful_run(model_server,tmp_path,monkeypatch,capsys):
    import json
    settings,_,_=model_server
    recorded=[]
    def fail_mapping(*args):
        raise TypeError('synthetic event payload must not affect the run')
    monkeypatch.setattr(context_runner.run_events,'from_runtime_events',fail_mapping)
    with open_runtime(settings,tmp_path,'mapping-failure',resume=False) as runtime:
        result=monitored_run(runtime,'hello','mapping-failure',lambda:{'status':'Running'},record=recorded.append)
    assert result=={'status':'Succeeded','answer':'DSHERP_OK'}
    assert all(item['kind']!='runtime_failed' for batch in recorded for item in batch)
    lines=capsys.readouterr().err.strip().splitlines()
    assert len(lines)==1 and lines[0].startswith('DSHERP_DIAGNOSTIC ')
    diagnostic=json.loads(lines[0].removeprefix('DSHERP_DIAGNOSTIC '))
    assert diagnostic=={'type':'EventMappingFailed','error':'TypeError'}


def test_cancel_state_stops_actual_model_request(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return {'status':'Cancelling' if state['received'].is_set() else 'Running'}
    try:
        with open_runtime(settings,tmp_path,'cancelled-run',resume=False) as runtime:
            result=monitored_run(runtime,'wait','cancelled-run',status,poll_interval=.05)
        assert result=={'status':'Cancelled','answer':''}
        assert len(requests)==1
    finally:state['release'].set()


def test_status_denial_before_start_calls_no_model(model_server,tmp_path):
    settings,requests,_=model_server
    def status():raise PermissionError('revoked')
    with open_runtime(settings,tmp_path,'denied-run',resume=False) as runtime:
        with pytest.raises(PermissionError):monitored_run(runtime,'wait','denied-run',status)
    assert not requests


def test_cancel_before_start_calls_no_model(model_server,tmp_path):
    settings,requests,_=model_server
    with open_runtime(settings,tmp_path,'early-cancel',resume=False) as runtime:
        assert monitored_run(runtime,'wait','early-cancel',lambda:{'status':'Cancelling'})=={'status':'Cancelled','answer':''}
    assert not requests


def test_revocation_during_model_call_aborts_then_fails(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():
        if state['received'].is_set():raise PermissionError('revoked')
        return {'status':'Running'}
    try:
        with open_runtime(settings,tmp_path,'revoked-run',resume=False) as runtime:
            with pytest.raises(PermissionError):
                monitored_run(runtime,'wait','revoked-run',status,poll_interval=.05)
        assert len(requests)==1
    finally:state['release'].set()


def test_cancel_returns_within_grace_even_if_model_hangs(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return {'status':'Cancelling' if state['received'].is_set() else 'Running'}
    started=time.monotonic()
    try:
        with open_runtime(settings,tmp_path,'hang-cancel',resume=False) as runtime:
            result=monitored_run(runtime,'wait','hang-cancel',status,poll_interval=.05,grace=1)
        assert result=={'status':'Cancelled','answer':''}
        assert time.monotonic()-started<8
    finally:state['release'].set()


def test_run_total_deadline_cancels_and_fails(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    recorded=[]
    try:
        with open_runtime(settings,tmp_path,'deadline-run',resume=False) as runtime:
            with pytest.raises(RuntimeError,match='budget'):
                monitored_run(runtime,'wait','deadline-run',lambda:{'status':'Running'},poll_interval=.05,
                              deadline=time.monotonic()+1,record=recorded.append,grace=1)
        items=[item for batch in recorded for item in batch]
        assert items[-1]['kind']=='runtime_failed'
        assert items[-1]['payload'].get('reason')=='run_total_exceeded'
    finally:state['release'].set()


def test_needs_input_status_stops_gracefully(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():
        return ({'status':'NeedsInput','needs_input':'请告诉我仓库'} if state['received'].is_set()
                else {'status':'Running'})
    try:
        with open_runtime(settings,tmp_path,'needs-input',resume=False) as runtime:
            result=monitored_run(runtime,'wait','needs-input',status,poll_interval=.05,grace=1)
        assert result=={'status':'NeedsInput','answer':'请告诉我仓库'}
    finally:state['release'].set()


def test_cancel_rpc_is_bounded_by_the_grace_budget(model_server,tmp_path):
    """原生取消 RPC 必须自带短超时；否则它继承 90s 模型超时，grace 只约束其后的 join。"""
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    seen=[]
    def status():return {'status':'Cancelling' if state['received'].is_set() else 'Running'}
    try:
        with open_runtime(settings,tmp_path,'bounded-cancel',resume=False) as runtime:
            original=runtime.client.request
            def spy(method,params,**kwargs):
                if method=='dsherp/session/cancel':seen.append(kwargs.get('timeout_seconds'))
                return original(method,params,**kwargs)
            runtime.client.request=spy
            assert monitored_run(runtime,'wait','bounded-cancel',status,poll_interval=.05,grace=2)=={'status':'Cancelled','answer':''}
    finally:state['release'].set()
    assert seen and all(isinstance(value,(int,float)) and 0<value<=2 for value in seen),seen


def test_unsettled_cancel_still_returns_within_grace(model_server,tmp_path):
    """原生取消不确认时仍必须在 grace 内返回 Cancelled：容器由宿主 subprocess 超时与 docker rm -f 收尾。"""
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return {'status':'Cancelling' if state['received'].is_set() else 'Running'}
    started=None
    try:
        with open_runtime(settings,tmp_path,'unsettled-cancel',resume=False) as runtime:
            original=runtime.client.request
            def stubborn(method,params,**kwargs):
                if method!='dsherp/session/cancel':return original(method,params,**kwargs)
                time.sleep(min(kwargs.get('timeout_seconds') or 30,30))
                raise TimeoutError('native cancel did not answer')
            runtime.client.request=stubborn
            started=time.monotonic()
            assert monitored_run(runtime,'wait','unsettled-cancel',status,poll_interval=.05,grace=2)=={'status':'Cancelled','answer':''}
            assert time.monotonic()-started<6
    finally:state['release'].set()


def test_runtime_failed_event_carries_no_stack_frames_to_the_user(model_server,tmp_path):
    """运行事件流对业务用户开放，栈帧只允许留在宿主 stderr 诊断通道。"""
    settings,requests,state=model_server
    state['finish_reason']='length';state['content']=''
    recorded=[]
    with open_runtime(settings,tmp_path,'no-frames',resume=False) as runtime:
        with pytest.raises(RuntimeError):
            monitored_run(runtime,'hello','no-frames',lambda:{'status':'Running'},poll_interval=.05,record=recorded.append,grace=1)
    failures=[item for batch in recorded for item in batch if item['kind']=='runtime_failed']
    assert failures and failures[-1]['error_class']=='RuntimeError'
    serialized=json.dumps(failures,ensure_ascii=False)
    assert 'frames' not in serialized and 'context_runner.py' not in serialized and 'monitored_run' not in serialized
