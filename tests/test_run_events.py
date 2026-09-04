import ast
import json
from pathlib import Path

import httpx

from dsherp.context_mcp import post
from dsherp import run_events as re_


def _server_constants():
    source = (
        Path(__file__).resolve().parents[1]
        / "frappe_app/dsherp_bridge/context_events.py"
    ).read_text()
    tree = ast.parse(source)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name in (
            "RUNNER_KINDS",
            "SECRET_KEY_PARTS",
            "MAX_STRING",
            "MAX_PAYLOAD",
            "MAX_BATCH",
        ):
            found[name] = ast.literal_eval(node.value)
        elif name == "SECRET_KEYS":
            found[name] = frozenset(ast.literal_eval(node.value.args[0]))
    return found


def test_constants_match_server_module():
    found = _server_constants()
    assert found["RUNNER_KINDS"] == re_.RUNNER_KINDS
    assert found["SECRET_KEYS"] == re_.SECRET_KEYS
    assert found["SECRET_KEY_PARTS"] == re_.SECRET_KEY_PARTS
    assert (
        found["MAX_STRING"],
        found["MAX_PAYLOAD"],
        found["MAX_BATCH"],
    ) == (re_.MAX_STRING, re_.MAX_PAYLOAD, re_.MAX_BATCH)


def test_sanitize_mirrors_normalized_keys_values_and_integer_token_counts():
    value = {
        "apiKey": "SECRET-KEY",
        "accessToken": "SECRET-TOKEN",
        "client-secret": "SECRET-CLIENT",
        "callId": "call-1",
        "toolCallId": "tool-1",
        "input_tokens": 3,
        "output-tokens": 4,
        "authTokens": "SECRET-NOT-A-COUNT",
        "credential": "sk-abcdefghij_12345",
        "header": "Bearer synthetic-value",
    }
    sanitized = re_.sanitize(value)
    assert sanitized == {
        "callId": "call-1",
        "toolCallId": "tool-1",
        "input_tokens": 3,
        "output-tokens": 4,
        "credential": "[redacted]",
        "header": "[redacted]",
    }


def test_runtime_events_map_to_tool_and_turn_records_without_secrets():
    events = [
        {"type": "turn/start", "seq": 1, "data": {"turn": 1}},
        {
            "type": "tool/call",
            "seq": 2,
            "data": {
                "turn": 1,
                "step": 1,
                "callId": "c1",
                "name": "mcp__erp__erp_read_record",
                "arguments": json.dumps(
                    {"doctype": "Item", "name": "I", "capability": "SECRET"}
                ),
            },
        },
        {
            "type": "tool/result",
            "seq": 3,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "source": {"kind": "tool", "callId": "c1"},
                    "role": "tool",
                    "id": "m1",
                    "content": [
                        {
                            "type": "tool_result",
                            "toolCallId": "c1",
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": "业务运行请求未完成（HTTP 417）",
                                }
                            ],
                        }
                    ],
                },
                "error": {"name": "ToolExecutionError", "code": "E_TOOL"},
            },
        },
        {
            "type": "assistant/message",
            "seq": 4,
            "data": {"message": {"content": [{"type": "text", "text": "x" * 5000}]}},
        },
        {"type": "turn/end", "seq": 5, "data": {"reason": {"kind": "completed"}}},
    ]
    items = re_.from_runtime_events(events, [])
    kinds = [item["kind"] for item in items]
    assert kinds == ["runtime_tool_call", "tool_error", "turn_end"], kinds
    assert items[0]["payload"]["name"] == "mcp__erp__erp_read_record"
    assert items[0]["payload"]["call_id"] == "c1"
    assert items[0]["payload"]["arguments"] == {"doctype": "Item", "name": "I"}
    assert items[1]["error_class"] == "ToolExecutionError"
    assert "417" in items[1]["payload"]["text"]
    assert items[1]["payload"]["call_id"] == "c1"
    assert items[2]["payload"]["reason"] == "completed"
    assert all(item["payload"].get("seq") for item in items)
    assert "SECRET" not in json.dumps(items)


def test_flush_batches_and_never_raises():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) < 3:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "recorded": len(seen[-1]["events"]),
                        "last_seq": 1,
                    }
                },
            )
        return httpx.Response(503)

    items = [
        {"kind": "turn_end", "payload": {"i": index}, "source": "runner"}
        for index in range(250)
    ]
    with httpx.Client(
        base_url="http://local", transport=httpx.MockTransport(handler)
    ) as client:
        out = re_.flush(
            lambda **kwargs: post(client, "record_run_event", **kwargs),
            "r",
            "cap",
            items,
            batch=100,
        )
    assert out["sent"] == 200 and out["error"] == "ToolFailure"
    assert all(len(body["events"]) <= 100 and body["run_id"] == "r" for body in seen)
