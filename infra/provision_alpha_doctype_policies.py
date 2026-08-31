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
POLICIES = [
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
    require(frappe.db.exists('DocType', 'DS Doctype Policy'), 'DS Doctype Policy schema is missing')
    require(
        frappe.db.exists('DocType', 'DS Doctype Policy Route'),
        'DS Doctype Policy Route schema is missing',
    )
    frappe.set_user('Administrator')
    require('System Manager' in frappe.get_roles(), 'System Manager control-plane role is required')
    if RUN_MODE == 'verify-conflict':
        baseline = provision()
        policy = frappe.get_doc('DS Doctype Policy', 'Item')
        policy.allow_update = 0
        policy.save()
        try:
            provision()
        except RuntimeError as error:
            require(str(error) == 'DS DocType policy conflict for Item', 'Unexpected conflict error')
        else:
            raise RuntimeError('Conflicting DS DocType policy was silently accepted')
        frappe.db.rollback()
        require(provision() == baseline, 'Conflict rollback changed persistent policy rows')
        frappe.db.rollback()
        result = {
            'site': SITE,
            'mode': 'conflict_rollback',
            'conflict': 'Item.allow_update',
            'baseline': baseline,
        }
    elif RUN_MODE == 'provision':
        policies = provision()
        frappe.db.commit()
        result = {'site': SITE, 'policies': policies}
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
    parser.add_argument("--verify-conflict", action="store_true")
    arguments = parser.parse_args()
    site = arguments.site
    service = SITE_SERVICES[site]
    mode = "verify-conflict" if arguments.verify_conflict else "provision"
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=f"SITE = {site!r}\nRUN_MODE = {mode!r}\n" + SITE_SCRIPT,
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
