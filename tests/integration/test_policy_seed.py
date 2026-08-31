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


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_creates_or_verifies_exact_legacy_rows(site, service):
    result = _provision(site)

    assert result == {
        "site": site,
        "policies": EXPECTED_ROWS,
    }
    assert _read_policy_rows(site, service) == EXPECTED_ROWS


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_second_run_is_idempotent(site, service):
    first_result = _provision(site)
    before = _read_policy_rows(site, service)
    second_result = _provision(site)
    after = _read_policy_rows(site, service)

    assert first_result == second_result
    assert before == after == EXPECTED_ROWS


@pytest.mark.parametrize(("site", "service"), SITES)
def test_policy_seed_fast_fails_on_conflicting_governance(site, service):
    _provision(site)
    result = _provision(site, "--verify-conflict")

    assert result == {
        "site": site,
        "mode": "conflict_rollback",
        "conflict": "Item.allow_update",
        "baseline": EXPECTED_ROWS,
    }
    assert _read_policy_rows(site, service) == EXPECTED_ROWS


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
