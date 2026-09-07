"""The harness itself, before anything is written against it.

`bench run-tests` returns 0 on a Site that refuses testing, having run nothing, so a native
suite proves nothing until something asserts where it ran. These two classes are that proof:
the Site it is on, the switch that let it run, the apps installed, the synthetic company the
ERPNext masters come from, and that a row written by one class is not there for the next.
"""
import frappe
from frappe.tests import IntegrationTestCase

CANARY = 'harness canary'


class TestHarness(IntegrationTestCase):
    def test_runs_on_the_throwaway_test_site_with_tests_allowed_and_a_synthetic_company(self):
        self.assertEqual(frappe.local.site, 'dsherp-test.localhost')
        self.assertTrue(frappe.conf.get('allow_tests'))
        installed = frappe.get_installed_apps()
        self.assertIn('dsherp_bridge', installed)
        self.assertIn('erpnext', installed)
        self.assertEqual(frappe.db.get_value('Company', {'abbr': 'DNT'}, 'name'), 'DSHERP 原生测试公司')

    def test_a_row_written_here_is_visible_here(self):
        frappe.get_doc({'doctype': 'DS Conversation', 'title': CANARY}).insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count('DS Conversation', {'title': CANARY}), 1)


class TestHarnessRollback(IntegrationTestCase):
    def test_the_previous_class_left_nothing_behind(self):
        """If this ever fails, the rollback the whole native suite relies on is gone and the
        test Site is accumulating rows; fix that before trusting any native result."""
        self.assertEqual(frappe.db.count('DS Conversation', {'title': CANARY}), 0)
