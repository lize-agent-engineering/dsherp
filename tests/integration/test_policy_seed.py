"""Real three-Site provisioning coverage for the fixed legacy DocType policies."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from site_exec import run_site_json, run_site_script


ROOT = Path(__file__).resolve().parents[2]
PROVISIONER = ROOT / "infra" / "provision_alpha_doctype_policies.py"
ALPHA = "dsherp-validation.localhost"


SITES = [
    ("dsherp-validation.localhost", "backend"),
    ("dsherp-beta.localhost", "beta-backend"),
    ("dsherp-daily.localhost", "backend"),
]

READ_ROWS_BODY = r'''
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
'''

SET_ROUTES_BODY = r'''
policy=frappe.get_doc('DS Doctype Policy','Sales Order')
policy.set('routes',json.loads(__ROUTES__))
policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
frappe.db.commit()
'''

# Idempotent on purpose: the policy demands a NEW change_reason on every save, so an
# unconditional rewrite would fail whenever the registry replays this body over routes that
# are already right. It writes only when the Site's routes differ from the provisioned ones.
ROUTES_RESTORE_BODY = r'''
expected=json.loads(EXPECTED)
policy=frappe.get_doc('DS Doctype Policy','Sales Order')
current=[{'route_name':row.route_name,'method_path':row.method_path,'target_doctype':row.target_doctype}
         for row in policy.routes]
if current!=expected:
    policy.set('routes',expected);policy.change_reason='集成测试恢复 '+frappe.generate_hash(length=8)
    policy.save();frappe.db.commit()
print(json.dumps({'restored':current!=expected}))
'''


def _run_provision(site, *arguments):
    return subprocess.run(
        [sys.executable, str(PROVISIONER), "--site", site, *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )


def _provision(site, *arguments):
    result = _run_provision(site, *arguments)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _read_policy_rows(site, service=None):
    """`service` is still accepted because the parametrisation table - and the test ids built
    from it - still carries it; site_exec is what maps a Site to its container service now."""
    return run_site_json(site, READ_ROWS_BODY, timeout=30)


def _set_alpha_sales_order_routes(routes):
    run_site_script(ALPHA, SET_ROUTES_BODY.replace("__ROUTES__", repr(json.dumps(routes))), timeout=30)


def _routes_restore():
    """The body the registry replays at teardown when a host-side timeout kills the client
    before the corrupting test's own finally can put the shared alpha routes back."""
    return ROUTES_RESTORE_BODY.replace("EXPECTED", repr(json.dumps(EXPECTED_ALPHA_SALES_ORDER["routes"])))


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
    {
        "target_doctype": "Supplier",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 1,
        "allow_update": 1,
        "allow_submit": 0,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Purchase Order",
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
                "route_name": "purchase_order_to_purchase_receipt",
                "method_path": "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
                "target_doctype": "Purchase Receipt",
            },
            {
                "route_name": "purchase_order_to_subcontracting_order",
                "method_path": "erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order",
                "target_doctype": "Subcontracting Order",
            },
        ],
    },
    {
        "target_doctype": "Purchase Receipt",
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
    {
        "target_doctype": "Subcontracting Order",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 1,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [
            {
                "route_name": "subcontracting_order_to_supply_stock_entry",
                "method_path": "erpnext.controllers.subcontracting_controller.make_rm_stock_entry",
                "target_doctype": "Stock Entry",
            },
            {
                "route_name": "subcontracting_order_to_subcontracting_receipt",
                "method_path": "erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order.make_subcontracting_receipt",
                "target_doctype": "Subcontracting Receipt",
            },
        ],
    },
    {
        "target_doctype": "Subcontracting Receipt",
        "enabled": 1,
        "allow_read": 1,
        "allow_create": 0,
        "allow_update": 0,
        "allow_submit": 1,
        "allow_cancel": 0,
        "allow_fill": 0,
        "company_scope": None,
        "routes": [],
    },
    {
        "target_doctype": "Delivery Note",
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

EXPECTED_ALPHA_SALES_ORDER = {
    **EXPECTED_ROWS[2],
    "routes": [
        {
            "route_name": "sales_order_to_delivery_note",
            "method_path": "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
            "target_doctype": "Delivery Note",
        },
    ],
}

EXPECTED_ALL_ROWS = sorted(
    [*EXPECTED_ROWS[:2], EXPECTED_ALPHA_SALES_ORDER, *EXPECTED_MANUFACTURING_ROWS],
    key=lambda row: row["target_doctype"].casefold(),
)


def test_policy_readback_does_not_hide_an_unexpected_extra_row(residue):
    site = "dsherp-beta.localhost"
    service = "beta-backend"
    _provision(site)
    create_script = r'''
assert not frappe.db.exists('DS Doctype Policy', 'BOM')
frappe.get_doc({
    'doctype': 'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),
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
'''
    delete_script = r'''
if frappe.db.exists('DS Doctype Policy', 'BOM'):
    frappe.delete_doc('DS Doctype Policy', 'BOM', ignore_permissions=True)
    frappe.db.commit()
'''
    # Registered before the row exists: a timeout on the create or on the readback kills the
    # docker client, not the committed row, and then the finally below never runs. The beta
    # Site is shared, and a stray BOM policy would fail every later beta readback.
    residue.doc(site, "DS Doctype Policy", "BOM")
    run_site_script(site, create_script, timeout=30)
    try:
        assert _read_policy_rows(site, service) == sorted(
            [*EXPECTED_ROWS, EXPECTED_MANUFACTURING_ROWS[0]],
            key=lambda row: row["target_doctype"].casefold(),
        )
    finally:
        run_site_script(site, delete_script, timeout=30)


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_creates_or_verifies_exact_legacy_rows(site, service):
    result = _provision(site)
    expected_rows = (
        EXPECTED_ALL_ROWS
        if site in ("dsherp-validation.localhost", "dsherp-daily.localhost")
        else EXPECTED_ROWS
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
        EXPECTED_ALL_ROWS
        if site in ("dsherp-validation.localhost", "dsherp-daily.localhost")
        else EXPECTED_ROWS
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
        EXPECTED_ALL_ROWS
        if site in ("dsherp-validation.localhost", "dsherp-daily.localhost")
        else EXPECTED_ROWS
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
        "policies": [
            *EXPECTED_ROWS[:2],
            EXPECTED_ALPHA_SALES_ORDER,
            *EXPECTED_MANUFACTURING_ROWS,
        ],
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


def test_manufacturing_policy_set_migrates_exact_pre_delivery_sales_order_route(residue):
    site = ALPHA
    service = "backend"
    # Registered before the routes are corrupted, so a timeout anywhere below still restores
    # them: every other alpha test reads these routes back.
    residue.restore(site, "alpha Sales Order routes", _routes_restore())
    _provision(site, "--policy-set", "manufacturing")
    _set_alpha_sales_order_routes([])
    try:
        downgraded = next(
            row
            for row in _read_policy_rows(site, service)
            if row["target_doctype"] == "Sales Order"
        )
        assert downgraded == EXPECTED_ROWS[2]
        result = _provision(site, "--policy-set", "manufacturing")
        assert result["policies"][2] == EXPECTED_ALPHA_SALES_ORDER
        assert _read_policy_rows(site, service) == EXPECTED_ALL_ROWS
    finally:
        rows = _read_policy_rows(site, service)
        sales_order = next(row for row in rows if row["target_doctype"] == "Sales Order")
        if sales_order != EXPECTED_ALPHA_SALES_ORDER:
            _set_alpha_sales_order_routes(EXPECTED_ALPHA_SALES_ORDER["routes"])


@pytest.mark.parametrize(
    "routes",
    [
        [
            {
                "route_name": "unknown_sales_order_route",
                "method_path": "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
                "target_doctype": "Delivery Note",
            }
        ],
        [
            {
                "route_name": "sales_order_to_delivery_not",
                "method_path": "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
                "target_doctype": "Delivery Note",
            }
        ],
        [
            *EXPECTED_ALPHA_SALES_ORDER["routes"],
            {
                "route_name": "sales_order_to_delivery_note_extra",
                "method_path": "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
                "target_doctype": "Delivery Note",
            },
        ],
    ],
    ids=("unknown", "near_match", "extra"),
)
def test_manufacturing_policy_set_rejects_sales_order_route_drift_without_rewrite(
    routes,
    residue,
):
    site = ALPHA
    service = "backend"
    # Same reason as the migration test: the drift written below is shared state, and the
    # registry is the only thing that undoes it when the client is killed mid-test.
    residue.restore(site, "alpha Sales Order routes", _routes_restore())
    _provision(site, "--policy-set", "manufacturing")
    _set_alpha_sales_order_routes(routes)
    try:
        before = _read_policy_rows(site, service)
        result = _run_provision(site, "--policy-set", "manufacturing")
        assert result.returncode != 0
        assert "DS DocType policy conflict for Sales Order" in result.stderr
        assert _read_policy_rows(site, service) == before
    finally:
        _set_alpha_sales_order_routes(EXPECTED_ALPHA_SALES_ORDER["routes"])
        assert _read_policy_rows(site, service) == EXPECTED_ALL_ROWS


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
        "baseline": [
            *EXPECTED_ROWS[:2],
            EXPECTED_ALPHA_SALES_ORDER,
            *EXPECTED_MANUFACTURING_ROWS,
        ],
    }
    assert _read_policy_rows(
        "dsherp-validation.localhost",
        "backend",
    ) == EXPECTED_ALL_ROWS


def test_manufacturing_policy_set_is_not_available_for_beta():
    result = subprocess.run(
        [
            sys.executable,
            str(PROVISIONER),
            "--site",
            "dsherp-beta.localhost",
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
