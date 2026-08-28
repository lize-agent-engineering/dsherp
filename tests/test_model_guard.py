"""Real Runtime denies provider dispatch when business authorization rejects."""
import json
import threading
import pytest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dsherp.session_runtime import open_runtime
from dsherp.runtime_revision import configuration_revision


@pytest.mark.parametrize('mode',['denied','drift','allow'])
def test_business_denial_prevents_actual_provider_request(model_server,tmp_path,mode):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            if mode in ('drift','allow'):
                if mode=='drift':config.write_text(config.read_text()+'\n')
                self.send_response(200);self.end_headers();self.wfile.write(b'{"message":{"allowed":true}}')
            else:
                self.send_response(403);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    settings,requests,_=model_server
    config=tmp_path/'run.json'
    config.write_text(json.dumps({**settings,'runtime_revision':configuration_revision(settings),'run_id':'synthetic','capability':'synthetic','site':'synthetic',
        'business_url':f'http://127.0.0.1:{server.server_port}'}));config.chmod(0o600)
    try:
        with open_runtime(settings,tmp_path/'native','denied',resume=False,run_config=config) as runtime:
            result=runtime.run('Do not transmit this without permission',session_id='denied')
            second=runtime.run('Do not continue after rejection',session_id='denied')
        if mode=='allow':
            assert result.finish_reason==second.finish_reason=='completed'
            assert len(requests)==len(observed)==2
        else:
            assert result.finish_reason!='completed'
            assert second.finish_reason!='completed'
            assert not requests
            assert len(observed)==1
        assert observed[0][0].endswith('.reserve_model_call')
        metadata=observed[0][1]
        assert metadata['purpose']=='conversation' and metadata['max_output_tokens']==2048
        assert metadata['input_bytes']>0
        assert 'Do not transmit' not in json.dumps(metadata)
    finally:
        config.unlink();server.shutdown();server.server_close();thread.join()
