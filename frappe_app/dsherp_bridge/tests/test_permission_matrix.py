"""G7 on a real Site: what Frappe's permission layer answers for each actor and action, and
what the controllers refuse regardless of it.

The matrix (permission_matrix.json) is the single source; tests/test_permission_matrix.py on
the host checks it against the definitions on disk, and this checks the same file against a
running Site. Everything inserted here lives inside the transaction IntegrationTestCase rolls
back, and only the Document API is used, so nothing commits."""
import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

MATRIX = Path(__file__).with_name('permission_matrix.json')
APP = 'dsherp_bridge'
ACTIONS = ('read', 'create', 'write', 'delete')


def load_rows(app):
    matrix = json.loads(MATRIX.read_text())
    return matrix, [row for row in matrix['doctypes'] if row['app'] == app]


def make_actors(matrix, tag):
    """A user per actor that is defined by roles; Guest and Administrator are themselves."""
    users = {}
    for actor, spec in matrix['actors'].items():
        if 'roles' not in spec:
            users[actor] = spec['user']
            continue
        email = f'matrix-{actor}-{tag}@example.invalid'
        frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': f'Matrix {actor}',
                        'enabled': 1, 'send_welcome_email': 0,
                        'roles': [{'role': role} for role in spec['roles']]}).insert(ignore_permissions=True)
        users[actor] = email
    return users


def insert(payload):
    return frappe.get_doc(payload).insert(ignore_permissions=True).name


class TestPermissionMatrix(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user('Administrator')
        cls.matrix, cls.rows = load_rows(APP)
        cls.tag = frappe.generate_hash(length=8)
        cls.users = make_actors(cls.matrix, cls.tag)
        cls.fixtures = cls.minimal_documents()

    @classmethod
    def minimal_documents(cls):
        """One row per DocType, built from each definition's required fields, in link order."""
        now = now_datetime()
        later = add_to_date(now, hours=1)
        f = {}
        f['DS Conversation'] = insert({'doctype': 'DS Conversation', 'title': 'matrix ' + cls.tag})
        f['DS Model Run'] = insert({'doctype': 'DS Model Run', 'conversation': f['DS Conversation'],
                                    'domain': 'query', 'status': 'Queued', 'request_id': cls.tag,
                                    'question': 'matrix'})
        f['DS Run Event'] = insert({'doctype': 'DS Run Event', 'run': f['DS Model Run'], 'seq': 1,
                                    'kind': 'matrix', 'source': 'server', 'recorded_at': now})
        f['DS Operation Proposal'] = insert({'doctype': 'DS Operation Proposal',
                                             'conversation': f['DS Conversation'],
                                             'model_run': f['DS Model Run'], 'payload': '{}',
                                             'digest': '0' * 64, 'expires_at': later, 'status': 'Pending'})
        f['DS Execution Record'] = insert({'doctype': 'DS Execution Record',
                                           'proposal': f['DS Operation Proposal'],
                                           'request_id': cls.tag, 'status': 'Running'})
        f['DS Configuration Bundle'] = insert({'doctype': 'DS Configuration Bundle',
                                               'conversation': f['DS Conversation'], 'payload': '{}',
                                               'digest': '1' * 64, 'baseline': '2' * 64})
        f['DS Configuration Confirmation'] = insert({'doctype': 'DS Configuration Confirmation',
                                                     'bundle': f['DS Configuration Bundle'],
                                                     'payload': '{}', 'digest': '3' * 64,
                                                     'expires_at': later, 'status': 'Pending'})
        f['DS Configuration Execution'] = insert({'doctype': 'DS Configuration Execution',
                                                  'confirmation': f['DS Configuration Confirmation'],
                                                  'request_id': cls.tag, 'status': 'Running', 'steps': '[]'})
        f['DS Configuration Transfer'] = insert({'doctype': 'DS Configuration Transfer',
                                                 'request_id': cls.tag,
                                                 'bundle': f['DS Configuration Bundle'], 'payload': '{}'})
        f['DS Doctype Policy'] = insert({'doctype': 'DS Doctype Policy', 'target_doctype': 'ToDo',
                                         'change_reason': 'matrix ' + cls.tag, 'enabled': 1, 'allow_read': 1})
        f['DS Business Credential'] = insert({'doctype': 'DS Business Credential',
                                              'user': cls.users['member'], 'api_key': 'matrix-' + cls.tag,
                                              'issued_at': now, 'expires_at': later, 'version': 1})
        f['DS Ops Snapshot'] = insert({'doctype': 'DS Ops Snapshot', 'collected_at': now, 'payload': '{}'})
        return f

    def test_every_doctype_actor_and_action_answers_as_the_matrix_says(self):
        mismatches = []
        for row in self.rows:
            for actor, expected in row['actions'].items():
                for action in ACTIONS:
                    want = expected[action]
                    if want == 'refused_by_controller':
                        # The permission layer lets Administrator through; the controller does not.
                        want = True
                    got = bool(frappe.has_permission(row['doctype'], action, user=self.users[actor],
                                                     parent_doctype=row.get('parent_doctype')))
                    if got != want:
                        mismatches.append((row['doctype'], actor, action, got, want))
        self.assertEqual(mismatches, [])

    def test_administrator_cannot_delete_what_the_controllers_protect_and_can_delete_the_rest(self):
        for row in self.rows:
            name = self.fixtures.get(row['doctype'])
            if not name:
                continue
            mode = row['controller_refuses_delete']
            if mode in ('always', 'while_referenced'):
                with self.assertRaises(frappe.ValidationError, msg=row['doctype']):
                    frappe.delete_doc(row['doctype'], name, force=True, ignore_permissions=True)
                self.assertTrue(frappe.db.exists(row['doctype'], name), row['doctype'])
            elif mode == 'never':
                frappe.db.savepoint('matrix_delete')
                frappe.delete_doc(row['doctype'], name, force=True, ignore_permissions=True)
                self.assertFalse(frappe.db.exists(row['doctype'], name), row['doctype'])
                frappe.db.rollback(save_point='matrix_delete')
                self.assertTrue(frappe.db.exists(row['doctype'], name), row['doctype'])

    def test_an_unreferenced_conversation_is_deletable_by_administrator(self):
        """`while_referenced` means exactly that: with a run hanging off it the controller
        refuses, without one it does not."""
        frappe.db.savepoint('matrix_conversation')
        name = insert({'doctype': 'DS Conversation', 'title': 'matrix unreferenced ' + self.tag})
        frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists('DS Conversation', name))
        frappe.db.rollback(save_point='matrix_conversation')

    def test_the_audit_report_is_refused_to_a_member_and_served_to_a_manager(self):
        """The report itself demands a date window, so the refusal has to be the role gate and
        not a missing filter: the same filters are passed both times."""
        from frappe.desk.query_report import run
        window = {'from_date': '2026-01-01', 'to_date': '2026-12-31'}
        frappe.set_user(self.users['member'])
        try:
            with self.assertRaises(frappe.PermissionError):
                run('DS Agent Audit', filters=window)
        finally:
            frappe.set_user('Administrator')
        frappe.set_user(self.users['manager'])
        try:
            self.assertIn('result', run('DS Agent Audit', filters=window))
        finally:
            frappe.set_user('Administrator')
