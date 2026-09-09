"""What the read tools hand the model, and what they now hold back.

The measured reason these limits exist (slice 0): every DocType's `read_schema` was over the
16KB cap with child tables inlined — Sales Order was 62,674 bytes, 3.8× — and a record's size
is driven by child rows, not field count. So the defaults changed: schema does not inline
child tables, a record returns only fields that have a value and counts its child rows
instead of expanding them. Everything held back stays reachable by asking for it.
"""
import json

import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge import api
from dsherp_bridge.tool_limits import LIMITS

ACTOR = 'native-read-tools@example.invalid'
ITEM = 'DSHERP-READTOOLS-ITEM'
CUSTOMER = 'DSHERP-READTOOLS-CUSTOMER'


def _bytes(payload):
    """What the model receives, as FastMCP serialises it."""
    from pydantic_core import to_json
    return len(to_json(payload, indent=2))


class TestReadTools(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ERPNext gates its masters and documents on its own roles; System Manager alone
        # cannot read an Item. Assigned every time rather than only at creation, so a user
        # left over from an earlier run gets them too.
        roles = [role for role in ('System Manager', 'Item Manager', 'Stock User', 'Stock Manager',
                                   'Sales User', 'Sales Manager', 'Accounts User', 'Purchase User')
                 if frappe.db.exists('Role', role)]
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Read',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': role} for role in roles]}).insert(ignore_permissions=True)
        else:
            user = frappe.get_doc('User', ACTOR)
            held = {row.role for row in user.roles}
            for role in roles:
                if role not in held:
                    user.append('roles', {'role': role})
            user.save(ignore_permissions=True)
        # The native test Site carries ERPNext and one synthetic company, but no master data:
        # these tests are about the shape of a read, so they bring their own subject.
        cls.company = frappe.db.get_value('Company', {'abbr': 'DNT'}, 'name') \
            or frappe.db.get_value('Company', {}, 'name')
        cls.warehouse = frappe.db.get_value(
            'Warehouse', {'is_group': 0, 'company': cls.company}, 'name')
        if not frappe.db.exists('Item', ITEM):
            frappe.get_doc({'doctype': 'Item', 'item_code': ITEM, 'item_name': '读取工具合成物料',
                            'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                            'stock_uom': frappe.db.get_single_value('Stock Settings', 'stock_uom') or 'Nos',
                            'is_stock_item': 0}).insert(ignore_permissions=True)
        if not frappe.db.exists('Customer', CUSTOMER):
            frappe.get_doc({'doctype': 'Customer', 'customer_name': CUSTOMER,
                            'customer_type': 'Individual',
                            'customer_group': frappe.db.get_value('Customer Group', {'is_group': 0}, 'name'),
                            'territory': frappe.db.get_value('Territory', {'is_group': 0}, 'name')
                            }).insert(ignore_permissions=True)
        # `_authorize` consults DS Doctype Policy before any read. These tests are about the
        # shape of a read, not about policy, so the two objects they touch get a read-only row.
        for target in ('Sales Order', 'Item'):
            if not frappe.db.exists('DS Doctype Policy', target):
                frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': target,
                                'enabled': 1, 'allow_read': 1, 'allow_create': 0,
                                'allow_update': 0, 'allow_submit': 0, 'allow_cancel': 0,
                                'allow_fill': 0,
                                'change_reason': '读取工具形状测试'}).insert(ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        frappe.set_user(ACTOR)
        self.company = type(self).company
        self.warehouse = type(self).warehouse
        self.item = ITEM

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def _order(self, rows=1):
        order = frappe.get_doc({
            'doctype': 'Sales Order', 'customer': CUSTOMER, 'company': self.company,
            'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
            'items': [{'item_code': self.item, 'qty': index + 1, 'rate': 100,
                       'warehouse': self.warehouse,
                       'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}
                      for index in range(rows)]})
        return order.insert(ignore_permissions=True)

    # --- read_record ----------------------------------------------------------------------
    def test_record_omits_empty_fields_by_default(self):
        order = self._order()
        fields = api.read_record('Sales Order', order.name)['fields']
        self.assertTrue(fields, 'the record still has content')
        self.assertNotIn(None, list(fields.values()))
        self.assertNotIn('', [value for value in fields.values() if isinstance(value, str)])

    def test_zero_and_false_are_not_empty(self):
        """A quantity of 0 and a flag of False are answers, not absences. Dropping them would
        make the model believe a field it can see is unset."""
        order = self._order()
        result = api.read_record('Sales Order', order.name, include_empty=True)
        zeros = [key for key, value in result['fields'].items()
                 if value == 0 and not isinstance(value, (list, dict))]
        self.assertTrue(zeros, 'the fixture has no zero-valued field to check')
        kept = api.read_record('Sales Order', order.name)['fields']
        for key in zeros:
            self.assertIn(key, kept, f'{key} was 0 and got dropped as if it were empty')

    def test_draft_docstatus_survives_the_filter(self):
        """docstatus 0 is the single most load-bearing zero in the whole surface: about ten
        integration assertions read it to know a document is still a draft."""
        order = self._order()
        fields = api.read_record('Sales Order', order.name)['fields']
        self.assertEqual(fields['docstatus'], 0)

    def test_include_empty_returns_them(self):
        order = self._order()
        default = api.read_record('Sales Order', order.name)
        everything = api.read_record('Sales Order', order.name, include_empty=True)
        self.assertGreater(len(everything['fields']), len(default['fields']))
        self.assertEqual(default['omitted_empty'],
                         len(everything['fields']) - len(default['fields']))

    def test_child_tables_are_counted_not_expanded_by_default(self):
        order = self._order(rows=3)
        result = api.read_record('Sales Order', order.name)
        self.assertNotIn('items', result['fields'])
        self.assertEqual(result['child_tables']['items']['rows'], 3)
        self.assertEqual(result['child_tables']['items']['child_doctype'], 'Sales Order Item')

    def test_requested_table_is_expanded_with_name_and_idx(self):
        order = self._order(rows=2)
        result = api.read_record('Sales Order', order.name, children=['items'])
        rows = result['fields']['items']
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn('name', row)
            self.assertIn('idx', row)

    def test_not_expanding_a_table_is_the_biggest_saving(self):
        order = self._order(rows=6)
        lean = _bytes(api.read_record('Sales Order', order.name))
        full = _bytes(api.read_record('Sales Order', order.name, children=['items']))
        self.assertLess(lean, full)
        self.assertLess(lean, LIMITS['record_max_bytes'])

    def test_oversized_table_is_truncated_and_marked_with_a_cursor(self):
        order = self._order(rows=LIMITS['child_rows_per_page'] + 5)
        result = api.read_record('Sales Order', order.name, children=['items'])
        table = result['truncated']['tables']['items']
        self.assertEqual(table['returned'], LIMITS['child_rows_per_page'])
        self.assertEqual(table['total'], LIMITS['child_rows_per_page'] + 5)
        self.assertEqual(table['next_after_idx'], LIMITS['child_rows_per_page'])

    def test_after_idx_continues_from_the_cursor(self):
        total = LIMITS['child_rows_per_page'] + 5
        order = self._order(rows=total)
        first = api.read_record('Sales Order', order.name, children=['items'])
        cursor = first['truncated']['tables']['items']['next_after_idx']
        rest = api.read_record('Sales Order', order.name, children=['items'],
                               after_idx={'items': cursor})
        self.assertEqual(len(rest['fields']['items']), 5)
        self.assertEqual(rest['fields']['items'][0]['idx'], cursor + 1)
        seen = [row['idx'] for row in first['fields']['items']] + \
               [row['idx'] for row in rest['fields']['items']]
        self.assertEqual(seen, list(range(1, total + 1)), 'the cursor must read the whole table')

    def test_a_record_that_fits_carries_no_truncation_key(self):
        order = self._order()
        self.assertNotIn('truncated', api.read_record('Sales Order', order.name, children=['items']))

    def test_children_rejects_a_non_table_field(self):
        order = self._order()
        with self.assertRaises(frappe.ValidationError):
            api.read_record('Sales Order', order.name, children=['customer'])

    def test_children_beyond_the_cap_is_refused(self):
        order = self._order()
        with self.assertRaises(frappe.ValidationError):
            api.read_record('Sales Order', order.name,
                            children=[f'table{n}' for n in range(LIMITS['max_child_tables'] + 1)])

    def test_named_fields_are_returned_even_when_empty(self):
        """Asking for a field by name is asking whether it has a value."""
        order = self._order()
        result = api.read_record('Sales Order', order.name, fields=['po_no', 'customer'])
        self.assertEqual(set(result['fields']), {'po_no', 'customer'})

    # --- read_schema ----------------------------------------------------------------------
    def _all_schema_fields(self, doctype, **kwargs):
        """Every field, following the cursor. Sales Order does not fit in one page even
        without inlined tables (23,102 bytes measured against a 16,384 cap), so paging is the
        ordinary path here, not an edge case."""
        page = api.read_schema(doctype, **kwargs)
        fields, cursor, guard = list(page['fields']), page.get('next_after_fieldname'), 0
        while cursor and guard < 20:
            page = api.read_schema(doctype, after_fieldname=cursor, **kwargs)
            fields += page['fields']
            cursor = page.get('next_after_fieldname')
            guard += 1
        return fields

    def test_schema_does_not_inline_child_tables_by_default(self):
        fields = self._all_schema_fields('Sales Order')
        tables = [field for field in fields if field['fieldtype'] == 'Table']
        self.assertTrue(tables)
        for table in tables:
            self.assertNotIn('fields', table)
            self.assertEqual(table['rows_of'], table['options'])

    def test_every_page_of_a_schema_fits_the_cap(self):
        """The cap is on the whole result the model receives, not on the field list inside
        it: measuring only the list leaves the envelope's own bytes unaccounted for.

        The one documented exception is a page carrying a single field: progress must never
        stall, so one field always goes out even if it alone is over."""
        cursor, guard = None, 0
        while guard < 20:
            page = api.read_schema('Sales Order', tables=['items'],
                                   **({'after_fieldname': cursor} if cursor else {}))
            if len(page['fields']) > 1:
                self.assertLessEqual(_bytes(page), LIMITS['schema_max_bytes'])
            cursor = page.get('next_after_fieldname')
            guard += 1
            if not cursor:
                break

    def test_schema_expands_only_the_named_tables(self):
        fields = self._all_schema_fields('Sales Order', tables=['items'])
        expanded = [field['fieldname'] for field in fields if field.get('fields')]
        self.assertEqual(expanded, ['items'])

    def test_a_requested_table_is_always_expanded_somewhere(self):
        """A table the caller explicitly asked for must never come back silently unexpanded:
        it is expanded on exactly one page, as far as that page's budget allows, and what did
        not fit is reachable through the column cursor."""
        pages, cursor, guard = [], None, 0
        while guard < 20:
            page = api.read_schema('Sales Order', tables=['items'],
                                   **({'after_fieldname': cursor} if cursor else {}))
            pages.append(page)
            cursor = page.get('next_after_fieldname')
            guard += 1
            if not cursor:
                break
        owning = [page for page in pages
                  if any(field.get('fields') for field in page['fields'])]
        self.assertEqual(len(owning), 1)
        table = next(field for field in owning[0]['fields'] if field.get('fields'))
        self.assertEqual(table['fieldname'], 'items')
        self.assertLessEqual(_bytes(owning[0]), LIMITS['schema_max_bytes'])
        self.assertNotIn('_column_offset', table, 'internal bookkeeping must not reach the model')

    def test_a_wide_table_pages_its_columns_and_the_cursor_reads_them_all(self):
        """Sales Order Item has 80 columns: 13,802 bytes on its own, 16,426 nested inside a
        result — the nesting alone costs 2,624, two spaces on every line. So the columns page
        too, and nothing becomes unreachable."""
        page = api.read_schema('Sales Order', tables=['items'], after_fieldname=self._before_items())
        table = next(field for field in page['fields'] if field['fieldname'] == 'items')
        self.assertTrue(table['fields'], 'the requested table must expand at least partly')
        self.assertLessEqual(_bytes(page), LIMITS['schema_max_bytes'])
        seen = [column['fieldname'] for column in table['fields']]
        cursor = table.get('columns_truncated', {}).get('next_after_child_fieldname')
        guard = 0
        while cursor and guard < 20:
            page = api.read_schema('Sales Order', tables=['items'],
                                   after_fieldname=self._before_items(),
                                   child_after={'items': cursor})
            table = next(field for field in page['fields'] if field['fieldname'] == 'items')
            seen += [column['fieldname'] for column in table['fields']]
            cursor = table.get('columns_truncated', {}).get('next_after_child_fieldname')
            guard += 1
        self.assertEqual(len(seen), table['total_columns'])
        self.assertEqual(len(set(seen)), table['total_columns'], 'no column twice')

    def test_a_child_cursor_that_names_nothing_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            api.read_schema('Sales Order', tables=['items'],
                            child_after={'items': 'not_a_column'})

    def _before_items(self):
        """The field just before `items`, so one call lands on the page that owns it."""
        fields = self._all_schema_fields('Sales Order')
        names = [field['fieldname'] for field in fields]
        return names[names.index('items') - 1]

    def test_schema_version_does_not_move_with_the_arguments(self):
        """propose_create pins the version returned here; if it drifted with `tables`, a
        proposal made after a wide read would be rejected for no reason."""
        plain = api.read_schema('Sales Order')
        expanded = api.read_schema('Sales Order', tables=['items'])
        self.assertEqual(plain['modified'], expanded['modified'])

    def test_schema_truncates_and_offers_a_cursor_that_reads_the_rest(self):
        first = api.read_schema('Sales Order', tables=['items'])
        self.assertTrue(first['truncated'], 'the widest DocType should not fit in one page')
        seen = self._all_schema_fields('Sales Order', tables=['items'])
        self.assertEqual(len(seen), first['total_fields'])
        self.assertEqual(len({field['fieldname'] for field in seen}), first['total_fields'])

    def test_schema_cursor_field_must_exist(self):
        with self.assertRaises(frappe.ValidationError):
            api.read_schema('Sales Order', after_fieldname='not_a_field')

    # --- search_records -------------------------------------------------------------------
    def test_search_still_returns_a_plain_list(self):
        """context_execution builds record_versions and _tool_summary from this being a list,
        and seven assertions in test_filtered_search.py read it as one."""
        rows = api.search_records('Item')
        self.assertIsInstance(rows, list)
        self.assertLessEqual(len(rows), LIMITS['search_name_page_length'])
        self.assertTrue(all(set(row) == {'name', 'modified'} for row in rows))

    def test_after_name_continues_the_ordered_page(self):
        first = api.search_records('Item')
        self.assertTrue(first, 'the Site has no Items to page through')
        rest = api.search_records('Item', after_name=first[-1]['name'])
        self.assertTrue(all(row['name'] > first[-1]['name'] for row in rest))
        self.assertFalse({row['name'] for row in first} & {row['name'] for row in rest})

    def test_after_name_works_with_filters_too(self):
        rows = api.search_records('Item', filters={'disabled': 0})
        if len(rows) < 2:
            self.skipTest('not enough rows to page')
        rest = api.search_records('Item', filters={'disabled': 0}, after_name=rows[0]['name'])
        self.assertTrue(all(row['name'] > rows[0]['name'] for row in rest))

    def test_the_page_lengths_are_the_ones_the_description_states(self):
        self.assertEqual(LIMITS['search_name_page_length'], 20)
        self.assertEqual(LIMITS['search_page_length'], 100)
        self.assertLessEqual(len(api.search_records('Item')), LIMITS['search_name_page_length'])
        self.assertLessEqual(len(api.search_records('Item', filters={'disabled': 0})),
                             LIMITS['search_page_length'])

    def test_json_arguments_are_still_accepted(self):
        """The bridge receives these over HTTP, where lists and dicts arrive as JSON text."""
        order = self._order(rows=2)
        result = api.read_record('Sales Order', order.name, children=json.dumps(['items']))
        self.assertEqual(len(result['fields']['items']), 2)
