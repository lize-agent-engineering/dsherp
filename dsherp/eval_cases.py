"""Build sanitized evaluation cases from recorded model runs, and say what each one expects.

Schema v1 recorded what a run *did*. v2 adds what a case *should* produce — the oracle's
input — plus the bookkeeping that keeps an evaluation honest:

- `scored` / `skip_reason`: a case nobody has confirmed is not a score. `upgrade_case`
  therefore always produces `scored=False`; a person has to read it and say otherwise.
- `script`: the replay turns are a **hand-written** artefact under `evals/scripts/`. They are
  never derived from `sources`, because `sources` only ever records read tools — the proposal
  tools return straight out of `_run_tool` and never reach it — so a mechanically derived
  script can never contain the very calls a proposal case is about. `validate_case` refuses a
  scored case whose script file is missing; that check is the gate on this discipline.
- `expect` has a **closed** key set: an unknown key is a typo that would otherwise silently
  assert nothing.
"""

import json
import re
from pathlib import Path

from dsherp.run_events import sanitize

SCHEMA_VERSION = 2

# The tools a case may name. Bare names; the wire prefixes them with `mcp__erp__` and the
# event stream does too, so `tool_name` normalises before comparing.
TOOL_NAMES = (
    'erp_read_schema', 'erp_read_record', 'erp_search_records', 'erp_request_input',
    'erp_propose_update', 'erp_propose_create', 'erp_propose_action', 'erp_propose_fill',
    'erp_propose_make', 'erp_read_configuration', 'erp_propose_configuration', 'skill',
)
PROPOSAL_TOOLS = tuple(name for name in TOOL_NAMES if name.startswith('erp_propose_'))
TOOL_PREFIX = 'mcp__erp__'

# Closed on purpose: an `expect` key nobody reads asserts nothing, and reads as coverage.
EXPECT_KEYS = ('tool_prefix', 'tool_forbidden', 'final_status', 'proposals', 'refusal',
               'answer_must_contain', 'answer_must_not_contain', 'injection')
ORIGIN_KINDS = ('run', 'synthetic')
STATUSES = ('Queued', 'Running', 'Cancelling', 'Cancelled', 'Succeeded', 'Failed',
            'NeedsInput', 'BudgetExceeded')
DOMAINS = ('query', 'operation', 'configuration')
CASE_ID = re.compile(r'^[a-z][a-z0-9-]{2,63}$')
CANARY = re.compile(r'^DSHERP-INJ-CANARY-\d{2}$')
MARKER = re.compile(r'^DSHERP-INJ-MARK-\d{2}$')
REFUSAL_CLASSES = ('validation', 'permission', 'transient')
DRAFT_REASON = '期望与回放脚本由机械派生，必须人工确认后才计分'


def tool_name(name):
    """`mcp__erp__erp_read_record` and `erp_read_record` are the same tool."""
    text = str(name or '')
    return text[len(TOOL_PREFIX):] if text.startswith(TOOL_PREFIX) else text


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


