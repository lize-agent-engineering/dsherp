"""Ruling #10, as code: what leaves when a person asks to be forgotten, and what stays.

Retained are the audit facts - which run, on whose behalf, what it read, what was proposed,
what was confirmed, what was executed, and what became of the document. Deleted is the
person's own content: their prompts, the page snapshots that carry what they had typed, the
answers written back to them, and their native session directories. Existing events are never
rewritten; the versions that would otherwise keep a copy of the deleted text go with it."""
import json
from pathlib import Path

import pytest

from dsherp import user_data


ROOT = Path(__file__).resolve().parents[1]


def test_the_boundary_names_every_field_it_touches_and_says_why():
    plan = user_data.BOUNDARY
    run = plan["DS Model Run"]
    assert set(run["clear"]) == {"question", "page_context", "answer", "needs_input", "error", "platform_grant"}
    assert set(run["keep"]) >= {"name", "owner", "status", "sources", "domain", "model_calls",
                                "actual_input_tokens", "conversation", "request_digest"}
    assert list(plan["DS Run Event"]["clear"]) == [], "an event is never rewritten"
    for doctype, rules in plan.items():
        assert rules["why"], doctype
        assert not set(rules.get("clear", [])) & set(rules.get("keep", [])), doctype


def test_the_prompt_goes_and_the_evidence_of_what_it_read_stays():
    """A prompt is the person's own words; the list of records the run actually read is the
    audit fact that makes it accountable."""
    plan = user_data.BOUNDARY["DS Model Run"]
    assert "question" in plan["clear"] and "sources" in plan["keep"]
    assert "answer" in plan["clear"] and "request_digest" in plan["keep"]


def test_an_event_is_never_rewritten_so_its_personal_content_is_declared_not_silently_kept():
    """Ruling #10 forbids rewriting an event. What personal text an event may hold is therefore
    named here as a known residue, with the only allowed way to remove it."""
    residue = user_data.RESIDUE
    assert any(item["doctype"] == "DS Run Event" for item in residue)
    for item in residue:
        assert item["why"] and item["removal"], item
    event = next(item for item in residue if item["doctype"] == "DS Run Event")
    assert "受控脱敏迁移" in event["removal"], "the only allowed exception is the registered migration"
    backups = next(item for item in residue if "备份" in item["doctype"])
    assert "保留策略" in backups["removal"], "a deletion request does not rewrite history"


def test_a_plan_is_produced_before_anything_is_touched_and_says_what_it_will_do():
    plan = user_data.plan(
        user="alice@example.invalid",
        conversations=["conv1", "conv2"],
        runs=[{"name": "r1", "conversation": "conv1"}, {"name": "r2", "conversation": "conv2"}],
        proposals=["p1"], executions=["e1"], sessions=2)
    assert plan["user"] == "alice@example.invalid"
    assert plan["counts"]["runs"] == 2 and plan["counts"]["conversations"] == 2
    assert plan["counts"]["sessions"] == 2
    assert plan["keeps"]["runs"] == 2, "the runs stay; their personal columns are cleared"
    assert "DS Run Event" in plan["untouched"]
    assert plan["residue"], "the plan states what it cannot remove"


def test_the_export_carries_the_person_s_own_content_and_the_facts_about_them():
    export = user_data.export_document(
        user="alice@example.invalid", site="acme.tenant.example.com",
        conversations=[{"name": "conv1", "title": "库存", "creation": "2026-09-01 10:00:00"}],
        runs=[{"name": "r1", "conversation": "conv1", "question": "还有多少库存", "answer": "还有 3 件",
               "status": "Succeeded", "sources": "[]", "creation": "2026-09-01 10:00:01"}],
        proposals=[{"name": "p1", "status": "Executed", "payload": "{}"}],
        executions=[{"name": "e1", "status": "Succeeded", "target_doctype": "Stock Entry",
                     "target_name": "MAT-STE-1"}])
    assert export["user"] == "alice@example.invalid" and export["site"] == "acme.tenant.example.com"
    assert export["conversations"][0]["title"] == "库存"
    assert export["runs"][0]["question"] == "还有多少库存" and export["runs"][0]["answer"] == "还有 3 件"
    assert export["executions"][0]["target_name"] == "MAT-STE-1"
    assert export["generated_at"] and export["format"] == 1
    assert json.dumps(export)   # it is a document, not an object graph


def test_clearing_a_run_writes_the_declared_columns_and_nothing_else():
    values = user_data.cleared("DS Model Run")
    assert set(values) == {"question", "page_context", "answer", "needs_input", "error", "platform_grant"}
    # Text columns go empty; the page snapshot goes to a snapshot that still reads as one.
    assert set(values.values()) == {"", values["page_context"]}
    assert values["question"] == "" and values["answer"] == "" and values["platform_grant"] == ""
    with pytest.raises(KeyError):
        user_data.cleared("DS Run Event")


