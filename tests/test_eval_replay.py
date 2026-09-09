"""The replay container is the production container, plus a stub and a script.

Host-side only: no container is started here. What matters is that nothing about the
isolation changes — an evaluation run under different isolation measures a different system.
"""
import json
import os
import tempfile
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsherp import deploy_env  # noqa: E402
from evals import model_server, replay  # noqa: E402

RESOLVED = {**deploy_env.settings({'DSHERP_ENV': 'dev'}), 'env': 'dev'}


def _script(tmp_path, **overrides):
    script = {'expected_verdict': 'pass',
              'turns': [{'tool_call': {'name': 'mcp__erp__erp_read_record',
                                       'arguments': json.dumps({'doctype': 'Item', 'name': 'I-1'})}},
                        {'content': '读到了 I-1', 'finish_reason': 'stop'}],
              'usage': {'input_tokens': 900, 'output_tokens': 40}}
    script.update(overrides)
    path = tmp_path / 'case.compliant.json'
    path.write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')
    return path


def test_replay_command_mounts_the_server_and_the_script_read_only(tmp_path):
    secret = tmp_path / 'run.json'
    secret.write_text('{}')
    script = _script(tmp_path)
    command = replay.replay_command(ROOT, secret, tmp_path / 'session', 'eval-1', script,
                                    resolved=RESOLVED)
    joined = ' '.join(command)
    assert f'{ROOT}/evals/model_server.py:/run/model_fixture.py:ro' in joined
    assert f'{script}:/run/eval_script.json:ro' in joined
    assert command[-2] == '-c' and 'run_business' in command[-1]
    # stdout must stay exactly what the worker's run_container parses: a bare result JSON.
    printed = [line for line in command[-1].splitlines() if line.startswith('print(')]
    assert printed == ["print(json.dumps(result,ensure_ascii=False))"], printed


def test_replay_command_keeps_every_production_container_flag(tmp_path):
    from dsherp.context_container import docker_command
    secret = tmp_path / 'run.json'
    secret.write_text('{}')
    resolved = RESOLVED
    production = docker_command(ROOT, secret, tmp_path / 'session', 'eval-1', resolved=resolved)
    replayed = replay.replay_command(ROOT, secret, tmp_path / 'session', 'eval-1',
                                     _script(tmp_path), resolved=resolved)
    # Everything production sets, the replay still sets: the only difference is the two extra
    # mounts and the entrypoint arguments.
    assert set(flag for flag in production if flag.startswith('--')) <= set(replayed)
    for flag in ('--read-only', '--cap-drop', '--user', '--network'):
        assert production.count(flag) == replayed.count(flag), flag
    assert replayed[:2] == production[:2]
    assert production[-2:] == ['-m', 'dsherp.context_runner']


def test_model_server_replays_turns_in_order_and_reports_usage(tmp_path):
    import urllib.request
    settings, requests, state = model_server.serve(_script(tmp_path), port=0)
    base = settings['DEEPSEEK_BASE_URL']
    try:
        def ask(compact=False):
            headers = {'Content-Type': 'application/json'}
            if compact:
                headers[model_server.COMPACT_HEADER] = '1'
            request = urllib.request.Request(base + '/chat/completions',
                                             data=json.dumps({'messages': []}).encode(),
                                             headers=headers)
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.read().decode()

        first = ask()
        assert 'tool_calls' in first and 'erp_read_record' in first
        # usage rides its own trailing chunk with an empty choices, as OpenAI-compatible
        # providers send it; hanging it off a delta chunk makes the harness reject the turn.
        usage_chunk = [line for line in first.splitlines() if '"usage"' in line]
        assert len(usage_chunk) == 1 and '"choices": []' in usage_chunk[0]
        # The provider's own key names. Anything else and the client's usage parser leaves
        # its fields undefined, the chunk it builds stops being JSON-serializable, and the
        # harness kills the turn with an error that says nothing about usage.
        assert '"prompt_tokens": 900' in usage_chunk[0]
        assert '"completion_tokens": 40' in usage_chunk[0]
        assert '"total_tokens": 940' in usage_chunk[0]
        second = ask()
        assert '读到了 I-1' in second and 'tool_calls' not in second
        third = ask()
        assert '读到了 I-1' in third, 'past the end the last turn repeats'
    finally:
        state['shutdown']()
    assert len(requests) == 3


