"""Run-bound business Site MCP transport."""
import json
import os
from pathlib import Path
import sys
from typing import Literal
from mcp.types import ToolAnnotations
from mcp.server.fastmcp import FastMCP

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


def _forbid_extra_tool_arguments(server,name):
    """FastMCP otherwise discards unknown keys before the tool function sees them."""
    tool=server._tool_manager.get_tool(name)
    model=tool.fn_metadata.arg_model
    model.model_config['extra']='forbid'
    model.model_rebuild(force=True)
    tool.parameters=model.model_json_schema(by_alias=True)


def create_server(client,run_id,capability,domain='query'):
    if domain not in ('query','operation','configuration'):raise ValueError('Unknown business domain')
    def invoke(tool,**arguments):
        return post(client,'run_tool',run_id=run_id,capability=capability,tool=tool,arguments=arguments)
    if domain=='configuration':
        server=FastMCP('dsherp-context-configuration')
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True))
        def erp_read_configuration(doctype: str) -> dict:
            """Read current-user native configuration for an exact existing or proposed DocType, with available modules and roles. No business records."""
            return invoke('erp_read_configuration',doctype=doctype)
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_configuration(package: dict) -> dict:
            """Store an immutable data-only native configuration proposal after reading its targets. Does not apply, publish, or create business records. Human preview and target confirmations are separate."""
            return invoke('erp_propose_configuration',package=package)
        return server
    server=create_read_server(invoke,'dsherp-context-'+domain)
    _forbid_extra_tool_arguments(server,'erp_search_records')
    if domain=='operation':
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_update(doctype: str,name: str,values: dict,version: str) -> dict:
            """Propose explicit changes to a previously read record/version. Does not save business data. The user must confirm the frozen proposal in Desk; no confirmation tool exists here."""
            return invoke('erp_propose_update',doctype=doctype,name=name,values=values,version=version)
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_create(doctype: str,values: dict,version: str) -> dict:
            """Propose creation using explicit field values and the modified version returned by erp_read_schema. Native defaults/naming apply only after the user confirms in Desk. Does not create a business record."""
            return invoke('erp_propose_create',doctype=doctype,values=values,version=version)
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_action(doctype: str,name: str,action: Literal['submit','cancel'],version: str) -> dict:
            """Propose native submit or cancel of the exact previously read document version. This only stores a proposal; the user must separately confirm it in Desk."""
            return invoke('erp_propose_action',doctype=doctype,name=name,action=action,version=version)
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_fill(doctype: str,name: str,values: dict,version: str) -> dict:
            """Propose values for the current native form only. Existing child rows must retain their names and order; change only explicit editable columns. Requires a previously read record/version. Confirmation authorizes browser draft fill, never saves or submits ERP."""
            return invoke('erp_propose_fill',doctype=doctype,name=name,values=values,version=version)
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
        def erp_propose_make(source_doctype: str,source_name: str,source_version: str,route: str) -> dict:
            """Propose a server-mapped target draft from an exact source record/version and enabled named route. Does not insert or submit the target; confirmation reruns and compares the mapper."""
            return invoke('erp_propose_make',source_doctype=source_doctype,source_name=source_name,
                          source_version=source_version,route=route)
        _forbid_extra_tool_arguments(server,'erp_propose_make')
    return server


def main():
    try:
        config=json.loads(Path(os.environ['DSHERP_RUN_CONFIG']).read_text())
        with httpx.Client(base_url=config['business_url'],headers={'X-Frappe-Site-Name':config['site']},
                          timeout=20,trust_env=False,follow_redirects=False) as client:
            create_server(client,config['run_id'],config['capability'],domain=config['domain']).run(transport='stdio')
        return 0
    except Exception as exc:
        print(f'Business MCP failed: {type(exc).__name__}',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
