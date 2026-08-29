"""Restore the newest daily backup into one fixed disposable Site and verify it."""
from pathlib import Path
import argparse
import json
import os
import subprocess

import frappe


ROOT = Path('/home/frappe/frappe-bench')
SOURCE = 'dsherp-daily.localhost'
RESTORED = 'dsherp-daily-restore.localhost'
BACKUPS = ROOT / 'sites' / SOURCE / 'private' / 'backups'
root_password = Path('/run/secrets/db_root_password').read_text().strip()
admin_password = Path('/run/secrets/daily_admin_password').read_text().strip()
parser = argparse.ArgumentParser()
parser.add_argument('--resume-restored', action='store_true', help='Verify an interrupted completed restore without overwriting it')
resume_restored = parser.parse_args().resume_restored


def run(command):
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(f'Bench backup verification command failed with exit code {result.returncode}')


def snapshot(site):
    os.chdir(ROOT / 'sites')
    frappe.init(site=site)
    frappe.connect()
    report = {
        'apps': sorted(frappe.get_installed_apps()),
        'setup_complete': int(frappe.db.get_single_value('System Settings', 'setup_complete') or 0),
        'companies': sorted(frappe.get_all('Company', pluck='name')),
        'items': sorted(frappe.get_all('Item', pluck='name')),
        'customers': sorted(frappe.get_all('Customer', pluck='customer_name')),
        'sales_orders': sorted(frappe.get_all('Sales Order', pluck='name')),
        'operator_exists': bool(frappe.db.exists('User', 'daily-operator@example.invalid')),
    }
    frappe.destroy()
    return report

restored_exists = (ROOT / 'sites' / RESTORED).exists()
if restored_exists and not resume_restored:
    raise SystemExit('Disposable restore Site already exists; inspect instead of overwriting it')
if resume_restored and not restored_exists:
    raise SystemExit('No completed disposable restore Site exists to resume')

expected = snapshot(SOURCE)

databases = sorted(BACKUPS.glob('*-database.sql.gz'))
if not databases:
    raise SystemExit('No daily database backup exists')
database = databases[-1]
prefix = database.name.removesuffix('-database.sql.gz')
public = BACKUPS / f'{prefix}-files.tgz'
private = BACKUPS / f'{prefix}-private-files.tgz'
for artifact in (database, public, private):
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise SystemExit(f'Missing or empty backup artifact: {artifact.name}')

if not restored_exists:
    run([
        'bench', 'new-site', RESTORED, '--db-host', 'db',
        '--db-root-username', 'root', '--db-root-password', root_password,
        '--admin-password', admin_password, '--mariadb-user-host-login-scope', '%',
    ])
    run([
        'bench', '--site', RESTORED, 'restore', str(database),
        '--db-root-username', 'root', '--db-root-password', root_password,
        '--admin-password', admin_password,
        '--with-public-files', str(public), '--with-private-files', str(private),
        '--force',
    ])

report = snapshot(RESTORED)
if report != expected:
    raise SystemExit('Restored daily Site does not match the source snapshot: ' + json.dumps({'expected': expected, 'restored': report}, sort_keys=True))
if not {'frappe', 'erpnext', 'dsherp_bridge'}.issubset(report['apps']):
    raise SystemExit('Restored daily Site is missing required apps: ' + json.dumps(report, sort_keys=True))

run([
    'bench', 'drop-site', RESTORED, '--force', '--no-backup',
    '--db-root-username', 'root', '--db-root-password', root_password,
])
print(json.dumps({'site': RESTORED, **report}, sort_keys=True))
