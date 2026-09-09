"""Real Runtime denies provider dispatch when business authorization rejects."""
import json
import threading
import pytest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dsherp.session_runtime import open_runtime
from dsherp.runtime_revision import configuration_revision


# The deployment identity is computed on the host and travels inside run.json.
SYNTHETIC_DEPLOYMENT='a1'*32


def _provider_settings(settings):
    return {**{key:settings[key] for key in ('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL')},
            'deployment_digest':SYNTHETIC_DEPLOYMENT}


def _complete_budget(domain,model):
    operation=domain=='operation'
    return {'model_request_timeout_seconds':90,'lease_seconds':180,'lease_renew_below_seconds':90,
        'queue_expires_seconds':600,'heartbeat_stale_seconds':60,
        'run_total_seconds':600 if operation else 300,'site_concurrency':1,
        'provider':'deepseek-official','model':model,
        'model_max_calls':15 if operation else 11,
        'model_max_input_bytes_per_call':131072,'model_max_input_bytes_total':786432,
        'model_max_output_tokens_per_call':8192,
        'model_max_output_tokens_total':61440 if operation else 24576}


@pytest.mark.parametrize('mode',['denied','drift','allow','skill','operation','loop'])
def test_business_denial_prevents_actual_provider_request(model_server,tmp_path,mode):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            if mode=='loop':
                # What the Site does once it has written `loop_detected`: the first
                # reservation was already granted, every later one is refused. Nothing kills
                # the container directly - this refusal is what ends the run.
                reserved=[item for item in observed if item[0].endswith('.reserve_model_call')]
                if self.path.endswith('.reserve_model_call') and len(reserved)>1:
                    self.send_response(417);self.end_headers()
                    self.wfile.write(b'{"exc_type":"ValidationError"}')
                    return
                self.send_response(200);self.end_headers();self.wfile.write(b'{"message":{"allowed":true}}')
                return
            if mode in ('drift','allow','skill','operation'):
                if mode=='drift':config.write_text(config.read_text()+'\n')
                self.send_response(200);self.end_headers();self.wfile.write(b'{"message":{"allowed":true}}')
            else:
                self.send_response(403);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    settings,requests,state=model_server
    model=settings['DSH_MODEL']
    settings=_provider_settings(settings)
    if mode=='skill':state['tool_call']={'name':'skill','arguments':json.dumps({'name':'erp-query'})}
    if mode=='operation':state['tool_call']={'name':'skill','arguments':json.dumps({'name':'erp-operation'})}
    personal=tmp_path/'native/.agents/skills/personal';personal.mkdir(parents=True)
    (personal/'SKILL.md').write_text('---\nname: personal\ndescription: PERSONAL_SKILL_FORBIDDEN\n---\nNot authorized')
    domain='operation' if mode=='operation' else 'query'
    budget=_complete_budget(domain,model)
    config=tmp_path/'run.json'
    config.write_text(json.dumps({**settings,'domain':domain,'budget':budget,
        'runtime_revision':configuration_revision(settings),'run_id':'synthetic','capability':'synthetic','site':'synthetic',
        'business_url':f'http://127.0.0.1:{server.server_port}'}));config.chmod(0o600)
    assert 'DSH_MODEL' not in json.loads(config.read_text())
    try:
        with open_runtime(settings,tmp_path/'native','denied',resume=False,run_config=config) as runtime:
            result=runtime.run('Do not transmit this without permission',session_id='denied')
            second=runtime.run('Do not continue after rejection',session_id='denied')
        if mode in ('allow','skill','operation'):
            assert result.finish_reason==second.finish_reason=='completed'
            authorizations=[item for item in observed if item[0].endswith('.reserve_model_call')]
            reports=[item for item in observed if item[0].endswith('.record_run_event')]
            assert len(requests)==len(authorizations)==len(reports)==(3 if mode in ('skill','operation') else 2)
            assert all(item[1]['events'][0]['kind']=='model_response' for item in reports)
            expected={'skill','mcp__erp__erp_read_schema','mcp__erp__erp_read_record','mcp__erp__erp_search_records','mcp__erp__erp_request_input'}
            if mode=='operation':expected.update({'mcp__erp__erp_propose_update','mcp__erp__erp_propose_create','mcp__erp__erp_propose_action','mcp__erp__erp_propose_fill','mcp__erp__erp_propose_make'})
            assert {tool['function']['name'] for tool in requests[0]['tools']}==expected
            assert 'PERSONAL_SKILL_FORBIDDEN' not in json.dumps(requests)
            if mode=='skill':
                assert '业务只读查询' in str(requests[1]['messages'])
                assert '工具错误与做不了的出口' in str(requests[1]['messages'])
            if mode=='operation':
                assert '业务操作提案' in str(requests[1]['messages'])
                assert '工具错误与做不了的出口' in str(requests[1]['messages'])
                assert 'erp-query' not in str(requests[0]['messages'])
            # A3：技能摘要与信封规则都在 **system** 段里，压缩掉不了、模型也不必先去取。
            system=requests[0]['messages'][0]
            assert system['role']=='system'
            text=system['content']
            expected_skill=('业务技能：erp-operation v2.3.0' if mode=='operation'
                            else '业务技能：erp-query v1.4.0')
            assert expected_skill in text
            assert 'untrusted' in text
            for label in ('"erp"','"erp-server"','"page"'):
                assert label in text, label
            # 只放摘要：正文仍然只在模型主动调 skill 之后才出现（既有断言保持不变）。
            assert '工具错误与做不了的出口' not in text
        elif mode=='loop':
            # The turn that had its reservation is allowed to finish; the next one gets no
            # provider request at all. That gap is the whole point of stopping a loop by
            # poisoning the authorization: no fourth call is paid for.
            assert result.finish_reason=='completed'
            assert second.finish_reason!='completed'
            assert len(requests)==1
            assert len([item for item in observed if item[0].endswith('.reserve_model_call')])==2
        else:
            assert result.finish_reason!='completed'
            assert second.finish_reason!='completed'
            assert not requests
            assert len(observed)==1
        assert observed[0][0].endswith('.reserve_model_call')
        metadata=observed[0][1]
        assert metadata['purpose']=='conversation'
        assert metadata['provider']==budget['provider']=='deepseek-official'
        assert metadata['model']==budget['model']==model
        assert metadata['max_output_tokens']==budget['model_max_output_tokens_per_call']
        assert metadata['claimed_budget']==budget
        assert metadata['input_bytes']>0
        assert 'Do not transmit' not in json.dumps(metadata)
    finally:
        config.unlink();server.shutdown();server.server_close();thread.join()


