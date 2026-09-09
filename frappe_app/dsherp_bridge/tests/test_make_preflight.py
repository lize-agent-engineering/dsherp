"""The manufacturing chain's order and dependencies, as code rather than as skill prose.

Read off the **source document**, never off a history of which routes have run: a person can
do the previous step by hand in Desk, and the document is the only evidence that covers both
that and the Agent.

`routes[]` on a record read is what lets the token list leave `SKILL.md`. `resolve_route`'s
refusal only echoes the name the model guessed wrong — it never lists what is available — so
before `routes[]` existed, a model that had not memorised the seven names had no way to find
them.
"""
import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge import api
from dsherp_bridge.make_adapters import (
    _ADAPTERS, _REQUIREMENTS, route_requirements, unmet_requirements,
)

ACTOR = 'native-make-preflight@example.invalid'


class TestRequirementTable(IntegrationTestCase):
    def test_every_registered_adapter_has_a_requirement_entry(self):
        """A behaviour contract, not a count: a route with an adapter and no prerequisites
        would be proposable from a draft."""
        self.assertEqual(set(_ADAPTERS), set(_REQUIREMENTS))

    def test_every_route_declares_a_progress_field(self):
        for key, requirement in _REQUIREMENTS.items():
            self.assertTrue(requirement.get('progress_field'), key)

    def test_progress_field_names_are_real_fields_of_their_doctypes(self):
        """The name is shown to the model as this record's progress; a name that is not a
        field would report `None` forever and read as "nothing has happened yet"."""
        for (source_doctype, route, _path, _target), requirement in _REQUIREMENTS.items():
            meta = frappe.get_meta(source_doctype)
            field = requirement['progress_field']
            self.assertTrue(meta.get_field(field) or field in ('status', 'docstatus'),
                            f'{source_doctype} 没有 {field} 这个字段（route {route}）')

    def test_subcontracting_route_requires_is_subcontracted(self):
        key = ('Purchase Order', 'purchase_order_to_subcontracting_order',
               'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
               'Subcontracting Order')
        self.assertIn(('is_subcontracted', 'eq', 1),
                      route_requirements(*key)['requires_source_fields'])
        receipt = ('Purchase Order', 'purchase_order_to_purchase_receipt',
                   'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
                   'Purchase Receipt')
        self.assertIn(('is_subcontracted', 'eq', 0),
                      route_requirements(*receipt)['requires_source_fields'])

    def test_manufacture_route_is_satisfied_by_transfer_or_skip_transfer(self):
        key = ('Work Order', 'work_order_manufacture',
               'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry',
               'Stock Entry')
        alternatives = route_requirements(*key)['satisfied_if']
        self.assertEqual(set(alternatives),
                         {('material_transferred_for_manufacturing', 'gt', 0),
                          ('skip_transfer', 'eq', 1)})

    def test_an_unregistered_route_has_no_requirements_and_says_so(self):
        with self.assertRaises(frappe.ValidationError):
            route_requirements('Sales Order', 'not_a_route', 'a.b', 'Delivery Note')

    # --- reasons --------------------------------------------------------------------------
    def test_a_draft_source_is_refused_and_the_reason_carries_the_actual_value(self):
        requirement = route_requirements(
            'Sales Order', 'sales_order_to_delivery_note',
            'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note', 'Delivery Note')
        draft = frappe._dict({'docstatus': 0, 'status': 'Draft', 'per_delivered': 0})
        reasons = unmet_requirements(draft, requirement)
        self.assertTrue(reasons)
        self.assertIn('docstatus=0', reasons[0])

    def test_a_submitted_source_passes(self):
        requirement = route_requirements(
            'Sales Order', 'sales_order_to_delivery_note',
            'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note', 'Delivery Note')
        self.assertEqual(unmet_requirements(
            frappe._dict({'docstatus': 1, 'status': 'To Deliver', 'per_delivered': 0}),
            requirement), [])

    def test_a_closed_source_is_blocked_even_when_submitted(self):
        requirement = route_requirements(
            'Sales Order', 'sales_order_to_delivery_note',
            'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note', 'Delivery Note')
        reasons = unmet_requirements(
            frappe._dict({'docstatus': 1, 'status': 'Closed', 'per_delivered': 0}), requirement)
        self.assertTrue(any('Closed' in reason for reason in reasons))

    def test_manufacture_before_material_transfer_names_the_predecessor(self):
        requirement = route_requirements(
            'Work Order', 'work_order_manufacture',
            'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry', 'Stock Entry')
        reasons = unmet_requirements(frappe._dict({
            'docstatus': 1, 'status': 'Not Started',
            'material_transferred_for_manufacturing': 0, 'skip_transfer': 0,
            'produced_qty': 0}), requirement)
        self.assertTrue(any('前一步' in reason for reason in reasons))
        self.assertTrue(any('material_transferred_for_manufacturing' in reason
                            for reason in reasons))

    def test_skip_transfer_makes_manufacture_available_without_a_transfer(self):
        requirement = route_requirements(
            'Work Order', 'work_order_manufacture',
            'erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry', 'Stock Entry')
        self.assertEqual(unmet_requirements(frappe._dict({
            'docstatus': 1, 'status': 'Not Started',
            'material_transferred_for_manufacturing': 0, 'skip_transfer': 1,
            'produced_qty': 0}), requirement), [])

    def test_the_unmet_message_carries_the_actual_value(self):
        requirement = route_requirements(
            'Purchase Order', 'purchase_order_to_subcontracting_order',
            'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
            'Subcontracting Order')
        reasons = unmet_requirements(
            frappe._dict({'docstatus': 1, 'status': 'To Receive', 'is_subcontracted': 0,
                          'per_received': 0}), requirement)
        self.assertTrue(any('is_subcontracted' in reason and '当前 0' in reason
                            for reason in reasons), reasons)


