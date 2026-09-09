"""The five business checks, and the five things they deliberately do not check.

Every one of these used to fail at `confirm` — after a person had read a proposal and clicked
to approve it. The point of moving them earlier is that **no Pending row is ever created**.

Most cases here call a check directly, so what they assert is the refusal and its wording.
The «no row was stored» half needs the whole `propose_*` path and is asserted where that path
runs: `TestNoProposalRowSurvivesAPreflightRefusal` below. (Until 2026-09-10 this docstring
claimed every assertion checked the proposal count; none of them did — the claim is now the
name of a test instead of a sentence.)
"""
import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge import preflight

ACTOR = 'native-preflight@example.invalid'
ROLES = ('System Manager', 'Item Manager', 'Stock User', 'Stock Manager', 'Sales User',
         'Sales Manager', 'Accounts User', 'Purchase User', 'Purchase Master Manager',
         'Manufacturing User')


def _ensure_actor():
    """The synthetic member every class here acts as. Idempotent, and called by **each** class:
    a from-zero Site may run them in any order, and a class that borrowed another's setup
    passes only on a machine where that other class has already run."""
    roles = [role for role in ROLES if frappe.db.exists('Role', role)]
    if not frappe.db.exists('User', ACTOR):
        frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Preflight',
                        'user_type': 'System User', 'send_welcome_email': 0,
                        'roles': [{'role': role} for role in roles]}).insert(ignore_permissions=True)
        return
    user = frappe.get_doc('User', ACTOR)
    held = {row.role for row in user.roles}
    for role in roles:
        if role not in held:
            user.append('roles', {'role': role})
    user.save(ignore_permissions=True)


ITEM = 'DSHERP-PREFLIGHT-ITEM'
BOM_FG = 'DSHERP-PREFLIGHT-BOM-FG'
BOM_RM = 'DSHERP-PREFLIGHT-BOM-RM'
# A second company exists only so the cross-company warehouse rule has something to refuse.
# Creating it costs about a second and brings four warehouses of its own with it.
OTHER_COMPANY = 'DSHERP 前置校验第二公司'
OTHER_ABBR = 'DPF2'
STOCK_ITEM = 'DSHERP-PREFLIGHT-STOCK'
CUSTOMER = 'DSHERP-PREFLIGHT-CUSTOMER'
SUPPLIER = 'DSHERP-PREFLIGHT-SUPPLIER'