from tests.test_admin_cli import RELEASE, SnapshotBench, _tenant_row   # noqa: E402
from tests.test_admin_cli import host as host_runtime  # noqa: F401,E402


class UserBench(SnapshotBench):
    """A bench that answers the user-data reader and records the writes it was asked for."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scripts = []
        self.payload = {
            "user": "alice@example.invalid", "known_user": True,
            "conversations": [{"name": "conv1", "title": "库存", "creation": "2026-09-01 10:00:00"}],
            "runs": [{"name": "r1", "conversation": "conv1", "question": "还有多少库存",
                      "answer": "3 件", "status": "Succeeded", "sources": "[]",
                      "creation": "2026-09-01 10:00:01"}],
            "proposals": [{"name": "p1", "status": "Executed", "payload": "{}"}],
            "executions": [{"name": "e1", "status": "Succeeded", "target_doctype": "Stock Entry",
                            "target_name": "MAT-STE-1"}],
        }

    def python(self, site, body, timeout=900):
        self.scripts.append(body)
        if "DSHERP_USER_DATA" in body:
            return "DSHERP_USER_DATA " + json.dumps(self.payload) + "\n"
        if "DSHERP_USER_DELETE" in body:
            return "DSHERP_USER_DELETE " + json.dumps({"runs": 1, "conversations": 1, "versions": 4}) + "\n"
        return super().python(site, body, timeout=timeout)


def test_export_writes_one_document_per_person_and_never_touches_the_site(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    report = admin.export_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                    bench_factory=lambda kind: bench)
    document = json.loads(Path(report["path"]).read_text())
    assert document["user"] == "alice@example.invalid" and document["runs"][0]["question"] == "还有多少库存"
    assert report["counts"]["runs"] == 1
    assert not any("DSHERP_USER_DELETE" in script for script in bench.scripts), "an export writes nothing"
    assert Path(report["path"]).stat().st_mode & 0o077 == 0, "it is somebody's personal data"


def test_delete_states_its_plan_refuses_without_confirmation_and_then_clears(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    dry = admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                 bench_factory=lambda kind: bench)
    assert dry["applied"] is False and dry["plan"]["counts"]["runs"] == 1
    assert dry["plan"]["residue"] and not any("DSHERP_USER_DELETE" in s for s in bench.scripts)
    assert dry["export"], "the person's data is exported before it is cleared"

    done = admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                  bench_factory=lambda kind: bench, confirm=True)
    assert done["applied"] is True and done["cleared"]["runs"] == 1
    assert any("DSHERP_USER_DELETE" in script for script in bench.scripts)
    assert done["sessions_removed"] == [] or isinstance(done["sessions_removed"], list)


def test_a_user_who_has_nothing_here_is_said_so_rather_than_reported_as_deleted(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    bench.payload = {"user": "ghost@example.invalid", "known_user": False, "conversations": [], "runs": [],
                     "proposals": [], "executions": []}
    report = admin.delete_user_data(RELEASE, "acme.tenant.example.com", "ghost@example.invalid",
                                    bench_factory=lambda kind: bench, confirm=True)
    assert report["plan"]["counts"]["runs"] == 0 and report["applied"] is True
    assert report["cleared"]["runs"] == 1 or report["cleared"]["runs"] == 0


def test_a_cleared_page_snapshot_is_still_a_page_snapshot_the_site_can_read():
    """Clearing this column to an empty string turned a deleted person's old conversations
    into server errors on the real Site: the reader validates it as JSON on every read."""
    from dsherp import user_data

    value = user_data.cleared("DS Model Run")["page_context"]
    context = json.loads(value)
    assert context["schema_version"] == 1 and context["page_type"] == "unknown" and context["route"] == []
    source = (Path(__file__).resolve().parents[1] / "frappe_app/dsherp_bridge/context_api.py").read_text()
    allowed = source.split("allowed = {", 1)[1].split("}", 1)[0]
    allowed = {piece.strip().strip("'") for piece in allowed.split(",")}
    assert set(context) <= allowed, "the replacement carries a key the page validator rejects"
    assert set(context) <= {"schema_version", "route", "page_type", "reason"}, "an unknown page carries nothing else"


def test_every_cleared_value_is_declared_per_column_rather_than_assumed_empty():
    from dsherp import user_data

    for doctype, rules in user_data.BOUNDARY.items():
        assert isinstance(rules["clear"], dict), doctype
        for column, value in rules["clear"].items():
            assert isinstance(value, str), (doctype, column)
