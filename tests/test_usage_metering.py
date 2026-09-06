"""What a run actually cost, recorded from what the server itself saw.

The provider's own usage numbers arrive as `model_response` events (runtime/model-guard.cjs
reports the chunk's usage); the run's totals are aggregated from those events at finish time,
so a runner cannot under-report and a lost回写 shows up as a missing number rather than a
wrong one."""
import json
from pathlib import Path

import pytest

from dsherp import usage
from tests.test_admin_cli import host as host_runtime  # noqa: F401  isolates runtime and secrets dirs


ROOT = Path(__file__).resolve().parents[1]
RUN_JSON = ROOT / "frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_model_run/ds_model_run.json"


def test_the_run_records_the_fields_a_monthly_bill_is_made_of():
    fields = {field["fieldname"]: field for field in json.loads(RUN_JSON.read_text())["fields"]}
    assert fields["model"]["fieldtype"] == "Data"
    assert fields["provider_request_ids"]["fieldtype"] in ("Long Text", "Small Text")
    for name in ("actual_input_tokens", "actual_output_tokens", "duration_ms"):
        assert fields[name]["fieldtype"] == "Int", name
    assert fields["skill_versions"]["fieldtype"] in ("Long Text", "Small Text")


def _event(kind, payload, recorded_at="2026-09-06 02:00:10", source="runner"):
    return {"kind": kind, "payload": json.dumps(payload), "recorded_at": recorded_at, "source": source}


def test_usage_is_summed_from_the_provider_s_own_numbers_and_the_run_s_own_clock():
    events = [
        _event("claimed", {"domain": "query"}, "2026-09-06 02:00:00", source="server"),
        _event("runtime_started", {"skill_versions": {"manufacturing": "3"}}),
        _event("model_response", {"usage": {"input_tokens": 1200, "output_tokens": 340, "request_id": "req-a"},
                                  "model": "deepseek-chat"}),
        _event("model_response", {"usage": {"prompt_tokens": 800, "completion_tokens": 120}, "model": "deepseek-chat",
                                  "request_id": "req-b"}),
        _event("finished", {"status": "Succeeded"}, "2026-09-06 02:00:42", source="server"),
    ]
    summary = usage.summarise(events)
    assert summary["actual_input_tokens"] == 2000 and summary["actual_output_tokens"] == 460
    assert summary["model"] == "deepseek-chat"
    assert json.loads(summary["provider_request_ids"]) == ["req-a", "req-b"]
    assert summary["duration_ms"] == 42000
    assert json.loads(summary["skill_versions"]) == {"manufacturing": "3"}


def test_a_response_without_usage_is_counted_as_unknown_rather_than_zero():
    """A provider that reports nothing must not make a run look free."""
    events = [
        _event("claimed", {}, "2026-09-06 02:00:00", source="server"),
        _event("model_response", {"usage": None, "model": "deepseek-chat"}),
        _event("model_response", {"usage": {"input_tokens": 10, "output_tokens": 5}, "model": "deepseek-chat"}),
    ]
    summary = usage.summarise(events)
    assert summary["actual_input_tokens"] == 10 and summary["actual_output_tokens"] == 5
    assert summary["usage_unknown_calls"] == 1


def test_more_than_one_model_in_a_run_is_reported_as_such_not_silently_last_wins():
    events = [
        _event("model_response", {"usage": {"input_tokens": 1}, "model": "deepseek-chat"}),
        _event("model_response", {"usage": {"input_tokens": 2}, "model": "deepseek-reasoner"}),
    ]
    summary = usage.summarise(events)
    assert summary["model"] == "deepseek-chat,deepseek-reasoner"


def test_a_run_with_no_model_call_costs_nothing_and_says_so():
    summary = usage.summarise([_event("claimed", {}, "2026-09-06 02:00:00", source="server")])
    assert summary["actual_input_tokens"] == 0 and summary["actual_output_tokens"] == 0
    assert summary["model"] == "" and json.loads(summary["provider_request_ids"]) == []
    assert summary["duration_ms"] is None, "a run that never finished has no duration"


def test_the_monthly_report_groups_by_tenant_and_month_and_counts_only_finished_runs():
    rows = [
        {"site": "acme.tenant.example.com", "creation": "2026-09-01 10:00:00", "status": "Succeeded",
         "actual_input_tokens": 100, "actual_output_tokens": 20, "model_calls": 2, "duration_ms": 3000},
        {"site": "acme.tenant.example.com", "creation": "2026-09-20 10:00:00", "status": "Failed",
         "actual_input_tokens": 50, "actual_output_tokens": 0, "model_calls": 1, "duration_ms": 1000},
        {"site": "acme.tenant.example.com", "creation": "2026-08-31 23:00:00", "status": "Succeeded",
         "actual_input_tokens": 7, "actual_output_tokens": 3, "model_calls": 1, "duration_ms": 500},
        {"site": "beta.tenant.example.com", "creation": "2026-09-02 10:00:00", "status": "Queued",
         "actual_input_tokens": 0, "actual_output_tokens": 0, "model_calls": 0, "duration_ms": None},
    ]
    report = usage.monthly(rows, month="2026-09")
    assert report["month"] == "2026-09"
    acme = report["sites"]["acme.tenant.example.com"]
    assert acme["runs"] == 2 and acme["input_tokens"] == 150 and acme["output_tokens"] == 20
    assert acme["model_calls"] == 3 and acme["succeeded"] == 1 and acme["failed"] == 1
    beta = report["sites"]["beta.tenant.example.com"]
    assert beta["runs"] == 0 and beta["unfinished"] == 1, "a run still in flight is not billed"
    assert report["totals"]["input_tokens"] == 150 and report["totals"]["runs"] == 2


