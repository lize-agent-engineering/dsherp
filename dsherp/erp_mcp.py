"""Two read tools bound to one operator-configured ERP site and ordinary user."""

import json
import os
from pathlib import Path
import sys
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DocType = Literal["Customer", "Item"]


def read_message(client: httpx.Client, path: str, params=None):
    response = client.get(path, params=params)
    if response.status_code in (401, 403):
        raise PermissionError("ERP read denied")
    if response.status_code != 200:
        raise RuntimeError(f"ERP request failed (HTTP {response.status_code})")
    return response.json()["message"]


def create_server(client: httpx.Client, expected_user: str) -> FastMCP:
    actual = read_message(client, "/api/method/frappe.auth.get_logged_user")
    if actual in ("Guest", "Administrator") or actual != expected_user:
        raise PermissionError("ERP identity does not match the configured ordinary user")
    server = FastMCP("dsherp-read")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def erp_read_schema(doctype: DocType) -> dict[str, Any]:
        """Read permitted field definitions for Customer or Item in the bound ERP site."""
        return read_message(client, "/api/method/dsherp_bridge.api.read_schema", {"doctype": doctype})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def erp_read_record(doctype: DocType, name: str) -> dict[str, Any]:
        """Read one permitted Customer or Item; no writes, user switching or arbitrary methods."""
        return read_message(client, "/api/method/dsherp_bridge.api.read_record", {"doctype": doctype, "name": name})

    return server


def main() -> int:
    config_path = os.environ.get("DSHERP_ERP_CONFIG", "").strip()
    if not config_path:
        print("DSHERP_ERP_CONFIG is required", file=sys.stderr)
        return 1
    try:
        config = json.loads(Path(config_path).read_text())
        for field in ("base_url", "site", "user", "api_key", "api_secret"):
            if not isinstance(config.get(field), str) or not config[field].strip():
                raise ValueError("Missing ERP configuration")
        url = urlsplit(config["base_url"])
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")
                or (url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"))):
            raise ValueError("ERP URL must be HTTPS or a loopback HTTP endpoint")
        with httpx.Client(
            base_url=config["base_url"],
            headers={"Authorization": "token " + config["api_key"] + ":" + config["api_secret"],
                     "X-Frappe-Site-Name": config["site"]},
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            create_server(client, config["user"]).run(transport="stdio")
    except Exception as error:
        # No raw response bodies, credentials, or traceback on the protocol channel.
        print(f"ERP MCP failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
