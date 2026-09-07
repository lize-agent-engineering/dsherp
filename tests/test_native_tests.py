"""Native tests are judged by two things together: the exit code (which the pinned image does
set on failure) and the runner's own count of what it ran (which is the only proof anything
ran at all). The runner sees exactly the compose commands, and no password is ever on an argv.
"""
import json
import subprocess
from pathlib import Path

import pytest

from dsherp import admin, deploy_env, native_tests

DEV = deploy_env.settings({'DSHERP_ENV': 'dev'})
BRIDGE_RAN = '\nRunning 12 unit tests for dsherp_bridge\n\nRunning 4 integration tests for dsherp_bridge\n'
PLATFORM_RAN = '\nRunning 5 unit tests for dsherp_platform\n'


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('DSHERP_RUNTIME_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('DSHERP_SECRETS_DIR', str(tmp_path / 'state' / 'control'))
    (tmp_path / 'infra').mkdir()
    (tmp_path / 'infra' / 'compose.validation.yml').write_text('name: dsherp-validation\n')
    return tmp_path


class Runner:
    def __init__(self, *, returncode=0, output=None, existing=(), disabled=False):
        self.calls, self.returncode, self.existing, self.disabled = [], returncode, set(existing), disabled
        self.output = output

    def _ran(self, command):
        app = command[command.index('--app') + 1]
        if self.disabled:
            return native_tests.DISABLED + '\n'
        if self.output is not None:
            return self.output
        return BRIDGE_RAN if app == 'dsherp_bridge' else PLATFORM_RAN

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        text = ' '.join(command)
        if 'site_config.json' in text and 'echo present' in text:
            site = text.split('/sites/', 1)[1].split('/', 1)[0]
            return subprocess.CompletedProcess(command, 0, ('present' if site in self.existing else 'absent') + '\n', '')
        if 'run-tests' in command:
            return subprocess.CompletedProcess(command, self.returncode, self._ran(command), '')
        return subprocess.CompletedProcess(command, 0, '', '')


def _compose(host):
    return ['docker', 'compose', '-p', DEV['project'], '-f', str(host / 'infra' / 'compose.validation.yml')]


def test_it_runs_both_apps_writes_the_output_beside_a_parsed_summary_and_reports_the_counts(host):
    runner = Runner()
    report = native_tests.run_native_tests(DEV, out=host / 'native', runner=runner, root=host)
    assert report['ok']
    assert report['apps']['dsherp_bridge']['tests'] == 16
    assert report['apps']['dsherp_bridge']['suites'] == [{'category': 'unit', 'tests': 12},
                                                         {'category': 'integration', 'tests': 4}]
    assert report['apps']['dsherp_platform']['tests'] == 5
    assert runner.calls[0] == [*_compose(host), 'exec', '-T', 'backend', 'bench', '--site',
                               'dsherp-test.localhost', 'run-tests', '--app', 'dsherp_bridge']
    second = runner.calls[1]
    assert second[second.index('exec') + 1:] == ['-T', 'platform-backend', 'bench', '--site',
                                                 'dsherp-platform-test.localhost', 'run-tests',
                                                 '--app', 'dsherp_platform']
    assert (host / 'native' / 'dsherp_bridge.log').read_text().strip() == BRIDGE_RAN.strip()
    assert json.loads((host / 'native' / 'dsherp_platform.json').read_text())['tests'] == 5
    assert not any('junit' in word for call in runner.calls for word in call), 'xmlrunner is absent from the image'


def test_a_single_module_and_failfast_are_passed_through(host):
    runner = Runner()
    native_tests.run_native_tests(DEV, out=host / 'native', runner=runner, root=host, kinds=('bridge',),
                                  module='dsherp_bridge.tests.test_permission_matrix', failfast=True)
    call = runner.calls[0]
    assert call[call.index('--module') + 1] == 'dsherp_bridge.tests.test_permission_matrix'
    assert '--failfast' in call
    assert len([c for c in runner.calls if 'run-tests' in c]) == 1


def test_a_non_zero_exit_is_a_failure_and_the_report_still_carries_both_apps_counts(host):
    with pytest.raises(admin.Fault) as caught:
        native_tests.run_native_tests(DEV, out=host / 'native', runner=Runner(returncode=1), root=host)
    assert caught.value.report['apps']['dsherp_bridge']['returncode'] == 1
    assert caught.value.report['apps']['dsherp_platform']['tests'] == 5, 'the second app still ran'
    assert (host / 'native' / 'dsherp_platform.log').exists()


def test_a_run_that_says_it_ran_nothing_is_a_failure_not_an_empty_success(host):
    """Exit 0 proves nothing ran badly; it does not prove anything ran."""
    with pytest.raises(admin.Fault, match='没有运行任何测试'):
        native_tests.run_native_tests(DEV, out=host / 'native', runner=Runner(output='All done\n'),
                                      root=host, kinds=('bridge',))
    with pytest.raises(admin.Fault, match='没有运行任何测试'):
        native_tests.run_native_tests(DEV, out=host / 'native', kinds=('bridge',), root=host,
                                      runner=Runner(output='\nRunning 0 unit tests for dsherp_bridge\n'))


def test_a_site_without_allow_tests_is_named_not_read_as_a_pass(host):
    with pytest.raises(admin.Fault, match='allow_tests'):
        native_tests.run_native_tests(DEV, out=host / 'native', runner=Runner(disabled=True),
                                      root=host, kinds=('bridge',))


def test_the_count_line_is_matched_per_app_so_one_app_cannot_vouch_for_the_other(host):
    mixed = '\nRunning 7 unit tests for erpnext\n\nRunning 2 unit tests for dsherp_bridge\n'
    assert native_tests.summarise(mixed, 'dsherp_bridge') == [{'category': 'unit', 'tests': 2}]
    assert native_tests.summarise(mixed, 'dsherp_platform') == []


def test_provisioning_creates_the_secret_once_runs_the_control_services_and_keeps_existing_sites(host):
    runner = Runner(existing=('dsherp-platform-test.localhost',))
    outcome = native_tests.provision_test_sites(DEV, root=host, runner=runner)
    assert outcome == {'bridge': 'created', 'platform': 'kept'}
    secret = admin.secrets_dir(DEV, host) / 'test_admin_password'
    assert secret.is_file() and (secret.stat().st_mode & 0o077) == 0
    runs = [c for c in runner.calls if 'run' in c and '--rm' in c]
    assert runs == [[*_compose(host), '--profile', 'control', 'run', '--rm', '--no-deps', 'test-provision']]
    assert not any(secret.read_text().strip() in word for call in runner.calls for word in call)
    again = native_tests.provision_test_sites(
        DEV, root=host, runner=Runner(existing=('dsherp-test.localhost', 'dsherp-platform-test.localhost')))
    assert again == {'bridge': 'kept', 'platform': 'kept'} and secret.read_text()


def test_native_tests_refuse_a_production_environment(host):
    with pytest.raises(admin.Fault):
        native_tests.run_native_tests(dict(DEV, env='prod'), out=host / 'native', runner=Runner(), root=host)


def test_the_running_line_this_parser_expects_is_the_one_the_pinned_image_prints():
    """If Frappe changes that f-string, `summarise` returns nothing and every run Faults with
    "ran no tests" - fail closed, but the reason would be obscure. This pins the shape next to
    the source line it came from."""
    source = Path(__file__).resolve().parents[1] / 'dsherp' / 'native_tests.py'
    assert 'Running {suite.countTestCases()} {category} tests for {app}' in source.read_text()
    assert native_tests.summarise('Running 3 unit tests for dsherp_bridge', 'dsherp_bridge') == [
        {'category': 'unit', 'tests': 3}]
