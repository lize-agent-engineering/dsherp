import json

from dsherp.eval_cases import build_case


def test_build_case_strips_secrets_and_trims_proposals():
    run = {
        "name": "r1",
        "domain": "operation",
        "question": "q",
        "page_context": json.dumps(
            {
                "schema_version": 1,
                "page_type": "unknown",
                "route": [],
                "capability": "S",
                "apiKey": "sk-abcdefghij_12345",
            }
        ),
        "status": "Failed",
        "error": "业务运行失败：RuntimeError",
        "sources": "[]",
        "creation": "2026-09-01 00:00:00",
        "capability_hash": "S",
        "platform_grant": "S",
    }
    events = [
        {
            "kind": "tool_error",
            "payload": {"text": "x", "api_secret": "S", "authorization": "Bearer synthetic-secret"},
        }
    ]
    proposals = [
        {
            "name": "p1",
            "status": "Pending",
            "payload": json.dumps({"action": "create", "doctype": "Item", "api_secret": "S"}),
        }
    ]

    case = build_case(run, events, proposals, site="alpha")

    assert case["schema_version"] == 1 and case["run_id"] == "r1" and case["site"] == "alpha"
    assert case["proposals"] == [{"id": "p1", "status": "Pending", "summary": "create · Item"}]
    serialized = json.dumps(case, ensure_ascii=False)
    assert "S" not in serialized.replace("schema_version", "")
    assert "sk-abcdefghij_12345" not in serialized and "Bearer synthetic-secret" not in serialized
    assert build_case({**run, "name": "r2"}, [], [], site="alpha")["events"] == []


def test_build_case_uses_frozen_proposal_when_business_target_is_gone():
    run = {
        "name": "r1",
        "domain": "operation",
        "question": "q",
        "page_context": "{}",
        "status": "Failed",
        "error": "failed",
        "sources": "[]",
        "creation": "2026-09-01 00:00:00",
    }
    stored = {
        "name": "p1",
        "status": "Pending",
        "payload": json.dumps({"action": "submit", "doctype": "Sales Order", "name": "REMOVED-SO"}),
    }

    case = build_case(run, [], [stored], site="alpha")

    assert case["proposals"] == [
        {"id": "p1", "status": "Pending", "summary": "submit · Sales Order · REMOVED-SO"}
    ]
