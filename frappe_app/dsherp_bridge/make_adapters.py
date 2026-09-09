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


# What has to be true of the **source document** before each route may be proposed.
#
# In code, not in `DS Doctype Policy Route`. The dependency table is a fact about the chain,
# and a route already has to be registered in `_ADAPTERS` to be resolvable at all
# (doctype_policy.resolve_route). Putting the prerequisites in a DocType instead would add a
# path where changing data relaxes a business precondition, and would rotate the permission
# revision every time somebody adjusted one.
#
# Read off the **source document**, never off a history of which routes have run. A person can
# perfectly well do the previous step by hand in Desk, and the document is the only evidence
# that covers both that and the Agent.
#
# Same keys as `_ADAPTERS`, so a route can never have an adapter and no requirements, or the
# reverse - a test asserts the two key sets are equal.
# `blocked_when` mirrors what ERPNext itself refuses at submit, per source DocType — measured
# against the pinned image, not assumed: `Purchase Order` and `Sales Order` both carry an
# `On Hold` status and `StockController.check_for_on_hold_or_closed_status` refuses
# ("Closed", "On Hold"); `Subcontracting Order` has no such status, and `Work Order` uses
# `Stopped` instead. A route that reports `ready` on a held order sends the person a proposal
# that can only fail at confirm.
_REQUIREMENTS={
    ('Sales Order','sales_order_to_delivery_note',
     'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note','Delivery Note'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Closed','On Hold'])],
        'progress_field':'per_delivered'},
    ('Work Order','work_order_material_transfer',
     'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry','Stock Entry'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Stopped','Closed'])],
        'progress_field':'material_transferred_for_manufacturing'},
    ('Work Order','work_order_manufacture',
     'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry','Stock Entry'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[],
        # Either material really was transferred, or this Work Order skips the transfer step.
        'satisfied_if':[('material_transferred_for_manufacturing','gt',0),
                        ('skip_transfer','eq',1)],
        'blocked_when':[('status','in',['Stopped','Closed'])],
        'progress_field':'produced_qty'},
    ('Purchase Order','purchase_order_to_purchase_receipt',
     'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
     'Purchase Receipt'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[('is_subcontracted','eq',0)],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Closed','On Hold'])],
        'progress_field':'per_received'},
    ('Purchase Order','purchase_order_to_subcontracting_order',
     'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
     'Subcontracting Order'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[('is_subcontracted','eq',1)],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Closed','On Hold'])],
        'progress_field':'per_received'},
    ('Subcontracting Order','subcontracting_order_to_supply_stock_entry',
     'erpnext.controllers.subcontracting_controller.make_rm_stock_entry','Stock Entry'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Closed'])],
        'progress_field':'status'},
    ('Subcontracting Order','subcontracting_order_to_subcontracting_receipt',
     'erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order.make_subcontracting_receipt',
     'Subcontracting Receipt'): {
        'requires_source_docstatus':1,
        'requires_source_fields':[],
        'satisfied_if':None,
        'blocked_when':[('status','in',['Closed'])],
        'progress_field':'per_received'},
}

_OPERATORS={
    'eq': lambda actual,expected: actual==expected,
    'ne': lambda actual,expected: actual!=expected,
    'gt': lambda actual,expected: (actual or 0)>expected,
    'gte': lambda actual,expected: (actual or 0)>=expected,
    'in': lambda actual,expected: actual in expected,
    'not in': lambda actual,expected: actual not in expected,
}


def get_make_adapter(source_doctype,route_name,method_path,target_doctype):
    adapter=_ADAPTERS.get((source_doctype,route_name,method_path,target_doctype))
    if not adapter:
        frappe.throw('make 路由未注册为受信 adapter：'+source_doctype+' / '+route_name)
    return adapter


def route_requirements(source_doctype,route_name,method_path,target_doctype):
    requirement=_REQUIREMENTS.get((source_doctype,route_name,method_path,target_doctype))
    if not requirement:
        frappe.throw('make 路由没有登记前置条件：'+source_doctype+' / '+route_name)
    return requirement


def _describe(field,operator,expected,actual):
    if operator=='in':
        return f'该 route 不允许 {field} 为 {actual!r}'
    if operator=='not in':
        return f'该 route 要求 {field} 不在 {expected!r} 内（当前 {actual!r}）'
    if operator in ('gt','gte'):
        return f'该 route 要求 {field} {">" if operator=="gt" else ">="} {expected}（当前 {actual!r}）'
    return f'该 route 要求 {field}{"=" if operator=="eq" else "≠"}{expected!r}（当前 {actual!r}）'


def unmet_requirements(doc,requirement):
    """Why this route cannot be proposed from this document, in Chinese, with actual values.

    A reason without the current value tells the model that something is wrong but not what
    to do next; with it, the model can either pick the step that is actually available or say
    plainly what the person has to do first.
    """
    reasons=[]
    expected_docstatus=requirement.get('requires_source_docstatus')
    if expected_docstatus is not None and int(doc.get('docstatus') or 0)!=expected_docstatus:
        state={0:'草稿',1:'已提交',2:'已取消'}.get(int(doc.get('docstatus') or 0),'未知')
        reasons.append(f'源单必须已提交（当前 docstatus={int(doc.get("docstatus") or 0)}，{state}）')
    for field,operator,expected in requirement.get('requires_source_fields') or []:
        if not _OPERATORS[operator](doc.get(field),expected):
            reasons.append(_describe(field,operator,expected,doc.get(field)))
    alternatives=requirement.get('satisfied_if')
    if alternatives and not any(_OPERATORS[operator](doc.get(field),expected)
                                for field,operator,expected in alternatives):
        reasons.append('这一步的前一步还没做：需要满足其中之一——'
                       +'；'.join(_describe(field,operator,expected,doc.get(field))
                                 for field,operator,expected in alternatives))
    for field,operator,expected in requirement.get('blocked_when') or []:
        if _OPERATORS[operator](doc.get(field),expected):
            reasons.append(f'源单当前状态不允许这一步：{field}={doc.get(field)!r}')
    return reasons
