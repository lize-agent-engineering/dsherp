"""Shared fixed read-tool catalog; authorization belongs to the business server."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def create_read_server(invoke, name):
    server=FastMCP(name)
    annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True)

    @server.tool(annotations=annotations)
    def erp_read_schema(doctype: str) -> dict:
        """Read fields allowed for the current business user."""
        return invoke('erp_read_schema',doctype=doctype)

    @server.tool(annotations=annotations)
    def erp_read_record(doctype: str,name: str) -> dict:
        """Read one record allowed for the current business user."""
        return invoke('erp_read_record',doctype=doctype,name=name)

    @server.tool(annotations=annotations)
    def erp_search_records(
        doctype: str, query: str='', filters: dict | None=None,
        fields: list[str] | None=None,
    ) -> list:
        """Search names, or batch-read up to 100 rows with restricted filters/fields."""
        return invoke(
            'erp_search_records',doctype=doctype,query=query,
            filters=filters,fields=fields,
        )
    return server