def build_case(run, events, proposals, site, *, case_id=None, expect=None, origin=None,
               tags=(), scored=False, skip_reason='', setup=None, script=''):
    """A v2 case. v1's fields are all kept: they are the audit facts the case rests on."""
    page_context = sanitize(_parse_json_field(run["page_context"]))
    sources = sanitize(_parse_json_field(run["sources"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id or run["name"],
        "site": site,
        "run_id": run["name"],
        "origin": origin or {"kind": "run", "run_id": run["name"]},
        "domain": run["domain"],
        "question": sanitize(run.get("question")),
        "page_context": page_context,
        "status": run["status"],
        "error": sanitize(run.get("error")),
        "sources": sources,
        "events": sanitize(events),
        "proposals": [_proposal_summary(item) for item in proposals],
        "created": run["creation"],
        "scored": bool(scored),
        "skip_reason": skip_reason,
        "tags": list(tags),
        "setup": setup or {"fixtures": []},
        "script": script,
        "expect": expect or {},
    }


def load_case(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def upgrade_case(case):
    """v1 → v2, mechanically, and **never scored**.

    The derived `tool_prefix` can only ever contain read tools: `run.sources` is appended in
    the read-tool branch of `_run_tool`, while the proposal tools return before reaching it.
    So for any case that is about a proposal, the derived prefix is not just incomplete —
    it is missing exactly the calls that matter. Hence the draft gate.
    """
    upgraded = dict(case)
    upgraded['schema_version'] = SCHEMA_VERSION
    upgraded.setdefault('case_id', case.get('run_id') or '')
    upgraded['origin'] = {'kind': 'run', 'run_id': case.get('run_id')}
    upgraded.setdefault('tags', [])
    upgraded.setdefault('setup', {'fixtures': []})
    upgraded.setdefault('script', '')
    prefix = [{'tool': tool_name(source.get('tool')), 'arguments': source.get('arguments') or {}}
              for source in case.get('sources') or [] if source.get('tool')]
    summary = [{'summary': item.get('summary', '')} for item in case.get('proposals') or []]
    expect = {'tool_prefix': prefix,
              'proposals': {'count_max': len(summary), 'summary': summary}}
    if case.get('status') in STATUSES:
        expect['final_status'] = [case['status']]
    upgraded['expect'] = expect
    upgraded['scored'] = False
    upgraded['skip_reason'] = DRAFT_REASON
    return upgraded


def _expect_problems(expect, root):
    problems = []
    unknown = sorted(set(expect) - set(EXPECT_KEYS))
    if unknown:
        problems.append('expect 出现未知键：' + ', '.join(unknown))
    for step in expect.get('tool_prefix') or []:
        if not isinstance(step, dict) or tool_name(step.get('tool')) not in TOOL_NAMES:
            problems.append(f'tool_prefix 里有未知工具：{step!r}')
        elif not isinstance(step.get('arguments', {}), dict):
            problems.append(f'tool_prefix 的 arguments 必须是对象：{step!r}')
    for name in expect.get('tool_forbidden') or []:
        if tool_name(name) not in TOOL_NAMES:
            problems.append(f'tool_forbidden 里有未知工具：{name!r}')
    for status in expect.get('final_status') or []:
        if status not in STATUSES:
            problems.append(f'final_status 里有未知状态：{status!r}')
    proposals = expect.get('proposals')
    if proposals is not None:
        if not isinstance(proposals, dict) or not isinstance(proposals.get('count_max'), int):
            problems.append('proposals 必须带整数 count_max')
        elif proposals['count_max'] < 0:
            problems.append('proposals.count_max 不能为负')
    refusal = expect.get('refusal')
    if refusal is not None:
        if not isinstance(refusal, dict) or refusal.get('error_class') not in REFUSAL_CLASSES:
            problems.append(f'refusal.error_class 必须是 {REFUSAL_CLASSES} 之一')
    injection = expect.get('injection')
    if injection is not None:
        if not isinstance(injection, dict):
            problems.append('injection 必须是对象')
        else:
            if not CANARY.match(str(injection.get('canary'))):
                problems.append('injection.canary 必须形如 DSHERP-INJ-CANARY-0N')
            if not MARKER.match(str(injection.get('marker'))):
                problems.append('injection.marker 必须形如 DSHERP-INJ-MARK-0N')
            planted = injection.get('planted_in')
            if not isinstance(planted, dict) or not planted.get('doctype') or not planted.get('field'):
                problems.append('injection.planted_in 必须写明 doctype 与 field')
    for key in ('answer_must_contain', 'answer_must_not_contain'):
        value = expect.get(key)
        if value is not None and (not isinstance(value, list)
                                  or any(not isinstance(item, str) for item in value)):
            problems.append(f'{key} 必须是字符串数组')
    return problems


def validate_case(case, root=None):
    """Everything wrong with this case, as a list of Chinese sentences. Empty means valid."""
    problems = []
    version = case.get('schema_version')
    if version not in (1, SCHEMA_VERSION):
        return [f'未知 schema_version：{version!r}']
    if not case.get('site'):
        problems.append('缺 site')
    if case.get('domain') not in DOMAINS:
        problems.append(f'未知 domain：{case.get("domain")!r}')
    if version == 1:
        # v1 cases stay readable for the audit trail, but they carry no expectations and so
        # can never be a score.
        return problems
    if not CASE_ID.match(str(case.get('case_id'))):
        problems.append(f'case_id 必须是可读 slug：{case.get("case_id")!r}')
    origin = case.get('origin')
    if not isinstance(origin, dict) or origin.get('kind') not in ORIGIN_KINDS:
        problems.append(f'origin.kind 必须是 {ORIGIN_KINDS} 之一')
    elif origin['kind'] == 'run' and not origin.get('run_id'):
        problems.append('origin.kind=run 必须带 run_id')
    if not isinstance(case.get('tags'), list):
        problems.append('tags 必须是数组')
    expect = case.get('expect')
    if not isinstance(expect, dict):
        problems.append('expect 必须是对象')
        expect = {}
    problems += _expect_problems(expect, root)
    if case.get('scored'):
        if not expect:
            problems.append('计分用例必须有非空 expect')
        script = case.get('script') or ''
        if not script:
            problems.append('计分用例必须指名 script')
        elif root is not None and not (Path(root) / script).is_file():
            problems.append(f'script 文件不存在：{script}')
    elif not case.get('skip_reason'):
        problems.append('不计分的用例必须写 skip_reason')
    return problems
