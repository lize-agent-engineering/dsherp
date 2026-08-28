import json
import os
from pathlib import Path
import subprocess
import sys

from deepseek_harness import DeepSeekHarness

ROOT = Path(__file__).resolve().parents[2]


def test_real_runtime_mcp_and_erp_chain_with_local_model(model_server, tmp_path, runtime_processes):
    settings, requests, state = model_server
    state["tool_call"] = {
        "name": "mcp__erp__erp_read_record",
        "arguments": json.dumps({"doctype": "Item", "name": "DSHERP-TEST-ITEM"}),
    }
    harness = DeepSeekHarness(
        provider="deepseek-official", model=settings["DSH_MODEL"],
        api_key=settings["DEEPSEEK_API_KEY"], base_url=settings["DEEPSEEK_BASE_URL"],
        cordis=str(ROOT / "config/dsh-erp.yml"), cwd=str(tmp_path),
        runtime_cwd=str(tmp_path), session_root=str(tmp_path / "sessions"),
        env={"DSHERP_PYTHON": sys.executable, "DSHERP_PROJECT": str(ROOT),
             "DSHERP_ERP_CONFIG": str(ROOT / ".runtime/erp-reader.json")},
        max_tokens=256, request_timeout_seconds=30, shutdown_timeout_seconds=3,
    )
    descendants = []
    try:
        result = harness.run("Read the synthetic Item through the ERP tool.")
        assert result.finish_reason == "completed"
        assert len(requests) == 2
        assert {t["function"]["name"] for t in requests[0]["tools"]} == {
            "mcp__erp__erp_read_schema", "mcp__erp__erp_read_record",
        }
        tool_messages = [m for m in requests[1]["messages"] if m["role"] == "tool"]
        assert len(tool_messages) == 1
        assert 'DSHERP-TEST-ITEM' in tool_messages[0]["content"]
        assert 'item_code' in tool_messages[0]["content"]
        assert 'ERP read denied' not in tool_messages[0]["content"]
        rows = subprocess.check_output(["ps", "-Ao", "pid=,ppid="], text=True).splitlines()
        descendants = [int(row.split()[0]) for row in rows if int(row.split()[1]) == runtime_processes[0].pid]
        assert descendants, "MCP child must exist while runtime is alive"
    finally:
        harness.close()
    assert runtime_processes[0].poll() is not None
    for pid in descendants:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"MCP child {pid} survived runtime close")
