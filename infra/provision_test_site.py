"""Create one throwaway test Site with `allow_tests` on an existing bench.

Runs only inside the `test-provision` / `platform-test-provision` control containers, on the
same sites volume the running backend serves. The bench already has apps.txt and
common_site_config.json from its first provisioning: they are checked, never rewritten, and
the new Site is never made the default.

The Site is created in-process through `frappe.installer._new_site`, so the root and admin
passwords stay Python values and never reach an argv. That path - not the two older
provisioners, which shell out to `bench new-site` with the passwords on the command line - is
the precedent here; dsherp/restore_drill.py does the same, including the `new_site=True` init
that Frappe's own `bench new-site` performs first.

The bridge variant completes ERPNext's native setup wizard with a synthetic company so
ERPNext masters exist for native tests. The nginx front ends do not know these Sites; tests
run in-process via `bench run-tests`.
"""
import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

TARGETS = {
    'bridge': {'site': 'dsherp-test.localhost', 'apps': ('erpnext', 'dsherp_bridge'),
               'company': 'DSHERP 原生测试公司', 'abbr': 'DNT'},
    'platform': {'site': 'dsherp-platform-test.localhost', 'apps': ('dsherp_platform',),
                 'company': None, 'abbr': None},
}
BENCH = Path('/home/frappe/frappe-bench')
SITES = BENCH / 'sites'
# The same payload infra/provision_validation_company.py posts; Frappe refuses to save without it.
SETUP = {'language': '简体中文', 'lang': 'zh', 'country': 'China', 'timezone': 'Asia/Shanghai',
         'currency': 'CNY', 'enable_telemetry': 0, 'chart_of_accounts': 'Standard',
         'fy_start_date': '2026-01-01', 'fy_end_date': '2026-12-31', 'setup_demo': 0}
SITE_CONFIG = {'allow_tests': 1, 'disable_scheduler': 1, 'mute_emails': 1, 'pause_scheduler': 1}


def preflight(sites, target):
    """What stops the creation, in words; empty means go."""
    problems = []
    sites = Path(sites)
    if (sites / target['site']).exists():
        return [f"{target['site']} already exists; inspect before retrying"]
    listed = set((sites / 'apps.txt').read_text().split()) if (sites / 'apps.txt').exists() else set()
    for app in target['apps']:
        if app not in listed:
            problems.append(f'bench apps.txt lacks {app}; provision the bench first')
    config = {}
    if (sites / 'common_site_config.json').exists():
        config = json.loads((sites / 'common_site_config.json').read_text() or '{}')
    if not config.get('db_host'):
        problems.append('common_site_config.json has no db_host; provision the bench first')
    return problems


def bench(*arguments):
    """bench sub-commands that carry no secret; their output goes to stderr."""
    subprocess.run(['bench', *arguments], cwd=BENCH, check=True, stdout=sys.stderr)


def create_site(target, db_root_password, admin_password):
    os.chdir(SITES)
    import frappe
    from frappe.installer import _new_site

    # `bench new-site` does this first: without new_site=True the config reader refuses a Site
    # directory that does not exist yet. A falsy db_name makes Frappe generate one.
    frappe.init(target['site'], new_site=True)
    try:
        _new_site(None, target['site'], db_root_username='root', db_root_password=db_root_password,
                  admin_password=admin_password, verbose=False,
                  install_apps=[app for app in target['apps'] if app == 'erpnext'] or None,
                  db_host='db', mariadb_user_host_login_scope='%')
    finally:
        frappe.destroy()


def complete_setup(site, company, abbr):
    os.chdir(SITES)
    import frappe

    frappe.init(site=site, sites_path=str(SITES))
    frappe.connect()
    frappe.set_user('Administrator')
    try:
        if frappe.is_setup_complete() or frappe.db.count('Company'):
            raise SystemExit('Company already initialized; inspect instead of retrying')
        from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
        result = setup_complete({**SETUP, 'company_name': company, 'company_abbr': abbr})
        if result != {'status': 'ok'}:
            raise SystemExit('Native setup returned an unexpected result')
        if not frappe.is_setup_complete() or not frappe.db.exists('Company', company):
            raise SystemExit('Native setup did not persist the synthetic company')
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def main(kind):
    target = TARGETS[kind]
    problems = preflight(SITES, target)
    if problems:
        raise SystemExit('; '.join(problems))
    db_root_password = Path('/run/secrets/db_root_password').read_text().strip()
    admin_password = Path('/run/secrets/test_admin_password').read_text().strip()
    # Frappe prints freely; the one line on stdout is this script's result.
    with contextlib.redirect_stdout(sys.stderr):
        create_site(target, db_root_password, admin_password)
        for app in target['apps']:
            if app != 'erpnext':
                bench('--site', target['site'], 'install-app', app)
        for key, value in SITE_CONFIG.items():
            bench('--site', target['site'], 'set-config', '--parse', key, str(value))
        if target['company']:
            complete_setup(target['site'], target['company'], target['abbr'])
    print(json.dumps({'site': target['site'], 'apps': list(target['apps']), 'company': target['company']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create a throwaway allow_tests Site on an existing bench')
    parser.add_argument('kind', choices=tuple(TARGETS))
    main(parser.parse_args().kind)
