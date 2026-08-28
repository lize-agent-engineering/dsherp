import threading
import pytest
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


def test_cancel_state_stops_actual_model_request(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return 'Cancelling' if state['received'].is_set() else 'Running'
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
        assert monitored_run(runtime,'wait','early-cancel',lambda:'Cancelling')=={'status':'Cancelled','answer':''}
    assert not requests


def test_revocation_during_model_call_aborts_then_fails(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():
        if state['received'].is_set():raise PermissionError('revoked')
        return 'Running'
    try:
        with open_runtime(settings,tmp_path,'revoked-run',resume=False) as runtime:
            with pytest.raises(PermissionError):
                monitored_run(runtime,'wait','revoked-run',status,poll_interval=.05)
        assert len(requests)==1
    finally:state['release'].set()
