"""Shared fixed read-tool catalog; authorization belongs to the business server."""
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def create_read_server(invoke, name):
    server=FastMCP(name)
    annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True)

    @server.tool(annotations=annotations)
    def erp_read_schema(doctype: Literal['Customer','Item']) -> dict:
        """Read fields allowed for the current business user."""
        return invoke('erp_read_schema',doctype=doctype)

    @server.tool(annotations=annotations)
    def erp_read_record(doctype: Literal['Customer','Item'],name: str) -> dict:
        """Read one record allowed for the current business user."""
        return invoke('erp_read_record',doctype=doctype,name=name)

    @server.tool(annotations=annotations)
    def erp_search_records(doctype: Literal['Customer','Item'],query: str='') -> list:
        """Find up to 20 permitted record names; read records for details."""
        return invoke('erp_search_records',doctype=doctype,query=query)
    return server
