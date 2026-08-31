import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]
PROVISIONER = ROOT / "infra" / "provision_manufacturing_fixture.py"


def _invoke_provisioner(*arguments):
    return subprocess.run(
        [sys.executable, str(PROVISIONER), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )


def _provision(*arguments):
    result = _invoke_provisioner(*arguments)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _read_probe_state():
    script = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
try:
    names = {
        'warehouses': [
            'DSHERP 制造测试瞬时合成仓库 - DVT',
            'DSHERP 制造测试瞬时合成原料仓 - DVT',
            'DSHERP 制造测试瞬时合成在制仓 - DVT',
            'DSHERP 制造测试瞬时合成成品仓 - DVT',
            'DSHERP 制造测试瞬时合成委外仓 - DVT',
        ],
        'suppliers': ['DSHERP 制造测试瞬时合成供应商', 'DSHERP-MFG-SYN-SUPPLIER-CONFLICT'],
        'items': [
            'DSHERP-MFG-FRESH-FG', 'DSHERP-MFG-FRESH-RM',
            'DSHERP-MFG-FRESH-SERVICE',
        ],
        'boms': ['BOM-DSHERP-MFG-FRESH-FG-001', 'BOM-DSHERP-MFG-SYN-FG-CONFLICT'],
        'reconciliations': ['DSHERP-MFG-FRESH-OPENING-STOCK'],
    }
    print(json.dumps({
        'warehouses': frappe.db.count('Warehouse', {'name': ['in', names['warehouses']]}),
        'suppliers': frappe.db.count('Supplier', {'name': ['in', names['suppliers']]}),
        'items': frappe.db.count('Item', {'name': ['in', names['items']]}),
        'boms': frappe.db.count('BOM', {'name': ['in', names['boms']]}),
        'reconciliations': frappe.db.count(
            'Stock Reconciliation', {'name': ['in', names['reconciliations']]}
        ),
        'stock_ledger_entries': frappe.db.count(
            'Stock Ledger Entry', {'item_code': 'DSHERP-MFG-FRESH-RM'}
        ),
    }, sort_keys=True))
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


def _read_fixture_state():
    script = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
try:
    frappe.set_user('Administrator')
    warehouse_names = [
        'DSHERP 制造测试合成仓库 - DVT',
        'DSHERP 制造测试合成原料仓 - DVT',
        'DSHERP 制造测试合成在制仓 - DVT',
        'DSHERP 制造测试合成成品仓 - DVT',
        'DSHERP 制造测试合成委外仓 - DVT',
    ]
    bom = frappe.get_doc('BOM', 'BOM-DSHERP-MFG-SYN-FG-001')
    reconciliation = frappe.get_doc('Stock Reconciliation', 'DSHERP-MFG-SYN-OPENING-STOCK')
    stock_ledger_entries = frappe.get_all(
        'Stock Ledger Entry',
        filters={
            'item_code': 'DSHERP-MFG-SYN-RM',
            'warehouse': 'DSHERP 制造测试合成原料仓 - DVT',
            'voucher_type': 'Stock Reconciliation',
        },
        fields=['name', 'voucher_no', 'actual_qty', 'qty_after_transaction'],
        order_by='name',
    )
    bin_state = frappe.db.get_value(
        'Bin',
        {'item_code': 'DSHERP-MFG-SYN-RM', 'warehouse': 'DSHERP 制造测试合成原料仓 - DVT'},
        ['name', 'actual_qty'],
        as_dict=True,
    )
    state = {
        'warehouses': frappe.get_all(
            'Warehouse', filters={'name': ['in', warehouse_names]},
            fields=['name', 'warehouse_name', 'parent_warehouse', 'is_group', 'company'], order_by='name'
        ),
        'suppliers': frappe.get_all(
            'Supplier', filters={'supplier_name': 'DSHERP 制造测试合成供应商'},
            fields=['name', 'supplier_name', 'supplier_group', 'supplier_type'], order_by='name'
        ),
        'items': frappe.get_all(
            'Item', filters={'name': ['in', [
                'DSHERP-MFG-SYN-FG', 'DSHERP-MFG-SYN-RM', 'DSHERP-MFG-SYN-SERVICE',
            ]]},
            fields=[
                'name', 'item_name', 'item_group', 'stock_uom', 'is_stock_item',
                'is_purchase_item', 'is_sub_contracted_item', 'disabled',
            ], order_by='name'
        ),
        'bom': {
            'name': bom.name, 'item': bom.item, 'company': bom.company,
            'docstatus': bom.docstatus, 'is_active': bom.is_active,
            'is_default': bom.is_default, 'quantity': float(bom.quantity),
            'items': [
                {'item_code': row.item_code, 'qty': float(row.qty), 'uom': row.uom}
                for row in bom.items
            ],
        },
        'reconciliation': {
            'name': reconciliation.name, 'company': reconciliation.company,
            'purpose': reconciliation.purpose, 'docstatus': reconciliation.docstatus,
            'posting_date': str(reconciliation.posting_date),
            'items': [
                {
                    'item_code': row.item_code, 'warehouse': row.warehouse,
                    'qty': float(row.qty), 'valuation_rate': float(row.valuation_rate),
                }
                for row in reconciliation.items
            ],
        },
        'bin_actual_qty': float(bin_state.actual_qty) if bin_state else None,
        'stock_ledger_entries': [
            {
                'name': row.name, 'voucher_no': row.voucher_no,
                'actual_qty': float(row.actual_qty),
                'qty_after_transaction': float(row.qty_after_transaction),
            }
            for row in stock_ledger_entries
        ],
        'owned_counts': {
            'warehouses': frappe.db.count('Warehouse', {'name': ['in', warehouse_names]}),
            'suppliers': frappe.db.count('Supplier', {'supplier_name': 'DSHERP 制造测试合成供应商'}),
            'items': frappe.db.count('Item', {'name': ['in', [
                'DSHERP-MFG-SYN-FG', 'DSHERP-MFG-SYN-RM', 'DSHERP-MFG-SYN-SERVICE',
            ]]}),
            'boms': frappe.db.count('BOM', {'item': 'DSHERP-MFG-SYN-FG'}),
            'reconciliations': frappe.db.count(
                'Stock Reconciliation Item',
                {'item_code': 'DSHERP-MFG-SYN-RM', 'warehouse': 'DSHERP 制造测试合成原料仓 - DVT'},
            ),
            'stock_ledger_entries': len(stock_ledger_entries),
        },
    }
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
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


def _transition_persistent_fixture(mode):
    script = rf'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
try:
    frappe.set_user('Administrator')
    mode = {mode!r}
    finished_good = 'DSHERP-MFG-SYN-FG'
    service_item = 'DSHERP-MFG-SYN-SERVICE'
    finished_expected = {{
        'item_code': finished_good,
        'item_name': 'DSHERP 制造测试合成成品',
        'item_group': '产品展示',
        'stock_uom': 'Nos',
        'is_stock_item': 1,
        'is_purchase_item': 1,
        'disabled': 0,
    }}
    service_expected = {{
        'item_code': service_item,
        'item_name': 'DSHERP 制造测试合成委外加工服务',
        'item_group': '服务',
        'stock_uom': 'Nos',
        'is_stock_item': 0,
        'is_purchase_item': 1,
        'is_sub_contracted_item': 0,
        'disabled': 0,
    }}

    def require_values(doc, expected, label):
        for fieldname, value in expected.items():
            actual = doc.get(fieldname)
            if actual != value:
                raise RuntimeError(
                    f'{{label}}.{{fieldname}} is {{actual!r}}; expected {{value!r}}'
                )

    finished = frappe.get_doc('Item', finished_good)
    if mode in ('legacy', 'legacy-conflict'):
        require_values(finished, finished_expected, finished_good)
        if finished.is_sub_contracted_item != 1:
            raise RuntimeError('Persistent finished good is not at the T2.4 baseline')
        service = frappe.get_doc('Item', service_item)
        require_values(service, service_expected, service_item)
        frappe.delete_doc('Item', service_item, ignore_permissions=True)
        finished.is_sub_contracted_item = 0
        if mode == 'legacy-conflict':
            finished.item_name = 'DSHERP 制造测试合成成品冲突'
        finished.save(ignore_permissions=True)
    elif mode == 'current':
        if finished.item_name == 'DSHERP 制造测试合成成品冲突':
            finished.item_name = finished_expected['item_name']
        require_values(finished, finished_expected, finished_good)
        if finished.is_sub_contracted_item not in (0, 1):
            raise RuntimeError('Persistent finished-good flag has an unknown value')
        if finished.is_sub_contracted_item == 0 or finished.has_value_changed('item_name'):
            finished.is_sub_contracted_item = 1
            finished.save(ignore_permissions=True)
        if frappe.db.exists('Item', service_item):
            service = frappe.get_doc('Item', service_item)
            require_values(service, service_expected, service_item)
        else:
            frappe.get_doc({{
                'doctype': 'Item',
                **service_expected,
                'valuation_rate': 10.0,
                'description': 'DSHERP 制造测试合成委外加工服务；仅用于隔离 alpha 验收。',
            }}).insert(ignore_permissions=True, set_name=service_item)
    else:
        raise RuntimeError(f'Unsupported fixture transition mode: {{mode}}')
    frappe.db.commit()
    print(json.dumps({{
        'mode': mode,
        'finished_good_flag': frappe.db.get_value(
            'Item', finished_good, 'is_sub_contracted_item'
        ),
        'service_item_exists': bool(frappe.db.exists('Item', service_item)),
    }}, sort_keys=True))
except Exception:
    frappe.db.rollback()
    raise
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


# Production break caught: a valid T0.3 fixture cannot be replayed after T2.4 extends its Item contract.
def test_provisioner_migrates_the_exact_legacy_fixture_then_remains_idempotent():
    before = _read_fixture_state()
    assert [item["name"] for item in before["items"]] == [
        "DSHERP-MFG-SYN-FG",
        "DSHERP-MFG-SYN-RM",
        "DSHERP-MFG-SYN-SERVICE",
    ]
    assert before["items"][0]["is_sub_contracted_item"] == 1

    transition = _transition_persistent_fixture("legacy")
    assert transition == {
        "finished_good_flag": 0,
        "mode": "legacy",
        "service_item_exists": False,
    }
    try:
        first_result = _provision()
        upgraded = _read_fixture_state()
        second_result = _provision()
        after_second = _read_fixture_state()

        assert first_result == second_result
        assert upgraded == after_second == before
        assert upgraded["items"][0]["is_sub_contracted_item"] == 1
        assert upgraded["items"][2] == before["items"][2]
    finally:
        restored = _transition_persistent_fixture("current")
        assert restored == {
            "finished_good_flag": 1,
            "mode": "current",
            "service_item_exists": True,
        }


# Production break caught: migration must not claim an unrelated same-name Item with field drift.
def test_provisioner_rejects_a_legacy_fixture_with_any_other_owned_field_conflict():
    before = _read_fixture_state()
    transition = _transition_persistent_fixture("legacy-conflict")
    assert transition == {
        "finished_good_flag": 0,
        "mode": "legacy-conflict",
        "service_item_exists": False,
    }
    try:
        result = _invoke_provisioner()
        assert result.returncode != 0
        assert (
            "DSHERP-MFG-SYN-FG.item_name is "
            "'DSHERP 制造测试合成成品冲突'; expected 'DSHERP 制造测试合成成品'"
        ) in result.stderr
    finally:
        restored = _transition_persistent_fixture("current")
        assert restored == {
            "finished_good_flag": 1,
            "mode": "current",
            "service_item_exists": True,
        }
    assert _read_fixture_state() == before


# Production break caught: a provisioned fixture is missing its real submitted BOM or opening Bin balance.
def test_provisioner_creates_readable_bom_and_deterministic_opening_stock():
    result = _provision()
    state = _read_fixture_state()

    assert result["site"] == "dsherp-validation.localhost"
    assert state["owned_counts"] == {
        "warehouses": 5,
        "suppliers": 1,
        "items": 3,
        "boms": 1,
        "reconciliations": 1,
        "stock_ledger_entries": 1,
    }
    assert state["items"] == [
        {
            "name": "DSHERP-MFG-SYN-FG",
            "item_name": "DSHERP 制造测试合成成品",
            "item_group": "产品展示",
            "stock_uom": "Nos",
            "is_stock_item": 1,
            "is_purchase_item": 1,
            "is_sub_contracted_item": 1,
            "disabled": 0,
        },
        {
            "name": "DSHERP-MFG-SYN-RM",
            "item_name": "DSHERP 制造测试合成原料",
            "item_group": "原材料",
            "stock_uom": "Nos",
            "is_stock_item": 1,
            "is_purchase_item": 1,
            "is_sub_contracted_item": 0,
            "disabled": 0,
        },
        {
            "name": "DSHERP-MFG-SYN-SERVICE",
            "item_name": "DSHERP 制造测试合成委外加工服务",
            "item_group": "服务",
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "is_purchase_item": 1,
            "is_sub_contracted_item": 0,
            "disabled": 0,
        },
    ]
    assert state["bom"] == {
        "name": "BOM-DSHERP-MFG-SYN-FG-001",
        "item": "DSHERP-MFG-SYN-FG",
        "company": "DSHERP 原生验收测试公司",
        "docstatus": 1,
        "is_active": 1,
        "is_default": 1,
        "quantity": 1.0,
        "items": [{"item_code": "DSHERP-MFG-SYN-RM", "qty": 2.0, "uom": "Nos"}],
    }
    assert state["reconciliation"] == {
        "name": "DSHERP-MFG-SYN-OPENING-STOCK",
        "company": "DSHERP 原生验收测试公司",
        "purpose": "Opening Stock",
        "docstatus": 1,
        "posting_date": "2026-01-01",
        "items": [
            {
                "item_code": "DSHERP-MFG-SYN-RM",
                "warehouse": "DSHERP 制造测试合成原料仓 - DVT",
                "qty": 100.0,
                "valuation_rate": 10.0,
            }
        ],
    }
    assert state["bin_actual_qty"] == 100.0
    assert state["stock_ledger_entries"][0]["voucher_no"] == "DSHERP-MFG-SYN-OPENING-STOCK"
    assert state["stock_ledger_entries"][0]["qty_after_transaction"] == 100.0


# Production break caught: repeat provisioning duplicates masters or posts the owned opening stock twice.
def test_repeat_provisioning_returns_the_same_fixture_without_new_posting():
    first_result = _provision()
    before = _read_fixture_state()
    second_result = _provision()
    after = _read_fixture_state()

    assert second_result == first_result
    assert after == before


# Production break caught: persistent alpha state hides a broken first-creation path on later test runs.
def test_fresh_rollback_mode_really_creates_then_removes_a_transient_fixture():
    empty = {
        "boms": 0,
        "items": 0,
        "reconciliations": 0,
        "stock_ledger_entries": 0,
        "suppliers": 0,
        "warehouses": 0,
    }
    assert _read_probe_state() == empty

    result = _provision("--verify-fresh")

    assert result.get("mode") == "fresh_rollback"
    assert result.get("site") == "dsherp-validation.localhost"
    assert result.get("provision_invocations") == 2
    assert result.get("first_result") == result.get("second_result") == {
        "bom": "BOM-DSHERP-MFG-FRESH-FG-001",
        "finished_good": "DSHERP-MFG-FRESH-FG",
        "opening_qty": 100.0,
        "raw_material": "DSHERP-MFG-FRESH-RM",
        "service_item": "DSHERP-MFG-FRESH-SERVICE",
        "reconciliation": "DSHERP-MFG-FRESH-OPENING-STOCK",
        "supplier": "DSHERP 制造测试瞬时合成供应商",
        "warehouse_group": "DSHERP 制造测试瞬时合成仓库 - DVT",
        "warehouses": {
            "finished_goods": "DSHERP 制造测试瞬时合成成品仓 - DVT",
            "raw": "DSHERP 制造测试瞬时合成原料仓 - DVT",
            "subcontracting": "DSHERP 制造测试瞬时合成委外仓 - DVT",
            "work_in_process": "DSHERP 制造测试瞬时合成在制仓 - DVT",
        },
    }
    assert result.get("first_state") == result.get("second_state")
    state = result.get("first_state")
    assert state["counts"] == {
        "boms": 1,
        "items": 3,
        "reconciliations": 1,
        "stock_ledger_entries": 1,
        "suppliers": 1,
        "warehouses": 5,
    }
    assert state["items"] == [
        {
            "disabled": 0,
            "is_purchase_item": 1,
            "is_stock_item": 1,
            "is_sub_contracted_item": 1,
            "item_group": "产品展示",
            "item_name": "DSHERP 制造测试合成成品",
            "name": "DSHERP-MFG-FRESH-FG",
            "stock_uom": "Nos",
        },
        {
            "disabled": 0,
            "is_purchase_item": 1,
            "is_stock_item": 1,
            "is_sub_contracted_item": 0,
            "item_group": "原材料",
            "item_name": "DSHERP 制造测试合成原料",
            "name": "DSHERP-MFG-FRESH-RM",
            "stock_uom": "Nos",
        },
        {
            "disabled": 0,
            "is_purchase_item": 1,
            "is_stock_item": 0,
            "is_sub_contracted_item": 0,
            "item_group": "服务",
            "item_name": "DSHERP 制造测试合成委外加工服务",
            "name": "DSHERP-MFG-FRESH-SERVICE",
            "stock_uom": "Nos",
        },
    ]
    assert state["bom"] == {
        "docstatus": 1,
        "is_active": 1,
        "is_default": 1,
        "item": "DSHERP-MFG-FRESH-FG",
        "items": [{"item_code": "DSHERP-MFG-FRESH-RM", "qty": 2.0, "uom": "Nos"}],
        "name": "BOM-DSHERP-MFG-FRESH-FG-001",
        "quantity": 1.0,
    }
    assert state["reconciliation"] == {
        "docstatus": 1,
        "items": [
            {
                "item_code": "DSHERP-MFG-FRESH-RM",
                "qty": 100.0,
                "valuation_rate": 10.0,
                "warehouse": "DSHERP 制造测试瞬时合成原料仓 - DVT",
            }
        ],
        "name": "DSHERP-MFG-FRESH-OPENING-STOCK",
        "purpose": "Opening Stock",
    }
    assert state["bin_actual_qty"] == 100.0
    assert state["stock_ledger_entries"] == [
        {
            "actual_qty": 0.0,
            "qty_after_transaction": 100.0,
            "voucher_no": "DSHERP-MFG-FRESH-OPENING-STOCK",
        }
    ]
    assert _read_probe_state() == empty


# Production break caught: conflicts and their rollback must cover every T2.4 fixture Item too.
def test_conflict_verification_rejects_owned_record_drift_and_rolls_back_full_fixture():
    before = _read_probe_state()

    result = _provision("--verify-conflicts")

    assert result == {
        "mode": "conflict_rollback",
        "rejected": {
            "bom": ["BOM-DSHERP-MFG-SYN-FG-001", "BOM-DSHERP-MFG-SYN-FG-CONFLICT"],
            "finished_good": "DSHERP-MFG-SYN-FG",
            "service_item": "DSHERP-MFG-SYN-SERVICE",
            "supplier": ["DSHERP 制造测试合成供应商", "DSHERP-MFG-SYN-SUPPLIER-CONFLICT"],
        },
        "site": "dsherp-validation.localhost",
    }
    assert _read_probe_state() == before
