"""Shared fixed read-tool catalog; authorization belongs to the business server.

Descriptions come from `tool_limits`, never from a docstring: FastMCP reads `__doc__` at
decoration time, so an f-string body would not work, and a hand-written sentence is exactly
how the old "up to 100 rows" drifted away from the 20 the server actually returns.
"""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from dsherp import tool_limits


def create_read_server(invoke, name):
    server=FastMCP(name)
    annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True)

    @server.tool(annotations=annotations,description=tool_limits.describe_read_schema())
    def erp_read_schema(doctype: str,tables: list[str] | None=None,
                        after_fieldname: str | None=None,
                        child_after: dict | None=None) -> dict:
        return invoke('erp_read_schema',doctype=doctype,tables=tables,
                      after_fieldname=after_fieldname,child_after=child_after)

    @server.tool(annotations=annotations,description=tool_limits.describe_read_record())
    def erp_read_record(doctype: str,name: str,fields: list[str] | None=None,
                        children: list[str] | None=None,include_empty: bool=False,
                        after_idx: dict | None=None) -> dict:
        return invoke('erp_read_record',doctype=doctype,name=name,fields=fields,
                      children=children,include_empty=include_empty,after_idx=after_idx)

    @server.tool(annotations=annotations,description=tool_limits.describe_search())
    def erp_search_records(
        doctype: str, query: str='', filters: dict | None=None,
        fields: list[str] | None=None, after_name: str | None=None,
    ) -> list:
        return invoke(
            'erp_search_records',doctype=doctype,query=query,
            filters=filters,fields=fields,after_name=after_name,
        )
    return server
