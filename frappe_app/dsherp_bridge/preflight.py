"""Business checks run before a proposal is stored, so a bad one is never stored at all.

Until now every one of these failed at `confirm` — after the user had read a proposal and
clicked to approve it. The refusal landed in a `DS Execution Record` with status Failed, and
the person who approved it learned only then that the warehouse was a group, or the BOM was
a draft, or the stock was not there.

All five raise `frappe.throw`, which the run surface already carries all the way back to the
model as `validation` (417 → `classify_failure` → `{"error_class": "validation", ...}`), and
the three SKILL.md bodies already tell the model what to do with that: fix the arguments and
retry at most once, then ask the person. Nothing new had to be built for the return path.

They run **before** `_propose`, so a refusal produces no Pending row — the model gets the
reason, the person is never shown a proposal that cannot work.

**What these do not do**, written out here and in the evidence document because a check that
looks complete is more dangerous than no check:

- `doc.run_method('validate')` is never called. It has side effects — `set_missing_values`
  fills currencies and price lists, naming series advance — and running it before the user
  has approved anything would make the preflight itself a write.
- `update` does not check mandatory fields. A draft is allowed to be incomplete; demanding
  completeness on every edit would refuse ordinary work.
- `cancel` does not check stock. Cancelling returns stock rather than consuming it.
- With `Stock Settings.allow_negative_stock` on, the stock check is skipped entirely.
  On such a Site "not enough stock" is not an error, so raising one would be a false refusal.
- Batches and serial numbers are not consulted: availability is item + warehouse totals only.
- The mandatory check covers only child tables and mandatory Links to DocTypes this Site
  governs — see `check_mandatory` for why "every reqd=1 field" is not checkable here.

**These functions may write to the document they are handed.** `get_invalid_links` populates
`fetch_from` values as a side effect of resolving links. Nothing is saved, but the in-memory
document changes, so every caller passes a **throwaway copy** — `update_diff` promises the
caller's document is not mutated, and that promise is kept by never handing it here.

So **passing preflight is not a promise that confirm will succeed.** It is a promise that
these five specific, cheap, side-effect-free mistakes are caught before a person is asked to
approve them.
"""
import frappe

# Field types whose Link target is a business object worth checking by hand.
WAREHOUSE = 'Warehouse'
BOM = 'BOM'


def check_links(doc):
    """Every Link on the document points at something that exists and is not cancelled.

    Frappe's own `_validate_links` does exactly this — document **and every child row** — and
    knows about `ignore_link_validation`, dynamic links and set-only-once. It is a private
    method, so its absence is checked for and **fails loudly**: silently skipping would leave
    a check that reads as done and is not.
    """
    # `get_invalid_links`, not `_validate_links`: the latter throws Frappe's own message and
    # returns nothing, so there is no way to say which field and which value in Chinese.
    # `get_invalid_links` answers `([(fieldname, value, label)], [same for cancelled])`.
    #
    # **Child rows too, and that is the whole point.** `BaseDocument.get_invalid_links` walks
    # `self` only; Frappe's own `_validate_links` calls it once for the document and then again
    # for every row of `get_all_children()`. Checking the parent alone missed the most common
    # mistake there is — a model inventing an `item_code` on a line — and the person only found
    # out after approving the proposal, which is exactly the failure this module exists to
    # prevent.
    # Both native methods are checked before either is used, and their absence is the same
    # loud failure: a version that renamed one of them must not leave a check that reads as
    # done and is not.
    children = getattr(doc, 'get_all_children', None)
    if not callable(getattr(doc, 'get_invalid_links', None)) or not callable(children):
        frappe.throw('当前 Frappe 版本缺少必要的原生校验方法，无法在提案前校验引用')
    for target, prefix in [(doc, '')] + [(row, f'{row.parentfield} 第 {row.idx} 行的 ')
                                         for row in children()]:
        collect = getattr(target, 'get_invalid_links', None)
        if not callable(collect):
            frappe.throw('当前 Frappe 版本缺少必要的原生校验方法，无法在提案前校验引用')
        invalid, cancelled = collect()
        for row in invalid or []:
            fieldname, value = row[0], row[1]
            label = row[2] if len(row) > 2 else fieldname
            frappe.throw(f'{prefix}引用的 {label} 不存在：{fieldname}={value}')
        for row in cancelled or []:
            fieldname, value = row[0], row[1]
            label = row[2] if len(row) > 2 else fieldname
            frappe.throw(f'{prefix}引用的 {label} 已取消，不能使用：{fieldname}={value}')


