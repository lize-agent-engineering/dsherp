"""Task-bound MCP bridge: no worker or ERP credentials enter this process."""
import json
import os
from pathlib import Path
import sys

import httpx
from dsherp.read_tools import create_read_server

API = '/api/method/dsherp_platform.agent_api.'


class PlatformError(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f'平台请求被拒绝或失败（HTTP {status_code}）')


def post(client, method, **data):
    response = client.post(API + method, json=data)
    if response.status_code != 200:
        raise PlatformError(response.status_code)
    body = response.json()
    if method == 'claim_task' and 'message' not in body:
        return None
    return body['message']


def create_server(client, task_id, capability):
    def invoke(tool, **arguments):
        return post(client, 'task_tool', task_id=task_id, capability=capability, tool=tool, arguments=arguments)
    return create_read_server(invoke,'dsherp-task-read')


def main():
    try:
        config = json.loads(Path(os.environ['DSHERP_TASK_CONFIG']).read_text())
        with httpx.Client(base_url='http://platform-frontend:8080', headers={'Host': 'platform.localhost'},
                          timeout=20, trust_env=False, follow_redirects=False) as client:
            create_server(client, config['task_id'], config['capability']).run(transport='stdio')
        return 0
    except Exception as exc:
        print(f'Task MCP failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
