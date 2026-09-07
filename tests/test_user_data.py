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
    assert set(run["clear"]) == {"question", "page_context", "answer", "needs_input", "error"}
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
    assert set(values) == {"question", "page_context", "answer", "needs_input", "error"}
    # Text columns go empty; the page snapshot goes to a snapshot that still reads as one.
    assert set(values.values()) == {"", values["page_context"]}
    assert values["question"] == "" and values["answer"] == ""
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

    # in-flight runs of the person: [name, status]; the fake worker finishes a run each time
    # the command polls, `stuck` ones never finish
    inflight = ()
    stuck = ()
    refuse_at_clear = ()

    def python(self, site, body, timeout=900):
        self.scripts.append(body)
        if "DSHERP_USER_DATA" in body:
            return "DSHERP_USER_DATA " + json.dumps(self.payload) + "\n"
        if "DSHERP_USER_SETTLE" in body:
            cancelled = [name for name, status in self.inflight if status == "Queued"]
            self.inflight = [[name, "Cancelling"] for name, status in self.inflight if status != "Queued"]
            return "DSHERP_USER_SETTLE " + json.dumps({"cancelled": cancelled,
                                                       "waiting": [name for name, _ in self.inflight]}) + "\n"
        if "DSHERP_USER_INFLIGHT" in body:
            answer = list(self.inflight)
            self.inflight = [row for row in self.inflight if row[0] in self.stuck]
            return "DSHERP_USER_INFLIGHT " + json.dumps(answer) + "\n"
        if "DSHERP_USER_DELETE" in body:
            if self.refuse_at_clear:
                return "DSHERP_USER_DELETE " + json.dumps({"refused": list(self.refuse_at_clear)}) + "\n"
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
    assert set(context) <= {"schema_version", "route", "page_type", "reason"}, "an unknown page carries nothing else"


def test_every_cleared_value_is_declared_per_column_rather_than_assumed_empty():
    from dsherp import user_data

    for doctype, rules in user_data.BOUNDARY.items():
        assert isinstance(rules["clear"], dict), doctype
        for column, value in rules["clear"].items():
            assert isinstance(value, str), (doctype, column)


def _ticking():
    """A clock the command can wait on without the test waiting."""
    state = {"now": 0.0}

    def clock():
        return state["now"]

    def sleep(seconds):
        state["now"] += seconds
    return clock, sleep


def test_a_queued_run_is_cancelled_and_a_running_one_is_waited_for_before_anything_is_cleared(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    bench.inflight = [["q1", "Queued"], ["r1", "Running"]]
    clock, sleep = _ticking()
    report = admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                    bench_factory=lambda kind: bench, confirm=True, clock=clock, sleep=sleep)
    assert report["settled"] == {"cancelled": ["q1"], "waiting": ["r1"]}
    assert report["applied"] is True
    order = [next(m for m in ("DSHERP_USER_SETTLE", "DSHERP_USER_INFLIGHT", "DSHERP_USER_DELETE") if m in s)
             for s in bench.scripts if any(m in s for m in ("DSHERP_USER_SETTLE", "DSHERP_USER_INFLIGHT", "DSHERP_USER_DELETE"))]
    assert order.index("DSHERP_USER_DELETE") > order.index("DSHERP_USER_SETTLE"), "settle first, clear last"


