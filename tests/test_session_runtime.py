"""OS writer ownership and actual native process recovery, not a retained Session wrapper."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from deepseek_harness.errors import JsonRpcError

from dsherp import session_runtime


def _settings():
    return {'DEEPSEEK_API_KEY':'k','DSH_MODEL':'m','DEEPSEEK_BASE_URL':'http://provider.invalid'}


def _capture_harness(monkeypatch):
    captured={}
    class Fake:
        def __init__(self,**kwargs):
            captured.update(kwargs)
            raise RuntimeError('harness-captured')
    monkeypatch.setattr(session_runtime,'DeepSeekHarness',Fake)
    return captured


def test_run_config_without_budget_fails_before_harness(tmp_path,monkeypatch):
    captured=_capture_harness(monkeypatch)
    path=tmp_path/'run.json'
    path.write_text('{"domain":"query"}')
    with pytest.raises(ValueError,match='Missing run budget'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured=={}


def test_claimed_budget_timeout_reaches_harness(tmp_path,monkeypatch):
    captured=_capture_harness(monkeypatch)
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':'operation','budget':{'model_request_timeout_seconds':90}}))
    with pytest.raises(RuntimeError,match='harness-captured'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured['request_timeout_seconds']==90


def test_context_runtime_without_run_config_keeps_90s(tmp_path,monkeypatch):
    captured=_capture_harness(monkeypatch)
    with pytest.raises(RuntimeError,match='harness-captured'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False):
            pass
    assert captured['request_timeout_seconds']==90


@pytest.mark.parametrize('timeout',[True,False,'90',90.0,0,-1])
def test_invalid_budget_timeout_is_missing_run_budget(tmp_path,monkeypatch,timeout):
    captured=_capture_harness(monkeypatch)
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':'query','budget':{'model_request_timeout_seconds':timeout}}))
    with pytest.raises(ValueError,match='Missing run budget'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured=={}


def test_other_process_cannot_write_and_process_death_releases_lock(tmp_path):
    script="""
from pathlib import Path
import sys
from dsherp.session_runtime import session_writer
with session_writer(Path(sys.argv[1])):
    print('owned',flush=True)
    sys.stdin.read()
"""
    proc=subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='owned'
        with pytest.raises(RuntimeError,match='writer'):
            with session_runtime.session_writer(tmp_path):
                pytest.fail('second writer acquired an occupied session')
        proc.kill();proc.wait(timeout=5)
        with session_runtime.session_writer(tmp_path):
            pass
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=5)
        proc.stdin.close();proc.stdout.close();proc.stderr.close()


def test_lock_release_after_exception_and_no_symlink_lock(tmp_path):
    with pytest.raises(ValueError):
        with session_runtime.session_writer(tmp_path):raise ValueError('synthetic')
    with session_runtime.session_writer(tmp_path):pass
    link=tmp_path/'other';link.mkdir();(link/'writer.lock').symlink_to(tmp_path/'writer.lock')
    with pytest.raises(OSError):
        with session_runtime.session_writer(link):pass


def test_managed_runtime_releases_lock_and_restores_native_history(model_server,tmp_path,runtime_processes):
    settings,requests,_=model_server
    with session_runtime.open_runtime(settings,tmp_path,'conversation-one',resume=False) as runtime:
        assert runtime.run('Remember RECOVERY-847',session_id='conversation-one').finish_reason=='completed'
        with pytest.raises(RuntimeError,match='writer'):
            with session_runtime.open_runtime(settings,tmp_path,'conversation-one',resume=True):pass
    assert runtime_processes[0].poll()==0
    with session_runtime.open_runtime(settings,tmp_path,'conversation-one',resume=True) as runtime:
        assert runtime.run('Recall prior marker',session_id='conversation-one').finish_reason=='completed'
    assert len(runtime_processes)==2 and all(p.poll()==0 for p in runtime_processes)
    assert 'RECOVERY-847' in str(requests[-1]['messages'])


def test_failed_native_open_releases_process_and_writer(model_server,tmp_path,runtime_processes):
    settings,requests,_=model_server
    with pytest.raises(JsonRpcError):
        with session_runtime.open_runtime(settings,tmp_path,'missing',resume=True):
            pytest.fail('missing native session opened')
    assert runtime_processes and all(p.poll()==0 for p in runtime_processes)
    with session_runtime.session_writer(tmp_path):pass
    assert not requests


def test_native_discovery_selects_explicit_create_then_resume(model_server,tmp_path):
    settings,requests,_=model_server
    with session_runtime.open_runtime(settings,tmp_path,'discovered',resume='inspect') as runtime:
        runtime.run('Remember DISCOVERY-732',session_id='discovered')
    with session_runtime.open_runtime(settings,tmp_path,'discovered',resume='inspect') as runtime:
        runtime.run('Recall',session_id='discovered')
    assert 'DISCOVERY-732' in str(requests[-1]['messages'])
