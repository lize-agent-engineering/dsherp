"""OS writer ownership and actual native process recovery, not a retained Session wrapper."""
import json
import subprocess
import sys

import pytest
from deepseek_harness.errors import JsonRpcError

from dsherp import session_runtime


def _settings():
    return {'DEEPSEEK_API_KEY':'k','DSH_MODEL':'m','DEEPSEEK_BASE_URL':'http://provider.invalid'}


def _business_settings():
    return {'DEEPSEEK_API_KEY':'k','DEEPSEEK_BASE_URL':'http://provider.invalid'}


_CLAIMED_BUDGET_FIELDS=('provider','model','model_request_timeout_seconds',
    'model_max_output_tokens_per_call')
_CLAIMED_BUDGET_INT_FIELDS=_CLAIMED_BUDGET_FIELDS[2:]


def _claimed_budget(domain):
    tokens={'query':1024,'configuration':1536,'operation':2560}[domain]
    return {'provider':'deepseek-official','model':'synthetic-site-model',
        'model_request_timeout_seconds':90,
        'model_max_calls':{'operation':15,'query':11,'configuration':8}[domain],
        'model_max_input_bytes_per_call':131072,'model_max_input_bytes_total':786432,
        'model_max_output_tokens_per_call':tokens,
        'model_max_output_tokens_total':{'operation':122880,'query':90112,'configuration':65536}[domain]}


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


@pytest.mark.parametrize('domain,tokens',[('query',1024),('configuration',1536),('operation',2560)])
@pytest.mark.parametrize('settings',[
    _business_settings(),
    {**_business_settings(),'DSH_MODEL':'env-override-model'},
])
def test_claimed_budget_reaches_harness(tmp_path,monkeypatch,domain,tokens,settings):
    captured=_capture_harness(monkeypatch)
    budget=_claimed_budget(domain)
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':domain,'budget':budget}))
    with pytest.raises(RuntimeError,match='harness-captured'):
        with session_runtime.open_runtime(settings,tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured['provider']==budget['provider']=='deepseek-official'
    assert captured['model']==budget['model']=='synthetic-site-model'
    assert captured['max_tokens']==budget['model_max_output_tokens_per_call']==tokens
    assert captured['request_timeout_seconds']==budget['model_request_timeout_seconds']==90


def test_context_runtime_without_run_config_keeps_90s(tmp_path,monkeypatch):
    captured=_capture_harness(monkeypatch)
    with pytest.raises(RuntimeError,match='harness-captured'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False):
            pass
    assert captured['request_timeout_seconds']==90


@pytest.mark.parametrize('field',_CLAIMED_BUDGET_FIELDS)
def test_claimed_budget_missing_field_fails_before_harness(tmp_path,monkeypatch,field):
    captured=_capture_harness(monkeypatch)
    budget=_claimed_budget('query')
    del budget[field]
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':'query','budget':budget}))
    with pytest.raises(ValueError,match='Missing run budget'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured=={}


@pytest.mark.parametrize('field',_CLAIMED_BUDGET_INT_FIELDS)
@pytest.mark.parametrize('value',[True,False,'90',90.0,0,-1])
def test_invalid_budget_field_fails_before_harness(tmp_path,monkeypatch,field,value):
    captured=_capture_harness(monkeypatch)
    budget=_claimed_budget('query')
    budget[field]=value
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':'query','budget':budget}))
    with pytest.raises(ValueError,match='Invalid run budget'):
        with session_runtime.open_runtime(_settings(),tmp_path,'s',resume=False,run_config=path):
            pass
    assert captured=={}


@pytest.mark.parametrize('field,value',[
    ('provider',''),('provider',' '),('provider','other'),('provider',True),
    ('model',''),('model',' '),('model',True),
])
def test_invalid_budget_policy_fails_before_harness(tmp_path,monkeypatch,field,value):
    captured=_capture_harness(monkeypatch)
    budget=_claimed_budget('query')
    budget[field]=value
    path=tmp_path/'run.json'
    path.write_text(json.dumps({'domain':'query','budget':budget}))
    with pytest.raises(ValueError,match='Invalid run budget'):
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
