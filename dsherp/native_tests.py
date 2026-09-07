"""Frappe-native tests on the two throwaway test Sites (plan 5).

`provision_test_sites` creates dsherp-test.localhost (bench `backend`) and
dsherp-platform-test.localhost (bench `platform-backend`) through the control-profile
services. `run_native_tests` runs `bench run-tests --app <app>` on each and judges the run.

Two facts about the pinned image decide how it judges, both checked against
apps/frappe/frappe/commands/testing.py inside the running container:

- The exit code IS meaningful for failures: `main()` collects every suite's result and does
  `sys.exit(1)` when any of them was not successful. So a non-zero exit means a real failure.
- The exit code is NOT meaningful for "did anything run": a Site without `allow_tests` prints
  "Testing is disabled for the site!" and returns 0, and an app with no tests simply runs none.
  `--junit-xml-output` cannot fill that gap either - it needs `xmlrunner`, which is not
  installed in this image, so the flag only prints "xmlrunner not found" and writes no file.

So a run must also produce positive evidence that it ran: the runner's own
`Running <n> <category> tests for <app>` line, which it emits per suite. Missing evidence is a
Fault, never a pass - if that line's shape ever changes, this fails closed and says so.
"""
import json
import re
import subprocess
from pathlib import Path

from dsherp import admin, deploy_env
from dsherp.admin import Fault, ROOT

TEST_SITES = {
    'bridge': {'site': 'dsherp-test.localhost', 'service': 'backend', 'bench': 'tenant',
               'app': 'dsherp_bridge', 'provision_service': 'test-provision'},
    'platform': {'site': 'dsherp-platform-test.localhost', 'service': 'platform-backend',
                 'bench': 'platform', 'app': 'dsherp_platform',
                 'provision_service': 'platform-test-provision'},
}
# frappe/commands/testing.py: click.secho(f"\nRunning {suite.countTestCases()} {category} tests for {app}")
RUNNING = re.compile(r'^Running (?P<count>\d+) (?P<category>\w+) tests for (?P<app>[\w.]+)\s*$', re.MULTILINE)
DISABLED = 'Testing is disabled for the site!'
RUN_TIMEOUT = 3600


def _compose(resolved, root):
    """The same prefix admin.Bench uses, so compose interpolates the same values."""
    if resolved.get('env') != 'dev':
        raise Fault('原生测试只在 dev 栈上运行；生产栈没有测试站')
    command = ['docker', 'compose', '-p', resolved['project']]
    env_file = deploy_env.env_file(resolved['env'], root)
    if env_file.exists():
        command += ['--env-file', str(env_file)]
    return command + ['-f', str(Path(root) / admin.COMPOSE['dev'])]


def _run(runner, command, *, root, timeout):
    return runner(command, cwd=root, text=True, capture_output=True, timeout=timeout,
                  stdin=subprocess.DEVNULL)


def _tail(result, lines=12):
    return '\n'.join(((result.stdout or '') + '\n' + (result.stderr or '')).strip().splitlines()[-lines:])


def provision_test_sites(resolved, *, root=ROOT, runner=subprocess.run, kinds=('bridge', 'platform')):
    """Create the test Sites that are absent; keep the ones present; never touch a live Site."""
    admin.ensure_secrets(resolved, root, names=('test_admin_password',))
    outcome = {}
    for kind in kinds:
        target = TEST_SITES[kind]
        bench = admin.Bench(resolved, target['bench'], root=root, runner=runner)
        if bench.site_exists(target['site']):
            outcome[kind] = 'kept'
            continue
        result = _run(runner, [*_compose(resolved, root), '--profile', 'control', 'run', '--rm', '--no-deps',
                               target['provision_service']], root=root, timeout=1800)
        if result.returncode:
            raise Fault(f"测试站 {target['site']} 建站失败；检查 {target['provision_service']} 的输出后再试，"
                        f"半成品不会自动清理：\n{_tail(result)}")
        outcome[kind] = 'created'
    return outcome


def summarise(output, app):
    """What the runner said it ran, per category. Empty means it ran nothing."""
    return [{'category': match.group('category'), 'tests': int(match.group('count'))}
            for match in RUNNING.finditer(output) if match.group('app') == app]


def run_native_tests(resolved, *, out, runner=subprocess.run, root=ROOT, kinds=('bridge', 'platform'),
                     module=None, failfast=False):
    """Run each app's tests on its test Site; write the raw output and a parsed summary next to
    each other; return the counts. Fault (carrying .report) on any failure."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'ok': True, 'apps': {}, 'out': str(out)}
    for kind in kinds:
        target = TEST_SITES[kind]
        command = [*_compose(resolved, root), 'exec', '-T', target['service'],
                   'bench', '--site', target['site'], 'run-tests', '--app', target['app']]
        if module:
            command += ['--module', module]
        if failfast:
            command += ['--failfast']
        result = _run(runner, command, root=root, timeout=RUN_TIMEOUT)
        output = (result.stdout or '') + '\n' + (result.stderr or '')
        (out / f"{target['app']}.log").write_text(output)
        suites = summarise(output, target['app'])
        counts = {'suites': suites, 'tests': sum(row['tests'] for row in suites),
                  'returncode': result.returncode}
        report['apps'][target['app']] = counts
        (out / f"{target['app']}.json").write_text(json.dumps(counts, ensure_ascii=False, indent=2) + '\n')
        if DISABLED in output:
            raise Fault(f"{target['site']} 没有开 allow_tests，bench run-tests 直接返回 0 什么也没跑；"
                        f"先 `bench --site {target['site']} set-config allow_tests true`")
        if not counts['tests']:
            raise Fault(f"{target['app']} 在 {target['site']} 上没有运行任何测试（退出码 "
                        f"{result.returncode}）；检查 tests 包与 discovery，或 runner 的输出格式是否变了：\n"
                        f"{_tail(result)}")
        if result.returncode:
            report['ok'] = False
    if not report['ok']:
        error = Fault('原生测试失败：' + '，'.join(
            f"{app} 跑了 {counts['tests']} 条、退出码 {counts['returncode']}"
            for app, counts in report['apps'].items()) + f'；输出在 {out}')
        error.report = report
        raise error
    return report
