"""Frozen, user-visible stock movements for native submit/cancel actions."""
from collections import defaultdict
from decimal import Decimal

import frappe
from frappe.utils import flt


_ENTRY_KEYS = {'item_code', 'quantity', 'uom', 'warehouse'}


def _fail(message):
    frappe.throw('库存影响无法确定：' + message)


def _table(doc, fieldname, child_doctype):
    definition = doc.meta.get_field(fieldname)
    if not definition or definition.fieldtype != 'Table' or definition.options != child_doctype:
        _fail(f'{doc.doctype}.{fieldname} 不符合固定业务结构')
    return doc.get(fieldname) or []


def _require_child_fields(child_doctype, fieldnames):
    meta = frappe.get_meta(child_doctype)
    missing = sorted(fieldname for fieldname in fieldnames if not meta.get_field(fieldname))
    if missing:
        _fail(f'{child_doctype} 缺少字段：{", ".join(missing)}')


def _require_parent_fields(doc, fieldnames):
    missing = sorted(fieldname for fieldname in fieldnames if not doc.meta.get_field(fieldname))
    if missing:
        _fail(f'{doc.doctype} 缺少字段：{", ".join(missing)}')


def _stock_item(item_code):
    if not isinstance(item_code, str) or not item_code:
        _fail('明细缺少物料')
    value = frappe.get_cached_value('Item', item_code, 'is_stock_item')
    if value is None:
        _fail('物料不存在：' + item_code)
    return bool(value)


def _row_number(row, fieldname, label, required=False):
    precision = row.precision(fieldname)
    if not isinstance(precision, int) or precision < 0:
        _fail(label + ' 精度无效')
    result = flt(row.get(fieldname), precision)
    if required and not result:
        _fail(label + ' 必须为非零数量')
    return result, precision


def _text(value, label):
    if not isinstance(value, str) or not value:
        _fail(label + ' 缺失')
    return value


def _add(movements, item_code, quantity, precision, uom, warehouse):
    item_code = _text(item_code, '物料')
    uom = _text(uom, '库存单位')
    warehouse = _text(warehouse, '仓库')
    normalized = flt(quantity, precision)
    movements[(item_code, uom, warehouse)] += Decimal(str(normalized))


def _stock_entry(doc):
    child = 'Stock Entry Detail'
    fields = {'item_code', 'transfer_qty', 'stock_uom', 's_warehouse', 't_warehouse'}
    _require_child_fields(child, fields)
    rows = _table(doc, 'items', child)
    if not rows:
        _fail('Stock Entry.items 为空')
    movements = defaultdict(Decimal)
    for row in rows:
        quantity, precision = _row_number(
            row, 'transfer_qty', 'Stock Entry.items.transfer_qty', required=True
        )
        if not row.s_warehouse and not row.t_warehouse:
            _fail('Stock Entry 明细缺少来源仓和目标仓')
        if row.s_warehouse:
            _add(movements, row.item_code, -quantity, precision, row.stock_uom, row.s_warehouse)
        if row.t_warehouse:
            _add(movements, row.item_code, quantity, precision, row.stock_uom, row.t_warehouse)
    return movements


def _purchase_receipt(doc):
    child = 'Purchase Receipt Item'
    fields = {
        'item_code', 'stock_qty', 'stock_uom', 'warehouse', 'rejected_qty',
        'rejected_warehouse', 'conversion_factor', 'from_warehouse',
    }
    _require_child_fields(child, fields)
    _require_parent_fields(doc, {'is_return'})
    rows = _table(doc, 'items', child)
    if not rows:
        _fail('Purchase Receipt.items 为空')
    if doc.get('is_return'):
        _fail('暂不支持 Purchase Receipt 退货库存影响')
    if any(row.from_warehouse for row in rows):
        _fail('暂不支持 Purchase Receipt 内部调拨库存影响')
    movements = defaultdict(Decimal)
    for row in rows:
        if not _stock_item(row.item_code):
            continue
        accepted, stock_precision = _row_number(
            row, 'stock_qty', 'Purchase Receipt.items.stock_qty'
        )
        rejected_qty, _ = _row_number(
            row, 'rejected_qty', 'Purchase Receipt.items.rejected_qty'
        )
        if accepted:
            _add(movements, row.item_code, accepted, stock_precision, row.stock_uom, row.warehouse)
        if rejected_qty:
            conversion, _ = _row_number(
                row, 'conversion_factor', 'Purchase Receipt.items.conversion_factor', required=True
            )
            rejected = flt(rejected_qty * conversion, stock_precision)
            _add(movements, row.item_code, rejected, stock_precision, row.stock_uom, row.rejected_warehouse)
        if not accepted and not rejected_qty:
            _fail('Purchase Receipt 库存明细数量为零')
    return movements


