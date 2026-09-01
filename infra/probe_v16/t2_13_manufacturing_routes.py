"""Run the existing manufacturing route tests against the isolated v16 probe Site."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infra.provision_manufacturing_fixture import SITE_SCRIPT
from infra.provision_alpha_doctype_policies import SITE_SCRIPT as POLICY_SCRIPT


CONTAINER = "dsherp-v16probe-backend-1"
SITE = "dsherp-v16probe.localhost"
ORIGINAL_RUN = subprocess.run


def run_in_probe(script, timeout=240):
    result = ORIGINAL_RUN(
        ["docker", "exec", "-i", CONTAINER, "env/bin/python", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


def load_test_module(relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem + "_v16_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def probe_run(arguments, **kwargs):
        rewritten = list(arguments)
        if rewritten[:3] != ["docker", "exec", "-i"]:
            raise AssertionError(f"Unexpected integration subprocess: {rewritten}")
        if rewritten[3] != "dsherp-validation-backend-1":
            raise AssertionError(f"Unexpected integration container: {rewritten[3]}")
        rewritten[3] = CONTAINER
        payload = kwargs.get("input")
        if not isinstance(payload, str):
            raise AssertionError("Manufacturing integration test did not provide a Python payload")
        kwargs["input"] = (
            payload.replace("dsherp-validation.localhost", SITE)
            .replace("DSHERP 原生验收测试公司", "DSHERP v16 Probe 合成企业")
        )
        return ORIGINAL_RUN(rewritten, **kwargs)

    module.subprocess = SimpleNamespace(run=probe_run)
    return module


fixture = f"RUN_MODE = 'provision'\nSITE = {SITE!r}\nOPERATOR = 'probe-business@example.invalid'\n" + SITE_SCRIPT
fixture_result = run_in_probe(fixture)
print(fixture_result.stdout.strip())

policy = (
    f"SITE = {SITE!r}\nRUN_MODE = 'provision'\nPOLICY_SET = 'manufacturing'\n"
    f"ALLOWED_MANUFACTURING_SITES = {{{SITE!r}}}\n"
    + POLICY_SCRIPT
)
policy_result = run_in_probe(policy)
print(policy_result.stdout.strip())

run_in_probe(r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-v16probe.localhost');frappe.connect()
try:
    frappe.set_user('Administrator')
    if not frappe.db.exists('Customer','DSHERP-TEST-CUSTOMER'):
        frappe.get_doc({
            'doctype':'Customer','customer_name':'DSHERP-TEST-CUSTOMER',
            'customer_type':'Company','customer_group':'Commercial','territory':'China',
        }).insert(set_name='DSHERP-TEST-CUSTOMER')
        frappe.db.commit()
finally:frappe.destroy()
''')

derived = run_in_probe(r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-v16probe.localhost');frappe.connect()
try:
    from dsherp_bridge import operations
    expected={
        'Delivery Note':{'installation_status','title'},
        'Delivery Note Item':{'incoming_rate','stock_uom_rate'},
        'Purchase Receipt':{'represents_company','title'},
        'Purchase Receipt Item':{'received_qty','stock_uom_rate','valuation_rate'},
        'Stock Entry':{'total_amount','total_incoming_value','total_outgoing_value'},
        'Stock Entry Detail':{
            'amount','basic_amount','basic_rate','description','expense_account','item_group','item_name',
            'secondary_item_type','valuation_rate',
        },
        'Subcontracting Order Item':{'conversion_factor'},
        'Subcontracting Receipt':{'represents_company','title'},
        'Subcontracting Receipt Item':{
            'expense_account','received_qty','rm_cost_per_qty','rm_supp_cost','service_expense_account',
        },
        'Subcontracting Receipt Supplied Item':{
            'amount','available_qty_for_consumption','cost_center','current_stock','expense_account',
        },
    }
    assert operations._MAKE_INSERT_DERIVED_FIELDS==expected
    print('T2.13 derived fields PASS: 10 parent/child DocTypes')
finally:frappe.destroy()
''')
print(derived.stdout.strip())

tests = [
    ("tests/integration/test_delivery_operations.py", "test_delivery_note_from_sales_order_updates_delivery_status"),
    ("tests/integration/test_work_order_operations.py", "test_work_order_chain_updates_stock"),
    ("tests/integration/test_purchase_operations.py", "test_purchase_order_to_receipt_updates_stock"),
    ("tests/integration/test_subcontracting_operations.py", "test_supplied_material_subcontracting_chain"),
]
for relative_path, function_name in tests:
    module = load_test_module(relative_path)
    getattr(module, function_name)()
    print(f"T2.13 PASS: {Path(relative_path).name}::{function_name}")

print("T2.13 manufacturing PASS: 7 route behaviors, native autoname, derived fields and confirm")
