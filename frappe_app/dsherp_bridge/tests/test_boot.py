"""What the Desk boot hands the browser, and what it refuses to hand it.

The link allowlist decides which absolute URLs a model answer may render as a clickable
anchor. It is a server decision precisely because the answer text is untrusted: a browser
that derived the allowlist from the answer, or from its own location, would be deciding
with the attacker's own data. Nothing here is a permission grant.
"""
import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge.boot import boot_session


class _Bootinfo(dict):
    """frappe hands boot_session a _dict; attribute writes must land in the payload."""

    def __setattr__(self, key, value):
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as error:
            raise AttributeError(key) from error


ACTOR = 'native-boot@example.invalid'


class TestBootLinkHosts(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Boot',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        self.saved = frappe.conf.get('dsherp_link_hosts')
        self.user = ACTOR

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()
        if self.saved is None:
            frappe.conf.pop('dsherp_link_hosts', None)
        else:
            frappe.conf['dsherp_link_hosts'] = self.saved

    def _boot(self, user):
        bootinfo = _Bootinfo()
        frappe.set_user(user)
        try:
            boot_session(bootinfo)
        finally:
            frappe.set_user('Administrator')
        return bootinfo.get('dsherp_link_hosts')

    def test_guest_and_administrator_get_an_empty_allowlist(self):
        frappe.conf['dsherp_link_hosts'] = ['erp.example.com']
        for user in ('Guest', 'Administrator'):
            with self.subTest(user=user):
                self.assertEqual(self._boot(user), [])

    def test_invalid_configuration_yields_an_empty_allowlist_without_raising(self):
        invalid = (
            'erp.example.com',                              # a bare string, not a list
            ['ERP.example.com'],                            # not lowercase
            ['erp.example.com/path'],                       # a URL, not a host
            ['https://erp.example.com'],                    # a URL, not a host
            [123],
            ['a' * 254],
            [f'h{index}.example.com' for index in range(21)],
        )
        for bad in invalid:
            with self.subTest(bad=bad):
                frappe.conf['dsherp_link_hosts'] = bad
                self.assertEqual(self._boot(self.user), [])

    def test_allowlist_is_deduplicated_and_sorted(self):
        frappe.conf['dsherp_link_hosts'] = ['b.example.com', 'a.example.com', 'b.example.com']
        self.assertEqual(self._boot(self.user), ['a.example.com', 'b.example.com'])

    def test_an_unconfigured_site_hands_out_an_empty_allowlist(self):
        frappe.conf.pop('dsherp_link_hosts', None)
        self.assertEqual(self._boot(self.user), [])

    def test_the_request_host_is_never_folded_in_automatically(self):
        """Folding in the site's own host would widen the allowlist without anyone deciding to."""
        frappe.conf['dsherp_link_hosts'] = ['erp.example.com']
        self.assertEqual(self._boot(self.user), ['erp.example.com'])