def _delivery_note(doc):
    child = 'Delivery Note Item'
    fields = {'item_code', 'stock_qty', 'stock_uom', 'warehouse', 'target_warehouse'}
    _require_child_fields(child, fields)
    _require_parent_fields(doc, {'is_return'})
    rows = _table(doc, 'items', child)
    packed_items = _table(doc, 'packed_items', 'Packed Item')
    if not rows:
        _fail('Delivery Note.items 为空')
    if doc.is_return:
        _fail('暂不支持 Delivery Note 退货库存影响')
    if packed_items:
        _fail('暂不支持 Delivery Note Product Bundle 库存影响')
    if any(row.target_warehouse for row in rows):
        _fail('暂不支持 Delivery Note 内部客户调拨库存影响')
    movements = defaultdict(Decimal)
    for row in rows:
        if not _stock_item(row.item_code):
            continue
        quantity, precision = _row_number(
            row, 'stock_qty', 'Delivery Note.items.stock_qty', required=True
        )
        _add(movements, row.item_code, -quantity, precision, row.stock_uom, row.warehouse)
    return movements


def _subcontracting_receipt(doc):
    item_child = 'Subcontracting Receipt Item'
    supplied_child = 'Subcontracting Receipt Supplied Item'
    _require_child_fields(item_child, {
        'item_code', 'qty', 'conversion_factor', 'stock_uom', 'warehouse',
        'rejected_qty', 'rejected_warehouse',
    })
    _require_child_fields(supplied_child, {'rm_item_code', 'consumed_qty', 'stock_uom'})
    _require_parent_fields(doc, {'is_return', 'supplier_warehouse'})
    items = _table(doc, 'items', item_child)
    supplied = _table(doc, 'supplied_items', supplied_child)
    if not items:
        _fail('Subcontracting Receipt.items 为空')
    if doc.is_return:
        _fail('暂不支持 Subcontracting Receipt 退货库存影响')
    movements = defaultdict(Decimal)
    for row in items:
        if not _stock_item(row.item_code):
            continue
        conversion, _ = _row_number(
            row, 'conversion_factor', 'Subcontracting Receipt.items.conversion_factor', required=True
        )
        quantity, quantity_precision = _row_number(
            row, 'qty', 'Subcontracting Receipt.items.qty'
        )
        rejected_qty, rejected_precision = _row_number(
            row, 'rejected_qty', 'Subcontracting Receipt.items.rejected_qty'
        )
        accepted = flt(quantity * conversion, quantity_precision)
        rejected = flt(rejected_qty * conversion, rejected_precision)
        if accepted:
            _add(movements, row.item_code, accepted, quantity_precision, row.stock_uom, row.warehouse)
        if rejected:
            _add(movements, row.item_code, rejected, rejected_precision, row.stock_uom, row.rejected_warehouse)
        if not accepted and not rejected:
            _fail('Subcontracting Receipt 库存明细数量为零')
    for row in supplied:
        if not _stock_item(row.rm_item_code):
            continue
        consumed, precision = _row_number(
            row, 'consumed_qty', 'Subcontracting Receipt.supplied_items.consumed_qty',
            required=True,
        )
        _add(movements, row.rm_item_code, -consumed, precision, row.stock_uom, doc.supplier_warehouse)
    return movements


