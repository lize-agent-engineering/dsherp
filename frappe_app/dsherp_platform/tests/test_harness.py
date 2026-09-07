"""The platform half of the harness: its own Site, its own bench, and no ERPNext at all."""
import frappe
from frappe.tests import IntegrationTestCase


class TestHarness(IntegrationTestCase):
    def test_runs_on_the_platform_test_site_without_erpnext(self):
        self.assertEqual(frappe.local.site, 'dsherp-platform-test.localhost')
        self.assertTrue(frappe.conf.get('allow_tests'))
        installed = frappe.get_installed_apps()
        self.assertIn('dsherp_platform', installed)
        self.assertNotIn('erpnext', installed)
