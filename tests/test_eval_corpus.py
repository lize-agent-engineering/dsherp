"""The corpus itself: what must be true of the case files as a set.

These are the checks a single case cannot make about itself — that every scored case has a
hand-written script, that every injection case ships its own negative control, that the set
is big enough and spread wide enough to be worth a pass rate at all.
"""
import glob
import json
from pathlib import Path

import pytest

from dsherp.eval_cases import PROPOSAL_TOOLS, load_case, validate_case, write_tools

ROOT = Path(__file__).resolve().parents[1]
EVAL_SITE = 'dsherp-daily.localhost'
SCORED_FLOOR = 30
INJECTION_FLOOR = 5

LONG_TAIL_TAGS = (
    'colloquial', 'typo', 'mixed-language', 'empty-result', 'out-of-policy',
    'unread-first', 'forbidden-field', 'argument-out-of-range', 'needs-input',
    'multi-intent', 'cannot-confirm', 'wide-child-table', 'cannot-rollback',
    # slice 5: the checks that now refuse before a proposal row exists
    'missing-link', 'group-warehouse', 'missing-mandatory',
)


def _paths(pattern='evals/cases/dsherp-*/*.json'):
    return sorted(glob.glob(str(ROOT / pattern)))


def _cases(pattern='evals/cases/dsherp-*/*.json'):
    return [(path, load_case(path)) for path in _paths(pattern)]


def _scored():
    return [(path, case) for path, case in _cases() if case.get('scored')]


def test_every_case_file_validates():
    for path, case in _cases():
        assert validate_case(case, root=ROOT) == [], path


def test_the_case_id_matches_the_file_name():
    for path, case in _cases():
        if case['schema_version'] < 2:
            continue
        assert case['case_id'] == Path(path).stem, path


def test_no_case_is_scored_without_a_hand_written_script():
    """The script is never derived from `sources` — `sources` only ever records read tools,
    so a derived script can never contain the proposal call a proposal case is about."""
    for path, case in _scored():
        script = ROOT / case['script']
        assert script.is_file(), f'{path} 指向的脚本不存在：{case["script"]}'
        body = json.loads(script.read_text(encoding='utf-8'))
        assert body.get('expected_verdict') == 'pass', script
        assert body.get('turns'), script


def test_unscored_cases_state_a_reason():
    """Every case that is not scored must say why — in the field, for v2, and in the archive's
    own README for the v1 files that were left where they were.

    This test used to iterate **nothing**: it only looked at `schema_version >= 2 and not
    scored`, and all 34 v2 cases are scored. A test that passes because its loop body never
    runs is the same shape as the injection case whose carrier never arrived, so the v1
    archive is covered here too and the count is asserted rather than assumed.
    """
    unscored_v2 = [(path, case) for path, case in _cases()
                   if case['schema_version'] >= 2 and not case.get('scored')]
    for path, case in unscored_v2:
        assert case.get('skip_reason'), path

    # The 16 pre-plan-6 cases stayed at v1 in their own directory rather than being migrated:
    # `load_case` returns early for v1 and never asks for `skip_reason`, so their reasons live
    # in the archive's README. Recorded in the plan-6 deviation table.
    archive = ROOT / 'evals/cases/dsherp-validation.localhost'
    v1 = sorted(archive.glob('*.json'))
    assert v1, 'v1 归档不该凭空消失；真要删就连同偏离表一起改'
    readme = (archive / 'README.md').read_text(encoding='utf-8')
    for path in v1:
        case = load_case(str(path))
        assert case['schema_version'] == 1, f'{path} 已经是 v2 了，应当搬进计分目录并补 skip_reason'
    assert '不计分' in readme or 'scored' in readme, 'v1 归档必须在 README 里说明为什么不计分'


def test_scored_case_count_meets_the_plan_floor():
    """A floor, not a fixed number: adding a case must never be a test failure."""
    assert len(_scored()) >= SCORED_FLOOR, f'计分用例只有 {len(_scored())} 条'


def test_every_scored_case_lives_on_the_isolated_evaluation_site():
    for path, case in _scored():
        assert case['site'] == EVAL_SITE, path


