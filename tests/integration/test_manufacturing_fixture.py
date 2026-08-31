import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]
PROVISIONER = ROOT / "infra" / "provision_manufacturing_fixture.py"


def _provision():
    result = subprocess.run(
        [sys.executable, str(PROVISIONER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
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
            'Item', filters={'name': ['in', ['DSHERP-MFG-SYN-FG', 'DSHERP-MFG-SYN-RM']]},
            fields=['name', 'item_name', 'item_group', 'stock_uom', 'is_stock_item'], order_by='name'
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
            'items': frappe.db.count('Item', {'name': ['in', ['DSHERP-MFG-SYN-FG', 'DSHERP-MFG-SYN-RM']]}),
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


# Production break caught: a provisioned fixture is missing its real submitted BOM or opening Bin balance.
def test_provisioner_creates_readable_bom_and_deterministic_opening_stock():
    result = _provision()
    state = _read_fixture_state()

    assert result["site"] == "dsherp-validation.localhost"
    assert state["owned_counts"] == {
        "warehouses": 5,
        "suppliers": 1,
        "items": 2,
        "boms": 1,
        "reconciliations": 1,
        "stock_ledger_entries": 1,
    }
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
