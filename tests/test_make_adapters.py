

def test_every_route_declares_the_progress_field_the_skill_used_to_name():
    """These four field names lived in `business-skills/erp-operation/SKILL.md` until slice 5.

    A field name in prose can silently disagree with the document it claims to describe, and
    nothing goes red when it does. Here it is a code fact, and `erp_read_record` reports each
    record's actual value alongside it — so the model reads the progress instead of recalling
    which field to look at.
    """
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'frappe_app/dsherp_bridge/make_adapters.py').read_text(encoding='utf-8'))
    table = next(node for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(getattr(target, 'id', None) == '_REQUIREMENTS' for target in node.targets))
    progress = {}
    for key, value in zip(table.value.keys, table.value.values):
        route = key.elts[1].value
        progress[route] = next(item.value for entry, item in zip(value.keys, value.values)
                               if entry.value == 'progress_field')
    assert progress == {
        'sales_order_to_delivery_note': 'per_delivered',
        'work_order_material_transfer': 'material_transferred_for_manufacturing',
        'work_order_manufacture': 'produced_qty',
        'purchase_order_to_purchase_receipt': 'per_received',
        'purchase_order_to_subcontracting_order': 'per_received',
        'subcontracting_order_to_supply_stock_entry': 'status',
        'subcontracting_order_to_subcontracting_receipt': 'per_received',
    }


def test_the_adapter_and_requirement_tables_have_the_same_keys():
    """A route with an adapter and no prerequisites would be proposable from a draft; one with
    prerequisites and no adapter could never be resolved at all."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'frappe_app/dsherp_bridge/make_adapters.py').read_text(encoding='utf-8'))

    def keys(name):
        node = next(item for item in tree.body
                    if isinstance(item, ast.Assign)
                    and any(getattr(target, 'id', None) == name for target in item.targets))
        return {tuple(part.value for part in key.elts) for key in node.value.keys}

    assert keys('_ADAPTERS') == keys('_REQUIREMENTS')
