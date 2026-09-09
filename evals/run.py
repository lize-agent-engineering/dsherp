"""Run the evaluation set and say whether it passed.

Two backends, one code path:

- `--mode replay` puts a **scripted** provider on the run container's own loopback. The model
  is deterministic, so anything below 100% is a real regression, and a paid call is impossible.
- `--mode live` uses the unmodified production container — the real provider, the real egress
  proxy. It costs money, so it needs `DSHERP_EVAL_LIVE_AUTHORIZED=1` **and** an explicit
  `--max-cases`, and it prints the expected call count before it starts.

Both go through the ordinary business path: `send_message` as the business user,
`claim_run` as the Site's runtime identity, the real container, `finish_run`. Nothing here
reaches into the database to shortcut a step, because a step that was shortcut was not tested.

**What replay proves and what it does not.** In replay the model's behaviour is written down
in the script, so a green injection case proves the **oracle and the server** behave — not
that a model resists injection. That can only be shown live. Every injection case therefore
ships two scripts: `.compliant.json` (must pass) and `.obedient.json` (must fail). A positive
run alone proves nothing; only the negative control shows the oracle can still fail.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from dsherp import deploy_env  # noqa: E402
from dsherp.context_mcp import post  # noqa: E402
from dsherp.eval_cases import load_case, validate_case  # noqa: E402
from dsherp.runtime_revision import configuration_revision  # noqa: E402
from evals import oracle, replay  # noqa: E402
from infra.site_exec import command_for, site_script  # noqa: E402

API = '/api/method/dsherp_bridge.context_api.'
EVAL_PREFIX = 'eval-'
DEFAULT_SITE = 'dsherp-daily.localhost'
# Worst case per domain, from run_budget.DOMAINS; used only to warn before a paid batch.
DOMAIN_MAX_CALLS = {'query': 8, 'operation': 10, 'configuration': 8}


class EvaluatorError(RuntimeError):
    """The evaluation could not be carried out. Never the subject's zero."""


# ---- the Site --------------------------------------------------------------------------
def site_json(site, body, timeout=180):
    result = subprocess.run(command_for(site), cwd=ROOT, input=site_script(site, body),
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-12:])
        raise EvaluatorError(f'站点脚本失败（{site}）：\n{tail}')
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise EvaluatorError(f'站点脚本没有输出（{site}）')
    return json.loads(lines[-1])


OBSERVE = r'''
import json
run = frappe.db.get_value('DS Model Run', RUN,
    ['name','status','answer','error','sources','runtime_revision','model','model_calls',
     'model_input_bytes','prompt_version','sampling','skill_versions','actual_input_tokens',
     'actual_output_tokens','duration_ms','usage_unknown_calls'], as_dict=True)
if run is None:
    print(json.dumps({'missing': True})); raise SystemExit(0)
events = frappe.get_all('DS Run Event', filters={'run': RUN},
    fields=['seq','kind','source','error_class','payload','recorded_at'], order_by='seq asc',
    limit_page_length=0)
for event in events:
    try: event['payload'] = json.loads(event['payload'] or '{}')
    except Exception: event['payload'] = {}
proposals = frappe.get_all('DS Operation Proposal', filters={'model_run': RUN},
    fields=['name','status','payload'], limit_page_length=0)
bundles = frappe.db.count('DS Configuration Bundle', {'model_run': RUN}) \
    if frappe.db.has_column('DS Configuration Bundle', 'model_run') else 0
summarised = []
for item in proposals:
    try: payload = json.loads(item['payload'] or '{}')
    except Exception: payload = {}
    summarised.append({'id': item['name'], 'status': item['status'],
                       'summary': ' · '.join(str(payload[k]) for k in ('action','doctype','name') if payload.get(k))})
run['sources'] = json.loads(run['sources'] or '[]')
print(json.dumps({'run': run, 'events': events, 'proposals': summarised,
                  'bundles': bundles}, default=str, ensure_ascii=False))
'''


