"""Real pinned Runtime receives classified tool failures from create_server."""
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from deepseek_harness import DeepSeekHarness

from dsherp.context_mcp import create_server

ROOT = Path(__file__).resolve().parents[1]


def _serve_classified_mcp():
    spec = json.loads(Path(os.environ['DSHERP_FAIL_SPEC_PATH']).read_text())
    def handler(request):
        return httpx.Response(spec['status'], json=spec['body'])
    with httpx.Client(base_url='http://synthetic', transport=httpx.MockTransport(handler)) as client:
        create_server(client, spec['run_id'], spec['capability'], domain=spec.get('domain', 'query')).run(transport='stdio')


def _write_secret(path, text):
    path.write_text(text)
    path.chmod(0o600)
    return path


def _last_tool_result(messages):
    tools = [item for item in messages if item.get('role') == 'tool']
    assert tools, messages
    return tools[-1]['content'] if isinstance(tools[-1]['content'], str) else str(tools[-1]['content'])


def _run_classified_tool_failure(model_server, tmp_path, status, body):
    settings, requests, state = model_server
    state['tool_call'] = {
        'name': 'mcp__erp__erp_read_record',
        'arguments': json.dumps({'doctype': 'Item', 'name': 'MISSING'}),
    }
    spec = _write_secret(tmp_path / 'fail-spec.json', json.dumps({
        'status': status, 'body': body, 'run_id': 'R', 'capability': 'C', 'domain': 'query',
    }))
    cordis = _write_secret(tmp_path / 'dsh-taxonomy.yml', (
        f"- id: base\n"
        f"  name: cordis:include\n"
        f"  config:\n"
        f"    path: {json.dumps(str(ROOT / 'config/dsh-no-tools.yml'))}\n"
        f"- id: erp-mcp\n"
        f"  name: '@deepseek-ai/dsh-mcp-client'\n"
        f"  config:\n"
        f"    serverName: erp\n"
        f"    transport: stdio\n"
        f"    command: {json.dumps(sys.executable)}\n"
        f"    args: [{json.dumps(str(Path(__file__).resolve()))}]\n"
        f"    cwd: {json.dumps(str(ROOT))}\n"
        f"    env:\n"
        f"      DSHERP_FAIL_SPEC_PATH: {json.dumps(str(spec))}\n"
        f"      PYTHONPATH: {json.dumps(str(ROOT))}\n"
        f"    failOnStartupError: true\n"
        f"    toolCallTimeoutMs: 25000\n"
        f"    reconnect:\n"
        f"      enabled: false\n"
    ))
    harness = DeepSeekHarness(
        provider='deepseek-official', model=settings['DSH_MODEL'],
        api_key=settings['DEEPSEEK_API_KEY'], base_url=settings['DEEPSEEK_BASE_URL'],
        cordis=str(cordis), cwd=str(tmp_path), runtime_cwd=str(tmp_path),
        session_root=str(tmp_path / 'sessions'),
        env={'DSHERP_FAIL_SPEC_PATH': str(spec), 'PYTHONPATH': str(ROOT)},
        max_tokens=256, request_timeout_seconds=30, shutdown_timeout_seconds=3,
    )
    try:
        result = harness.run('Read the synthetic Item through the ERP tool.')
        assert result.finish_reason == 'completed'
        assert len(requests) == 2
        return _last_tool_result(requests[1]['messages'])
    finally:
        harness.close()
        spec.unlink(missing_ok=True)
        cordis.unlink(missing_ok=True)


@pytest.mark.parametrize('status,body,error_class,message',[
    (417,{'exc_type':'ValidationError','_server_messages':json.dumps([json.dumps({'message':'仓库不存在'})])},
     'validation','仓库不存在'),
    (403,{'exception':'frappe.exceptions.PermissionError: 无权读取'},'permission','无权读取'),
    (503,{'_server_messages':json.dumps([json.dumps({'message':'上游超时'})])},'transient','上游超时'),
])
def test_classified_tool_failure_reaches_next_model_request(model_server,tmp_path,status,body,error_class,message):
    text=_run_classified_tool_failure(model_server,tmp_path,status,body)
    assert f'"error_class":"{error_class}"' in text or f'"error_class": "{error_class}"' in text
    assert message in text


if __name__ == '__main__':
    _serve_classified_mcp()
    raise SystemExit(0)
