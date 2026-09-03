"""Append-only per-run event stream. Never stores credential values."""

import json
import re

import frappe
from frappe.utils import now_datetime


SERVER_KINDS = (
    "queued",
    "claimed",
    "expired",
    "lease_renewed",
    "cancel_requested",
    "model_call_reserved",
    "tool_call",
    "finished",
)
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
KINDS = SERVER_KINDS + RUNNER_KINDS
SOURCES = ("server", "runner", "worker")
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
MAX_STRING = 2000
MAX_PAYLOAD = 8192
MAX_BATCH = 200
MAX_ITEMS = 50
MAX_DEPTH = 6
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


def _secret_key(key, value):
    normalized = str(key).lower().replace("-", "").replace("_", "")
    if normalized.endswith("tokens") and type(value) is int:
        return False
    return any(part in normalized for part in SECRET_KEY_PARTS)


def sanitize(value, depth=0):
    if depth > MAX_DEPTH:
        return "…[depth]"
    if isinstance(value, dict):
        return {
            str(key): sanitize(item, depth + 1)
            for key, item in list(value.items())[:MAX_ITEMS]
            if not _secret_key(key, item)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    if isinstance(value, str):
        if re.fullmatch(r"sk-[A-Za-z0-9_-]{10,}", value) or value.startswith("Bearer "):
            return "[redacted]"
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _serialize(kind, payload):
    sanitized = sanitize(payload if isinstance(payload, dict) else {"value": payload})
    text = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode()) > MAX_PAYLOAD:
        by_size = sorted(
            sanitized,
            key=lambda key: len(
                json.dumps(sanitized[key], ensure_ascii=False, separators=(",", ":")).encode()
            ),
            reverse=True,
        )
        for key in by_size:
            size = len(
                json.dumps(sanitized[key], ensure_ascii=False, separators=(",", ":")).encode()
            )
            sanitized[key] = f"…[omitted {size} bytes]"
            text = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
            if len(text.encode()) <= MAX_PAYLOAD:
                break
        if len(text.encode()) > MAX_PAYLOAD:
            frappe.throw(f"{kind} 事件顶层键超过载荷上限")
    return text


def _insert(run, seq, kind, payload, source, error_class):
    return frappe.get_doc(
        {
            "doctype": "DS Run Event",
            "run": run,
            "seq": seq,
            "kind": kind,
            "source": source,
            "error_class": (error_class or "")[:140],
            "recorded_at": now_datetime(),
            "payload": _serialize(kind, payload),
        }
    ).insert(ignore_permissions=True).name


def record(run, kind, payload, *, source="server", error_class=None):
    if kind not in KINDS:
        frappe.throw("未知运行事件类型")
    if source not in SOURCES:
        frappe.throw("未知运行事件来源")
    seq = (frappe.db.count("DS Run Event", {"run": run}) or 0) + 1
    try:
        return _insert(run, seq, kind, payload, source, error_class)
    except frappe.DuplicateEntryError:
        seq = (
            frappe.db.sql(
                "SELECT COALESCE(MAX(seq),0) FROM `tabDS Run Event` WHERE run=%s",
                (run,),
            )[0][0]
            or 0
        ) + 1
        return _insert(run, seq, kind, payload, source, error_class)


def record_safely(run, kind, payload, **kwargs):
    try:
        return record(run, kind, payload, **kwargs)
    except Exception as error:
        form_dict = getattr(frappe.local, "form_dict", None)
        try:
            if isinstance(form_dict, dict):
                frappe.local.form_dict = frappe._dict(sanitize(form_dict))
            frappe.log_error(
                title="dsherp run event write failed",
                message=f"{type(error).__name__}: event persistence failed",
            )
        except Exception:
            pass
        finally:
            frappe.local.form_dict = form_dict
        return None


def record_many(run, items):
    if not isinstance(items, list) or not 0 < len(items) <= MAX_BATCH:
        frappe.throw("运行事件批次无效")
    last = None
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            frappe.throw("运行事件无效")
        source = item.get("source", "runner")
        if source not in ("runner", "worker") or item["kind"] not in RUNNER_KINDS:
            frappe.throw("运行事件来源或类型无效")
        last = record(
            run,
            item["kind"],
            item.get("payload") or {},
            source=source,
            error_class=item.get("error_class"),
        )
    return {"recorded": len(items), "last_seq": int(last.rsplit("-", 1)[1])}


def list_events(run, page=1, page_length=200, fetch_length=None):
    rows = frappe.get_all(
        "DS Run Event",
        filters={"run": run},
        fields=["name", "seq", "kind", "source", "error_class", "payload", "recorded_at"],
        order_by="seq asc",
        start=(page - 1) * page_length,
        page_length=fetch_length or page_length,
    )
    return [
        {
            "name": row.name,
            "seq": row.seq,
            "kind": row.kind,
            "source": row.source,
            "error_class": row.error_class or "",
            "payload": json.loads(row.payload or "{}"),
            "recorded_at": str(row.recorded_at),
        }
        for row in rows
    ]
