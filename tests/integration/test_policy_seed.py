"""Real three-Site provisioning coverage for the fixed legacy DocType policies."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]
PROVISIONER = ROOT / "infra" / "provision_alpha_doctype_policies.py"


SITES = [
    ("dsherp-validation.localhost", "backend"),
    ("dsherp-beta.localhost", "beta-backend"),
    ("dsherp-daily.localhost", "backend"),
]


def _provision(site, *arguments):
    result = subprocess.run(
        [sys.executable, str(PROVISIONER), "--site", site, *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _read_policy_rows(site, service):
    script = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=__SITE__)
frappe.connect()
try:
    rows = frappe.get_all(
        'DS Doctype Policy',
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
'''.replace('__SITE__', repr(site))
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "/home/frappe/frappe-bench/env/bin/python", "-"],
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

EXPECTED_MANUFACTURING_ROWS = [
    {
        "target_doctype": "BOM",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Warehouse",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Bin",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Work Order",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 1,
        "allow_update": 1,
        "allow_submit": 1,
        "allow_cancel": 1,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [
            {
                "route_name": "work_order_material_transfer",
                "method_path": "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
                "target_doctype": "Stock Entry",
            },
            {
                "route_name": "work_order_manufacture",
                "method_path": "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
                "target_doctype": "Stock Entry",
            },
        ],
    },
    {
        "target_doctype": "Stock Entry",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 1,
        "allow_cancel": 1,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
]

EXPECTED_ALL_ROWS = sorted(
    [*EXPECTED_ROWS, *EXPECTED_MANUFACTURING_ROWS],
    key=lambda row: row["target_doctype"].casefold(),
)


def test_policy_readback_does_not_hide_an_unexpected_extra_row():
    site = "dsherp-beta.localhost"
    service = "beta-backend"
    _provision(site)
    create_script = r'''
import os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-beta.localhost')
frappe.connect()
try:
    assert not frappe.db.exists('DS Doctype Policy', 'BOM')
    frappe.get_doc({
        'doctype': 'DS Doctype Policy',
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
    }).insert(ignore_permissions=True)
    frappe.db.commit()
finally:
    frappe.destroy()
'''
    delete_script = r'''
import os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-beta.localhost')
frappe.connect()
try:
    if frappe.db.exists('DS Doctype Policy', 'BOM'):
        frappe.delete_doc('DS Doctype Policy', 'BOM', ignore_permissions=True)
        frappe.db.commit()
finally:
    frappe.destroy()
'''
    create = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=create_script,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert create.returncode == 0, create.stderr
    try:
        assert _read_policy_rows(site, service) == sorted(
            [*EXPECTED_ROWS, EXPECTED_MANUFACTURING_ROWS[0]],
            key=lambda row: row["target_doctype"].casefold(),
        )
    finally:
        delete = subprocess.run(
            [*COMPOSE, "exec", "-T", service, "/home/frappe/frappe-bench/env/bin/python", "-"],
            cwd=ROOT,
            input=delete_script,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert delete.returncode == 0, delete.stderr


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_creates_or_verifies_exact_legacy_rows(site, service):
    result = _provision(site)
    expected_rows = (
        EXPECTED_ALL_ROWS if site == "dsherp-validation.localhost" else EXPECTED_ROWS
    )

    assert result == {
        "site": site,
        "policies": EXPECTED_ROWS,
    }
    assert _read_policy_rows(site, service) == expected_rows


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_second_run_is_idempotent(site, service):
    first_result = _provision(site)
    expected_rows = (
        EXPECTED_ALL_ROWS if site == "dsherp-validation.localhost" else EXPECTED_ROWS
    )
    before = _read_policy_rows(site, service)
    second_result = _provision(site)
    after = _read_policy_rows(site, service)

    assert first_result == second_result
    assert before == after == expected_rows


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_fast_fails_on_conflicting_governance(site, service):
    _provision(site)
    result = _provision(site, "--verify-conflict")
    expected_rows = (
        EXPECTED_ALL_ROWS if site == "dsherp-validation.localhost" else EXPECTED_ROWS
    )

    assert result == {
        "site": site,
        "mode": "conflict_rollback",
        "conflict": "Item.allow_update",
        "baseline": EXPECTED_ROWS,
    }
    assert _read_policy_rows(site, service) == expected_rows


def test_manufacturing_policy_set_provisions_exact_alpha_rows_and_routes():
    result = _provision(
        "dsherp-validation.localhost",
        "--policy-set",
        "manufacturing",
    )

    assert result == {
        "site": "dsherp-validation.localhost",
        "policy_set": "manufacturing",
        "policies": [*EXPECTED_ROWS, *EXPECTED_MANUFACTURING_ROWS],
    }
    assert _read_policy_rows(
        "dsherp-validation.localhost",
        "backend",
    ) == EXPECTED_ALL_ROWS


def test_manufacturing_policy_set_second_run_is_idempotent():
    first_result = _provision(
        "dsherp-validation.localhost",
        "--policy-set",
        "manufacturing",
    )
    before = _read_policy_rows("dsherp-validation.localhost", "backend")
    second_result = _provision(
        "dsherp-validation.localhost",
        "--policy-set",
        "manufacturing",
    )
    after = _read_policy_rows("dsherp-validation.localhost", "backend")

    assert first_result == second_result
    assert before == after == EXPECTED_ALL_ROWS


def test_manufacturing_policy_set_fast_fails_and_rolls_back_conflict():
    _provision("dsherp-validation.localhost", "--policy-set", "manufacturing")
    result = _provision(
        "dsherp-validation.localhost",
        "--policy-set",
        "manufacturing",
        "--verify-conflict",
    )

    assert result == {
        "site": "dsherp-validation.localhost",
        "policy_set": "manufacturing",
        "mode": "conflict_rollback",
        "conflict": "Work Order.allow_update",
        "baseline": [*EXPECTED_ROWS, *EXPECTED_MANUFACTURING_ROWS],
    }
    assert _read_policy_rows(
        "dsherp-validation.localhost",
        "backend",
    ) == EXPECTED_ALL_ROWS


def test_manufacturing_policy_set_is_not_available_for_beta_or_daily():
    for site in ("dsherp-beta.localhost", "dsherp-daily.localhost"):
        result = subprocess.run(
            [
                sys.executable,
                str(PROVISIONER),
                "--site",
                site,
                "--policy-set",
                "manufacturing",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert result.stdout == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["--site", "dsherp-unknown.localhost"],
        ["--site", "http://daily.localhost:18086"],
        ["--service", "backend"],
        ["--user", "Administrator"],
        ["--method", "GET"],
    ],
)
def test_policy_seed_rejects_non_allowlisted_control_plane_selectors(arguments):
    result = subprocess.run(
        [sys.executable, str(PROVISIONER), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == ""