def test_model_server_always_answers_a_compaction_request_with_text(tmp_path):
    """A scripted tool call replayed into a compaction request would land in the wrong place
    and shift every later turn."""
    import urllib.request
    settings, requests, state = model_server.serve(_script(tmp_path), port=0)
    try:
        request = urllib.request.Request(settings['DEEPSEEK_BASE_URL'] + '/chat/completions',
                                         data=b'{"messages": []}',
                                         headers={'Content-Type': 'application/json',
                                                  model_server.COMPACT_HEADER: '1'})
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode()
        assert 'tool_calls' not in body and '回放压缩摘要' in body
        # ...and the compaction did not consume a turn: the next real call is still turn 1.
        with urllib.request.urlopen(urllib.request.Request(
                settings['DEEPSEEK_BASE_URL'] + '/chat/completions', data=b'{"messages": []}',
                headers={'Content-Type': 'application/json'}), timeout=5) as response:
            assert 'erp_read_record' in response.read().decode()
    finally:
        state['shutdown']()


def test_a_script_without_an_expected_verdict_is_refused(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps({'turns': [{'content': 'x'}]}))
    with pytest.raises(ValueError, match='expected_verdict'):
        model_server.load_script(path)


def test_a_tool_call_turn_needs_string_arguments(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps({'expected_verdict': 'pass',
                                'turns': [{'tool_call': {'name': 'x', 'arguments': {'a': 1}}}]}))
    with pytest.raises(ValueError, match='arguments'):
        model_server.load_script(path)


def test_usage_is_normalised_to_the_provider_key_names():
    assert model_server.wire_usage({'input_tokens': 5, 'output_tokens': 2}) == \
        {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}
    assert model_server.wire_usage({'prompt_tokens': 5, 'completion_tokens': 2}) == \
        {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}
    assert model_server.wire_usage(None) == \
        {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}


def test_newest_wire_reads_the_last_recorded_wire(tmp_path):
    assert replay.newest_wire(tmp_path) is None, 'a live run records no wire'
    first = tmp_path / 'a'; first.mkdir()
    (first / replay.WIRE_FILE).write_text(json.dumps({'requests': [{'n': 1}]}))
    second = tmp_path / 'b'; second.mkdir()
    later = second / replay.WIRE_FILE
    later.write_text(json.dumps({'requests': [{'n': 2}]}))
    os.utime(later, (2 ** 31, 2 ** 31))
    assert replay.newest_wire(tmp_path)['requests'] == [{'n': 2}]


def test_a_placeholder_is_filled_from_the_last_tool_result():
    """Without this, a replay script can never make a **successful** proposal: every proposal
    is grounded on a version the server only reveals at run time, so a static script can only
    ever be refused with "read the target first" — leaving the whole "the model proposed the
    right thing" half of the set unreachable."""
    messages = [{'role': 'user', 'content': 'q'},
                {'role': 'tool', 'content': json.dumps(
                    {'source': 'erp', 'untrusted': True, 'tool': 'erp_read_record',
                     'data': {'name': 'SO-1', 'modified': '2026-09-09 01:02:03',
                              'fields': {'items': [{'name': 'ROW-1'}]}}})}]
    filled = model_server.substitute(
        '{"doctype": "Sales Order", "name": "{{data.name}}", "version": "{{data.modified}}"}',
        messages)
    assert json.loads(filled) == {'doctype': 'Sales Order', 'name': 'SO-1',
                                  'version': '2026-09-09 01:02:03'}
    assert model_server.substitute('{"row": "{{data.fields.items[0].name}}"}', messages) \
        == '{"row": "ROW-1"}'


def test_an_unresolvable_placeholder_is_left_alone():
    """A silently blank version would be refused with a message about freshness, and the
    script's real mistake would stay hidden behind it."""
    messages = [{'role': 'tool', 'content': json.dumps({'data': {}})}]
    assert model_server.substitute('{"version": "{{data.modified}}"}', messages) \
        == '{"version": "{{data.modified}}"}'
    assert model_server.substitute('{"version": "{{data.modified}}"}', []) \
        == '{"version": "{{data.modified}}"}'


def test_arguments_without_a_placeholder_are_untouched():
    assert model_server.substitute('{"doctype": "Item"}', []) == '{"doctype": "Item"}'


