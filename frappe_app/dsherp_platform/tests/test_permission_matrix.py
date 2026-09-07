"""The platform half of the G7 matrix, read from the one matrix file the bridge app carries.

Both benches mount ../frappe_app at /opt/dsherp-frappe, so the two apps see the same file and
there is one source for what each actor may do."""
import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

MATRIX = Path(__file__).resolve().parents[2] / 'dsherp_bridge' / 'tests' / 'permission_matrix.json'
APP = 'dsherp_platform'
ACTIONS = ('read', 'create', 'write', 'delete')


def insert(payload):
    return frappe.get_doc(payload).insert(ignore_permissions=True).name


class TestPlatformPermissionMatrix(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user('Administrator')
        matrix = json.loads(MATRIX.read_text())
        cls.rows = [row for row in matrix['doctypes'] if row['app'] == APP]
        cls.tag = frappe.generate_hash(length=8)
        cls.users = {}
        for actor, spec in matrix['actors'].items():
            if 'roles' not in spec:
                cls.users[actor] = spec['user']
                continue
            email = f'matrix-{actor}-{cls.tag}@example.invalid'
            frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': f'Matrix {actor}',
                            'enabled': 1, 'send_welcome_email': 0,
                            'roles': [{'role': role} for role in spec['roles']]}).insert(ignore_permissions=True)
            cls.users[actor] = email
        cls.fixtures = {}
        cls.fixtures['DS Enterprise'] = insert({
            'doctype': 'DS Enterprise', 'enterprise_id': 'matrix-' + cls.tag, 'title': 'Matrix',
            'site': 'matrix.localhost', 'base_url': 'http://matrix.localhost', 'status': 'Provisioning'})
        cls.fixtures['DS Membership'] = insert({
            'doctype': 'DS Membership', 'enterprise': cls.fixtures['DS Enterprise'],
            'platform_user': cls.users['member'], 'enabled': 1,
            'erp_user': f'matrix-erp-{cls.tag}@example.invalid',
            'api_key': 'k' + cls.tag, 'api_secret': 's' + cls.tag})
        cls.fixtures['DS Agent Task'] = insert({
            'doctype': 'DS Agent Task', 'enterprise': cls.fixtures['DS Enterprise'],
            'question': 'matrix', 'status': 'Succeeded'})

    def test_every_platform_doctype_actor_and_action_answers_as_the_matrix_says(self):
        mismatches = []
        for row in self.rows:
            for actor, expected in row['actions'].items():
                for action in ACTIONS:
                    want = expected[action] if expected[action] != 'refused_by_controller' else True
                    got = bool(frappe.has_permission(row['doctype'], action, user=self.users[actor]))
                    if got != want:
                        mismatches.append((row['doctype'], actor, action, got, want))
        self.assertEqual(mismatches, [])

    def test_the_secret_columns_of_a_membership_are_permlevel_one(self):
        """A borrowed business credential is what the platform hands out; an ordinary member
        must not be able to read one back off the binding that lent it."""
        meta = frappe.get_meta('DS Membership')
        manager = set(meta.get_permitted_fieldnames(user=self.users['manager'], permission_type='read'))
        member = set(meta.get_permitted_fieldnames(user=self.users['member'], permission_type='read'))
        self.assertTrue({'api_key', 'api_secret'} <= manager)
        self.assertEqual(member & {'api_key', 'api_secret'}, set())
        self.assertEqual(sorted(f.fieldname for f in meta.fields if f.permlevel == 1),
                         ['api_key', 'api_secret'])

    def test_an_enabled_membership_refuses_deletion(self):
        """Deleting an enabled binding would leave the credential it lent out un-revoked, so
        the controller refuses whatever the permission layer says."""
        name = self.fixtures['DS Membership']
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc('DS Membership', name, force=True, ignore_permissions=True)
        self.assertTrue(frappe.db.exists('DS Membership', name))

    def test_a_manager_cannot_delete_retired_task_history_while_administrator_still_can(self):
        """README: retired task history is kept read-only. There is no on_trash here, so the
        role grant is the whole gate - and Administrator bypasses role grants by design."""
        self.assertFalse(frappe.has_permission('DS Agent Task', 'delete', user=self.users['manager']))
        self.assertTrue(frappe.has_permission('DS Agent Task', 'delete', user='Administrator'))
        frappe.set_user(self.users['manager'])
        try:
            with self.assertRaises(frappe.PermissionError):
                frappe.delete_doc('DS Agent Task', self.fixtures['DS Agent Task'])
        finally:
            frappe.set_user('Administrator')
        self.assertTrue(frappe.db.exists('DS Agent Task', self.fixtures['DS Agent Task']))
