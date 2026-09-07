"""A configuration transfer is usable only inside its window and only while its authorization
lease is in the Site cache; the row never holds the platform token.

prepare_transfer starts with frappe.db.rollback(), which would discard this class's uncommitted
fixtures, so the transfer row is inserted directly and the export side is what gets exercised."""
import time

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from dsherp_bridge import grants
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.configuration_transfer import TRANSFER_WINDOW_SECONDS, check_window, export_transfer
from dsherp_bridge.configuration_transport import open_envelope, seal
from dsherp_bridge.context_api import _json

PEER = {'site': 'isolated-preview.localhost', 'url': 'http://preview-backend:8000',
        'public_url': 'http://preview.localhost:18085', 'secret': 'native-pair-secret'}
PACKAGE = {'version': 1,
           'doctypes': [{'name': 'DS Native Transfer Test', 'module': 'DSHERP Bridge',
                         'fields': [{'fieldname': 'result', 'label': 'Result', 'fieldtype': 'Data'}],
                         'permissions': [{'role': 'System Manager', 'read': 1, 'write': 1, 'create': 1}]}],
           'extensions': [], 'workflows': []}
ACTOR = 'native-transfer@example.invalid'


class TestTransferGrant(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Transfer',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        self.leases = []
        frappe.conf.dsherp_configuration_preview = PEER
        frappe.set_user(ACTOR)
        conversation = frappe.get_doc({'doctype': 'DS Conversation',
                                       'title': 'Native transfer test'}).insert(ignore_permissions=True)
        self.bundle = propose_bundle(conversation.name, PACKAGE)

    def tearDown(self):
        for name in self.leases:
            grants.drop_transfer(name)
        frappe.conf.pop('dsherp_configuration_preview', None)
        frappe.set_user('Administrator')
        super().tearDown()

    def _transfer(self, expires_at):
        payload = {'source_site': frappe.local.site, 'preview_site': PEER['site'], 'actor': ACTOR,
                   'bundle_digest': self.bundle['digest'],
                   'package_digest': freeze_bundle(self.bundle['package'])['digest']}
        doc = frappe.get_doc({'doctype': 'DS Configuration Transfer',
                              'request_id': frappe.generate_hash(length=64), 'bundle': self.bundle['id'],
                              'payload': _json(payload), 'expires_at': expires_at}).insert(ignore_permissions=True)
        self.leases.append(doc.name)
        return doc

    def _export(self, doc):
        request = seal({'transfer_id': doc.name, 'actor': ACTOR, 'source_site': frappe.local.site,
                        'preview_site': PEER['site']}, PEER['secret'], 'export-request')
        frappe.set_user('Guest')
        try:
            return export_transfer(request)
        finally:
            frappe.set_user(ACTOR)

    def test_inside_the_window_with_a_lease_the_frozen_package_is_exported_and_no_row_holds_a_grant(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        data = open_envelope(self._export(doc), PEER['secret'], 'export-response')
        self.assertEqual(data['package'], PACKAGE)
        self.assertEqual(data['actor'], ACTOR)
        self.assertNotIn('platform_grant', frappe.db.get_table_columns('DS Configuration Transfer'))

    def test_an_expired_transfer_is_refused_by_name(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=-1))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        with self.assertRaises(frappe.ValidationError) as caught:
            self._export(doc)
        self.assertIn('配置交接已过期', str(caught.exception))
        with self.assertRaises(frappe.ValidationError):
            check_window(doc)

    def test_a_transfer_whose_lease_is_gone_is_refused_even_inside_its_window(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        with self.assertRaises(frappe.ValidationError) as caught:
            self._export(doc)
        self.assertIn('配置交接授权已失效', str(caught.exception))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        grants.drop_transfer(doc.name)
        with self.assertRaises(frappe.ValidationError):
            self._export(doc)

    def test_the_lease_carries_the_grant_is_written_even_when_empty_and_dies_with_its_ttl(self):
        grants.stash_transfer('native-lease-probe', 'encrypted-grant', 60)
        self.leases.append('native-lease-probe')
        self.assertEqual(grants.of_transfer('native-lease-probe'), {'grant': 'encrypted-grant'})
        grants.stash_transfer('native-lease-empty', None, 60)
        self.leases.append('native-lease-empty')
        self.assertEqual(grants.of_transfer('native-lease-empty'), {'grant': None},
                         'a Site with no platform grant still gets a lease')
        grants.stash_transfer('native-lease-short', 'x', 1)
        time.sleep(1.5)
        self.assertIsNone(grants.of_transfer('native-lease-short'))

    def test_the_window_is_part_of_the_frozen_binding(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        doc.expires_at = add_to_date(doc.expires_at, hours=1)
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