def _ensure_fixtures(cls):
    """Everything this module acts on, created by whichever class runs first.

    Called from **every** `setUpClass` here on purpose. Reading a Customer or an Item that
    some other class happened to insert reads fine and passes on a machine that has run
    before; on a from-zero Site, run order decides whether the row is there. So each class
    creates what it needs, under this module's own names."""
    _ensure_actor()
    cls.company = frappe.db.get_value('Company', {'abbr': 'DNT'}, 'name') \
        or frappe.db.get_value('Company', {}, 'name')
    cls.warehouse = frappe.db.get_value('Warehouse', {'is_group': 0, 'company': cls.company}, 'name')
    cls.group_warehouse = frappe.db.get_value('Warehouse', {'is_group': 1, 'company': cls.company}, 'name')
    uom = frappe.db.get_single_value('Stock Settings', 'stock_uom') or 'Nos'
    group = frappe.db.get_value('Item Group', {'is_group': 0}, 'name')
    for code, stock in ((ITEM, 0), (STOCK_ITEM, 1)):
        if not frappe.db.exists('Item', code):
            frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': code,
                            'item_group': group, 'stock_uom': uom,
                            'is_stock_item': stock}).insert(ignore_permissions=True)
    if not frappe.db.exists('Customer', CUSTOMER):
        frappe.get_doc({'doctype': 'Customer', 'customer_name': CUSTOMER,
                        'customer_type': 'Individual',
                        'customer_group': frappe.db.get_value('Customer Group', {'is_group': 0}, 'name'),
                        'territory': frappe.db.get_value('Territory', {'is_group': 0}, 'name')
                        }).insert(ignore_permissions=True)
    # `check_mandatory` reports only child tables and mandatory Links to DocTypes this
    # Site governs, so the rule needs a governed Customer to be observable at all.
    for target in ('Sales Order', 'Customer', 'Item'):
        if not frappe.db.exists('DS Doctype Policy', target):
            frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': target,
                            'enabled': 1, 'allow_read': 1, 'allow_create': 0,
                            'allow_update': 0, 'allow_submit': 0, 'allow_cancel': 0,
                            'allow_fill': 0, 'change_reason': '前置校验测试'
                            }).insert(ignore_permissions=True)
    cls.bom = _ensure_bom(cls.company, uom, group)
    cls.other_company = _ensure_other_company(cls.company)
    if not frappe.db.exists('Supplier', SUPPLIER):
        frappe.get_doc({'doctype': 'Supplier', 'supplier_name': SUPPLIER,
                        'supplier_group': frappe.db.get_value('Supplier Group', {'is_group': 0}, 'name')
                        }).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_bom(company, uom, group):
    """A submitted, active BOM. `check_bom` had no test that ran anywhere without one.

    Until 2026-09-10 its only test read whatever BOM the Site happened to hold and skipped
    when there was none — which is every Site the nightly builds. A check whose test skips
    everywhere is not a checked check.
    """
    for code in (BOM_FG, BOM_RM):
        if not frappe.db.exists('Item', code):
            frappe.get_doc({'doctype': 'Item', 'item_code': code, 'item_name': code,
                            'item_group': group, 'stock_uom': uom, 'is_stock_item': 1
                            }).insert(ignore_permissions=True)
    existing = frappe.db.get_value('BOM', {'item': BOM_FG, 'docstatus': 1, 'is_active': 1}, 'name')
    if existing:
        return existing
    # 'Valuation Rate' is one of this version's three options and the raw material has none,
    # so the cost comes out 0 with an alert. Cost is not what any of these cases measure.
    bom = frappe.get_doc({'doctype': 'BOM', 'item': BOM_FG, 'company': company, 'quantity': 1,
                          'is_active': 1, 'is_default': 1, 'rm_cost_as_per': 'Valuation Rate',
                          'currency': frappe.db.get_value('Company', company, 'default_currency'),
                          'items': [{'item_code': BOM_RM, 'qty': 2, 'uom': uom}]})
    bom.insert(ignore_permissions=True)
    bom.submit()
    return bom.name


def _ensure_other_company(company):
    """The other half of `check_warehouses`, for the same reason: its test skipped on any Site
    with one company, which is every Site the nightly builds."""
    if not frappe.db.exists('Company', OTHER_COMPANY):
        base = frappe.db.get_value('Company', company, ['default_currency', 'country'], as_dict=True)
        frappe.get_doc({'doctype': 'Company', 'company_name': OTHER_COMPANY, 'abbr': OTHER_ABBR,
                        'default_currency': base.default_currency, 'country': base.country
                        }).insert(ignore_permissions=True)
    return OTHER_COMPANY