def observe(site, run_id):
    seen = site_json(site, OBSERVE.replace('RUN', repr(run_id)))
    if seen.get('missing'):
        raise EvaluatorError(f'站上找不到运行 {run_id}')
    run = seen['run']
    return {'status': run['status'], 'answer': run['answer'] or '', 'error': run['error'] or '',
            'sources': run['sources'], 'events': seen['events'],
            'proposals': seen['proposals'], 'bundles': seen['bundles'],
            'runtime_revision': run['runtime_revision'],
            'prompt_version': run['prompt_version'], 'run': run}


# ---- one case --------------------------------------------------------------------------
# A Site runs one business run at a time, so a case whose predecessor is still in flight has
# to wait for the lease rather than be called a failure. Long enough to cover a normal run
# plus the lease (180s), short enough that a genuinely stuck queue is still reported.
CLAIM_WAIT_SECONDS = 600
CLAIM_POLL_SECONDS = 5
TERMINAL_STATUSES = ('Succeeded', 'Failed', 'Cancelled', 'NeedsInput', 'BudgetExceeded')


def _drive_until_finished(worker_module, worker, reader, settings, state_root, service, site,
                          session_id, run_id):
    """Keep driving the queue until **this case's own run** has finished.

    `claim_run` hands out whatever is next in the Site's queue, which is not necessarily the
    run this case just sent: a Site executes one run at a time, and a predecessor (or a run
    a crashed evaluation left queued) is ahead of it. Driving until the target run leaves
    Queued/Running both drains those and guarantees the record we then read back is the one
    this case produced. Without it the evaluator reads a still-Queued row and reports the
    case as failed on evidence belonging to another run entirely.
    """
    deadline = time.monotonic() + CLAIM_WAIT_SECONDS
    others = 0
    while True:
        status = _status_of(reader, session_id, run_id)
        if status in TERMINAL_STATUSES:
            return True, others
        drove = worker_module.run_once(worker, settings, state_root,
                                       business={'business_url': service['business_url'], 'site': site})
        if drove and _status_of(reader, session_id, run_id) not in TERMINAL_STATUSES:
            others += 1                      # we executed somebody else's run; keep going
        if time.monotonic() >= deadline:
            return _status_of(reader, session_id, run_id) in TERMINAL_STATUSES, others
        if not drove:
            time.sleep(CLAIM_POLL_SECONDS)
            post(worker, 'worker_heartbeat')


def _status_of(reader, session_id, run_id):
    response = reader.get(API + 'get_session', params={'session_id': session_id})
    if response.status_code != 200:
        raise EvaluatorError(f'get_session 返回 {response.status_code}')
    for message in response.json()['message'].get('messages') or []:
        if message.get('run_id') == run_id or message.get('id') == run_id:
            return message.get('status')
    return None


def _identity(path, key):
    try:
        return json.loads(Path(path).read_text())[key]
    except (OSError, ValueError, KeyError) as error:
        raise EvaluatorError(f'读不到评估身份 {path}（{type(error).__name__}）；'
                             f'先跑 infra/provision_eval_identity.py') from error


def _client(profile, timeout=40):
    return httpx.Client(base_url=profile['base_url'],
                        headers={'X-Frappe-Site-Name': profile['site'],
                                 'Authorization': 'token ' + profile['api_key'] + ':' + profile['api_secret']},
                        trust_env=False, timeout=timeout)


def _run_setup(site, case):
    for fixture in (case.get('setup') or {}).get('fixtures') or []:
        module = ROOT / 'evals' / 'setup' / f'{fixture}.py'
        if not module.is_file():
            raise EvaluatorError(f'用例 {case["case_id"]} 要的夹具不存在：{module}')
        done = subprocess.run([sys.executable, str(module), '--site', site],
                              cwd=ROOT, capture_output=True, text=True, timeout=600)
        if done.returncode:
            raise EvaluatorError(f'夹具 {fixture} 失败：{done.stderr.strip()[-400:]}')


def identity_for(case, identities):
    """Which person asks. The configuration domain reads DocType definitions, which an
    ordinary business user cannot see; giving the business user that permission would widen
    what every other case can reach."""
    if case['domain'] == 'configuration' and identities.get('configurator'):
        return identities['configurator']
    return identities['operator']


