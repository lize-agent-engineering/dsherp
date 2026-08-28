"""Run-bound business Site MCP transport."""
import json
import os
from pathlib import Path
import sys

import httpx
from dsherp.read_tools import create_read_server

API='/api/method/dsherp_bridge.context_execution.'


class BusinessRuntimeError(RuntimeError):
    def __init__(self,status_code):
        self.status_code=status_code
        super().__init__(f'业务运行请求未完成（HTTP {status_code}）')


def post(client,method,**data):
    response=client.post(API+method,json=data)
    if response.status_code!=200:raise BusinessRuntimeError(response.status_code)
    body=response.json()
    if method=='claim_run' and 'message' not in body:return None
    return body['message']


def create_server(client,run_id,capability):
    def invoke(tool,**arguments):
        return post(client,'run_tool',run_id=run_id,capability=capability,tool=tool,arguments=arguments)
    return create_read_server(invoke,'dsherp-context-read')


def main():
    try:
        config=json.loads(Path(os.environ['DSHERP_RUN_CONFIG']).read_text())
        with httpx.Client(base_url=config['business_url'],headers={'X-Frappe-Site-Name':config['site']},
                          timeout=20,trust_env=False,follow_redirects=False) as client:
            create_server(client,config['run_id'],config['capability']).run(transport='stdio')
        return 0
    except Exception as exc:
        print(f'Business MCP failed: {type(exc).__name__}',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
