"""Real pinned Runtime + project protocol. Only the model HTTP server is synthetic."""
from pathlib import Path
import uuid

from deepseek_harness import DeepSeekHarness
from deepseek_harness.errors import JsonRpcError
from pydantic import BaseModel
import pytest

ROOT = Path(__file__).resolve().parents[1]

class Opened(BaseModel):
    sessionId: str
    source: str

class Cancelled(BaseModel):
    sessionId: str
    status: str

def harness(settings, path):
    return DeepSeekHarness(provider='deepseek-official', model=settings['DSH_MODEL'],
        api_key=settings['DEEPSEEK_API_KEY'],base_url=settings['DEEPSEEK_BASE_URL'],
        cordis=str(ROOT/'config/dsh-context.yml'),cwd=str(path),runtime_cwd=str(path),
        session_root=str(path/'sessions'),max_tokens=256,request_timeout_seconds=10,
        shutdown_timeout_seconds=5)

def open_session(h, session_id, resume=False):
    h.start()
    return h.client.request('dsherp/session/open',{'sessionId':session_id,'resume':resume},response_model=Opened)

def test_native_resume_across_processes_retains_actual_prior_turn(model_server,tmp_path,runtime_processes):
    settings,requests,_=model_server
    session_id='context-'+uuid.uuid4().hex
    with harness(settings,tmp_path) as first:
        assert open_session(first,session_id).source=='startup'
        assert first.run('Remember the synthetic marker ALPHA-493.',session_id=session_id).finish_reason=='completed'
    assert runtime_processes[0].poll()==0
    with harness(settings,tmp_path) as second:
        assert open_session(second,session_id,True).source=='resume'
        assert second.run('What marker did I provide?',session_id=session_id).finish_reason=='completed'
    assert len(runtime_processes)==2 and all(p.poll()==0 for p in runtime_processes)
    assert len(requests)==2
    assert any('ALPHA-493' in str(m.get('content')) for m in requests[1]['messages'] if m['role']=='user')
    assert any(m['role']=='assistant' for m in requests[1]['messages'])

def test_missing_resume_fails_without_model_call_or_implicit_create(model_server,tmp_path):
    settings,requests,_=model_server
    with harness(settings,tmp_path) as h:
        with pytest.raises(JsonRpcError):open_session(h,'missing-session',True)
    assert requests==[]

def test_prompt_requires_explicit_open_and_repeated_open_is_rejected(model_server,tmp_path):
    settings,requests,_=model_server
    with harness(settings,tmp_path) as h:
        with pytest.raises(JsonRpcError):h.client.session_prompt('not-open',[{'type':'text','text':'No implicit create'}])
        open_session(h,'one')
        with pytest.raises(JsonRpcError):open_session(h,'one')
    assert requests==[]

def test_create_cannot_overwrite_persisted_session(model_server,tmp_path):
    settings,requests,_=model_server
    with harness(settings,tmp_path) as h:
        open_session(h,'persisted')
        h.run('First saved turn',session_id='persisted')
    with harness(settings,tmp_path) as h:
        with pytest.raises(JsonRpcError):open_session(h,'persisted')
    assert len(requests)==1

def test_cancel_aborts_actual_inflight_model_without_new_request(model_server,tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    with harness(settings,tmp_path) as h:
        open_session(h,'cancel-test')
        with ThreadPoolExecutor(max_workers=1) as pool:
            running=pool.submit(h.run,'Wait for cancellation',session_id='cancel-test')
            try:
                assert state['received'].wait(timeout=5)
                result=h.client.request('dsherp/session/cancel',{'sessionId':'cancel-test'},response_model=Cancelled)
                assert result.status=='idle'
                completed=running.result(timeout=5)
                assert completed.finish_reason=='aborted'
            finally:
                state['release'].set()
    assert len(requests)==1
