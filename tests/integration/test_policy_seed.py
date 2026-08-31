"""Real alpha provisioning coverage for the fixed legacy DocType policies."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]
PROVISIONER = ROOT / "infra" / "provision_alpha_doctype_policies.py"


def _provision(*arguments):
    result = subprocess.run(
        [sys.executable, str(PROVISIONER), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _read_policy_rows():
    script = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
try:
    rows = frappe.get_all(
        'DS Doctype Policy',
        filters={'target_doctype': ['in', ['Item', 'Customer', 'Sales Order']]},
        fields=['target_doctype', 'enabled', 'allow_read', 'allow_create', 'allow_update',
                'allow_submit', 'allow_cancel', 'allow_fill', 'company_scope'],
        order_by='target_doctype asc',
    )
    for row in rows:
        row['routes'] = frappe.get_all(
            'DS Doctype Policy Route',
            filters={'parent': row['target_doctype']},
            fields=['route_name', 'method_path', 'target_doctype'],
            order_by='idx asc, name asc',
        )
    print(json.dumps(rows, ensure_ascii=False, sort_keys=True))
finally:
    frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


EXPECTED_ROWS = [
    {
        "target_doctype": "Customer",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 1,
        "allow_update": 1,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 1,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Item",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 1,
        "allow_update": 1,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 1,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Sales Order",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 1,
        "allow_update": 1,
        "allow_submit": 1,
        "allow_cancel": 1,
        "allow_fill": 1,
        "company_scope": None,
        "routes": [],
    },
]


def test_alpha_policy_seed_creates_exact_legacy_rows():
    result = _provision()

    assert result == {
        "site": "dsherp-validation.localhost",
        "policies": EXPECTED_ROWS,
    }
    assert _read_policy_rows() == EXPECTED_ROWS


def test_alpha_policy_seed_second_run_is_idempotent():
    first_result = _provision()
    before = _read_policy_rows()
    second_result = _provision()
    after = _read_policy_rows()

    assert first_result == second_result
    assert before == after == EXPECTED_ROWS


def test_alpha_policy_seed_fast_fails_on_conflicting_governance():
    _provision()
    result = _provision("--verify-conflict")

    assert result == {
        "site": "dsherp-validation.localhost",
        "mode": "conflict_rollback",
        "conflict": "Item.allow_update",
        "baseline": EXPECTED_ROWS,
    }
    assert _read_policy_rows() == EXPECTED_ROWS
