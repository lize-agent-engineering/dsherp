import asyncio
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from dsherp.agent_worker import docker_command, load_settings, run_container
from dsherp.task_mcp import create_server
from dsherp.task_runner import run_task


def test_explicit_settings_never_expand_shell_or_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET', 'do-not-expand')
    path = tmp_path / '.env'
    path.write_text('DEEPSEEK_API_KEY="${SECRET}"\nDSH_MODEL=m\nDEEPSEEK_BASE_URL=https://api.deepseek.com\nUNRELATED=x\n')
    assert load_settings(path) == {'DEEPSEEK_API_KEY': '${SECRET}', 'DSH_MODEL': 'm', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com'}
    path.write_text('DSH_MODEL=m')
    with pytest.raises(ValueError):
        load_settings(path)


def test_container_mounts_only_code_and_current_secret(tmp_path):
    command = docker_command(tmp_path, tmp_path / 'one.json', 'task-123')
    joined = ' '.join(command)
    assert '--read-only' in command and '--pull=never' in command
    assert '384m' in command and '0.1' in command
    assert f'{tmp_path}/dsherp:/opt/dsherp/dsherp:ro' in command
    assert f'{tmp_path}/config:/opt/dsherp/config:ro' in command
    assert 'docker.sock' not in joined and '.env' not in joined
    assert 'dsherp-agent-runtime:/opt/runtime:ro' in command


def test_timeout_removes_container_without_retry(tmp_path, monkeypatch):
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == 'run':
            raise subprocess.TimeoutExpired(command, 120)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        run_container({'task_id': 't', 'question': 'q', 'capability': 'cap'}, {}, tmp_path)
    assert [c[1] for c in calls] == ['run', 'rm']
    assert not list((tmp_path / 'work').glob('agent-*'))


def test_mcp_has_exact_scoped_tools_and_posts_capability():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'name': 'ITEM'}})
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
        server = create_server(client, 'task', 'cap')
        names = asyncio.run(server.list_tools())
        assert {t.name for t in names} == {'erp_read_schema', 'erp_read_record', 'erp_search_records'}
        assert all(not ({'enterprise', 'user', 'url'} & set(t.inputSchema['properties'])) for t in names)
        asyncio.run(server.call_tool('erp_read_record', {'doctype': 'Item', 'name': 'ITEM'}))
    assert calls == [{'task_id': 'task', 'capability': 'cap', 'tool': 'erp_read_record', 'arguments': {'doctype': 'Item', 'name': 'ITEM'}}]


def test_real_sdk_has_only_task_tools_and_sanitized_environment(model_server, tmp_path, monkeypatch):
    settings, requests, _ = model_server
    monkeypatch.setenv('DSHERP_ADMIN_SECRET', 'must-not-inherit')
    spawned_envs = []
    original_popen = subprocess.Popen
    def observe_spawn(*args, **kwargs):
        spawned_envs.append(dict(kwargs.get('env') or os.environ))
        return original_popen(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', observe_spawn)
    secret = tmp_path / 'task.json'
    secret.write_text(json.dumps({'task_id': 't', 'capability': 'c', 'question': 'hello', **settings}))
    answer = run_task(secret, tmp_path)
    assert answer == 'DSHERP_OK'
    assert os.environ['DSHERP_ADMIN_SECRET'] == 'must-not-inherit'
    assert spawned_envs and all('DSHERP_ADMIN_SECRET' not in env for env in spawned_envs)
    assert {t['function']['name'] for t in requests[0]['tools']} == {
        'mcp__erp__erp_read_schema', 'mcp__erp__erp_read_record', 'mcp__erp__erp_search_records'}


def test_empty_claim_response_is_idle_but_other_missing_messages_fail():
    from dsherp.task_mcp import post
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={}))) as client:
        assert post(client, 'claim_task') is None
        with pytest.raises(KeyError):
            post(client, 'finish_task')


@pytest.mark.parametrize('content,reason', [('', 'stop'), ('partial', 'length')])
def test_real_sdk_rejects_empty_or_truncated_answer(model_server, tmp_path, content, reason):
    settings, _, state = model_server
    state.update(content=content, finish_reason=reason)
    secret = tmp_path / 'task.json'
    secret.write_text(json.dumps({'task_id': 't', 'capability': 'c', 'question': 'hello', **settings}))
    with pytest.raises(RuntimeError):
        run_task(secret, tmp_path)


def test_timeout_finishes_failed_and_next_job_can_run_without_replay(monkeypatch):
    import dsherp.agent_worker as worker
    jobs = iter([{'task_id': 'one', 'question': 'q', 'capability': 'c1'},
                 {'task_id': 'two', 'question': 'q', 'capability': 'c2'}])
    invoked, finished = [], []
    def handler(request):
        method = request.url.path.rsplit('.', 1)[-1]
        if method == 'claim_task':
            return httpx.Response(200, json={'message': next(jobs)})
        if method == 'finish_task':
            finished.append(json.loads(request.content))
        return httpx.Response(200, json={'message': True})
    def execute(task, settings):
        invoked.append(task['task_id'])
        if task['task_id'] == 'one':
            raise subprocess.TimeoutExpired('docker', 120)
        return 'answer'
    monkeypatch.setattr(worker, 'run_container', execute)
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
        assert worker.run_once(client, {})
        assert worker.run_once(client, {})
    assert invoked == ['one', 'two']
    assert [x['status'] for x in finished] == ['Failed', 'Succeeded']


@pytest.mark.parametrize('status_code', [403, 417])
def test_rejected_success_is_failed_then_worker_handles_next_job(monkeypatch, status_code):
    import dsherp.agent_worker as worker
    jobs = iter([{'task_id': 'one', 'question': 'q', 'capability': 'c1'},
                 {'task_id': 'two', 'question': 'q', 'capability': 'c2'}])
    invoked, finished = [], []
    def handler(request):
        method = request.url.path.rsplit('.', 1)[-1]
        if method == 'claim_task':
            return httpx.Response(200, json={'message': next(jobs)})
        if method == 'finish_task':
            data = json.loads(request.content)
            finished.append(data)
            if data['task_id'] == 'one' and data['status'] == 'Succeeded':
                return httpx.Response(status_code, json={})
        return httpx.Response(200, json={'message': True})
    def execute(task, settings):
        invoked.append(task['task_id'])
        return 'answer'
    monkeypatch.setattr(worker, 'run_container', execute)
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
        assert worker.run_once(client, {})
        assert worker.run_once(client, {})
    assert invoked == ['one', 'two']
    assert [(x['task_id'], x['status']) for x in finished] == [
        ('one', 'Succeeded'), ('one', 'Failed'), ('two', 'Succeeded')]


def test_uncertain_success_response_is_not_overwritten_or_reexecuted(monkeypatch):
    import dsherp.agent_worker as worker
    finished = []
    def handler(request):
        method = request.url.path.rsplit('.', 1)[-1]
        if method == 'claim_task':
            return httpx.Response(200, json={'message': {'task_id': 'one', 'question': 'q', 'capability': 'c'}})
        if method == 'finish_task':
            finished.append(json.loads(request.content))
            return httpx.Response(500, json={})
        return httpx.Response(200, json={'message': True})
    monkeypatch.setattr(worker, 'run_container', lambda task, settings: 'answer')
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):
            worker.run_once(client, {})
    assert len(finished) == 1 and finished[0]['status'] == 'Succeeded'
