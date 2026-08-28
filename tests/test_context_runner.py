import threading
import pytest
from dsherp.context_runner import monitored_run
from dsherp.session_runtime import open_runtime


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
