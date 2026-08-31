"""Provision the idempotent synthetic manufacturing fixture on the alpha Site."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]

SITE_SCRIPT = r'''
import json
import os

import erpnext
import frappe
from frappe.utils import flt


RUN_MODE = globals().get('RUN_MODE', 'provision')
SITE = 'dsherp-validation.localhost'
ERP_VERSION = '15.119.3'
FRAPPE_VERSION = '15.118.0'
WAREHOUSE_LABELS = {
    'group': 'DSHERP 制造测试合成仓库',
    'raw': 'DSHERP 制造测试合成原料仓',
    'work_in_process': 'DSHERP 制造测试合成在制仓',
    'finished_goods': 'DSHERP 制造测试合成成品仓',
    'subcontracting': 'DSHERP 制造测试合成委外仓',
}
SUPPLIER = 'DSHERP 制造测试合成供应商'
PERSISTENT_FINISHED_GOOD = 'DSHERP-MFG-SYN-FG'
FINISHED_GOOD = PERSISTENT_FINISHED_GOOD
RAW_MATERIAL = 'DSHERP-MFG-SYN-RM'
SERVICE_ITEM = 'DSHERP-MFG-SYN-SERVICE'
BOM = 'BOM-DSHERP-MFG-SYN-FG-001'
RECONCILIATION = 'DSHERP-MFG-SYN-OPENING-STOCK'
OPENING_DATE = '2026-01-01'
OPENING_QTY = 100.0
VALUATION_RATE = 10.0
REQUIRED_RAW_QTY = 2.0

if RUN_MODE == 'verify-fresh':
    WAREHOUSE_LABELS = {
        'group': 'DSHERP 制造测试瞬时合成仓库',
        'raw': 'DSHERP 制造测试瞬时合成原料仓',
        'work_in_process': 'DSHERP 制造测试瞬时合成在制仓',
        'finished_goods': 'DSHERP 制造测试瞬时合成成品仓',
        'subcontracting': 'DSHERP 制造测试瞬时合成委外仓',
    }
    SUPPLIER = 'DSHERP 制造测试瞬时合成供应商'
    FINISHED_GOOD = 'DSHERP-MFG-FRESH-FG'
    RAW_MATERIAL = 'DSHERP-MFG-FRESH-RM'
    SERVICE_ITEM = 'DSHERP-MFG-FRESH-SERVICE'
    BOM = 'BOM-DSHERP-MFG-FRESH-FG-001'
    RECONCILIATION = 'DSHERP-MFG-FRESH-OPENING-STOCK'
elif RUN_MODE not in ('provision', 'verify-conflicts'):
    raise RuntimeError(f'Unsupported manufacturing fixture mode: {RUN_MODE}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def exactly_one(values, label):
    require(len(values) == 1, f'Expected exactly one {label}; found {len(values)}')
    return values[0]


def require_field(doctype, fieldname, fieldtype, options=None):
    field = frappe.get_meta(doctype).get_field(fieldname)
    require(field is not None, f'{doctype}.{fieldname} is missing from installed schema')
    require(field.fieldtype == fieldtype, f'{doctype}.{fieldname} must be {fieldtype}')
    if options is not None:
        require(field.options == options, f'{doctype}.{fieldname} must link to {options}')


def require_values(doc, expected, label):
    for fieldname, value in expected.items():
        actual = doc.get(fieldname)
        require(actual == value, f'{label}.{fieldname} is {actual!r}; expected {value!r}')


def values_match(doc, expected):
    return all(doc.get(fieldname) == value for fieldname, value in expected.items())


def warehouse_name(label, abbreviation):
    return f'{label} - {abbreviation}'


def ensure_warehouse(label, abbreviation, company, parent, is_group):
    name = warehouse_name(label, abbreviation)
    expected = {
        'warehouse_name': label,
        'company': company,
        'parent_warehouse': parent,
        'is_group': is_group,
        'disabled': 0,
    }
    if frappe.db.exists('Warehouse', name):
        doc = frappe.get_doc('Warehouse', name)
        require_values(doc, expected, name)
        return doc
    doc = frappe.get_doc({'doctype': 'Warehouse', **expected})
    doc.insert(set_name=name)
    return doc


def ensure_supplier(supplier_group, country):
    expected = {
        'supplier_name': SUPPLIER,
        'supplier_group': supplier_group,
        'supplier_type': 'Company',
        'country': country,
        'disabled': 0,
    }
    matches = sorted(frappe.get_all('Supplier', filters={'supplier_name': SUPPLIER}, pluck='name'))
    if frappe.db.exists('Supplier', SUPPLIER):
        require(matches == [SUPPLIER], f'Synthetic Supplier name collision: {matches}')
        doc = frappe.get_doc('Supplier', SUPPLIER)
        require_values(doc, expected, SUPPLIER)
        return doc
    require(not matches, f'Synthetic Supplier name collision: {matches}')
    doc = frappe.get_doc({'doctype': 'Supplier', **expected})
    doc.insert(set_name=SUPPLIER)
    return doc


def ensure_item(
    item_code, item_name, item_group, stock_uom, *, is_stock_item,
    is_sub_contracted_item=0,
):
    expected = {
        'item_code': item_code,
        'item_name': item_name,
        'item_group': item_group,
        'stock_uom': stock_uom,
        'is_stock_item': is_stock_item,
        'is_purchase_item': 1,
        'is_sub_contracted_item': is_sub_contracted_item,
        'disabled': 0,
    }
    if frappe.db.exists('Item', item_code):
        doc = frappe.get_doc('Item', item_code)
        legacy_expected = {**expected, 'is_sub_contracted_item': 0}
        if (
            RUN_MODE == 'provision'
            and item_code == PERSISTENT_FINISHED_GOOD
            and is_sub_contracted_item == 1
            and not frappe.db.exists('Item', SERVICE_ITEM)
            and values_match(doc, legacy_expected)
        ):
            doc.is_sub_contracted_item = 1
            doc.save()
        require_values(doc, expected, item_code)
        return doc
    doc = frappe.get_doc({
        'doctype': 'Item',
        **expected,
        'valuation_rate': VALUATION_RATE,
        'description': f'{item_name}；仅用于隔离 alpha 验收。',
    })
    doc.insert(set_name=item_code)
    return doc


def ensure_bom(company, currency, stock_uom):
    matches = sorted(frappe.get_all('BOM', filters={'item': FINISHED_GOOD}, pluck='name'))
    if frappe.db.exists('BOM', BOM):
        require(matches == [BOM], f'Synthetic finished good BOM collision: {matches}')
        doc = frappe.get_doc('BOM', BOM)
    else:
        require(not matches, f'Synthetic finished good already has another BOM: {matches}')
        doc = frappe.get_doc({
            'doctype': 'BOM',
            'item': FINISHED_GOOD,
            'company': company,
            'quantity': 1,
            'currency': currency,
            'conversion_rate': 1,
            'rm_cost_as_per': 'Valuation Rate',
            'is_active': 1,
            'is_default': 1,
            'with_operations': 0,
            'items': [{
                'item_code': RAW_MATERIAL,
                'qty': REQUIRED_RAW_QTY,
                'uom': stock_uom,
                'rate': VALUATION_RATE,
            }],
        })
        doc.insert(set_name=BOM)
        doc.submit()
    require_values(doc, {
        'item': FINISHED_GOOD,
        'company': company,
        'docstatus': 1,
        'is_active': 1,
        'is_default': 1,
    }, BOM)
    require(flt(doc.quantity) == 1, f'{BOM}.quantity must be 1')
    require(len(doc.items) == 1, f'{BOM} must contain exactly one raw-material row')
    item = doc.items[0]
    require(item.item_code == RAW_MATERIAL, f'{BOM} raw material differs')
    require(flt(item.qty) == REQUIRED_RAW_QTY, f'{BOM} raw-material quantity differs')
    require(item.uom == stock_uom, f'{BOM} raw-material UOM differs')
    return doc


def ensure_opening_stock(company, raw_warehouse, opening_account, cost_center):
    owned_rows = frappe.get_all(
        'Stock Reconciliation Item',
        filters={'item_code': RAW_MATERIAL, 'warehouse': raw_warehouse},
        fields=['parent', 'qty', 'valuation_rate'],
        order_by='parent',
    )
    if frappe.db.exists('Stock Reconciliation', RECONCILIATION):
        doc = frappe.get_doc('Stock Reconciliation', RECONCILIATION)
    else:
        require(not owned_rows, 'Synthetic raw-material opening stock already belongs to another reconciliation')
        require(
            not frappe.db.exists('Stock Ledger Entry', {'item_code': RAW_MATERIAL}),
            'Synthetic raw material already has stock ledger entries',
        )
        require(
            not frappe.db.exists('Bin', {'item_code': RAW_MATERIAL, 'actual_qty': ['!=', 0]}),
            'Synthetic raw material already has non-zero stock',
        )
        doc = frappe.get_doc({
            'doctype': 'Stock Reconciliation',
            'naming_series': 'MAT-RECO-.YYYY.-',
            'company': company,
            'purpose': 'Opening Stock',
            'posting_date': OPENING_DATE,
            'posting_time': '09:00:00',
            'set_posting_time': 1,
            'expense_account': opening_account,
            'cost_center': cost_center,
            'items': [{
                'item_code': RAW_MATERIAL,
                'warehouse': raw_warehouse,
                'qty': OPENING_QTY,
                'valuation_rate': VALUATION_RATE,
            }],
        })
        doc.insert(set_name=RECONCILIATION)
        doc.submit()
    require_values(doc, {
        'company': company,
        'purpose': 'Opening Stock',
        'docstatus': 1,
        'expense_account': opening_account,
        'cost_center': cost_center,
    }, RECONCILIATION)
    require(str(doc.posting_date) == OPENING_DATE, f'{RECONCILIATION} posting date differs')
    require(len(doc.items) == 1, f'{RECONCILIATION} must contain exactly one item row')
    item = doc.items[0]
    require(item.item_code == RAW_MATERIAL, f'{RECONCILIATION} item differs')
    require(item.warehouse == raw_warehouse, f'{RECONCILIATION} warehouse differs')
    require(flt(item.qty) == OPENING_QTY, f'{RECONCILIATION} quantity differs')
    require(flt(item.valuation_rate) == VALUATION_RATE, f'{RECONCILIATION} valuation rate differs')
    owned_rows = frappe.get_all(
        'Stock Reconciliation Item',
        filters={'item_code': RAW_MATERIAL, 'warehouse': raw_warehouse},
        fields=['parent'],
    )
    require([row.parent for row in owned_rows] == [RECONCILIATION], 'Synthetic opening reconciliation is not unique')
    actual_qty = frappe.db.get_value(
        'Bin', {'item_code': RAW_MATERIAL, 'warehouse': raw_warehouse}, 'actual_qty'
    )
    require(flt(actual_qty) == OPENING_QTY, f'Synthetic raw-material Bin quantity is {actual_qty!r}')
    stock_entries = frappe.get_all(
        'Stock Ledger Entry',
        filters={
            'item_code': RAW_MATERIAL,
            'warehouse': raw_warehouse,
            'voucher_type': 'Stock Reconciliation',
        },
        fields=['voucher_no'],
    )
    require(
        [row.voucher_no for row in stock_entries] == [RECONCILIATION],
        'Synthetic opening stock must have exactly one stock-ledger posting',
    )
    return doc, flt(actual_qty)


def fixture_counts(abbreviation):
    warehouse_names = [warehouse_name(label, abbreviation) for label in WAREHOUSE_LABELS.values()]
    return {
        'warehouses': frappe.db.count('Warehouse', {'name': ['in', warehouse_names]}),
        'suppliers': frappe.db.count('Supplier', {'name': SUPPLIER}),
        'items': frappe.db.count(
            'Item', {'name': ['in', [FINISHED_GOOD, RAW_MATERIAL, SERVICE_ITEM]]}
        ),
        'boms': frappe.db.count('BOM', {'name': BOM}),
        'reconciliations': frappe.db.count('Stock Reconciliation', {'name': RECONCILIATION}),
        'stock_ledger_entries': frappe.db.count('Stock Ledger Entry', {'item_code': RAW_MATERIAL}),
    }


def fixture_state(abbreviation, raw_warehouse):
    bom = frappe.get_doc('BOM', BOM)
    reconciliation = frappe.get_doc('Stock Reconciliation', RECONCILIATION)
    bin_actual_qty = frappe.db.get_value(
        'Bin', {'item_code': RAW_MATERIAL, 'warehouse': raw_warehouse}, 'actual_qty'
    )
    return {
        'counts': fixture_counts(abbreviation),
        'warehouses': [
            dict(row) for row in frappe.get_all(
                'Warehouse',
                filters={
                    'name': ['in', [
                        warehouse_name(label, abbreviation) for label in WAREHOUSE_LABELS.values()
                    ]],
                },
                fields=['name', 'warehouse_name', 'parent_warehouse', 'is_group', 'company'],
                order_by='name',
            )
        ],
        'supplier': dict(frappe.db.get_value(
            'Supplier', SUPPLIER,
            ['name', 'supplier_name', 'supplier_group', 'supplier_type'], as_dict=True,
        )),
        'items': [
            dict(row) for row in frappe.get_all(
                'Item',
                filters={'name': ['in', [FINISHED_GOOD, RAW_MATERIAL, SERVICE_ITEM]]},
                fields=[
                    'name', 'item_name', 'item_group', 'stock_uom', 'is_stock_item',
                    'is_purchase_item', 'is_sub_contracted_item', 'disabled',
                ],
                order_by='name',
            )
        ],
        'bom': {
            'name': bom.name,
            'item': bom.item,
            'docstatus': bom.docstatus,
            'is_active': bom.is_active,
            'is_default': bom.is_default,
            'quantity': flt(bom.quantity),
            'items': [
                {'item_code': row.item_code, 'qty': flt(row.qty), 'uom': row.uom}
                for row in bom.items
            ],
        },
        'reconciliation': {
            'name': reconciliation.name,
            'purpose': reconciliation.purpose,
            'docstatus': reconciliation.docstatus,
            'items': [
                {
                    'item_code': row.item_code,
                    'warehouse': row.warehouse,
                    'qty': flt(row.qty),
                    'valuation_rate': flt(row.valuation_rate),
                }
                for row in reconciliation.items
            ],
        },
        'bin_actual_qty': flt(bin_actual_qty),
        'stock_ledger_entries': [
            {
                'voucher_no': row.voucher_no,
                'actual_qty': flt(row.actual_qty),
                'qty_after_transaction': flt(row.qty_after_transaction),
            }
            for row in frappe.get_all(
                'Stock Ledger Entry',
                filters={
                    'item_code': RAW_MATERIAL,
                    'warehouse': raw_warehouse,
                    'voucher_type': 'Stock Reconciliation',
                },
                fields=['voucher_no', 'actual_qty', 'qty_after_transaction'],
                order_by='name',
            )
        ],
    }


def provision_fixture(
    company, root_warehouse, supplier_group, finished_group, raw_group,
    service_group, stock_uom, opening_account,
):
    group = ensure_warehouse(
        WAREHOUSE_LABELS['group'], company.abbr, company.name, root_warehouse, 1
    )
    warehouses = {
        key: ensure_warehouse(label, company.abbr, company.name, group.name, 0).name
        for key, label in WAREHOUSE_LABELS.items()
        if key != 'group'
    }
    supplier = ensure_supplier(supplier_group, company.country)
    finished_good = ensure_item(
        FINISHED_GOOD, 'DSHERP 制造测试合成成品', finished_group, stock_uom,
        is_stock_item=1, is_sub_contracted_item=1,
    )
    raw_material = ensure_item(
        RAW_MATERIAL, 'DSHERP 制造测试合成原料', raw_group, stock_uom,
        is_stock_item=1,
    )
    service_item = ensure_item(
        SERVICE_ITEM, 'DSHERP 制造测试合成委外加工服务', service_group, stock_uom,
        is_stock_item=0,
    )
    bom = ensure_bom(company.name, company.default_currency, stock_uom)
    reconciliation, actual_qty = ensure_opening_stock(
        company.name, warehouses['raw'], opening_account, company.cost_center
    )
    return {
        'warehouse_group': group.name,
        'warehouses': warehouses,
        'supplier': supplier.name,
        'finished_good': finished_good.name,
        'raw_material': raw_material.name,
        'service_item': service_item.name,
        'bom': bom.name,
        'reconciliation': reconciliation.name,
        'opening_qty': actual_qty,
    }


def require_runtime_error(operation, label):
    try:
        operation()
    except RuntimeError:
        return
    raise RuntimeError(f'{label} conflict was not rejected')


def verify_conflicts(company, supplier_group, finished_group, service_group, stock_uom):
    expected = {
        'warehouses': 5, 'suppliers': 1, 'items': 3, 'boms': 1,
        'reconciliations': 1, 'stock_ledger_entries': 1,
    }
    raw_warehouse = warehouse_name(WAREHOUSE_LABELS['raw'], company.abbr)
    baseline = fixture_state(company.abbr, raw_warehouse)
    require(baseline['counts'] == expected, 'Persistent synthetic fixture baseline differs')
    supplier_conflict = 'DSHERP-MFG-SYN-SUPPLIER-CONFLICT'
    bom_conflict = 'BOM-DSHERP-MFG-SYN-FG-CONFLICT'
    require(not frappe.db.exists('Supplier', supplier_conflict), 'Transient Supplier conflict already exists')
    require(not frappe.db.exists('BOM', bom_conflict), 'Transient BOM conflict already exists')
    finished_good = frappe.get_doc('Item', FINISHED_GOOD)
    finished_good.item_name = 'DSHERP 制造测试合成成品冲突'
    finished_good.is_sub_contracted_item = 0
    finished_good.save()
    service_item = frappe.get_doc('Item', SERVICE_ITEM)
    service_item.item_name = 'DSHERP 制造测试合成委外加工服务冲突'
    service_item.save()
    frappe.get_doc({
        'doctype': 'Supplier',
        'supplier_name': SUPPLIER,
        'supplier_group': supplier_group,
        'supplier_type': 'Company',
        'country': company.country,
        'disabled': 0,
    }).insert(set_name=supplier_conflict)
    frappe.get_doc({
        'doctype': 'BOM',
        'item': FINISHED_GOOD,
        'company': company.name,
        'quantity': 1,
        'currency': company.default_currency,
        'conversion_rate': 1,
        'rm_cost_as_per': 'Valuation Rate',
        'is_active': 1,
        'is_default': 0,
        'with_operations': 0,
        'items': [{
            'item_code': RAW_MATERIAL,
            'qty': REQUIRED_RAW_QTY,
            'uom': stock_uom,
            'rate': VALUATION_RATE,
        }],
    }).insert(set_name=bom_conflict)
    require_runtime_error(
        lambda: ensure_item(
            FINISHED_GOOD, 'DSHERP 制造测试合成成品', finished_group, stock_uom,
            is_stock_item=1, is_sub_contracted_item=1,
        ),
        'Synthetic finished-good Item',
    )
    require_runtime_error(
        lambda: ensure_item(
            SERVICE_ITEM, 'DSHERP 制造测试合成委外加工服务', service_group, stock_uom,
            is_stock_item=0,
        ),
        'Synthetic subcontracting-service Item',
    )
    require_runtime_error(
        lambda: ensure_supplier(supplier_group, company.country), 'Synthetic Supplier name'
    )
    require_runtime_error(
        lambda: ensure_bom(company.name, company.default_currency, stock_uom),
        'Synthetic finished good BOM',
    )
    frappe.db.rollback()
    require(
        fixture_state(company.abbr, raw_warehouse) == baseline,
        'Conflict verification rollback changed fixture state',
    )
    return {
        'site': SITE,
        'mode': 'conflict_rollback',
        'rejected': {
            'finished_good': FINISHED_GOOD,
            'service_item': SERVICE_ITEM,
            'supplier': [SUPPLIER, supplier_conflict],
            'bom': [BOM, bom_conflict],
        },
    }


os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE)
frappe.connect()
try:
    require(frappe.local.site == SITE, f'Unexpected Site {frappe.local.site!r}')
    require(erpnext.__version__ == ERP_VERSION, f'ERPNext version is {erpnext.__version__!r}')
    require(frappe.__version__ == FRAPPE_VERSION, f'Frappe version is {frappe.__version__!r}')
    frappe.set_user('Administrator')

    require_field('Warehouse', 'parent_warehouse', 'Link', 'Warehouse')
    require_field('BOM', 'items', 'Table', 'BOM Item')
    require_field('BOM', 'is_active', 'Check')
    require_field('BOM', 'is_default', 'Check')
    require_field('Stock Reconciliation', 'items', 'Table', 'Stock Reconciliation Item')
    purpose = frappe.get_meta('Stock Reconciliation').get_field('purpose')
    require('Opening Stock' in (purpose.options or '').splitlines(), 'Opening Stock purpose is unavailable')
    require(frappe.get_meta('BOM').is_submittable, 'BOM must be submittable')
    require(frappe.get_meta('Stock Reconciliation').is_submittable, 'Stock Reconciliation must be submittable')

    company = exactly_one(
        frappe.get_all('Company', fields=['name', 'abbr', 'default_currency', 'country', 'cost_center']),
        'alpha Company',
    )
    require(company.abbr, 'Alpha Company abbreviation is missing')
    require(company.default_currency, 'Alpha Company currency is missing')
    require(company.country and frappe.db.exists('Country', company.country), 'Alpha Company country is invalid')
    require(company.cost_center and frappe.db.exists('Cost Center', company.cost_center), 'Alpha Company cost center is invalid')

    stock_uom = frappe.db.get_single_value('Stock Settings', 'stock_uom')
    require(stock_uom and frappe.db.get_value('UOM', stock_uom, 'enabled'), 'Stock Settings UOM is missing or disabled')
    finished_group = exactly_one(
        frappe.get_all('Item Group', filters={'name': '产品展示', 'is_group': 0}, pluck='name'),
        'finished-good Item Group',
    )
    raw_group = exactly_one(
        frappe.get_all('Item Group', filters={'name': '原材料', 'is_group': 0}, pluck='name'),
        'raw-material Item Group',
    )
    service_group = exactly_one(
        frappe.get_all('Item Group', filters={'name': '服务', 'is_group': 0}, pluck='name'),
        'service Item Group',
    )
    supplier_group = exactly_one(
        frappe.get_all('Supplier Group', filters={'name': '原材料', 'is_group': 0}, pluck='name'),
        'raw-material Supplier Group',
    )
    require(
        frappe.db.get_single_value('Buying Settings', 'supp_master_name') == 'Supplier Name',
        'Supplier naming must use Supplier Name on alpha',
    )
    root_warehouse = exactly_one(
        frappe.get_all(
            'Warehouse',
            filters={'company': company.name, 'is_group': 1, 'parent_warehouse': ['is', 'not set']},
            pluck='name',
        ),
        'Company root Warehouse',
    )
    opening_account = exactly_one(
        frappe.get_all(
            'Account',
            filters={
                'company': company.name, 'account_type': 'Temporary', 'report_type': 'Balance Sheet',
                'is_group': 0, 'disabled': 0,
            },
            pluck='name',
        ),
        'temporary opening Account',
    )

    if RUN_MODE == 'verify-conflicts':
        result = verify_conflicts(
            company, supplier_group, finished_group, service_group, stock_uom
        )
    else:
        empty_counts = {
            'warehouses': 0, 'suppliers': 0, 'items': 0, 'boms': 0,
            'reconciliations': 0, 'stock_ledger_entries': 0,
        }
        if RUN_MODE == 'verify-fresh':
            require(fixture_counts(company.abbr) == empty_counts, 'Transient fixture baseline is not empty')
        first_result = provision_fixture(
            company, root_warehouse, supplier_group, finished_group, raw_group,
            service_group, stock_uom, opening_account,
        )
        if RUN_MODE == 'verify-fresh':
            first_state = fixture_state(company.abbr, first_result['warehouses']['raw'])
            require(first_state['counts'] == {
                'warehouses': 5, 'suppliers': 1, 'items': 3, 'boms': 1,
                'reconciliations': 1, 'stock_ledger_entries': 1,
            }, f"Transient fixture creation counts differ: {first_state['counts']}")
            second_result = provision_fixture(
                company, root_warehouse, supplier_group, finished_group, raw_group,
                service_group, stock_uom, opening_account,
            )
            second_state = fixture_state(company.abbr, second_result['warehouses']['raw'])
            require(second_result == first_result, 'Second transient provision returned another fixture')
            require(second_state == first_state, 'Second transient provision changed owned state')
            frappe.db.rollback()
            require(fixture_counts(company.abbr) == empty_counts, 'Transient fixture rollback left records')
            result = {
                'site': SITE,
                'mode': 'fresh_rollback',
                'provision_invocations': 2,
                'first_result': first_result,
                'second_result': second_result,
                'first_state': first_state,
                'second_state': second_state,
            }
        else:
            frappe.db.commit()
            result = {
                'site': SITE,
                'company': company.name,
                **first_result,
            }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
except Exception:
    frappe.db.rollback()
    raise
finally:
    frappe.destroy()
'''


def main():
    arguments = sys.argv[1:]
    if not arguments:
        mode = "provision"
    elif arguments == ["--verify-fresh"]:
        mode = "verify-fresh"
    elif arguments == ["--verify-conflicts"]:
        mode = "verify-conflicts"
    else:
        raise SystemExit("Usage: provision_manufacturing_fixture.py [--verify-fresh|--verify-conflicts]")
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=f"RUN_MODE = {mode!r}\n" + SITE_SCRIPT,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    payload = json.loads(result.stdout)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