def _result(case_id, verdict='pass', final_status='Succeeded'):
    return {'case_id': case_id, 'verdict': verdict, 'tags': [], 'checks': [],
            'final_status': final_status, 'model_calls': 2, 'actual_input_tokens': 100,
            'actual_output_tokens': 20, 'usage_unknown_calls': 0}


def test_a_replay_run_that_hit_a_budget_is_reported_and_red():
    """The slice-6 gate, as a number in the report rather than something to notice by eye.

    A replay batch that starts stopping runs on budget is the signal that a change grew the
    call count or the input size. Raising the budget would erase exactly the measurement the
    official values are derived from, so this is deliberately a red, not a warning.
    """
    from evals import run as runner
    summary = runner.report([_result('a'), _result('b', final_status='BudgetExceeded')],
                            mode='replay', site='s', started='t')
    assert summary['totals']['budget_exceeded'] == 1
    assert '因预算或循环停止 **1** 条' in runner.markdown(summary)
    clean = runner.report([_result('a'), _result('b')], mode='replay', site='s', started='t')
    assert clean['totals']['budget_exceeded'] == 0
    assert '因预算或循环停止 **0** 条' in runner.markdown(clean)


def test_rejudging_uses_todays_cases_and_marks_the_report_as_not_fresh():
    """A corpus change is a change to judging, never to the run — so it can be measured
    against the recorded behaviour without paying for the model again. What must never happen
    is a re-judged report being read as a fresh batch."""
    from evals import rejudge as rejudger
    previous = {'mode': 'live', 'started': 'r1', 'pass_rate': 0.5,
                'cases': [{'case_id': 'c1', 'run_id': 'run-1', 'verdict': 'fail',
                           'model_calls': 3, 'prompt_version': '2'}]}
    case = {'schema_version': 2, 'case_id': 'c1', 'domain': 'query', 'scored': True,
            'tags': ['t'], 'expect': {'final_status': ['Succeeded']}}
    root = Path(tempfile.mkdtemp())
    (root / 'c1.json').write_text(json.dumps(case), encoding='utf-8')
    seen = {'status': 'Succeeded', 'answer': 'ok', 'sources': [], 'events': [], 'proposals': []}
    summary = rejudger.rejudge(previous, 'site', root, observer=lambda site, run: dict(seen))
    assert summary['pass_rate'] == 1.0
    assert summary['cases'][0]['run_id'] == 'run-1', '重判的是那一次真实运行，不是新跑一次'
    assert summary['cases'][0]['model_calls'] == 3, '原始用量原样带过来'
    assert summary['rejudged_from']['pass_rate_before'] == 0.5


def test_the_report_names_the_assembly_the_batch_ran_under():
    """A report whose numbers cannot be tied to one assembly is a report nobody can reproduce.

    The plan asked for `runtime_revision` / `prompt_version` / `skill_versions` / `model` at
    the top level of `report.json`; they were only ever per case. A set rather than a single
    value, so a batch that straddled a rotation says so instead of quietly reporting the first
    one it saw — which is exactly the situation `--compare-baseline` must not paper over.
    """
    from evals import run as runner

    def row(case_id, **over):
        base = {'case_id': case_id, 'verdict': 'pass', 'tags': [], 'checks': [],
                'runtime_revision': 'rev-1', 'prompt_version': '2', 'model': 'deepseek-v4-flash',
                'skill_versions': '{"erp-query": "1.4.0"}'}
        base.update(over)
        return base

    steady = runner.report([row('a'), row('b')], mode='replay', site='s', started='t')
    assert steady['runtime_revision'] == 'rev-1'
    assert steady['prompt_version'] == '2'
    assert steady['model'] == 'deepseek-v4-flash'
    assert steady['skill_versions'] == '{"erp-query": "1.4.0"}'

    straddled = runner.report([row('a'), row('b', runtime_revision='rev-2')],
                              mode='replay', site='s', started='t')
    assert straddled['runtime_revision'] == ['rev-1', 'rev-2'], '跨轮换必须两个都写出来'

    empty = runner.report([row('a', runtime_revision=None, model='')],
                          mode='replay', site='s', started='t')
    assert empty['runtime_revision'] == [] and empty['model'] == []
