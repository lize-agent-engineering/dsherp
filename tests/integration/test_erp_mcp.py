import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

ROOT = Path(__file__).resolve().parents[2]


def invoke(actor, operation):
    async def run():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "dsherp.erp_mcp"],
            cwd=str(ROOT),
            env={"DSHERP_ERP_CONFIG": str(ROOT / ".runtime" / f"erp-{actor}.json")},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await operation(client)
    return asyncio.run(run())


def test_mcp_lists_only_two_read_tools_without_identity_or_url_parameters():
    async def operation(client):
        return await client.list_tools()
    tools = invoke("reader", operation).tools
    assert {tool.name for tool in tools} == {"erp_read_schema", "erp_read_record"}
    for tool in tools:
        assert set(tool.inputSchema["properties"]) <= {"doctype", "name"}
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False


def test_mcp_returns_actual_erp_record():
    async def operation(client):
        return await client.call_tool("erp_read_record", {"doctype": "Item", "name": "DSHERP-TEST-ITEM"})
    result = invoke("reader", operation)
    assert not result.isError
    assert result.structuredContent["name"] == "DSHERP-TEST-ITEM"
    assert result.structuredContent["fields"]["item_code"] == "DSHERP-TEST-ITEM"


@pytest.mark.parametrize("actor,doctype", [("denied", "Customer"), ("reader", "User")])
def test_mcp_rejects_forbidden_access(actor, doctype):
    async def operation(client):
        return await client.call_tool("erp_read_schema", {"doctype": doctype})
    result = invoke(actor, operation)
    assert result.isError
    assert "DSHERP-TEST-RESTRICTED" not in str(result)


def test_mcp_refuses_credential_identity_mismatch(tmp_path):
    config = json.loads((ROOT / ".runtime/erp-reader.json").read_text())
    config["user"] = "dsherp-denied@example.invalid"
    path = tmp_path / "wrong-identity.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-m", "dsherp.erp_mcp"],
        env={**os.environ, "DSHERP_ERP_CONFIG": str(path)},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "PermissionError" in result.stderr
    assert config["api_secret"] not in result.stderr
