"""Build sanitized evaluation cases from recorded model runs."""

import json

from dsherp.run_events import sanitize


def _parse_json_field(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _proposal_summary(item):
    payload = _parse_json_field(item["payload"])
    summary = " · ".join(
        str(payload[key]) for key in ("action", "doctype", "name") if payload.get(key)
    )
    return {
        "id": item["name"],
        "status": item["status"],
        "summary": sanitize(summary),
    }


def build_case(run, events, proposals, site):
    page_context = sanitize(_parse_json_field(run["page_context"]))
    sources = sanitize(_parse_json_field(run["sources"]))
    return {
        "schema_version": 1,
        "site": site,
        "run_id": run["name"],
        "domain": run["domain"],
        "question": sanitize(run.get("question")),
        "page_context": page_context,
        "status": run["status"],
        "error": sanitize(run.get("error")),
        "sources": sources,
        "events": sanitize(events),
        "proposals": [_proposal_summary(item) for item in proposals],
        "created": run["creation"],
    }