def test_the_guard_and_the_prompt_plugin_derive_the_same_pinned_marker():
    """A3 / spec:163. The property that matters — "the summary did not load" costs zero
    provider requests — is asserted in `runtime/model-guard.test.cjs`, where the guard can be
    handed a system prompt without the marker directly. It is deliberately **not** simulated
    here: doing so from Python would need a test-only switch in the plugin that turns the
    section off, and a guard with a documented way to disable it is not a guard.

    What this asserts instead is the other half, which only a real assembly can show: the two
    derivations of the pinned marker — model-guard.cjs from the manifest, prompt-sections.cjs
    from the manifest plus the SKILL.md frontmatter — agree for every domain. If they ever
    drift, every run of that domain stops, so the agreement is the thing to keep tested.
    """
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = (
        "const s=require('./runtime/prompt-sections.cjs');"
        "const out={};"
        "for (const d of ['query','operation','configuration']){"
        "  out[d]={marker:s.skillMarker('.',d), section:s.skillSection('.',d)};"
        "}"
        "console.log(JSON.stringify(out));")
    done = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    derived = json.loads(done.stdout)
    manifest = json.loads((root / 'config/business-skills.json').read_text(encoding='utf-8'))
    versions = {row['name']: row['version'] for row in manifest['skills']}
    for domain, values in derived.items():
        expected = f'业务技能：erp-{domain} v{versions["erp-" + domain]}'
        assert values['marker'] == expected, domain
        assert values['section'].startswith(expected), domain
        # A summary, never the body: the body is what the `skill` tool is for (spec:154).
        assert '工具错误与做不了的出口' not in values['section'], domain
