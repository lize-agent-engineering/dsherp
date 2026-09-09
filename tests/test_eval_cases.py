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

    assert case["schema_version"] == 2 and case["run_id"] == "r1" and case["site"] == "alpha"
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


# --- schema v2 -----------------------------------------------------------------------------
import glob  # noqa: E402
from pathlib import Path  # noqa: E402

from dsherp import eval_cases  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _v2(**overrides):
    case = {
        'schema_version': 2, 'case_id': 'sample-case-01', 'site': 'dsherp-daily.localhost',
        'run_id': 'r1', 'origin': {'kind': 'synthetic', 'run_id': None}, 'domain': 'query',
        'question': 'q', 'page_context': {}, 'status': 'Succeeded', 'error': '', 'sources': [],
        'events': [], 'proposals': [], 'created': '2026-09-08 00:00:00',
        'scored': False, 'skip_reason': '示例', 'tags': [], 'setup': {'fixtures': []},
        'script': '', 'expect': {},
    }
    case.update(overrides)
    return case


def test_v1_case_still_loads_and_is_never_scored():
    """The 16 exported runs stay readable: they are audit facts, not expectations."""
    paths = sorted(glob.glob(str(ROOT / 'evals/cases/dsherp-*/*.json')))
    assert paths, 'no exported cases to check'
    for path in paths:
        case = eval_cases.load_case(path)
        if case['schema_version'] == 1:
            assert eval_cases.validate_case(case, root=ROOT) == [], path
            assert not case.get('scored'), path


def test_scored_v2_case_requires_expectations_and_an_existing_script(tmp_path):
    bare = _v2(scored=True, skip_reason='')
    problems = eval_cases.validate_case(bare, root=tmp_path)
    assert any('非空 expect' in p for p in problems), problems
    assert any('script' in p for p in problems), problems

    named = _v2(scored=True, skip_reason='', expect={'final_status': ['Succeeded']},
                script='evals/scripts/nope.compliant.json')
    assert any('script 文件不存在' in p for p in eval_cases.validate_case(named, root=tmp_path))

    (tmp_path / 'evals/scripts').mkdir(parents=True)
    (tmp_path / 'evals/scripts/nope.compliant.json').write_text('{}')
    assert eval_cases.validate_case(named, root=tmp_path) == []


def test_unknown_expect_key_is_rejected():
    case = _v2(expect={'final_status': ['Succeeded'], 'tool_prefixes': []})
    assert any('未知键' in p for p in eval_cases.validate_case(case, root=ROOT))


def test_unknown_tool_in_an_expectation_is_rejected():
    assert any('未知工具' in p for p in eval_cases.validate_case(
        _v2(expect={'tool_prefix': [{'tool': 'erp_delete_everything', 'arguments': {}}]}), root=ROOT))
    assert any('未知工具' in p for p in eval_cases.validate_case(
        _v2(expect={'tool_forbidden': ['erp_nope']}), root=ROOT))


def test_a_wire_prefixed_tool_name_is_the_same_tool():
    assert eval_cases.tool_name('mcp__erp__erp_read_record') == 'erp_read_record'
    assert eval_cases.validate_case(
        _v2(expect={'tool_prefix': [{'tool': 'mcp__erp__erp_read_record', 'arguments': {}}]}),
        root=ROOT) == []


def test_unscored_cases_must_state_a_reason():
    assert any('skip_reason' in p for p in eval_cases.validate_case(
        _v2(scored=False, skip_reason=''), root=ROOT))


def test_injection_case_requires_canary_and_marker():
    good = {'planted_in': {'doctype': 'Item', 'name': 'X', 'field': 'description'},
            'canary': 'DSHERP-INJ-CANARY-01', 'marker': 'DSHERP-INJ-MARK-01'}
    assert eval_cases.validate_case(_v2(expect={'injection': good}), root=ROOT) == []
    for bad in ({**good, 'canary': 'CANARY-01'}, {**good, 'marker': 'MARK'},
                {**good, 'planted_in': {'doctype': 'Item'}}):
        assert eval_cases.validate_case(_v2(expect={'injection': bad}), root=ROOT), bad


def test_upgrade_case_never_marks_a_case_scored():
    path = sorted(glob.glob(str(ROOT / 'evals/cases/dsherp-*/*.json')))[0]
    upgraded = eval_cases.upgrade_case(eval_cases.load_case(path))
    assert upgraded['schema_version'] == 2
    assert upgraded['scored'] is False
    assert upgraded['skip_reason'] == eval_cases.DRAFT_REASON


def test_upgrade_case_derives_only_read_tools_from_sources():
    """`run.sources` is appended in the read-tool branch of `_run_tool`; the proposal tools
    return before reaching it. A mechanically derived prefix therefore can never contain the
    call a proposal case is about — which is why replay scripts are hand-written."""
    case = {'schema_version': 1, 'site': 's', 'domain': 'operation', 'run_id': 'r1',
            'status': 'Failed', 'sources': [{'tool': 'erp_read_schema', 'arguments': {'doctype': 'Item'}},
                                            {'tool': 'erp_read_record', 'arguments': {'doctype': 'Item', 'name': 'I'}}],
            'proposals': [{'id': 'p1', 'status': 'Pending', 'summary': 'create · Item'}]}
    upgraded = eval_cases.upgrade_case(case)
    tools = [step['tool'] for step in upgraded['expect']['tool_prefix']]
    assert tools == ['erp_read_schema', 'erp_read_record']
    assert not any(tool in eval_cases.PROPOSAL_TOOLS for tool in tools)
    assert upgraded['expect']['proposals']['count_max'] == 1


def test_build_case_defaults_to_an_unscored_v2_case():
    run = {'name': 'r9', 'domain': 'query', 'question': 'q', 'page_context': '{}',
           'status': 'Failed', 'error': '', 'sources': '[]', 'creation': '2026-09-08 00:00:00'}
    case = eval_cases.build_case(run, [], [], 'dsherp-daily.localhost')
    assert case['schema_version'] == 2 and case['scored'] is False
    assert case['origin'] == {'kind': 'run', 'run_id': 'r9'}


def test_a_tool_the_domain_does_not_offer_is_rejected():
    """A query-domain case naming erp_propose_update would fail as UNKNOWN_TOOL inside the
    harness — a red that says nothing about the behaviour the case meant to measure."""
    case = _v2(domain='query', expect={'tool_prefix': [{'tool': 'erp_propose_update', 'arguments': {}}]})
    assert any('query 域没有' in p for p in eval_cases.validate_case(case, root=ROOT))
    case = _v2(domain='configuration',
               expect={'tool_prefix': [{'tool': 'erp_read_record', 'arguments': {}}]})
    assert any('configuration 域没有' in p for p in eval_cases.validate_case(case, root=ROOT))
    ok = _v2(domain='operation', expect={'tool_prefix': [{'tool': 'erp_propose_update', 'arguments': {}}]})
    assert eval_cases.validate_case(ok, root=ROOT) == []


def test_write_tools_are_the_ones_that_domain_actually_offers():
    assert eval_cases.write_tools('query') == ()
    assert 'erp_propose_configuration' not in eval_cases.write_tools('operation')
    assert eval_cases.write_tools('configuration') == ('erp_propose_configuration',)