def run_case(case, *, mode, site, operator, service, settings, state_root, script_path=None):
    """send_message → claim_run → the container → finish_run → read the record back."""
    _run_setup(site, case)
    request_id = EVAL_PREFIX + case['case_id'] + '-' + uuid.uuid4().hex
    with _client(operator) as reader, _client(service) as worker:
        post(worker, 'worker_heartbeat')
        response = reader.post(API + 'send_message', json={
            'question': case['question'], 'context': case.get('page_context') or
            {'schema_version': 1, 'page_type': 'unknown', 'route': []},
            'request_id': request_id, 'domain': case['domain']})
        if response.status_code != 200:
            raise EvaluatorError(f'send_message 返回 {response.status_code}：{response.text[:300]}')
        conversation = response.json()['message']
        run_id, session_id = conversation['active_run'], conversation['id']
        if not run_id:
            raise EvaluatorError('send_message 没有产生运行')

        import dsherp.context_worker as worker_module
        original = worker_module.docker_command
        if mode == 'replay':
            def command(root, secret, directory, name):
                return replay.replay_command(root, secret, directory, name, script_path)
            worker_module.docker_command = command
        try:
            finished, others = _drive_until_finished(
                worker_module, worker, reader, settings, state_root, service, site,
                session_id, run_id)
        finally:
            worker_module.docker_command = original
        if others:
            print(f'  （顺带执行了队列里 {others} 条不属于本用例的运行）')
        if not finished:
            raise EvaluatorError(
                f'{CLAIM_WAIT_SECONDS} 秒内这次运行没有结束。站点并发是 1，'
                '队列里可能还压着别的运行；也可能是常驻 worker 抢先了')
    seen = observe(site, run_id)
    seen['mode'] = mode
    seen['run_id'] = run_id
    wire = replay.newest_wire(state_root) if mode == 'replay' else None
    if wire is not None:
        seen['requests'] = wire.get('requests')
    return seen


# ---- the batch -------------------------------------------------------------------------
def _load(patterns):
    paths = []
    for pattern in patterns:
        matched = sorted(ROOT.glob(pattern)) if not Path(pattern).is_absolute() else [Path(pattern)]
        paths += [path for path in matched if path.suffix == '.json']
    if not paths:
        raise EvaluatorError(f'没有匹配到任何用例：{patterns}')
    return [(path, load_case(path)) for path in sorted(set(paths))]


def _script_for(case, variant):
    script = case.get('script') or ''
    if variant != 'compliant':
        script = script.replace('.compliant.json', f'.{variant}.json')
    return ROOT / script


def require_stopped_worker():
    pid_file = ROOT / '.runtime/agent-worker.pid'
    if not pid_file.exists():
        return
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return
    except (OSError, ValueError):
        raise EvaluatorError('常驻 worker PID 状态无效；先停止它再跑评估')
    raise EvaluatorError('常驻 worker 正在运行，会抢先领取评估运行；先 '
                         'launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16')


def expected_calls(cases):
    return sum(DOMAIN_MAX_CALLS.get(case['domain'], 10) for _path, case in cases)


def junit(results, path):
    suite = ET.Element('testsuite', name='dsherp-evals', tests=str(len(results)),
                       failures=str(sum(1 for r in results if r['verdict'] == 'fail')),
                       errors=str(sum(1 for r in results if r['verdict'] in
                                      ('evaluator_failed', 'case_invalid'))),
                       skipped=str(sum(1 for r in results if r['verdict'] == 'skipped')))
    for result in results:
        case = ET.SubElement(suite, 'testcase', name=result['case_id'], classname=result['mode'])
        detail = '; '.join(f'{c["name"]}: {c["detail"]}' for c in result['checks'] if not c['ok'])
        if result['verdict'] == 'fail':
            ET.SubElement(case, 'failure', message=detail or 'failed').text = detail
        elif result['verdict'] in ('evaluator_failed', 'case_invalid', 'version_changed'):
            ET.SubElement(case, 'error', message=result['verdict']).text = detail
        elif result['verdict'] == 'skipped':
            ET.SubElement(case, 'skipped', message=result.get('skip_reason', ''))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding='utf-8', xml_declaration=True)