class TestRoutesOnARecordRead(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        roles = [role for role in ('System Manager', 'Item Manager', 'Stock User', 'Stock Manager',
                                   'Sales User', 'Sales Manager', 'Accounts User', 'Purchase User',
                                   'Purchase Master Manager', 'Manufacturing User')
                 if frappe.db.exists('Role', role)]
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Make',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': role} for role in roles]}).insert(ignore_permissions=True)
        else:
            user = frappe.get_doc('User', ACTOR)
            held = {row.role for row in user.roles}
            for role in roles:
                if role not in held:
                    user.append('roles', {'role': role})
            user.save(ignore_permissions=True)
        cls.company = frappe.db.get_value('Company', {'abbr': 'DNT'}, 'name') \
            or frappe.db.get_value('Company', {}, 'name')
        cls.warehouse = frappe.db.get_value('Warehouse', {'is_group': 0, 'company': cls.company}, 'name')
        uom = frappe.db.get_single_value('Stock Settings', 'stock_uom') or 'Nos'
        if not frappe.db.exists('Item', 'DSHERP-MAKEPF-ITEM'):
            frappe.get_doc({'doctype': 'Item', 'item_code': 'DSHERP-MAKEPF-ITEM',
                            'item_name': 'make 前置合成物料',
                            'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                            'stock_uom': uom, 'is_stock_item': 0}).insert(ignore_permissions=True)
        if not frappe.db.exists('Supplier', 'DSHERP-MAKEPF-SUPPLIER'):
            frappe.get_doc({'doctype': 'Supplier', 'supplier_name': 'DSHERP-MAKEPF-SUPPLIER',
                            'supplier_group': frappe.db.get_value('Supplier Group', {'is_group': 0}, 'name')
                            }).insert(ignore_permissions=True)
        if not frappe.db.exists('DS Doctype Policy', 'Purchase Order'):
            frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': 'Purchase Order',
                            'enabled': 1, 'allow_read': 1, 'allow_create': 0, 'allow_update': 0,
                            'allow_submit': 0, 'allow_cancel': 0, 'allow_fill': 0,
                            'change_reason': 'make 前置测试',
                            'routes': [
                                {'route_name': 'purchase_order_to_purchase_receipt',
                                 'method_path': 'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
                                 'target_doctype': 'Purchase Receipt'},
                                {'route_name': 'purchase_order_to_subcontracting_order',
                                 'method_path': 'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
                                 'target_doctype': 'Subcontracting Order'}]}).insert(ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        frappe.set_user(ACTOR)

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def _purchase_order(self, submit=False):
        order = frappe.get_doc({
            'doctype': 'Purchase Order', 'supplier': 'DSHERP-MAKEPF-SUPPLIER',
            'company': type(self).company,
            'schedule_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
            'items': [{'item_code': 'DSHERP-MAKEPF-ITEM', 'qty': 5, 'rate': 10,
                       'warehouse': type(self).warehouse,
                       'schedule_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}]
        }).insert(ignore_permissions=True)
        if submit:
            order.submit()
        return order

    def test_a_draft_order_lists_its_routes_as_not_ready_with_reasons(self):
        order = self._purchase_order()
        routes = api.read_record('Purchase Order', order.name)['routes']
        self.assertTrue(routes, 'the enabled routes must be listed even when none is ready')
        for route in routes:
            self.assertFalse(route['ready'], route)
            self.assertTrue(route['unmet'], route)
            self.assertTrue(any('docstatus=0' in reason for reason in route['unmet']), route)

    def test_a_submitted_order_lists_purchase_receipt_as_ready(self):
        order = self._purchase_order(submit=True)
        routes = {route['route']: route
                  for route in api.read_record('Purchase Order', order.name)['routes']}
        receipt = routes['purchase_order_to_purchase_receipt']
        self.assertTrue(receipt['ready'], receipt)
        self.assertEqual(receipt['target_doctype'], 'Purchase Receipt')
        self.assertEqual(receipt['progress_field'], 'per_received')
        self.assertEqual(receipt['progress_value'], 0)

    def test_a_plain_order_lists_the_subcontracting_route_as_not_ready_with_a_reason(self):
        order = self._purchase_order(submit=True)
        routes = {route['route']: route
                  for route in api.read_record('Purchase Order', order.name)['routes']}
        subcontract = routes['purchase_order_to_subcontracting_order']
        self.assertFalse(subcontract['ready'])
        self.assertTrue(any('is_subcontracted' in reason for reason in subcontract['unmet']))

    def test_disabling_a_route_removes_it_from_routes(self):
        order = self._purchase_order(submit=True)
        policy = frappe.get_doc('DS Doctype Policy', 'Purchase Order')
        kept = [row for row in policy.routes
                if row.route_name != 'purchase_order_to_subcontracting_order']
        policy.set('routes', [])
        for row in kept:
            policy.append('routes', {'route_name': row.route_name,
                                     'method_path': row.method_path,
                                     'target_doctype': row.target_doctype})
        policy.change_reason = 'make 前置测试：暂时移除委外路由'
        policy.save(ignore_permissions=True)
        try:
            names = {route['route']
                     for route in api.read_record('Purchase Order', order.name)['routes']}
            self.assertNotIn('purchase_order_to_subcontracting_order', names)
            self.assertIn('purchase_order_to_purchase_receipt', names)
        finally:
            frappe.db.rollback()

    def test_a_doctype_with_no_routes_carries_no_routes_key(self):
        """Absent rather than empty: an empty list would read as "there are steps, none of
        them available"."""
        if not frappe.db.exists('DS Doctype Policy', 'Item'):
            self.skipTest('no Item policy on this Site')
        self.assertNotIn('routes', api.read_record('Item', 'DSHERP-MAKEPF-ITEM'))

    def test_routes_agree_with_what_propose_make_would_do(self):
        """A route reported ready that the proposal then refuses would be worse than no
        report at all."""
        order = self._purchase_order(submit=True)
        routes = {route['route']: route['ready']
                  for route in api.read_record('Purchase Order', order.name)['routes']}
        from dsherp_bridge.doctype_policy import resolve_route
        for name, ready in routes.items():
            resolved = resolve_route('Purchase Order', name)
            reasons = unmet_requirements(
                frappe.get_doc('Purchase Order', order.name),
                route_requirements('Purchase Order', resolved['route_name'],
                                   resolved['method_path'], resolved['target_doctype']))
            self.assertEqual(ready, not reasons, name)
