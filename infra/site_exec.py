"""How a Site's interpreter is addressed: the pure half, importable outside the test tree.

`tests/integration/site_exec.py` has been the one way into a Site for every integration test.
The evaluation runner needs the same addressing (which compose service hosts which Site, how
a body is wrapped so `frappe.destroy()` is guaranteed) but lives under `evals/`, which pytest
does not collect and which must not import from `tests/`.

So the pure, side-effect-free half lives here and the test module re-exports it: the
integration suite's imports are unchanged, and there is still exactly one table mapping Sites
to services. Running a script — the part that spawns docker, times out, and notifies the
residue registry — stays in the test module, because only tests need it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
