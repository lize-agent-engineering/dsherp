"""Code-owned mapped-document adapters reviewed for operation-domain HITL."""
import frappe


def _sales_order_to_delivery_note(source_name):
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    return make_delivery_note(source_name)


_ADAPTERS={
    ('Sales Order','sales_order_to_delivery_note',
     'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note','Delivery Note'):
        _sales_order_to_delivery_note,
}


def get_make_adapter(source_doctype,route_name,method_path,target_doctype):
    adapter=_ADAPTERS.get((source_doctype,route_name,method_path,target_doctype))
    if not adapter:
        frappe.throw('make 路由未注册为受信 adapter：'+source_doctype+' / '+route_name)
    return adapter