def metering_health(results, *, mode):
    """Whether the provider's own token counts actually reached the run rows.

    In replay the scripted provider **always** reports a usage, so a case whose every call
    came back unaccounted means the metering chain is broken, not that the provider was
    quiet. That chain has silently broken twice already — once because the guard read usage
    off the terminal chunk when it arrives on its own chunk, once because the summariser did
    not know the camelCase names the runtime normalises to — and neither showed up as a red
    anywhere: the runs passed, the rows just said zero. So it is checked here, by name.
    """
    scored = [r for r in results if r['verdict'] in ('pass', 'fail') and r.get('model_calls')]
    unaccounted = [r['case_id'] for r in scored
                   if (r.get('usage_unknown_calls') or 0) >= (r.get('model_calls') or 0)]
    tokens = sum((r.get('actual_input_tokens') or 0) + (r.get('actual_output_tokens') or 0)
                 for r in scored)
    return {'cases_with_calls': len(scored), 'fully_unaccounted': unaccounted,
            'total_tokens': tokens,
            # Only replay can assert this: a live provider may genuinely fail to report.
            'ok': (not unaccounted and tokens > 0) if mode == 'replay' and scored else True}


def report(results, *, mode, site, started):
    scored = [r for r in results if r['verdict'] in ('pass', 'fail')]
    passed = [r for r in scored if r['verdict'] == 'pass']
    groups = {}
    for result in results:
        for tag in result.get('tags') or ['untagged']:
            bucket = groups.setdefault(tag, {'total': 0, 'pass': 0})
            if result['verdict'] in ('pass', 'fail'):
                bucket['total'] += 1
                bucket['pass'] += result['verdict'] == 'pass'
    # The assembly the batch ran under, at the top level rather than only inside each case: a
    # report whose numbers cannot be tied to one runtime_revision / prompt_version / model /
    # skill set is a report nobody can reproduce, and reproducibility is what this plan is
    # for. A **set**, not a single value — a batch that straddled a rotation has to say so
    # rather than quietly reporting whichever one it saw first.
    def _spread(field):
        seen = sorted({str(result[field]) for result in results
                       if result.get(field) not in (None, '')})
        return seen[0] if len(seen) == 1 else seen
    return {'mode': mode, 'site': site, 'started': started,
            'runtime_revision': _spread('runtime_revision'),
            'prompt_version': _spread('prompt_version'),
            'model': _spread('model'),
            'skill_versions': _spread('skill_versions'),
            'totals': {'cases': len(results), 'scored': len(scored), 'passed': len(passed),
                       'failed': len(scored) - len(passed),
                       'evaluator_failed': sum(1 for r in results if r['verdict'] == 'evaluator_failed'),
                       'case_invalid': sum(1 for r in results if r['verdict'] == 'case_invalid'),
                       'version_changed': sum(1 for r in results if r['verdict'] == 'version_changed'),
                       'skipped': sum(1 for r in results if r['verdict'] == 'skipped'),
                       'budget_exceeded': sum(1 for r in results
                                              if r.get('final_status') == 'BudgetExceeded')},
            'pass_rate': (len(passed) / len(scored)) if scored else None,
            'by_group': groups, 'cases': results}


def markdown(summary):
    totals = summary['totals']
    rate = '—' if summary['pass_rate'] is None else f'{summary["pass_rate"] * 100:.1f}%'
    lines = [f'# 评估报告（{summary["mode"]} / {summary["site"]}）', '',
             f'- 用例 {totals["cases"]}，计分 {totals["scored"]}，通过 {totals["passed"]}，'
             f'失败 {totals["failed"]}，通过率 **{rate}**',
             f'- 评估器失败 {totals["evaluator_failed"]}，用例非法 {totals["case_invalid"]}，'
             f'装配已变 {totals["version_changed"]}，跳过 {totals["skipped"]}',
             f'- 因预算或循环停止 **{totals.get("budget_exceeded", 0)}** 条', '',
             '| 用例 | 判定 | 域 | 红在哪 |', '|---|---|---|---|']
    for case in summary['cases']:
        detail = '; '.join(f'{c["name"]}' for c in case['checks'] if not c['ok']) or '—'
        lines.append(f'| `{case["case_id"]}` | {case["verdict"]} | {case.get("domain", "")} | {detail} |')
    if summary['mode'] == 'replay':
        lines += ['', '> **回放层证明的是预言机与服务端；模型的注入抗性只能由 live 证明。**',
                  '> 回放里模型行为由脚本写死，用它证明「模型不听指令」是自证。']
    return '\n'.join(lines) + '\n'


