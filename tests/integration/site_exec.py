"""One way into a Site's interpreter for every integration test.

`run_site_script` wraps `docker compose -f infra/compose.validation.yml exec -T <service>
/home/frappe/frappe-bench/env/bin/python -`. The body runs inside a connected Frappe context
as Administrator (or the user asked for), and `frappe.destroy()` is guaranteed on the
container side. Sites map to compose services here, so no test names a container. A body
must not call frappe.init/connect/destroy itself.

A host-side timeout kills the docker client, not the interpreter inside the container: the
body's own `finally` may never run. That is why the residue registry (residue.py) subscribes
to ON_TIMEOUT, and why anything a script commits must be registered before the script runs.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ['docker', 'compose', '-f', 'infra/compose.validation.yml']
BENCH_PYTHON = '/home/frappe/frappe-bench/env/bin/python'
SITES_DIR = '/home/frappe/frappe-bench/sites'
SITES = {
    'dsherp-validation.localhost': 'backend',
    'dsherp-daily.localhost': 'backend',
    'dsherp-test.localhost': 'backend',
    'dsherp-beta.localhost': 'beta-backend',
    'dsherp-platform.localhost': 'platform-backend',
    'dsherp-platform-test.localhost': 'platform-backend',
}
# Called as observer(site, body) when the host timeout kills a script.
ON_TIMEOUT = []


def service_of(site):
    try:
        return SITES[site]
    except KeyError:
        raise ValueError(f'未知站点 {site!r}；只认识 ' + ', '.join(SITES)) from None


def site_script(site, body, *, connect=True, user='Administrator'):
    """The exact text fed to the interpreter. The body is compiled from a string literal, so
    it keeps its own indentation and may contain triple-quoted strings."""
    lines = ['import json,os,sys,uuid', 'import frappe', f'os.chdir({SITES_DIR!r})',
             f'frappe.init(site={site!r},sites_path={SITES_DIR!r})']
    if connect:
        lines += ['frappe.connect()', f'frappe.set_user({user!r})']
    lines += ['try:',
              f"    exec(compile({body!r},'<site-script>','exec'),globals())",
              'finally:',
              '    frappe.destroy()']
    return '\n'.join(lines) + '\n'


def command_for(site):
    return [*COMPOSE, 'exec', '-T', service_of(site), BENCH_PYTHON, '-']


def run_site_script(site, body, *, timeout=120, connect=True, user='Administrator', run=subprocess.run):
    """Run `body` on `site`; return its stdout. A non-zero exit is an AssertionError carrying
    the last lines of stderr; a timeout is announced to ON_TIMEOUT and re-raised."""
    command = command_for(site)
    try:
        result = run(command, cwd=ROOT, input=site_script(site, body, connect=connect, user=user),
                     text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        for observer in list(ON_TIMEOUT):
            observer(site, body)
        raise
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-15:])
        raise AssertionError(f'站点脚本失败（{site} / {service_of(site)}，退出码 {result.returncode}）：\n{tail}')
    return result.stdout


def run_site_json(site, body, **kwargs):
    """The script's last non-empty stdout line parsed as JSON - the convention every script uses."""
    lines = [line for line in run_site_script(site, body, **kwargs).splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f'站点脚本没有输出（{site}）')
    return json.loads(lines[-1])