def check_mandatory(doctype, values):
    """The mandatory fields that are provably the caller's to supply, and only when creating.

    Deliberately **not** every `reqd=1` field, and deliberately **not** Frappe's
    `_validate_mandatory`. Measured on the real Site: a Sales Order created with only
    customer, company, delivery date and one item inserts successfully, and afterwards
    `naming_series`, `currency`, `conversion_rate`, `selling_price_list` and
    `plc_conversion_rate` are all filled — every one of them `reqd=1` with no `default` and no
    `fetch_from`. So "mandatory, and not obviously defaulted" cannot tell "the caller must
    give this" from "the Site will fill it", and a check built on it reports five fields the
    user was never supposed to provide. A preflight that cries wolf is one people learn to
    work around, which is worse than not having it.

    What *is* provable: a **child table** is always the caller's to fill, and a mandatory
    **Link to a DocType this Site lets the Agent work with** (an enabled `DS Doctype Policy`
    target) is a business party or object the caller named or failed to. Currency, Price List
    and naming series are not policy targets, so they never appear here.

    Narrow on purpose. The five things this whole module does not check are listed in the
    module docstring; this is the sixth, and it is listed there too.
    """
    meta = frappe.get_meta(doctype)
    supplied = {field for field, value in (values or {}).items()
                if not (value is None or value == '' or value == [])}
    from dsherp_bridge.doctype_policy import require_policy_schema
    require_policy_schema()
    governed = set(frappe.get_all('DS Doctype Policy', filters={'enabled': 1},
                                  pluck='target_doctype'))
    missing = []
    for field in meta.fields:
        if not field.reqd or field.read_only or field.default or field.fetch_from:
            continue
        if field.fieldname in supplied:
            continue
        if field.fieldtype == 'Table' or (field.fieldtype == 'Link' and field.options in governed):
            missing.append(f'{field.label or field.fieldname}（{field.fieldname}）')
    if missing:
        frappe.throw(f'创建 {doctype} 还缺这些必填项：' + '、'.join(missing))


def _warehouse_fields(meta):
    return [field.fieldname for field in meta.fields
            if field.fieldtype == 'Link' and field.options == WAREHOUSE]


def check_warehouses(doc):
    """No group warehouses, and none belonging to another company.

    Both are refused at submit deep inside ERPNext, with a message about a document the model
    never mentioned. Checked here, the reason names the warehouse and the company.
    """
    company = doc.get('company')
    seen = []
    for fieldname in _warehouse_fields(doc.meta):
        seen.append(doc.get(fieldname))
    for table in (field.fieldname for field in doc.meta.fields if field.fieldtype == 'Table'):
        for row in doc.get(table) or []:
            child = frappe.get_meta(row.doctype)
            for fieldname in _warehouse_fields(child):
                seen.append(row.get(fieldname))
    for name in {value for value in seen if value}:
        row = frappe.db.get_value(WAREHOUSE, name, ['is_group', 'company'], as_dict=True)
        if row is None:
            frappe.throw(f'仓库 {name} 不存在')
        if row.is_group:
            frappe.throw(f'仓库 {name} 是分组仓库，不能作为业务仓库')
        if company and row.company and row.company != company:
            frappe.throw(f'仓库 {name} 属于公司 {row.company}，与单据公司 {company} 不一致')


def check_bom(doc):
    """A BOM referenced by this document is active, submitted, and for the right item."""
    for field in doc.meta.fields:
        if field.fieldtype != 'Link' or field.options != BOM:
            continue
        name = doc.get(field.fieldname)
        if not name:
            continue
        row = frappe.db.get_value(BOM, name, ['is_active', 'docstatus', 'item'], as_dict=True)
        if row is None:
            frappe.throw(f'BOM {name} 不存在')
        item = doc.get('production_item') or doc.get('item_code') or doc.get('item')
        if not row.is_active or row.docstatus != 1 or (item and row.item != item):
            frappe.throw(f'BOM {name} 未启用或未提交，或与物料 {item} 不匹配')


def check_stock_available(doc, impact):
    """Enough stock for what this submit would take out, by item and warehouse.

    Reuses the signed entries `stock_impact.action_impact` has already frozen for the
    proposal, so the quantities checked are exactly the ones the confirmation will move, in
    the same `stock_uom`. A snapshot of `Bin.actual_qty`: nothing is locked or reserved, and
    the real check still happens at submit.

    Skipped entirely when the Site allows negative stock — there, "not enough" is not an
    error, and raising one would refuse work the Site permits.
    """
    if frappe.db.get_single_value('Stock Settings', 'allow_negative_stock'):
        return
    # stock_impact's frozen entry shape: {'item_code','quantity','uom','warehouse'},
    # already normalised to the item's stock UOM and signed (negative leaves the warehouse).
    needed = {}
    for entry in (impact or {}).get('entries') or []:
        quantity = entry.get('quantity') or 0
        if quantity >= 0:
            continue
        key = (entry.get('item_code'), entry.get('warehouse'), entry.get('uom') or '')
        if not key[0] or not key[1]:
            continue
        needed[key] = needed.get(key, 0) - quantity
    for (item, warehouse, uom), quantity in sorted(needed.items()):
        actual = frappe.db.get_value('Bin', {'item_code': item, 'warehouse': warehouse},
                                     'actual_qty') or 0
        if actual < quantity:
            frappe.throw(f'{warehouse} 的 {item} 可用 {actual} {uom}，本次出库 {quantity}，'
                         f'不足（未按批次/序列号核对）')


def before_create(doctype, doc, values):
    """Everything checkable before a creation proposal is stored."""
    check_mandatory(doctype, values)
    check_links(doc)
    check_warehouses(doc)
    check_bom(doc)


def before_update(doc):
    """Everything checkable before an update or fill proposal is stored.

    No mandatory check: a draft is allowed to be incomplete, and demanding completeness on
    every edit would refuse ordinary work.
    """
    check_links(doc)
    check_warehouses(doc)
    check_bom(doc)


def before_submit(doc, impact):
    check_warehouses(doc)
    check_stock_available(doc, impact)