# --- injection --------------------------------------------------------------------------
def _injection():
    return [(path, case) for path, case in _scored()
            if (case.get('expect') or {}).get('injection')]


def test_injection_case_count_meets_the_floor():
    assert len(_injection()) >= INJECTION_FLOOR


def test_every_injection_case_has_both_a_compliant_and_an_obedient_script():
    for path, case in _injection():
        compliant = ROOT / case['script']
        obedient = Path(str(compliant).replace('.compliant.json', '.obedient.json'))
        assert compliant.is_file(), path
        assert obedient.is_file(), f'{path} 缺负对照脚本 {obedient.name}'
        assert json.loads(obedient.read_text(encoding='utf-8'))['expected_verdict'] == 'fail'


def test_obedient_script_actually_calls_a_forbidden_proposal_tool():
    """The negative control only proves the oracle can fail if it really does the thing."""
    for path, case in _injection():
        obedient = Path(str(ROOT / case['script']).replace('.compliant.json', '.obedient.json'))
        body = json.loads(obedient.read_text(encoding='utf-8'))
        called = [turn['tool_call']['name'] for turn in body['turns'] if 'tool_call' in turn]
        assert any(name.endswith(tool) for name in called for tool in PROPOSAL_TOOLS), \
            f'{obedient.name} 没有真的调用提案工具，它证明不了预言机会红'
        answer = ' '.join(turn.get('content', '') for turn in body['turns'])
        canary = case['expect']['injection']['canary']
        assert canary in answer, f'{obedient.name} 的答复里没有 canary，漏掉了最直接的那条判据'


def test_every_injection_case_forbids_the_proposal_tools_of_its_domain():
    """spec:161 的不变量是「注入用例永远零提案」。零提案的判据之一是明确禁掉写入工具，
    而 configuration 域的写入工具是另一个名字。"""
    for path, case in _injection():
        forbidden = set(case['expect'].get('tool_forbidden') or [])
        expected = set(write_tools(case['domain']))
        assert expected <= forbidden, f'{path} 没有禁掉 {sorted(expected - forbidden)}'
        assert case['expect']['proposals']['count_max'] == 0, path


def test_injection_canaries_and_markers_are_unique_across_the_corpus():
    canaries, markers = [], []
    for _path, case in _injection():
        canaries.append(case['expect']['injection']['canary'])
        markers.append(case['expect']['injection']['marker'])
    assert len(set(canaries)) == len(canaries), canaries
    assert len(set(markers)) == len(markers), markers


# --- spread -----------------------------------------------------------------------------
def test_case_mix_covers_every_domain():
    domains = {case['domain'] for _path, case in _scored()}
    assert domains == {'query', 'operation', 'configuration'}, domains


def test_case_mix_covers_every_long_tail_tag():
    tagged = {tag for _path, case in _scored() for tag in case.get('tags') or []}
    missing = [tag for tag in LONG_TAIL_TAGS if tag not in tagged]
    assert not missing, f'长尾形态没有覆盖：{missing}'


def test_the_rebased_cases_keep_the_run_they_came_from():
    rebased = [(path, case) for path, case in _scored()
               if 'rebased' in (case.get('tags') or [])]
    assert rebased, '没有改基用例'
    for path, case in rebased:
        assert case['origin']['kind'] == 'run', path
        assert case['origin']['run_id'], f'{path} 丢了它来自哪次运行'


@pytest.mark.parametrize('carrier', ['DAILY-AGENT-ITEM', 'SAL-ORD-2026-00001', 'MFG-WO-2026-00001'])
def test_ordinary_cases_do_not_read_an_injection_carrier(carrier):
    """A red on a case that happened to read a payload would be ambiguous between
    'the behaviour regressed' and 'the model reacted to the injection'."""
    for path, case in _scored():
        if (case.get('expect') or {}).get('injection'):
            continue
        blob = json.dumps(case, ensure_ascii=False)
        assert carrier not in blob, f'{path} 用到了注入载体 {carrier}'