_REGISTRY = {
    'Stock Entry': (
        _stock_entry,
        {'items'},
        {'items': {'item_code', 'transfer_qty', 'stock_uom', 's_warehouse', 't_warehouse'}},
    ),
    'Purchase Receipt': (
        _purchase_receipt,
        {'items', 'is_return'},
        {'items': {
            'item_code', 'stock_qty', 'stock_uom', 'warehouse', 'rejected_qty',
            'rejected_warehouse', 'conversion_factor', 'from_warehouse',
        }},
    ),
    'Delivery Note': (
        _delivery_note,
        {'items', 'packed_items', 'is_return'},
        {'items': {'item_code', 'stock_qty', 'stock_uom', 'warehouse', 'target_warehouse'}},
    ),
    'Subcontracting Receipt': (
        _subcontracting_receipt,
        {'items', 'supplied_items', 'supplier_warehouse', 'is_return'},
        {
            'items': {
                'item_code', 'qty', 'conversion_factor', 'stock_uom', 'warehouse',
                'rejected_qty', 'rejected_warehouse',
            },
            'supplied_items': {'rm_item_code', 'consumed_qty', 'stock_uom'},
        },
    ),
}


def action_impact(doc, action):
    """Return a canonical movement block selected only by exact DocType registry."""
    if action not in ('submit', 'cancel'):
        frappe.throw('库存影响只支持提交或取消操作')
    registered = _REGISTRY.get(doc.doctype)
    if not registered:
        return {'kind': 'none', 'entries': []}
    movements = registered[0](doc)
    direction = -1 if action == 'cancel' else 1
    entries = [
        {
            'item_code': item_code,
            'quantity': float(quantity * direction),
            'uom': uom,
            'warehouse': warehouse,
        }
        for (item_code, uom, warehouse), quantity in sorted(movements.items())
        if quantity
    ]
    impact = {'kind': 'stock', 'entries': entries} if entries else {'kind': 'none', 'entries': []}
    validate_frozen_impact(impact)
    return impact


def validate_frozen_impact(impact):
    if not isinstance(impact, dict) or set(impact) != {'kind', 'entries'}:
        frappe.throw('库存影响结构无效，请重新提出操作')
    if impact['kind'] == 'none':
        if impact['entries'] != []:
            frappe.throw('库存影响结构无效，请重新提出操作')
        return
    if impact['kind'] != 'stock' or not isinstance(impact['entries'], list) or not impact['entries']:
        frappe.throw('库存影响结构无效，请重新提出操作')
    keys = []
    for entry in impact['entries']:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            frappe.throw('库存影响结构无效，请重新提出操作')
        if any(not isinstance(entry[field], str) or not entry[field]
               for field in ('item_code', 'uom', 'warehouse')):
            frappe.throw('库存影响结构无效，请重新提出操作')
        if isinstance(entry['quantity'], bool) or not isinstance(entry['quantity'], (int, float)) or not entry['quantity']:
            frappe.throw('库存影响结构无效，请重新提出操作')
        keys.append((entry['item_code'], entry['uom'], entry['warehouse']))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        frappe.throw('库存影响结构无效，请重新提出操作')


def validate_impact_read_access(doc, user):
    """Recheck every source field before exposing its frozen derived value."""
    registered = _REGISTRY.get(doc.doctype)
    if not registered:
        return
    parent_fields, child_fields = registered[1], registered[2]
    readable = set(doc.meta.get_permitted_fieldnames(user=user, permission_type='read'))
    levels = doc.get_permlevel_access('read')
    readable.update(
        field.fieldname for field in doc.meta.fields
        if field.fieldtype == 'Table' and field.permlevel in levels
    )
    if not parent_fields <= readable:
        raise frappe.PermissionError('无权读取库存影响字段')
    for table_field, required in child_fields.items():
        definition = doc.meta.get_field(table_field)
        if not definition or definition.fieldtype != 'Table':
            frappe.throw('库存影响业务结构已变化，请重新提出操作')
        child_readable = set(frappe.get_meta(definition.options).get_permitted_fieldnames(
            parenttype=doc.doctype, user=user, permission_type='read'
        ))
        if not required <= child_readable:
            raise frappe.PermissionError('无权读取库存影响明细字段')
