"""A run that asks the same thing three times in a row is stopped.

The judgement itself is a pure function with its own host tests (`tests/test_loop_guard.py`);
what is asserted here is everything the Site adds to it: which events are read, that a
**refused** call counts too, and how the run actually comes to an end.

That last part is the deviation the plan records. `run_tool` runs inside an HTTP request and
cannot kill a container that is still going, so the third call is refused and the fact is
written down; the next `reserve_model_call` then refuses every model call, the runtime's
guard disables itself, the container exits, and `finish_run` reads the fact and rules
`BudgetExceeded`. The test walks that whole path rather than asserting the refusal alone.
"""
import hashlib
import json
import secrets

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from dsherp_bridge import context_execution as execution
from dsherp_bridge import context_permissions

ACTOR = 'native-loop@example.invalid'
ITEM = 'DSHERP-LOOP-ITEM'
PAGE = json.dumps({'schema_version': 1, 'page_type': 'unknown', 'route': []})


class TestLoopDetection(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        roles = [role for role in ('System Manager', 'Item Manager', 'Stock User', 'Sales User')
                 if frappe.db.exists('Role', role)]
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Loop',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': role} for role in roles]}).insert(ignore_permissions=True)
        else:
            user = frappe.get_doc('User', ACTOR)
            held = {row.role for row in user.roles}
            for role in roles:
                if role not in held:
                    user.append('roles', {'role': role})
            user.save(ignore_permissions=True)
        if not frappe.db.exists('Item', ITEM):
            frappe.get_doc({'doctype': 'Item', 'item_code': ITEM, 'item_name': '循环检测合成物料',
                            'item_group': frappe.db.get_value('Item Group', {'is_group': 0}, 'name'),
                            'stock_uom': frappe.db.get_single_value('Stock Settings', 'stock_uom') or 'Nos',
                            'is_stock_item': 0}).insert(ignore_permissions=True)
        if not frappe.db.exists('DS Doctype Policy', 'Item'):
            frappe.get_doc({'doctype': 'DS Doctype Policy', 'target_doctype': 'Item', 'enabled': 1,
                            'allow_read': 1, 'allow_create': 0, 'allow_update': 0, 'allow_submit': 0,
                            'allow_cancel': 0, 'allow_fill': 0,
                            'change_reason': '循环检测测试'}).insert(ignore_permissions=True)
        frappe.db.commit()

    def _running_run(self):
        conversation = frappe.get_doc({'doctype': 'DS Conversation',
                                       'title': 'loop'}).insert(ignore_permissions=True)
        frappe.db.set_value('DS Conversation', conversation.name, 'owner', ACTOR)
        capability = secrets.token_urlsafe(32)
        run = frappe.get_doc({
            'doctype': 'DS Model Run', 'conversation': conversation.name, 'domain': 'query',
            'status': 'Running', 'question': '循环检测', 'page_context': PAGE, 'sources': '[]',
            'runtime_revision': 'a' * 64,
            'permission_revision': context_permissions.run_revision(ACTOR, 'query'),
            'capability_hash': hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at': add_to_date(now_datetime(), minutes=3),
        }).insert(ignore_permissions=True)
        frappe.db.set_value('DS Model Run', run.name, 'owner', ACTOR)
        return {'run_id': run.name, 'capability': capability}

    def _kinds(self, run_id):
        return frappe.get_all('DS Run Event', filters={'run': run_id}, pluck='kind',
                              order_by='seq asc', limit_page_length=0)

    def test_third_identical_call_is_refused_and_recorded(self):
        cap = self._running_run()
        arguments = {'doctype': 'Item'}
        for _ in range(2):
            self.assertTrue(execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments))
        with self.assertRaises(frappe.ValidationError) as raised:
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        self.assertEqual(str(raised.exception), execution.LOOP_MESSAGE)
        self.assertEqual(self._kinds(cap['run_id']).count('tool_call'), 2,
                         'the third call must not have run')
        self.assertIn('loop_detected', self._kinds(cap['run_id']))

    def test_a_different_argument_breaks_the_streak(self):
        cap = self._running_run()
        for arguments in ({'doctype': 'Item'}, {'doctype': 'Item'}, {'doctype': 'DS Conversation'},
                          {'doctype': 'Item'}):
            try:
                execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
            except frappe.PermissionError:
                # DS Conversation is not a governed business DocType; the refusal is the point
                # — it still lands between the two Item reads and breaks the run.
                pass
        self.assertNotIn('loop_detected', self._kinds(cap['run_id']))

    def test_a_refused_call_counts_toward_the_streak(self):
        """The most typical loop of all: the model resending the very call just rejected.

        A refused call writes `tool_refused` and no `tool_call`, so a guard that read only
        `tool_call` would let this run forever — every round a paid model call.
        """
        cap = self._running_run()
        arguments = {'doctype': 'DS Conversation'}
        for _ in range(2):
            with self.assertRaises(frappe.PermissionError):
                execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        with self.assertRaises(frappe.ValidationError) as raised:
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        self.assertEqual(str(raised.exception), execution.LOOP_MESSAGE)
        self.assertEqual(self._kinds(cap['run_id']).count('tool_refused'), 2)
        self.assertIn('loop_detected', self._kinds(cap['run_id']))

    def test_reserve_after_loop_detected_is_refused_so_the_container_stops(self):
        cap = self._running_run()
        arguments = {'doctype': 'Item'}
        for _ in range(2):
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        with self.assertRaises(frappe.ValidationError):
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        from dsherp_bridge.run_budget import budget
        plan = budget('query')
        with self.assertRaises(frappe.ValidationError) as raised:
            execution.reserve_model_call(
                **cap, input_bytes=100, max_output_tokens=1024, provider=plan['provider'],
                model=plan['model'], purpose='conversation', runtime_revision='a' * 64,
                domain='query', claimed_budget=plan)
        self.assertEqual(str(raised.exception), execution.LOOP_MESSAGE)
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'model_calls') or 0, 0,
                         'a poisoned reservation must not be charged')

    def test_the_run_ends_as_budget_exceeded_with_the_loop_wording(self):
        cap = self._running_run()
        arguments = {'doctype': 'Item'}
        for _ in range(2):
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        with self.assertRaises(frappe.ValidationError):
            execution.run_tool(**cap, tool='erp_read_schema', arguments=arguments)
        self.assertEqual(execution.finish_run(**cap, status='Failed',
                                              error='业务运行失败：RuntimeError')['status'],
                         'BudgetExceeded')
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'error'),
                         execution.LOOP_MESSAGE)

    def test_the_refusal_event_records_the_arguments_the_streak_was_built_from(self):
        """Without them a refusal cannot be told apart from another refusal of the same tool,
        which is exactly what the guard has to do."""
        cap = self._running_run()
        with self.assertRaises(frappe.PermissionError):
            execution.run_tool(**cap, tool='erp_read_schema', arguments={'doctype': 'DS Conversation'})
        payload = json.loads(frappe.get_all(
            'DS Run Event', filters={'run': cap['run_id'], 'kind': 'tool_refused'},
            fields=['payload'], limit_page_length=1)[0]['payload'])
        self.assertEqual(payload['arguments'], {'doctype': 'DS Conversation'})
