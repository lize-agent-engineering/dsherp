"""Real per-task Docker runtime, MCP, platform and ERP; only model is local SSE."""
import json
import uuid

import dsherp.agent_worker as worker_module
from dsherp.agent_worker import run_once, ROOT
from test_agent_tasks import created_tasks, worker_client
from test_platform_identity import platform_client

API = '/api/method/dsherp_platform.agent_api.'


# Docker Desktop cannot route this host's loopback-only SSE listener. Keep the
# exact fixture loopback-only inside the task container, not on any public port.
CONTAINER_TEST = """
import json, runpy
from pathlib import Path
from dsherp.task_runner import run_task
fixture = runpy.run_path('/run/model_fixture.py')['model_server'].__wrapped__()
settings, requests, state = next(fixture)
state['tool_call'] = {'name': 'mcp__erp__erp_read_record',
    'arguments': json.dumps({'doctype': 'Item', 'name': 'DSHERP-TEST-ITEM'})}
try:
    config = json.loads(Path('/run/task.json').read_text())
    config.update(settings)
    path = Path('/tmp/local-task.json')
    path.write_text(json.dumps(config)); path.chmod(0o600)
    answer = run_task(path, Path('/tmp'))
    assert len(requests) == 2
    assert {t['function']['name'] for t in requests[0]['tools']} == {
        'mcp__erp__erp_read_schema', 'mcp__erp__erp_read_record', 'mcp__erp__erp_search_records'}
    assert 'DSHERP-TEST-ITEM' in str([m for m in requests[1]['messages'] if m['role'] == 'tool'])
    print(json.dumps({'answer': answer}))
finally:
    fixture.close()
"""


def test_real_container_task_reads_erp_and_persists_result(created_tasks, monkeypatch):
    original_command = worker_module.docker_command
    def test_command(root, secret, name):
        command = original_command(root, secret, name)
        command[2:2] = ['-v', f'{ROOT}/tests/conftest.py:/run/model_fixture.py:ro']
        command[-2:] = ['-c', CONTAINER_TEST]
        return command
    monkeypatch.setattr(worker_module, 'docker_command', test_command)
    settings = {'DEEPSEEK_API_KEY': 'synthetic', 'DSH_MODEL': 'deepseek-v4-flash',
                'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1'}
    with platform_client() as member, worker_client() as worker:
        assert worker.post(API + 'worker_heartbeat').status_code == 200
        response = member.post(API + 'submit_task', json={'enterprise': 'alpha',
            'question': '容器集成测试：读取 DSHERP-TEST-ITEM', 'request_id': str(uuid.uuid4())})
        assert response.status_code == 200
        task_id = response.json()['message']['id']
        created_tasks.append(task_id)
        assert run_once(worker, settings)
        task = member.get(API + 'get_task', params={'task_id': task_id}).json()['message']
        assert task['status'] == 'Succeeded', task['error']
        assert task['answer'] == 'DSHERP_OK'
        assert len(task['events']) == 1 and task['events'][0]['status'] == 'Succeeded'
        assert task['events'][0]['result']['name'] == 'DSHERP-TEST-ITEM'
        assert run_once(worker, settings) is False