def compare_baseline(summary, baseline_path):
    """Only-up: any case that used to pass and now fails is a regression, whatever the average."""
    baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8'))
    was = {case['case_id']: case['verdict'] for case in baseline.get('cases', [])}
    regressed = [case['case_id'] for case in summary['cases']
                 if was.get(case['case_id']) == 'pass' and case['verdict'] != 'pass']
    return regressed, baseline.get('pass_rate')


def main(argv=None):
    parser = argparse.ArgumentParser(description='dsherp 评估集运行器')
    parser.add_argument('--mode', choices=('replay', 'live'), required=True)
    parser.add_argument('--site', default=DEFAULT_SITE)
    parser.add_argument('--cases', nargs='+', default=[f'evals/cases/{DEFAULT_SITE}/*.json'])
    parser.add_argument('--out', default='work/evals')
    parser.add_argument('--junit', default='work/junit-evals.xml')
    parser.add_argument('--compare-baseline')
    parser.add_argument('--identity', default='.runtime/eval-users.json')
    parser.add_argument('--service', default='.runtime/context-worker-daily.json')
    parser.add_argument('--provider-env')
    parser.add_argument('--max-cases', type=int)
    parser.add_argument('--started', default='unknown')
    parser.add_argument('--variant', default='compliant',
                        help='回放脚本变体；negative control 用 obedient')
    args = parser.parse_args(argv)

    if args.mode == 'live':
        if os.environ.get('DSHERP_EVAL_LIVE_AUTHORIZED') != '1' or not args.max_cases:
            raise SystemExit('live 后端需要 DSHERP_EVAL_LIVE_AUTHORIZED=1 与 --max-cases')

    require_stopped_worker()
    cases = _load(args.cases)
    if args.max_cases:
        cases = cases[:args.max_cases]
    if args.mode == 'live':
        print(f'live 批次：{len(cases)} 条用例，预计最多 {expected_calls(cases)} 次付费模型调用')
        for path, case in cases:
            print(f'  - {case["case_id"]} ({case["domain"]})')

    if not (ROOT / args.identity).is_file():
        raise EvaluatorError(f'读不到评估身份 {args.identity}；先跑 infra/provision_eval_identity.py')
    identities = json.loads((ROOT / args.identity).read_text())
    service = json.loads(Path(ROOT / args.service).read_text())
    resolved = deploy_env.settings({'DSHERP_ENV': 'dev'})
    digest = deploy_env.deployment_digest(resolved)
    if args.mode == 'replay':
        settings = replay.replay_settings(digest)
    else:
        from dsherp.runtime_host import agent_settings
        settings = {**agent_settings(Path(args.provider_env)), 'deployment_digest': digest}
    configuration_revision(settings)          # fail fast if the runtime files do not verify
    state_root = ROOT / '.runtime' / 'eval-sessions'
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    results = []
    for path, case in cases:
        problems = validate_case(case, root=ROOT)
        entry = {'case_id': case.get('case_id') or path.stem, 'mode': args.mode,
                 'domain': case.get('domain'), 'tags': case.get('tags') or [],
                 'origin_run_id': (case.get('origin') or {}).get('run_id'),
                 'skip_reason': case.get('skip_reason', '')}
        if problems:
            results.append({**entry, 'verdict': 'case_invalid',
                            'checks': [{'name': 'schema', 'ok': False, 'detail': '；'.join(problems)}]})
            continue
        if not case.get('scored'):
            results.append({**entry, 'verdict': 'skipped',
                            'checks': [{'name': 'scored', 'ok': True,
                                        'detail': case.get('skip_reason', '')}]})
            continue
        script_path = _script_for(case, args.variant) if args.mode == 'replay' else None
        try:
            seen = run_case(case, mode=args.mode, site=args.site,
                            operator=identity_for(case, identities),
                            service=service, settings=settings, state_root=state_root,
                            script_path=script_path)
        except EvaluatorError as error:
            results.append({**entry, 'verdict': 'evaluator_failed',
                            'checks': [{'name': 'evaluator', 'ok': False, 'detail': str(error)}]})
            continue
        except Exception as error:                          # noqa: BLE001 - reported, not hidden
            results.append({**entry, 'verdict': 'evaluator_failed',
                            'checks': [{'name': 'evaluator', 'ok': False,
                                        'detail': f'{type(error).__name__}: {error}'}]})
            continue
        judged = oracle.judge(case, seen)
        # The script declares what it should produce. A positive-only run proves nothing:
        # only the `.obedient` control shows the oracle can still fail. Enforcing the
        # declaration here is what makes "compliant→pass and obedient→fail" a gate rather
        # than a convention somebody has to remember to check by eye.
        if script_path is not None and script_path.is_file():
            declared = json.loads(script_path.read_text(encoding='utf-8')).get('expected_verdict')
            if declared in ('pass', 'fail') and judged['verdict'] in ('pass', 'fail') \
                    and judged['verdict'] != declared:
                judged = {'verdict': 'fail', 'checks': judged['checks'] + [
                    {'name': 'expected_verdict', 'ok': False,
                     'detail': f'脚本声明 {declared}，实得 {judged["verdict"]}'}]}
            elif declared == 'fail' and judged['verdict'] == 'fail':
                # A control that failed as declared is a pass *of the control*.
                judged = {'verdict': 'pass', 'checks': judged['checks'] + [
                    {'name': 'expected_verdict', 'ok': True,
                     'detail': '负对照按声明变红，预言机没有漏判'}]}
        run = seen.get('run') or {}
        results.append({**entry, **judged, 'run_id': seen.get('run_id'),
                        # Read back as its own field, not only inside a check's message: from
                        # slice 6 a run can end `BudgetExceeded`, and the replay layer's gate
                        # is that none of them do.
                        'final_status': run.get('status'),
                        'model_calls': run.get('model_calls'),
                        'input_bytes': run.get('model_input_bytes'),
                        'actual_input_tokens': run.get('actual_input_tokens'),
                        'actual_output_tokens': run.get('actual_output_tokens'),
                        'usage_unknown_calls': run.get('usage_unknown_calls'),
                        'prompt_version': run.get('prompt_version'),
                        'model': run.get('model'),
                        'skill_versions': run.get('skill_versions'),
                        'runtime_revision': run.get('runtime_revision')})

    summary = report(results, mode=args.mode, site=args.site, started=args.started)
    summary['metering'] = metering_health(results, mode=args.mode)
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'report.md').write_text(markdown(summary), encoding='utf-8')
    junit(results, ROOT / args.junit)
    print(markdown(summary))

    failed = summary['totals']
    bad = failed['evaluator_failed'] or failed['case_invalid']
    if args.compare_baseline:
        regressed, previous = compare_baseline(summary, ROOT / args.compare_baseline)
        if regressed:
            print('由 pass 转 fail：' + ', '.join(regressed))
            bad = True
        if previous is not None and summary['pass_rate'] is not None and summary['pass_rate'] < previous:
            print(f'通过率 {summary["pass_rate"]:.3f} 低于归档基线 {previous:.3f}')
            bad = True
    if args.mode == 'replay' and summary['pass_rate'] not in (None, 1.0):
        print('回放层不是 100%：脚本化的模型是确定的，任何低于 100% 都是真实回归')
        bad = True
    stopped = [r['case_id'] for r in results if r.get('final_status') == 'BudgetExceeded']
    if stopped:
        # 预算正式值是从改完之后的观测值裁定的：抬上去就再也量不出调用膨胀。所以这里红，
        # 要查的是「这次改动是不是把调用数或输入字节撑大了」，而不是把预算调高。
        print('有运行因预算或循环被停止，先查调用膨胀而不是调高预算：' + ', '.join(stopped))
        bad = True
    if not summary['metering']['ok']:
        print('用量没有落库：回放的替身总会报 usage，所以这说明计量链断了，而不是 provider 没报。'
              f'完全无计量的用例：{summary["metering"]["fully_unaccounted"]}')
        bad = True
    if failed['failed']:
        bad = True
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