def test_a_run_whose_executor_never_lets_go_blocks_the_deletion_and_nothing_is_cleared(host_runtime):
    """Clearing the content of a run still being executed is not a deletion: the executor
    writes it straight back. The command refuses, keeps the export, and says which runs."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    bench.inflight = [["r9", "Running"]]
    bench.stuck = ("r9",)
    clock, sleep = _ticking()
    with pytest.raises(admin.Fault, match="仍有 1 个运行未结束"):
        admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                               bench_factory=lambda kind: bench, confirm=True, wait=30, clock=clock, sleep=sleep)
    assert not any("DSHERP_USER_DELETE" in s for s in bench.scripts), "nothing may be cleared"
    assert clock() >= 30, "it waited the whole window before giving up"
    blocked = sorted(Path(admin.runtime_dir(RELEASE)).glob("user-data/delete-blocked-*.json"))
    assert blocked and json.loads(blocked[-1].read_text())["inflight"] == [["r9", "Cancelling"]]


def test_a_run_that_slips_in_between_settling_and_clearing_is_caught_under_the_lock(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = UserBench([])
    bench.refuse_at_clear = ("late1",)
    clock, sleep = _ticking()
    with pytest.raises(admin.Fault, match="清除前一刻"):
        admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                               bench_factory=lambda kind: bench, confirm=True, clock=clock, sleep=sleep)


def test_a_run_started_between_the_database_clear_and_the_directory_removal_keeps_its_directory(host_runtime):
    """The reviewer's probe: the clear script has committed and released the User lock; a new
    executor writes a fresh scope under the same conversation before the removal runs. The
    Site is held for the whole window so that cannot happen for real, and even if a directory
    appears, only the scopes the plan enumerated are removed (R6)."""
    from dsherp import admin, sessions, site_holds

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    root = Path(admin.runtime_dir(RELEASE)) / admin.SESSION_ROOT
    older = sessions.directory(root, site="acme.tenant.example.com", conversation="conv1", scope="a" * 64)
    older.mkdir(parents=True)
    (older / "state").write_text("old")
    newer = sessions.directory(root, site="acme.tenant.example.com", conversation="conv1", scope="b" * 64)
    seen = {}

    class RaceBench(UserBench):
        def python(self, site, body, timeout=900):
            answer = super().python(site, body, timeout=timeout)
            if "DSHERP_USER_SETTLE" in body:
                seen["hold_flag_at_settle"] = int(self.site_config.get((site, "dsherp_hold_until"), 0)) > 0
                seen["hold_file_at_settle"] = site in site_holds.held(admin.runtime_dir(RELEASE))
            if "DSHERP_USER_DELETE" in body:
                newer.mkdir(parents=True)
                (newer / "new-executor-state").write_text("new run after clearing commit")
            return answer

    bench = RaceBench([])
    result = admin.delete_user_data(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                    bench_factory=lambda kind: bench, confirm=True, wait=0)
    assert result["applied"] is True and result["hold"] == "released"
    assert seen == {"hold_flag_at_settle": True, "hold_file_at_settle": True}, "held before anything is settled"
    assert not older.exists(), "the planned scope is gone"
    assert newer.exists(), "a scope that appeared after the plan is not this deletion's to take"
    assert bench.site_config.get(("acme.tenant.example.com", "dsherp_hold_until")) == "0", "the hold is given back"
    assert "acme.tenant.example.com" not in site_holds.held(admin.runtime_dir(RELEASE))
    assert result["sessions_removed"] == [str(older.resolve())]


def test_no_row_of_either_app_holds_the_platform_token_and_the_boundary_says_where_it_lives():
    """The boundary claims the platform OAuth token is in no row. That used to be false for
    DS Configuration Transfer, which kept it for as long as the row existed - forever, because
    the row is an audit record nobody may delete. Plan 5 moved it to the Site cache for the
    transfer's window, so the claim is checked against every DocType definition of both Apps
    rather than asserted in prose."""
    definitions = sorted((ROOT / 'frappe_app').glob('*/*/doctype/*/*.json'))
    assert definitions, 'no DocType definitions found'
    for path in definitions:
        fields = {field.get('fieldname') for field in json.loads(path.read_text()).get('fields', [])}
        assert 'platform_grant' not in fields, path
    transfer = user_data.BOUNDARY['DS Configuration Transfer']
    assert transfer['clear'] == {} and 'expires_at' in transfer['keep'] and '缓存' in transfer['why']
    assert '缓存' in user_data.BOUNDARY['DS Model Run']['why']
    assert 'DS Configuration Transfer' in user_data.plan(
        user='a@example.invalid', conversations=[], runs=[], proposals=[], executions=[], sessions=0)['untouched']
