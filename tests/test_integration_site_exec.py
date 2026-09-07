"""Every integration script enters a Site the same way: one wrapper, sites mapped to compose
services, the body compiled from a literal so it keeps its own indentation, destroy guaranteed."""
import subprocess

import pytest

from tests.integration import site_exec


class Result:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_sites_map_to_compose_services_and_unknown_sites_are_refused():
    assert site_exec.service_of('dsherp-validation.localhost') == 'backend'
    assert site_exec.service_of('dsherp-daily.localhost') == 'backend'
    assert site_exec.service_of('dsherp-test.localhost') == 'backend'
    assert site_exec.service_of('dsherp-beta.localhost') == 'beta-backend'
    assert site_exec.service_of('dsherp-platform.localhost') == 'platform-backend'
    assert site_exec.service_of('dsherp-platform-test.localhost') == 'platform-backend'
    with pytest.raises(ValueError):
        site_exec.service_of('dsherp-validation-backend-1')
    assert site_exec.command_for('dsherp-beta.localhost') == [
        'docker', 'compose', '-f', 'infra/compose.validation.yml', 'exec', '-T', 'beta-backend',
        '/home/frappe/frappe-bench/env/bin/python', '-']


def test_the_script_keeps_the_body_verbatim_connects_as_the_user_and_always_destroys():
    body = 'x = """three\n  quotes"""\nprint(json.dumps({"ok": frappe.session.user}))\n'
    text = site_exec.site_script('dsherp-validation.localhost', body, user='dsherp-reader@example.invalid')
    assert "frappe.init(site='dsherp-validation.localhost'" in text
    assert 'frappe.connect()' in text and "frappe.set_user('dsherp-reader@example.invalid')" in text
    assert repr(body) in text and text.rstrip().endswith('finally:\n    frappe.destroy()')
    offline = site_exec.site_script('dsherp-validation.localhost', body, connect=False)
    assert 'frappe.connect()' not in offline and 'frappe.set_user' not in offline
    compile(text, '<wrapper>', 'exec')  # the wrapper itself is valid Python


def test_a_body_that_would_be_mangled_by_formatting_survives_verbatim():
    """Bodies carry braces, %-signs, f-strings and their own indentation; the wrapper embeds
    the body as a literal, so none of that may be formatted, dedented or re-interpolated."""
    import ast

    body = ('data = {"a": 1}\n'
            'if data:\n'
            '    label = "%s/%s" % ("x", "y")\n'
            '    text = f"{label} {data[\'a\']}"\n'
            'print(json.dumps({"text": text}))\n')
    tree = ast.parse(site_exec.site_script('dsherp-beta.localhost', body))
    embedded = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == body]
    assert embedded == [body], 'the body must reach the container byte for byte'


def test_a_failing_script_raises_with_the_site_the_service_and_the_stderr_tail():
    def run(command, **kwargs):
        return Result(1, '', 'line1\n' + '\n'.join(f'frame {i}' for i in range(30))
                      + '\nValidationError: 策略目标 DocType 不存在')

    with pytest.raises(AssertionError) as caught:
        site_exec.run_site_script('dsherp-beta.localhost', "print('x')", run=run)
    message = str(caught.value)
    assert 'dsherp-beta.localhost' in message and 'beta-backend' in message
    assert '策略目标 DocType 不存在' in message and 'frame 0' not in message


def test_a_timeout_is_announced_to_observers_then_re_raised():
    seen = []

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    site_exec.ON_TIMEOUT.append(lambda site, body: seen.append((site, body)))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            site_exec.run_site_script('dsherp-validation.localhost', 'frappe.db.commit()', timeout=7, run=run)
    finally:
        site_exec.ON_TIMEOUT.clear()
    assert seen == [('dsherp-validation.localhost', 'frappe.db.commit()')]


def test_run_site_json_returns_the_last_line_and_refuses_silence():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs['input'], kwargs['cwd'], kwargs['timeout']))
        return Result(0, 'noise\n{"ok": true}\n')

    assert site_exec.run_site_json('dsherp-platform.localhost', 'print(1)', timeout=33, run=run) == {'ok': True}
    assert calls[0][0][6] == 'platform-backend' and calls[0][3] == 33 and calls[0][2] == site_exec.ROOT
    with pytest.raises(AssertionError):
        site_exec.run_site_json('dsherp-platform.localhost', 'pass', run=lambda *a, **k: Result(0, '\n'))
