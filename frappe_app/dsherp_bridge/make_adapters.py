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
}


def get_make_adapter(source_doctype,route_name,method_path,target_doctype):
    adapter=_ADAPTERS.get((source_doctype,route_name,method_path,target_doctype))
    if not adapter:
        frappe.throw('make 路由未注册为受信 adapter：'+source_doctype+' / '+route_name)
    return adapter
