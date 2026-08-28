import os
import subprocess
import sys

import pytest
from deepseek_harness.errors import HarnessError

from dsherp.dsh_probe import run_probe


@pytest.mark.parametrize("field", ["DEEPSEEK_API_KEY", "DSH_MODEL", "DEEPSEEK_BASE_URL"])
@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_settings_fail_before_launch(field, value, tmp_path, runtime_processes):
    settings = {"DEEPSEEK_API_KEY": "synthetic", "DSH_MODEL": "deepseek-v4-flash", "DEEPSEEK_BASE_URL": "http://127.0.0.1:1/v1"}
    if value is None:
        del settings[field]
    else:
        settings[field] = value
    with pytest.raises(ValueError, match=field):
        run_probe(settings, tmp_path)
    assert not runtime_processes
    assert not list(tmp_path.iterdir())


def test_real_runtime_zero_tools_events_and_shutdown(model_server, tmp_path, runtime_processes):
    settings, requests, _ = model_server
    notifications = []
    result = run_probe(settings, tmp_path, notifications.append)
    assert result.final_response == "DSHERP_OK"
    assert result.finish_reason == "completed"
    assert any(e["type"] == "turn/end" for e in result.events)
    assert notifications
    assert len(requests) == 1
    assert requests[0].get("tools", []) == []
    assert requests[0]["max_tokens"] == 64
    assert runtime_processes and all(p.poll() is not None for p in runtime_processes)
    assert not list(tmp_path.iterdir())


def test_callback_failure_reaps_real_runtime(model_server, tmp_path, runtime_processes):
    settings, _, _ = model_server

    def fail(_notification):
        raise RuntimeError("notification consumer failed")

    with pytest.raises(RuntimeError, match="notification consumer failed"):
        run_probe(settings, tmp_path, fail)
    assert runtime_processes and all(p.poll() is not None for p in runtime_processes)
    assert not list(tmp_path.iterdir())


def test_token_limit_is_not_success(model_server, tmp_path, runtime_processes):
    settings, _, state = model_server
    state["finish_reason"] = "length"
    with pytest.raises(RuntimeError):
        run_probe(settings, tmp_path)
    assert runtime_processes and all(p.poll() is not None for p in runtime_processes)


def test_initialization_failure_reaps_real_runtime(model_server, tmp_path, runtime_processes, monkeypatch):
    settings, requests, _ = model_server
    # Missing configuration fails CLI boot before a model request is possible.
    monkeypatch.setattr("dsherp.dsh_probe.CONFIG", tmp_path / "missing.yml")
    with pytest.raises(HarnessError):
        run_probe(settings, tmp_path)
    assert not requests
    assert runtime_processes and all(p.poll() is not None for p in runtime_processes)
    assert not list(tmp_path.iterdir())


def test_cli_missing_credential_has_nonzero_exit_without_secret():
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    result = subprocess.run([sys.executable, "-m", "dsherp.dsh_probe"], env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert "DEEPSEEK_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_emits_only_sanitized_result(model_server):
    settings, requests, _ = model_server
    result = subprocess.run([sys.executable, "-m", "dsherp.dsh_probe"], env={**os.environ, **settings}, text=True, capture_output=True, timeout=45)
    assert result.returncode == 0
    assert '"finish_reason": "completed"' in result.stdout
    assert '"turn/end"' in result.stdout
    assert settings["DEEPSEEK_API_KEY"] not in result.stdout + result.stderr
    assert len(requests) == 1


@pytest.mark.parametrize("content", ["", "unexpected response"])
def test_wrong_response_is_not_success(model_server, tmp_path, runtime_processes, content):
    settings, _, state = model_server
    state["content"] = content
    with pytest.raises(RuntimeError):
        run_probe(settings, tmp_path)
    assert runtime_processes and all(p.poll() is not None for p in runtime_processes)
