"""Runner/worker event records mirrored with the Frappe event module."""

import json
import re


RUNNER_KINDS = (
    "runtime_started",
    "model_request",
    "model_response",
    "model_error",
    "runtime_tool_call",
    "tool_result",
    "tool_error",
    "compaction",
    "turn_end",
    "runtime_failed",
    "container_finished",
    "worker_error",
)
SECRET_KEYS = frozenset(
    {
        "capability",
        "capability_hash",
        "api_key",
        "api_secret",
        "token",
        "access_token",
        "password",
        "secret",
        "platform_grant",
        "authorization",
        "deepseek_api_key",
        "cookie",
    }
)
SECRET_KEY_PARTS = (
    "secret",
    "password",
    "token",
    "apikey",
    "capability",
    "authorization",
    "cookie",
    "grant",
)
MAX_STRING = 2000
MAX_PAYLOAD = 8192
MAX_BATCH = 200
MAX_ITEMS = 50
MAX_DEPTH = 6
# The marks a lossy step leaves. `loop_guard` reads both to decide two calls are not
# comparable; nothing else may reuse these strings as data.
TRUNCATED = "…[truncated]"
DEPTH_CUT = "…[depth]"


def _secret_key(key, value):
    normalized = str(key).lower().replace("-", "").replace("_", "")
    if normalized.endswith("tokens") and type(value) is int:
        return False
    return any(part in normalized for part in SECRET_KEY_PARTS)


def sanitize(value, depth=0):
    """Every lossy step leaves a mark.

    A long string always did. A dict past `MAX_ITEMS`, a list past `MAX_ITEMS` and anything
    below `MAX_DEPTH` used to be cut silently, and two callers read that silence as sameness:
    a person looking at the event could not tell a 50-row proposal from a 60-row one, and
    `loop_guard.comparable` — which is what stands between a model fixing line 55 and line 58
    of the same proposal and having its run killed as a loop — saw two genuinely different
    calls as identical. The marker is what makes those two cases distinguishable.
    """
    if depth > MAX_DEPTH:
        return DEPTH_CUT
    if isinstance(value, dict):
        kept = {
            str(key): sanitize(item, depth + 1)
            for key, item in list(value.items())[:MAX_ITEMS]
            if not _secret_key(key, item)
        }
        if len(value) > MAX_ITEMS:
            kept[TRUNCATED] = len(value) - MAX_ITEMS
        return kept
    if isinstance(value, (list, tuple)):
        kept = [sanitize(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            kept.append(TRUNCATED)
        return kept
    if isinstance(value, str):
        if re.fullmatch(r"sk-[A-Za-z0-9_-]{10,}", value) or value.startswith("Bearer "):
            return "[redacted]"
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + TRUNCATED
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text(content.get("text", content.get("content")))
    if isinstance(content, list):
        return "".join(_text(block) for block in content)
    return "" if content is None else str(content)


def _arguments(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}
    return raw


def from_runtime_events(events, notifications):
    """Map the fixed Runtime event shapes captured by the C0 probe."""
    del notifications
    items = []
    compactions = 0
    for event in events:
        kind = event.get("type") or ""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        seq = event.get("seq")
        if kind == "tool/call":
            items.append(
                {
                    "kind": "runtime_tool_call",
                    "source": "runner",
                    "payload": sanitize(
                        {
                            "seq": seq,
                            "call_id": data.get("callId"),
                            "name": data.get("name"),
                            "arguments": _arguments(data.get("arguments")),
                        }
                    ),
                }
            )
        elif kind == "tool/result":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            blocks = [
                block
                for block in (message.get("content") or [])
                if isinstance(block, dict)
            ]
            error = data.get("error") if isinstance(data.get("error"), dict) else None
            is_error = bool(error) or any(block.get("isError") for block in blocks)
            call_id = next(
                (
                    block.get("toolCallId")
                    for block in blocks
                    if block.get("toolCallId")
                ),
                (message.get("source") or {}).get("callId"),
            )
            item = {
                "kind": "tool_error" if is_error else "tool_result",
                "source": "runner",
                "payload": sanitize(
                    {
                        "seq": seq,
                        "call_id": call_id,
                        "text": _text([block.get("content") for block in blocks]),
                        "code": (error or {}).get("code"),
                    }
                ),
            }
            error_class = (error or {}).get("name") or (
                "ToolError" if is_error else None
            )
            if error_class:
                item["error_class"] = error_class
            items.append(item)
        elif kind == "turn/end":
            reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
            items.append(
                {
                    "kind": "turn_end",
                    "source": "runner",
                    "payload": {"seq": seq, "reason": reason.get("kind")},
                }
            )
        elif "compact" in kind:
            compactions += 1
    if compactions:
        items.append(
            {
                "kind": "compaction",
                "source": "runner",
                "payload": {"count": compactions},
            }
        )
    return items


def flush(post, run_id, capability, items, *, batch=100):
    sent = 0
    for start in range(0, len(items), batch):
        chunk = items[start : start + batch]
        try:
            post(run_id=run_id, capability=capability, events=chunk)
        except Exception as error:
            return {"sent": sent, "error": type(error).__name__}
        sent += len(chunk)
    return {"sent": sent, "error": None}
