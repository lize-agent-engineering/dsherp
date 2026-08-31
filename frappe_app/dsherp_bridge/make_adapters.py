"""Code-owned mapped-document adapters reviewed for operation-domain HITL."""
import frappe


def _sales_order_to_delivery_note(source_name):
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    return make_delivery_note(source_name)


def _work_order_material_transfer(source_name):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    return make_stock_entry(source_name, 'Material Transfer for Manufacture')


def _work_order_manufacture(source_name):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    return make_stock_entry(source_name, 'Manufacture')


def _purchase_order_to_purchase_receipt(source_name):
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    return make_purchase_receipt(source_name)


def _purchase_order_to_subcontracting_order(source_name):
    from erpnext.buying.doctype.purchase_order.purchase_order import make_subcontracting_order
    return make_subcontracting_order(source_name)


def _subcontracting_order_to_supply_stock_entry(source_name):
    from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
    return make_rm_stock_entry(
        source_name,
        rm_items=None,
        order_doctype='Subcontracting Order',
        target_doc=None,
    )


def _subcontracting_order_to_subcontracting_receipt(source_name):
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
        make_subcontracting_receipt,
    )
    return make_subcontracting_receipt(source_name)


_ADAPTERS={
    ('Sales Order','sales_order_to_delivery_note',
     'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note','Delivery Note'):
        _sales_order_to_delivery_note,
    ('Work Order','work_order_material_transfer',
     'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry','Stock Entry'):
        _work_order_material_transfer,
    ('Work Order','work_order_manufacture',
     'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry','Stock Entry'):
        _work_order_manufacture,
    ('Purchase Order','purchase_order_to_purchase_receipt',
     'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
     'Purchase Receipt'):
        _purchase_order_to_purchase_receipt,
    ('Purchase Order','purchase_order_to_subcontracting_order',
     'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
     'Subcontracting Order'):
        _purchase_order_to_subcontracting_order,
    ('Subcontracting Order','subcontracting_order_to_supply_stock_entry',
     'erpnext.controllers.subcontracting_controller.make_rm_stock_entry','Stock Entry'):
        _subcontracting_order_to_supply_stock_entry,
    ('Subcontracting Order','subcontracting_order_to_subcontracting_receipt',
     'erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order.make_subcontracting_receipt',
     'Subcontracting Receipt'):
        _subcontracting_order_to_subcontracting_receipt,
}


def get_make_adapter(source_doctype,route_name,method_path,target_doctype):
    adapter=_ADAPTERS.get((source_doctype,route_name,method_path,target_doctype))
    if not adapter:
        frappe.throw('make 路由未注册为受信 adapter：'+source_doctype+' / '+route_name)
    return adapter
