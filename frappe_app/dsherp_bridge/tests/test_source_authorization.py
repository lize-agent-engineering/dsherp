"""Replaying a run's own reads, and grounding a proposal on one.

Both of these read what a previous tool call recorded and ask "is that still allowed / is
that the target you claim to have read". Slice 4 changed what a read returns, and both broke
in ways no test saw: `read_tools.py` calls `api` directly and never goes through `run_tool`,
so 50 green native tests said nothing about either.

The two failures were quiet in the worst way — a `PermissionError` about *historical* field
permissions on a record the user had just read, and a "read the target first" refusal
immediately after reading the target.
"""
import json

import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge import api
from dsherp_bridge.context_execution import TOOL_DEFAULTS, authorize_sources, grounds

ACTOR = 'native-source-auth@example.invalid'
ITEM = 'DSHERP-SRCAUTH-ITEM'
CUSTOMER = 'DSHERP-SRCAUTH-CUSTOMER'


class TestSourceAuthorization(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        roles = [role for role in ('System Manager', 'Item Manager', 'Stock User', 'Stock Manager',
                                   'Sales User', 'Sales Manager', 'Accounts User', 'Purchase User')
                 if frappe.db.exists('Role', role)]
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Source',
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
        if not frappe.db.exists('Item', ITEM):
            frappe.get_doc({'doctype': 'Item', 'item_code': ITEM, 'item_name': '来源授权合成物料',
                            'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                            'stock_uom': frappe.db.get_single_value('Stock Settings', 'stock_uom') or 'Nos',
                            'is_stock_item': 0}).insert(ignore_permissions=True)
        if not frappe.db.exists('Customer', CUSTOMER):
            frappe.get_doc({'doctype': 'Customer', 'customer_name': CUSTOMER,
                            'customer_type': 'Individual',
                            'customer_group': frappe.db.get_value('Customer Group', {'is_group': 0}, 'name'),
                            'territory': frappe.db.get_value('Territory', {'is_group': 0}, 'name')
                            }).insert(ignore_permissions=True)
        for target in ('Sales Order', 'Item'):
            if not frappe.db.exists('DS Doctype Policy', target):
                frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': target,
                                'enabled': 1, 'allow_read': 1, 'allow_create': 0, 'allow_update': 0,
                                'allow_submit': 0, 'allow_cancel': 0, 'allow_fill': 0,
                                'change_reason': '来源授权测试'}).insert(ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        frappe.set_user(ACTOR)

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def _order(self, rows=1):
        return frappe.get_doc({
            'doctype': 'Sales Order', 'customer': CUSTOMER, 'company': type(self).company,
            'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
            'items': [{'item_code': ITEM, 'qty': index + 1, 'rate': 100,
                       'warehouse': type(self).warehouse,
                       'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}
                      for index in range(rows)]}).insert(ignore_permissions=True)

    def _source(self, doctype, name, **arguments):
        """The source `_run_tool` would record for this read, built the same way."""
        arguments = {**TOOL_DEFAULTS['erp_read_record'], 'doctype': doctype, 'name': name, **arguments}
        result = api.read_record(**arguments)
        seen = set(result['fields']) | set(result.get('child_tables') or {})
        source = {'tool': 'erp_read_record', 'arguments': arguments,
                  'fields': [key for key in seen if frappe.get_meta(doctype).get_field(key)],
                  'records': [result['name']],
                  'record_versions': {result['name']: str(result['modified'])}}
        source['child_fields'] = {field: sorted({column for row in rows for column in row})
                                  for field, rows in result['fields'].items() if isinstance(rows, list)}
        return source, result

    # --- replay ---------------------------------------------------------------------------
    def test_a_read_the_user_just_made_is_still_authorized(self):
        """A wide DocType's fields no longer fit in one page of `read_schema`. Rebuilding the
        visible set from that page would refuse the very read that produced the source — a
        permission error about history, on something that just happened."""
        order = self._order()
        source, _ = self._source('Sales Order', order.name)
        authorize_sources([source])

    def test_an_expanded_child_table_is_still_authorized(self):
        """Child tables are no longer inlined in a schema read, so a column whitelist built
        from one would be empty and every expanded read would be refused."""
        order = self._order(rows=2)
        source, result = self._source('Sales Order', order.name, children=['items'])
        self.assertTrue(source['child_fields']['items'], 'the read really did expose columns')
        authorize_sources([source])

    def test_a_field_the_user_cannot_read_is_still_refused(self):
        """The check must still bite: this is the whole point of replaying it."""
        order = self._order()
        source, _ = self._source('Sales Order', order.name)
        source['fields'] = source['fields'] + ['not_a_readable_field']
        with self.assertRaises(frappe.PermissionError):
            authorize_sources([source])

    def test_a_child_column_the_user_cannot_read_is_still_refused(self):
        order = self._order(rows=1)
        source, _ = self._source('Sales Order', order.name, children=['items'])
        source['child_fields']['items'] = source['child_fields']['items'] + ['not_a_column']
        with self.assertRaises(frappe.PermissionError):
            authorize_sources([source])

    def test_a_child_table_that_is_not_a_table_of_this_doctype_is_refused(self):
        order = self._order()
        source, _ = self._source('Sales Order', order.name)
        source['child_fields'] = {'not_a_table': ['name']}
        with self.assertRaises(frappe.PermissionError):
            authorize_sources([source])

    # --- grounding ------------------------------------------------------------------------
    def test_a_proposal_is_grounded_on_the_read_that_preceded_it(self):
        """`_run_tool` records arguments with the defaults filled in, so comparing the whole
        dict against a two-key literal can never match: every proposal would be refused
        immediately after reading its own target."""
        order = self._order()
        source, result = self._source('Sales Order', order.name)
        self.assertTrue(grounds([source], 'erp_read_record',
                                {'doctype': 'Sales Order', 'name': order.name},
                                version=str(result['modified']), key='record_versions'))

    def test_a_proposal_on_a_different_record_is_not_grounded(self):
        first, first_result = self._source('Sales Order', self._order().name)
        other = self._order()
        self.assertFalse(grounds([first], 'erp_read_record',
                                 {'doctype': 'Sales Order', 'name': other.name},
                                 version=str(first_result['modified']), key='record_versions'))

    def test_a_stale_version_is_not_grounded(self):
        order = self._order()
        source, _ = self._source('Sales Order', order.name)
        self.assertFalse(grounds([source], 'erp_read_record',
                                 {'doctype': 'Sales Order', 'name': order.name},
                                 version='2020-01-01 00:00:00', key='record_versions'))

    def test_a_partial_read_still_grounds_the_target_it_named(self):
        """Reading fewer columns is still reading that record at that version; the freshness
        guarantee is `record_versions`, not the shape of the read."""
        order = self._order()
        source, result = self._source('Sales Order', order.name, fields=['customer'])
        self.assertTrue(grounds([source], 'erp_read_record',
                                {'doctype': 'Sales Order', 'name': order.name},
                                version=str(result['modified']), key='record_versions'))

    def test_a_schema_read_grounds_a_creation(self):
        arguments = {**TOOL_DEFAULTS['erp_read_schema'], 'doctype': 'Sales Order'}
        result = api.read_schema(**arguments)
        source = {'tool': 'erp_read_schema', 'arguments': arguments,
                  'fields': [field['fieldname'] for field in result['fields']], 'records': [],
                  'schema_version': str(result['modified'])}
        self.assertTrue(grounds([source], 'erp_read_schema', {'doctype': 'Sales Order'},
                                version=str(result['modified']), key='schema_version'))
        self.assertFalse(grounds([source], 'erp_read_schema', {'doctype': 'Item'},
                                 version=str(result['modified']), key='schema_version'))

    def test_a_later_page_of_the_same_schema_still_grounds_it(self):
        """Paging is not a different read: the version it pins is the same one."""
        first = api.read_schema('Sales Order')
        cursor = first['next_after_fieldname']
        arguments = {**TOOL_DEFAULTS['erp_read_schema'], 'doctype': 'Sales Order',
                     'after_fieldname': cursor}
        page = api.read_schema(**arguments)
        source = {'tool': 'erp_read_schema', 'arguments': arguments,
                  'fields': [field['fieldname'] for field in page['fields']], 'records': [],
                  'schema_version': str(page['modified'])}
        self.assertTrue(grounds([source], 'erp_read_schema', {'doctype': 'Sales Order'},
                                version=str(page['modified']), key='schema_version'))

    def test_a_source_recorded_before_the_defaults_existed_still_grounds(self):
        """Runs written before slice 4 carry two-key arguments; they are audit facts and
        cannot be rewritten."""
        order = self._order()
        result = api.read_record('Sales Order', order.name)
        legacy = {'tool': 'erp_read_record',
                  'arguments': {'doctype': 'Sales Order', 'name': order.name},
                  'fields': list(result['fields']), 'records': [order.name],
                  'record_versions': {order.name: str(result['modified'])}}
        self.assertTrue(grounds([legacy], 'erp_read_record',
                                {'doctype': 'Sales Order', 'name': order.name},
                                version=str(result['modified']), key='record_versions'))
        self.assertEqual(json.loads(json.dumps(legacy))['arguments'],
                         {'doctype': 'Sales Order', 'name': order.name})