class TestPreflight(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_fixtures(cls)

    def setUp(self):
        super().setUp()
        frappe.set_user(ACTOR)
        self.company = type(self).company
        self.warehouse = type(self).warehouse

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def _order(self, **overrides):
        payload = {'doctype': 'Sales Order', 'customer': CUSTOMER, 'company': self.company,
                   'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
                   'items': [{'item_code': ITEM, 'qty': 1, 'rate': 100,
                              'warehouse': self.warehouse,
                              'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}]}
        payload.update(overrides)
        return frappe.get_doc(payload)

    # --- links ----------------------------------------------------------------------------
    def test_a_missing_link_is_named(self):
        order = self._order(customer='DSHERP-NO-SUCH-CUSTOMER')
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_links(order)
        self.assertIn('customer', str(caught.exception))

    def test_a_document_whose_links_all_exist_passes(self):
        preflight.check_links(self._order())

    def test_missing_native_validation_method_fails_loud(self):
        """Silently skipping would leave a check that reads as done and is not — the one
        failure mode worse than having no check."""
        class Stripped:
            meta = None
            get_invalid_links = None
            get_all_children = None
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_links(Stripped())
        self.assertIn('原生校验方法', str(caught.exception))

        class NoChildren:
            """Child rows are half the check now, so losing the way to reach them is the
            same loud failure as losing the link check itself."""
            meta = None

            def get_invalid_links(self):
                return [], []
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_links(NoChildren())
        self.assertIn('原生校验方法', str(caught.exception))

    # --- mandatory ------------------------------------------------------------------------
    def test_create_missing_a_required_field_names_it(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_mandatory('Sales Order', {'company': self.company})
        message = str(caught.exception)
        self.assertIn('customer', message)

    def test_fields_the_site_fills_itself_are_not_demanded(self):
        """Measured on the real Site: a Sales Order created with only customer, company,
        delivery date and one item inserts fine, and afterwards naming_series, currency,
        conversion_rate, selling_price_list and plc_conversion_rate are all set — every one
        `reqd=1` with no default and no fetch_from. Demanding them would report five fields
        the user was never supposed to provide."""
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_mandatory('Sales Order', {})
        message = str(caught.exception)
        for filled in ('naming_series', 'currency', 'conversion_rate',
                       'selling_price_list', 'plc_conversion_rate'):
            self.assertNotIn(filled, message, f'{filled} 由站点自己填，不该催用户填')
        self.assertIn('customer', message, '真正该催的那个必须在')

    def test_a_complete_creation_passes(self):
        preflight.check_mandatory('Sales Order', {
            'customer': CUSTOMER, 'company': self.company,
            'delivery_date': frappe.utils.nowdate(), 'items': [{'item_code': ITEM}]})

    def test_an_empty_child_table_counts_as_missing(self):
        """A child table is always the caller's to fill; `[]` is not supplying it."""
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_mandatory('Sales Order', {
                'customer': CUSTOMER, 'company': self.company,
                'delivery_date': frappe.utils.nowdate(), 'items': []})
        self.assertIn('items', str(caught.exception))

    # --- warehouses -----------------------------------------------------------------------
    def test_group_warehouse_is_refused(self):
        if not type(self).group_warehouse:
            self.skipTest('this Site has no group warehouse')
        order = self._order()
        order.items[0].warehouse = type(self).group_warehouse
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_warehouses(order)
        self.assertIn('分组仓库', str(caught.exception))

    def test_warehouse_of_another_company_is_refused(self):
        other = frappe.db.get_value('Warehouse',
                                    {'is_group': 0, 'company': type(self).other_company},
                                    ['name', 'company'], as_dict=True)
        self.assertIsNotNone(other, '第二家公司应当带出自己的仓库；fixture 没建成就不能静默跳过')
        order = self._order()
        order.items[0].warehouse = other.name
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_warehouses(order)
        self.assertIn(other.company, str(caught.exception))

    def test_an_ordinary_warehouse_passes(self):
        preflight.check_warehouses(self._order())

    def test_a_child_row_warehouse_is_checked_too(self):
        """Most warehouses live on the detail rows, not the parent."""
        if not type(self).group_warehouse:
            self.skipTest('this Site has no group warehouse')
        order = self._order()
        order.items[0].warehouse = type(self).group_warehouse
        with self.assertRaises(frappe.ValidationError):
            preflight.check_warehouses(order)

    # --- BOM ------------------------------------------------------------------------------
    def _flip(self, field, value):
        restore = frappe.db.get_value('BOM', type(self).bom, field)
        self.addCleanup(frappe.db.set_value, 'BOM', type(self).bom, field, restore)
        frappe.db.set_value('BOM', type(self).bom, field, value)

    def _work_order(self, item=BOM_FG):
        return frappe.get_doc({'doctype': 'Work Order', 'production_item': item,
                               'bom_no': type(self).bom, 'qty': 1, 'company': self.company})

    def test_a_submitted_active_bom_for_the_right_item_passes(self):
        """The control the three refusals below need: without it they would all also hold if
        `check_bom` simply refused everything."""
        preflight.check_bom(self._work_order())

    def test_a_bom_for_another_item_is_refused(self):
        order = self._work_order(item=ITEM)
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_bom(order)
        self.assertIn(type(self).bom, str(caught.exception))

    def test_an_inactive_bom_is_refused(self):
        """Put back by this test, not by the rollback: `IntegrationTestCase` rolls back once
        per class, so a flip left behind here is read by every later test in the class."""
        self._flip('is_active', 0)
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_bom(self._work_order())
        self.assertIn(type(self).bom, str(caught.exception))

    def test_a_draft_bom_is_refused(self):
        self._flip('docstatus', 0)
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_bom(self._work_order())
        self.assertIn(type(self).bom, str(caught.exception))

    def test_a_document_with_no_bom_field_passes(self):
        preflight.check_bom(self._order())

    # --- stock ----------------------------------------------------------------------------
    def _impact(self, quantity):
        return {'kind': 'stock', 'entries': [
            {'item_code': STOCK_ITEM, 'quantity': quantity, 'uom': 'Nos',
             'warehouse': self.warehouse}]}

    def test_submit_with_insufficient_stock_states_the_available_quantity(self):
        actual = frappe.db.get_value('Bin', {'item_code': STOCK_ITEM,
                                             'warehouse': self.warehouse}, 'actual_qty') or 0
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_stock_available(self._order(), self._impact(-(actual + 100)))
        message = str(caught.exception)
        self.assertIn(str(actual), message)
        self.assertIn(STOCK_ITEM, message)
        self.assertIn('批次', message, '必须说清这次没按批次/序列号核对')

    def test_stock_coming_in_is_never_short(self):
        preflight.check_stock_available(self._order(), self._impact(500))

    def test_stock_check_is_skipped_when_negative_stock_is_allowed(self):
        """On such a Site "not enough stock" is not an error, so raising one would refuse
        work the Site permits."""
        settings = frappe.get_doc('Stock Settings')
        before = settings.allow_negative_stock
        settings.allow_negative_stock = 1
        settings.save(ignore_permissions=True)
        try:
            preflight.check_stock_available(self._order(), self._impact(-999999))
        finally:
            settings = frappe.get_doc('Stock Settings')
            settings.allow_negative_stock = before
            settings.save(ignore_permissions=True)

    def test_repeated_draws_on_one_bin_are_added_together(self):
        """Two rows taking 60 each from a bin holding 100 is short, even though neither is."""
        impact = {'kind': 'stock', 'entries': [
            {'item_code': STOCK_ITEM, 'quantity': -60, 'uom': 'Nos', 'warehouse': self.warehouse},
            {'item_code': STOCK_ITEM, 'quantity': -60, 'uom': 'Nos', 'warehouse': self.warehouse}]}
        actual = frappe.db.get_value('Bin', {'item_code': STOCK_ITEM,
                                             'warehouse': self.warehouse}, 'actual_qty') or 0
        if actual >= 120:
            self.skipTest('this bin has enough for both rows')
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_stock_available(self._order(), impact)
        self.assertIn('120', str(caught.exception))

    def test_an_empty_impact_checks_nothing(self):
        preflight.check_stock_available(self._order(), {'kind': 'none', 'entries': []})
        preflight.check_stock_available(self._order(), None)

    # --- the promise the preflight does not make ------------------------------------------
    def test_the_preflight_never_saves(self):
        """It may write to the copy it is handed — `get_invalid_links` populates `fetch_from`
        values while resolving links, and Frappe stamps a provisional name on the way — but
        nothing reaches the database. The database is what this asserts; `is_new()` is a flag
        on the in-memory object and says nothing about what was stored."""
        order = self._order()
        before = frappe.db.count('Sales Order')
        preflight.before_update(order)
        self.assertEqual(frappe.db.count('Sales Order'), before, '前置校验绝不能落库')
        if order.get('name'):
            self.assertFalse(frappe.db.exists('Sales Order', order.name),
                             '前置校验期间被赋予的名字不能在库里存在')

    def test_an_update_preflight_does_not_mutate_the_stored_document(self):
        """`update_diff` promises the caller's document is not mutated. The preflight does
        write to what it is given, so `propose_update` hands it a separate `get_doc` — this
        asserts that promise end to end, not just the intention."""
        from dsherp_bridge import operations
        order = self._order()
        order.insert(ignore_permissions=True)
        inspected = frappe.get_doc('Sales Order', order.name)
        before = inspected.as_dict()
        operations._preflight_update('Sales Order', order.name, {'po_no': 'DSHERP-PF-1'})
        self.assertEqual(inspected.as_dict(), before, '被检查的那份文档不能被前置校验改动')
        self.assertIsNone(frappe.db.get_value('Sales Order', order.name, 'po_no'),
                          '前置校验不能落库')


class TestChildRowLinks(IntegrationTestCase):
    """The most common mistake a model makes is in a line, not in the header.

    `BaseDocument.get_invalid_links` walks `self` only; Frappe's own `_validate_links` calls it
    for the document **and then for every row of `get_all_children()`**. Checking the parent
    alone let an invented `item_code` through the preflight, be stored as a Pending proposal,
    be shown to a person, and fail at confirm with a `LinkValidationError` — exactly the class
    of failure this module exists to move before the approval.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_fixtures(cls)

    def _order(self, item_code):
        return frappe.get_doc({
            'doctype': 'Sales Order', 'customer': CUSTOMER,
            'company': type(self).company,
            'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
            'items': [{'item_code': item_code, 'qty': 1, 'rate': 100,
                       'warehouse': type(self).warehouse,
                       'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}]})

    def test_an_invented_item_code_in_a_line_is_refused_before_the_proposal(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            preflight.check_links(self._order('DSHERP-NO-SUCH-ITEM'))
        message = str(caught.exception)
        self.assertIn('DSHERP-NO-SUCH-ITEM', message)
        self.assertIn('items 第 1 行的', message, '要说清是哪一行，否则模型改不动')

    def test_a_line_whose_references_all_exist_passes(self):
        preflight.check_links(self._order(ITEM))


class TestNoProposalRowSurvivesAPreflightRefusal(IntegrationTestCase):
    """The half the module docstring promises: a refused request stores nothing.

    Every other case here calls a check directly, so it can only assert the refusal and its
    wording. What the person actually cares about is that a proposal they would have been
    asked to approve was never created — and that needs the whole `propose_*` path, because
    the preflight runs inside it, before `_propose`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_fixtures(cls)

    def setUp(self):
        super().setUp()
        # Creation has to be allowed for this class, and **only** for this class: other native
        # modules commit a read-only Sales Order policy as their own fixture. Flipped inside
        # the test transaction with no commit, so the class rollback puts it back.
        if frappe.db.exists('DS Doctype Policy', 'Sales Order'):
            frappe.db.set_value('DS Doctype Policy', 'Sales Order', 'allow_create', 1)
        else:
            frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': 'Sales Order',
                            'enabled': 1, 'allow_read': 1, 'allow_create': 1, 'allow_update': 0,
                            'allow_submit': 0, 'allow_cancel': 0, 'allow_fill': 0,
                            'change_reason': '前置校验落库测试'}).insert(ignore_permissions=True)
        # `propose_create` acts for a business member; `_user()` refuses Administrator, and the
        # conversation it stores against has to belong to that member too.
        frappe.set_user(ACTOR)

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def _values(self, item_code):
        return {'customer': CUSTOMER, 'company': type(self).company,
                'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14),
                'items': [{'item_code': item_code, 'qty': 1, 'rate': 100,
                           'warehouse': type(self).warehouse,
                           'delivery_date': frappe.utils.add_days(frappe.utils.nowdate(), 14)}]}

    def _conversation(self):
        doc = frappe.get_doc({'doctype': 'DS Conversation',
                              'title': 'preflight'}).insert(ignore_permissions=True)
        frappe.db.set_value('DS Conversation', doc.name, 'owner', ACTOR)
        return doc.name

    def test_create_with_a_missing_link_is_refused_before_any_proposal_row_exists(self):
        from dsherp_bridge import operations
        before = frappe.db.count('DS Operation Proposal')
        version = str(frappe.get_meta('Sales Order').modified)
        with self.assertRaises(frappe.ValidationError) as caught:
            operations.propose_create(self._conversation(), 'Sales Order',
                                      self._values('DSHERP-NO-SUCH-ITEM'), version)
        message = str(caught.exception)
        self.assertIn('DSHERP-NO-SUCH-ITEM', message)
        self.assertIn('items 第 1 行的', message, '要说清是哪一行，模型才改得动')
        self.assertEqual(frappe.db.count('DS Operation Proposal'), before,
                         '被前置校验拒绝的请求不能留下待确认的提案')

    def test_a_request_that_passes_preflight_does_store_its_proposal(self):
        """The control. Without it the assertion above would also pass if `propose_create`
        never stored anything at all."""
        from dsherp_bridge import operations
        before = frappe.db.count('DS Operation Proposal')
        version = str(frappe.get_meta('Sales Order').modified)
        operations.propose_create(self._conversation(), 'Sales Order',
                                  self._values(ITEM), version)
        self.assertEqual(frappe.db.count('DS Operation Proposal'), before + 1)
