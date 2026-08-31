"""Provision the fixed legacy DocType policies on an allowlisted synthetic Site."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]
SITE_SERVICES = {
    "dsherp-validation.localhost": "backend",
    "dsherp-beta.localhost": "beta-backend",
    "dsherp-daily.localhost": "backend",
}

SITE_SCRIPT = r'''
import json
import os

import frappe


RUN_MODE = globals().get('RUN_MODE', 'provision')
POLICY_SET = globals().get('POLICY_SET', 'base')
BASE_POLICIES = [
    {
        'target_doctype': 'Customer',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 1,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Item',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 1,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Sales Order',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 1,
        'allow_cancel': 1,
        'allow_fill': 1,
        'company_scope': None,
        'routes': [],
    },
]
MANUFACTURING_POLICIES = [
    {
        'target_doctype': 'BOM',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 0,
        'allow_update': 0,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Warehouse',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 0,
        'allow_update': 0,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Bin',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 0,
        'allow_update': 0,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Work Order',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 1,
        'allow_cancel': 1,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [
            {
                'route_name': 'work_order_material_transfer',
                'method_path': 'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry',
                'target_doctype': 'Stock Entry',
            },
            {
                'route_name': 'work_order_manufacture',
                'method_path': 'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry',
                'target_doctype': 'Stock Entry',
            },
        ],
    },
    {
        'target_doctype': 'Stock Entry',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 0,
        'allow_update': 0,
        'allow_submit': 1,
        'allow_cancel': 1,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Supplier',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 0,
        'allow_cancel': 0,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
    {
        'target_doctype': 'Purchase Order',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 1,
        'allow_update': 1,
        'allow_submit': 1,
        'allow_cancel': 1,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [
            {
                'route_name': 'purchase_order_to_purchase_receipt',
                'method_path': 'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
                'target_doctype': 'Purchase Receipt',
            },
        ],
    },
    {
        'target_doctype': 'Purchase Receipt',
        'enabled': 1,
        'allow_read': 1,
        'allow_create': 0,
        'allow_update': 0,
        'allow_submit': 1,
        'allow_cancel': 1,
        'allow_fill': 0,
        'company_scope': None,
        'routes': [],
    },
]
POLICIES = BASE_POLICIES + (MANUFACTURING_POLICIES if POLICY_SET == 'manufacturing' else [])
SCALAR_FIELDS = (
    'target_doctype', 'enabled', 'allow_read', 'allow_create', 'allow_update',
    'allow_submit', 'allow_cancel', 'allow_fill', 'company_scope',
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def policy_values(doc):
    return {field: doc.get(field) for field in SCALAR_FIELDS}


def policy_routes(doc):
    return [
        {
            'route_name': row.route_name,
            'method_path': row.method_path,
            'target_doctype': row.target_doctype,
        }
        for row in doc.routes
    ]


def ensure_policy(expected):
    name = frappe.db.get_value(
        'DS Doctype Policy', {'target_doctype': expected['target_doctype']}, 'name'
    )
    if name:
        doc = frappe.get_doc('DS Doctype Policy', name)
        actual = {**policy_values(doc), 'routes': policy_routes(doc)}
        require(
            actual == expected,
            'DS DocType policy conflict for ' + expected['target_doctype'],
        )
        return actual
    doc = frappe.get_doc({'doctype': 'DS Doctype Policy', **expected})
    doc.insert()
    return {**policy_values(doc), 'routes': policy_routes(doc)}


def provision():
    return [ensure_policy(expected) for expected in POLICIES]


os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE)
frappe.connect()
try:
    require(frappe.local.site == SITE, 'Unexpected Site: ' + str(frappe.local.site))
    require(POLICY_SET in ('base', 'manufacturing'), 'Unsupported policy set: ' + POLICY_SET)
    require(
        POLICY_SET != 'manufacturing' or SITE == 'dsherp-validation.localhost',
        'Manufacturing policy set is alpha-only in Phase 2',
    )
    require(frappe.db.exists('DocType', 'DS Doctype Policy'), 'DS Doctype Policy schema is missing')
    require(
        frappe.db.exists('DocType', 'DS Doctype Policy Route'),
        'DS Doctype Policy Route schema is missing',
    )
    frappe.set_user('Administrator')
    require('System Manager' in frappe.get_roles(), 'System Manager control-plane role is required')
    if RUN_MODE == 'verify-conflict':
        baseline = provision()
        conflict_target = 'Work Order' if POLICY_SET == 'manufacturing' else 'Item'
        policy = frappe.get_doc('DS Doctype Policy', conflict_target)
        policy.allow_update = 0
        policy.save()
        try:
            provision()
        except RuntimeError as error:
            require(
                str(error) == 'DS DocType policy conflict for ' + conflict_target,
                'Unexpected conflict error',
            )
        else:
            raise RuntimeError('Conflicting DS DocType policy was silently accepted')
        frappe.db.rollback()
        require(provision() == baseline, 'Conflict rollback changed persistent policy rows')
        frappe.db.rollback()
        result = {
            'site': SITE,
            'mode': 'conflict_rollback',
            'conflict': conflict_target + '.allow_update',
            'baseline': baseline,
        }
        if POLICY_SET == 'manufacturing':
            result['policy_set'] = POLICY_SET
    elif RUN_MODE == 'provision':
        policies = provision()
        frappe.db.commit()
        result = {'site': SITE, 'policies': policies}
        if POLICY_SET == 'manufacturing':
            result['policy_set'] = POLICY_SET
    else:
        raise RuntimeError('Unsupported policy provisioning mode: ' + RUN_MODE)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
except Exception:
    frappe.db.rollback()
    raise
finally:
    frappe.destroy()
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site",
        choices=tuple(SITE_SERVICES),
        default="dsherp-validation.localhost",
    )
    parser.add_argument(
        "--policy-set",
        choices=("base", "manufacturing"),
        default="base",
    )
    parser.add_argument("--verify-conflict", action="store_true")
    arguments = parser.parse_args()
    site = arguments.site
    if arguments.policy_set == "manufacturing" and site != "dsherp-validation.localhost":
        parser.error("manufacturing policy set is alpha-only in Phase 2")
    service = SITE_SERVICES[site]
    mode = "verify-conflict" if arguments.verify_conflict else "provision"
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=(
            f"SITE = {site!r}\n"
            f"RUN_MODE = {mode!r}\n"
            f"POLICY_SET = {arguments.policy_set!r}\n"
            + SITE_SCRIPT
        ),
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    payload = json.loads(result.stdout)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