def test_the_site_and_the_host_count_usage_by_the_same_rules():
    """The bridge runs inside the container where dsherp/ is not importable, so it carries its
    own copy; a copy that drifts would bill differently from what the Site recorded."""
    host = (ROOT / "dsherp/usage.py").read_text()
    bridge = (ROOT / "frappe_app/dsherp_bridge/usage.py").read_text()
    def body(text):
        return text.split('"""', 2)[2]
    assert body(host) == body(bridge), "the two copies of the usage rules differ"


def test_the_host_reports_usage_per_site_for_a_month(host_runtime):
    """`dsherp-admin usage 2026-09` asks every Site for its finished runs and adds them up;
    the numbers are the Site's own, not the host's arithmetic on raw events."""
    from dsherp import admin
    from tests.test_admin_cli import RELEASE, SnapshotBench, _tenant_row

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    rows = {
        "acme.tenant.example.com": [
            {"site": "acme.tenant.example.com", "creation": "2026-09-03 10:00:00", "status": "Succeeded",
             "actual_input_tokens": 900, "actual_output_tokens": 120, "model_calls": 3, "duration_ms": 8000},
            {"site": "acme.tenant.example.com", "creation": "2026-08-03 10:00:00", "status": "Succeeded",
             "actual_input_tokens": 5, "actual_output_tokens": 1, "model_calls": 1, "duration_ms": 100},
        ],
        "platform.tenant.example.com": [],
    }

    class UsageBench(SnapshotBench):
        def python(self, site, body, timeout=900):
            if "DSHERP_USAGE" in body:
                return "DSHERP_USAGE " + json.dumps(rows[site]) + "\n"
            return super().python(site, body, timeout=timeout)

    report = admin.usage_report(RELEASE, "2026-09", bench_factory=lambda kind: UsageBench([]))
    assert report["month"] == "2026-09"
    acme = report["sites"]["acme.tenant.example.com"]
    assert acme["runs"] == 1 and acme["input_tokens"] == 900 and acme["model_calls"] == 3
    assert report["sites"]["platform.tenant.example.com"]["runs"] == 0
    assert report["totals"]["input_tokens"] == 900
    assert report["path"].endswith("usage-2026-09.json")


def test_a_month_that_is_not_a_month_is_refused(host_runtime):
    from dsherp import admin
    from tests.test_admin_cli import RELEASE, SnapshotBench

    for bad in ("2026-13", "2026", "september", "", "2026-09-01"):
        with pytest.raises(admin.Fault):
            admin.usage_report(RELEASE, bad, bench_factory=lambda kind: SnapshotBench([]))


def test_a_month_is_the_same_month_everywhere_so_the_bill_converts_each_site_s_own_clock():
    """Frappe stores datetimes in the Site's system timezone. A tenant in Shanghai and one in
    UTC must still be billed for the same month, so the host converts before it buckets."""
    rows = [
        # 2026-10-01 01:00 in Shanghai is 2026-09-30 17:00 UTC: September's bill, not October's.
        {"site": "acme.tenant.example.com", "creation": "2026-10-01 01:00:00", "time_zone": "Asia/Shanghai",
         "status": "Succeeded", "actual_input_tokens": 11, "actual_output_tokens": 1, "model_calls": 1,
         "duration_ms": 10},
        {"site": "beta.tenant.example.com", "creation": "2026-09-30 17:00:00", "time_zone": "UTC",
         "status": "Succeeded", "actual_input_tokens": 22, "actual_output_tokens": 2, "model_calls": 1,
         "duration_ms": 20},
    ]
    report = usage.monthly(rows, month="2026-09")
    assert report["sites"]["acme.tenant.example.com"]["runs"] == 1
    assert report["sites"]["beta.tenant.example.com"]["runs"] == 1
    assert report["totals"]["input_tokens"] == 33
    october = usage.monthly(rows, month="2026-10")
    assert october["totals"]["runs"] == 0


def test_a_site_without_a_declared_timezone_is_read_as_utc_and_flagged():
    rows = [{"site": "acme.tenant.example.com", "creation": "2026-09-30 17:00:00", "status": "Succeeded",
             "actual_input_tokens": 1, "actual_output_tokens": 0, "model_calls": 1, "duration_ms": 1}]
    report = usage.monthly(rows, month="2026-09")
    assert report["sites"]["acme.tenant.example.com"]["runs"] == 1
    assert "acme.tenant.example.com" in report["assumed_utc"]


def test_an_unknown_duration_is_left_out_of_the_row_rather_than_written_as_null_or_zero():
    """The column is an Int: a null is refused by the database and a zero would read as an
    instant run. A run that never finished simply has no duration to store."""
    summary = usage.summarise([_event("claimed", {}, "2026-09-06 02:00:00", source="server")])
    assert summary["duration_ms"] is None
    row = usage.storable(summary)
    assert "duration_ms" not in row
    assert row["actual_input_tokens"] == 0 and row["model"] == ""
    finished = usage.summarise([
        _event("claimed", {}, "2026-09-06 02:00:00", source="server"),
        _event("finished", {"status": "Succeeded"}, "2026-09-06 02:00:05", source="server"),
    ])
    assert usage.storable(finished)["duration_ms"] == 5000
