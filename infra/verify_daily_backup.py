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
    try:
        result = subprocess.run(command, cwd=ROOT)
    finally:
        bench_log = ROOT / 'logs' / 'bench.log'
        if bench_log.exists():
            redacted = bench_log.read_text(errors='replace')
            for secret in (root_password, admin_password):
                redacted = redacted.replace(secret, "[redacted]")
            bench_log.write_text(redacted)
    if result.returncode:
        raise SystemExit(f'Bench backup verification command failed with exit code {result.returncode}')


def snapshot(site):
    os.chdir(ROOT / 'sites')
    frappe.init(site=site)
    frappe.connect()
    abbreviation = frappe.db.get_value('Company', 'DSHERP 日常合成企业', 'abbr')
    warehouse_names = [
        f'DSHERP 制造测试合成{label} - {abbreviation}'
        for label in ('仓库', '原料仓', '在制仓', '委外仓', '成品仓')
    ]
    policies = []
    for name in frappe.get_all('DS Doctype Policy', pluck='name', order_by='target_doctype'):
        policy = frappe.get_doc('DS Doctype Policy', name)
        policies.append({
            'target_doctype': policy.target_doctype,
            'enabled': policy.enabled,
            'allow_read': policy.allow_read,
            'allow_create': policy.allow_create,
            'allow_update': policy.allow_update,
            'allow_submit': policy.allow_submit,
            'allow_cancel': policy.allow_cancel,
            'allow_fill': policy.allow_fill,
            'company_scope': policy.company_scope,
            'routes': [
                {
                    'route_name': row.route_name,
                    'method_path': row.method_path,
                    'target_doctype': row.target_doctype,
                }
                for row in policy.routes
            ],
        })
    bom = frappe.get_doc('BOM', 'BOM-DSHERP-MFG-SYN-FG-001')
    reconciliation = frappe.get_doc(
        'Stock Reconciliation', 'DSHERP-MFG-SYN-OPENING-STOCK'
    )
    report = {
        'apps': sorted(frappe.get_installed_apps()),
        'setup_complete': int(frappe.db.get_single_value('System Settings', 'setup_complete') or 0),
        'companies': sorted(frappe.get_all('Company', pluck='name')),
        'items': sorted(frappe.get_all('Item', pluck='name')),
        'customers': sorted(frappe.get_all('Customer', pluck='customer_name')),
        'sales_orders': sorted(frappe.get_all('Sales Order', pluck='name')),
        'operator_roles': sorted(
            row.role
            for row in frappe.get_doc('User', 'daily-operator@example.invalid').roles
        ),
        'fixture_warehouses': [
            dict(row)
            for row in frappe.get_all(
                'Warehouse', filters={'name': ['in', warehouse_names]},
                fields=['name', 'warehouse_name', 'parent_warehouse', 'is_group', 'company'],
                order_by='name',
            )
        ],
        'fixture_supplier': frappe.get_all(
            'Supplier', filters={'name': 'DSHERP 制造测试合成供应商'},
            fields=['name', 'supplier_name', 'supplier_group', 'supplier_type'],
        ),
        'fixture_bom': {
            'name': bom.name,
            'item': bom.item,
            'company': bom.company,
            'docstatus': bom.docstatus,
            'is_active': bom.is_active,
            'is_default': bom.is_default,
            'quantity': float(bom.quantity),
            'items': [
                {'item_code': row.item_code, 'qty': float(row.qty), 'uom': row.uom}
                for row in bom.items
            ],
        },
        'fixture_reconciliation': {
            'name': reconciliation.name,
            'company': reconciliation.company,
            'purpose': reconciliation.purpose,
            'docstatus': reconciliation.docstatus,
            'posting_date': str(reconciliation.posting_date),
            'items': [
                {
                    'item_code': row.item_code,
                    'warehouse': row.warehouse,
                    'qty': float(row.qty),
                    'valuation_rate': float(row.valuation_rate),
                }
                for row in reconciliation.items
            ],
        },
        'raw_qty': float(frappe.db.get_value(
            'Bin',
            {
                'item_code': 'DSHERP-MFG-SYN-RM',
                'warehouse': f'DSHERP 制造测试合成原料仓 - {abbreviation}',
            },
            'actual_qty',
        ) or 0),
        'policies': policies,
        'business_documents': {
            doctype: sorted(frappe.get_all(doctype, pluck='name'))
            for doctype in (
                'Sales Order', 'Work Order', 'Stock Entry', 'Purchase Order',
                'Purchase Receipt', 'Subcontracting Order',
                'Subcontracting Receipt', 'Delivery Note',
            )
        },
        'audit_counts': {
            doctype: frappe.db.count(doctype)
            for doctype in (
                'DS Conversation', 'DS Model Run', 'DS Operation Proposal',
                'DS Execution Record', 'DS Run Event',
            )
        },
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
public = BACKUPS / f'{prefix}-files.tar'
private = BACKUPS / f'{prefix}-private-files.tar'
config = BACKUPS / f'{prefix}-site_config_backup.json'
for artifact in (database, config, public, private):
    if not artifact.is_file() or artifact.stat().st_size == 0:
        # v16 writes plain .tar; `bench backup --compress` writes the v15-era .tgz instead.
        compressed = artifact.with_suffix('.tgz')
        if compressed.is_file():
            raise SystemExit(
                f'Missing or empty backup artifact: {artifact.name}; found {compressed.name} instead. '
                'Retake the backup without --compress.'
            )
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
print(json.dumps({
    'site': RESTORED,
    'backup_prefix': prefix,
    'backup_artifacts': [database.name, config.name, public.name, private.name],
    **report,
}, sort_keys=True))
