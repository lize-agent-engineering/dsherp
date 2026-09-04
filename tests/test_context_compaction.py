"""Native automatic compaction through the real llm/stream authorization seam."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from dsherp.session_runtime import open_runtime
from dsherp.runtime_revision import configuration_revision


@pytest.mark.parametrize('domain,deny_summary,pressure',[
    ('query',False,True),('query',True,True),('query',False,False),
    ('operation',False,True),
])
def test_native_auto_compaction_cannot_bypass_authorization(model_server,tmp_path,domain,deny_summary,pressure):
    observed=[];reports=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('.record_run_event'):
                reports.append(payload);self.send_response(200);self.end_headers();return
            observed.append(payload)
            self.send_response(403 if deny_summary and payload['purpose']=='compaction' else 200)
            self.end_headers();self.wfile.write(b'{"message":{"allowed":true}}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    settings,requests,state=model_server
    multiplier=4 if pressure else 1
    state.update(content='Business context. '*(350*multiplier),summary_content='Item I-44; source version v2; continue read-only inquiry.')
    config=tmp_path/'run.json';config.write_text(json.dumps({**settings,
        'domain':domain,'budget':{'model_request_timeout_seconds':90,'run_total_seconds':300},
        'runtime_revision':configuration_revision(settings),'run_id':'test','capability':'test','site':'synthetic',
        'business_url':f'http://127.0.0.1:{server.server_port}'}));config.chmod(0o600)
    try:
        with open_runtime(settings,tmp_path/'native','compression',resume=False,run_config=config) as runtime:
            for index in range(4):
                result=runtime.run(f'Round {index}: Item I-44 modified v2. '+'Business question. '*(330*multiplier),session_id='compression')
                if result.finish_reason!='completed':break
        summaries=[item for item in observed if item['purpose']=='compaction']
        if not pressure:
            assert not summaries,'Ordinary multi-turn context should not repeatedly summarize'
            assert result.finish_reason=='completed'
            return
        assert summaries,'Native pressure never invoked compaction'
        expected_summary_tokens=3072 if domain=='operation' else 2048
        assert all(item['max_output_tokens']==expected_summary_tokens for item in summaries)
        if deny_summary:
            assert result.finish_reason!='completed'
            assert not any(state['compaction_calls'])
            assert observed[-1]['purpose']=='compaction'
        else:
            assert result.finish_reason=='completed'
            assert any(state['compaction_calls'])
            assert 'compacted-summary' in str(requests[-1]['messages'])
            with open_runtime(settings,tmp_path/'native','compression',resume=True,run_config=config) as restored:
                continued=restored.run('Continue from the saved Item source.',session_id='compression')
            assert continued.finish_reason=='completed'
            assert 'compacted-summary' in str(requests[-1]['messages'])
            assert 'source version v2' in str(requests[-1]['messages'])
    finally:
        config.unlink();server.shutdown();server.server_close();thread.join()
