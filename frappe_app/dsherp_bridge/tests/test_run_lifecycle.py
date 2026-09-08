"""A run that has ended stays ended — including the most expensive way to end.

`ds_model_run.TERMINAL` is what makes ruling #3 / G7 real on the Document path, and it is a
plain tuple: forgetting to add a new terminal state there turns that state into the one kind
of finished run anybody could rewrite, and **no existing test goes red** —
`tests/test_audit_immutability.py` only asserts the source mentions `TERMINAL` and
`frappe.throw`, never what is inside the tuple. So the tuple's contents are asserted here,
by behaviour, one state at a time.
"""
import hashlib
import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from dsherp_bridge.doctype.ds_model_run.ds_model_run import TERMINAL

PAGE = json.dumps({'schema_version': 1, 'page_type': 'unknown', 'route': []})


class TestRunTerminalStates(IntegrationTestCase):
    def _run(self, status):
        conversation = frappe.get_doc({'doctype': 'DS Conversation', 'title': 'lifecycle'}).insert(
            ignore_permissions=True)
        return frappe.get_doc({
            'doctype': 'DS Model Run', 'conversation': conversation.name, 'domain': 'query',
            'status': status, 'question': 'lifecycle', 'page_context': PAGE, 'sources': '[]',
            'permission_revision': 'rev', 'capability_hash': hashlib.sha256(b'cap').hexdigest(),
            'expires_at': add_to_date(now_datetime(), minutes=3),
        }).insert(ignore_permissions=True)

    def test_a_budget_exceeded_run_cannot_be_rewritten_through_the_document_path(self):
        run = self._run('BudgetExceeded')
        run.answer = '被改写的答复'
        with self.assertRaises(frappe.ValidationError):
            run.save(ignore_permissions=True)

    def test_every_terminal_state_refuses_a_rewrite(self):
        for status in TERMINAL:
            with self.subTest(status=status):
                run = self._run(status)
                run.error = 'rewritten'
                with self.assertRaises(frappe.ValidationError):
                    run.save(ignore_permissions=True)

    def test_budget_exceeded_is_declared_terminal(self):
        """The behaviour above is what matters; this names the omission that would cause it."""
        self.assertIn('BudgetExceeded', TERMINAL)

    def test_the_status_field_accepts_the_new_state(self):
        options = frappe.get_meta('DS Model Run').get_field('status').options.split('\n')
        self.assertIn('BudgetExceeded', options)
        for state in TERMINAL:
            self.assertIn(state, options)

    def test_the_new_columns_exist_and_start_empty(self):
        run = self._run('Running')
        stored = frappe.db.get_value('DS Model Run', run.name, ['prompt_version', 'sampling'], as_dict=True)
        self.assertIn(stored.prompt_version, (None, ''))
        self.assertIn(stored.sampling, (None, ''))

    def test_a_running_run_may_still_be_written_by_the_server(self):
        """The guard is about ended runs; an in-flight one must stay writable."""
        run = self._run('Running')
        frappe.db.set_value('DS Model Run', run.name,
                            {'prompt_version': '1', 'sampling': 'provider-default'})
        stored = frappe.db.get_value('DS Model Run', run.name, ['prompt_version', 'sampling'], as_dict=True)
        self.assertEqual((stored.prompt_version, stored.sampling), ('1', 'provider-default'))
