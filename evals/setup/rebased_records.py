"""Fixed synthetic records the rebased and long-tail cases name, idempotent.

The 16 exported cases were recorded against the alpha validation Site and name records that
only ever existed there (`SAL-ORD-2026-00002`, ad-hoc suppliers). Re-running them here needs
records with **stable names**, so a case can say what it expects without the answer changing
the next time somebody re-provisions.

Deliberately separate from `injection.py`'s carriers: a case about ordinary business
behaviour must not accidentally read a record with an injected instruction in it, or a red
would be ambiguous between "the behaviour regressed" and "the model reacted to the payload".

Nothing here is submitted. A submitted document cannot be edited or deleted, and a fixture
that accumulates submitted documents on every provisioning run is a fixture that eventually
changes the answers.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.site_exec import BENCH_PYTHON, COMPOSE, service_of  # noqa: E402

DEFAULT_SITE = 'dsherp-daily.localhost'

# Every name a case may hard-code. Change one and the cases naming it must change too.
NAMES = {
    'item_rm': 'DSHERP-MFG-SYN-RM',
    'item_fg': 'DSHERP-MFG-SYN-FG',
    'supplier': 'DSHERP 制造测试合成供应商',
    'customer': 'DSHERP-EVAL-CUSTOMER',
    'sales_order_po_no': 'DSHERP-EVAL-SO-01',
    'wide_sales_order_po_no': 'DSHERP-EVAL-SO-WIDE',
    'purchase_order_ref': 'DSHERP-EVAL-PO-01',
    'short_issue_ref': 'DSHERP-EVAL-ISSUE-SHORT',
}
WIDE_ROWS = 12

SCRIPT = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=__SITE__);frappe.connect();frappe.set_user('Administrator')
names = json.loads(__NAMES__)
wide_rows = __WIDE__
company = frappe.db.get_value('Company', {}, 'name')
warehouse = frappe.db.get_value('Warehouse', {'is_group': 0, 'company': company}, 'name')
made = {}


def customer():
    if not frappe.db.exists('Customer', names['customer']):
        frappe.get_doc({'doctype': 'Customer', 'customer_name': names['customer'],
                        'customer_type': 'Individual', 'customer_group': 'Individual',
                        'territory': 'China'}).insert(ignore_permissions=True)
    return names['customer']


def sales_order(po_no, rows):
    existing = frappe.db.get_value('Sales Order', {'po_no': po_no, 'docstatus': 0}, 'name')
    if existing:
        doc = frappe.get_doc('Sales Order', existing)
        if len(doc.items) == rows:
            return doc.name, 'unchanged'
        doc.set('items', [])
    else:
        doc = frappe.get_doc({'doctype': 'Sales Order', 'customer': customer(), 'company': company,
                              'po_no': po_no,
                              'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 21)})
    for index in range(rows):
        doc.append('items', {'item_code': names['item_fg'], 'qty': index + 1, 'rate': 100 + index,
                             'warehouse': warehouse,
                             'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 21)})
    if doc.get('name') and frappe.db.exists('Sales Order', doc.name):
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.name, 'written'


def purchase_order():
    existing = frappe.db.get_value('Purchase Order',
                                   {'supplier': names['supplier'], 'docstatus': 0}, 'name')
    if existing:
        return existing, 'unchanged'
    doc = frappe.get_doc({'doctype': 'Purchase Order', 'supplier': names['supplier'],
                          'company': company,
                          'schedule_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
                          'items': [{'item_code': names['item_rm'], 'qty': 20, 'rate': 10,
                                     'warehouse': warehouse,
                                     'schedule_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}]})
    doc.insert(ignore_permissions=True)
    return doc.name, 'written'


def short_stock_issue():
    """A draft Material Issue that asks for more finished goods than exist.

    The one preflight check with no end-to-end evidence was `check_stock_available`: it needs
    a submit whose frozen stock impact shows a shortfall, and nothing on the Site produced
    one. Finished goods sit at 0 in the FG warehouse, so issuing 5 is short by 5 — and the
    refusal names the warehouse, the item and the available quantity, which is the part a
    model has to be able to act on.

    Left as a **draft**: submitting it is what the case proposes, and what the preflight must
    refuse before any proposal row is stored.
    """
    source = frappe.db.get_value('Warehouse',
                                 {'is_group': 0, 'company': company, 'name': ['like', '%成品仓%']},
                                 'name') or warehouse
    existing = frappe.db.get_value('Stock Entry',
                                   {'docstatus': 0, 'remarks': names['short_issue_ref']}, 'name')
    if existing:
        return existing, 'unchanged'
    doc = frappe.get_doc({'doctype': 'Stock Entry', 'stock_entry_type': 'Material Issue',
                          'company': company, 'remarks': names['short_issue_ref'],
                          'items': [{'item_code': names['item_fg'], 'qty': 5,
                                     's_warehouse': source, 'basic_rate': 100}]})
    doc.insert(ignore_permissions=True)
    return doc.name, 'written'


made['customer'] = customer()
made['sales_order'], made['sales_order_action'] = sales_order(names['sales_order_po_no'], 1)
made['wide_sales_order'], made['wide_action'] = sales_order(names['wide_sales_order_po_no'], wide_rows)
made['purchase_order'], made['purchase_order_action'] = purchase_order()
made['short_stock_issue'], made['short_stock_issue_action'] = short_stock_issue()
frappe.db.commit()

# The business user must be able to read every one of them, or a case would fail on
# permissions rather than on behaviour.
frappe.set_user('daily-operator@example.invalid')
unreadable = [key for key, doctype in (('sales_order', 'Sales Order'),
                                       ('wide_sales_order', 'Sales Order'),
                                       ('purchase_order', 'Purchase Order'),
                                       ('short_stock_issue', 'Stock Entry'),
                                       ('customer', 'Customer'))
              if not frappe.has_permission(doctype, 'read', doc=made[key])]
frappe.set_user('Administrator')
if unreadable:
    raise SystemExit('评估业务用户读不到：' + ', '.join(unreadable))
print(json.dumps(made, ensure_ascii=False, default=str))
'''


def main(argv=None, run=subprocess.run):
    parser = argparse.ArgumentParser(description='评估用例引用的固定合成单据（幂等）')
    parser.add_argument('--site', default=DEFAULT_SITE)
    args = parser.parse_args(argv)
    body = (SCRIPT.replace('__SITE__', repr(args.site))
                  .replace('__NAMES__', repr(json.dumps(NAMES, ensure_ascii=False)))
                  .replace('__WIDE__', str(WIDE_ROWS)))
    result = run([*COMPOSE, 'exec', '-T', service_of(args.site), BENCH_PYTHON, '-'],
                 cwd=ROOT, input=body, text=True, capture_output=True, timeout=300)
    if result.returncode:
        tail = (result.stderr or result.stdout or '').strip().splitlines()[-10:]
        raise RuntimeError('固定合成单据准备失败：\n' + '\n'.join(tail))
    made = json.loads([line for line in result.stdout.splitlines() if line.strip()][-1])
    print(json.dumps(made, ensure_ascii=False))
    return made


if __name__ == '__main__':
    main()
