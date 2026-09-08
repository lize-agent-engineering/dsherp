"""What happens when a run runs out of budget, and who is allowed to say so.

Before this slice, hitting a model budget raised a plain validation error and the run ended
as `Failed`, indistinguishable from a broken tool or a provider outage. It is now its own
terminal state — but only ever as a **ruling the server makes from facts it wrote itself**.

Two properties are load-bearing and both are asserted here by behaviour:

- The refusal persists its fact and **leaves the run `Running`**. Writing a terminal status
  from inside a refusal would be rolled back with the request, and the worker's own
  `finish_run` would then be refused as "运行凭据失效".
- `BudgetExceeded` cannot be claimed from outside. A worker that could pass it would be able
  to relabel any failure as an expense.
"""
import hashlib
import json
import secrets

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from dsherp_bridge import context_execution as execution
from dsherp_bridge import context_permissions

ACTOR = 'native-budget@example.invalid'
PAGE = json.dumps({'schema_version': 1, 'page_type': 'unknown', 'route': []})


class BudgetCase(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Budget',
                            'user_type': 'System User', 'send_welcome_email': 0,
                            'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)
        frappe.db.commit()

    def _running_run(self, domain='query'):
        """A claimed run, built the way `claim_run` leaves one: Running, with a live lease and
        a capability only the caller knows."""
        conversation = frappe.get_doc({'doctype': 'DS Conversation',
                                       'title': 'budget'}).insert(ignore_permissions=True)
        # Owner is stamped from the session on insert, and `_actor` reads the conversation as
        # the run's owner — so both rows have to belong to the same synthetic member.
        frappe.db.set_value('DS Conversation', conversation.name, 'owner', ACTOR)
        capability = secrets.token_urlsafe(32)
        run = frappe.get_doc({
            'doctype': 'DS Model Run', 'conversation': conversation.name, 'domain': domain,
            'owner': ACTOR, 'status': 'Running', 'question': '预算测试', 'page_context': PAGE,
            'sources': '[]', 'runtime_revision': 'a' * 64,
            'permission_revision': context_permissions.run_revision(ACTOR, domain),
            'capability_hash': hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at': add_to_date(now_datetime(), minutes=3),
        }).insert(ignore_permissions=True)
        frappe.db.set_value('DS Model Run', run.name, 'owner', ACTOR)
        return {'run_id': run.name, 'capability': capability}

    def _call(self, cap, **overrides):
        from dsherp_bridge.run_budget import budget
        plan = budget('query')
        payload = {'input_bytes': 100, 'max_output_tokens': 1024, 'provider': plan['provider'],
                   'model': plan['model'], 'purpose': 'conversation', 'runtime_revision': 'a' * 64,
                   'domain': 'query', 'claimed_budget': plan}
        payload.update(overrides)
        return execution.reserve_model_call(**cap, **payload)

    def _kinds(self, run_id):
        return frappe.get_all('DS Run Event', filters={'run': run_id}, pluck='kind',
                              limit_page_length=0)


class TestBudgetRefusal(BudgetCase):
    def test_reserve_over_budget_persists_the_fact_and_keeps_the_run_running(self):
        """The fact must outlive the refusal — it is what `finish_run` later judges on."""
        cap = self._running_run()
        from dsherp_bridge.run_budget import budget
        plan = budget('query')
        with self.assertRaises(frappe.ValidationError) as raised:
            self._call(cap, input_bytes=plan['model_max_input_bytes_per_call'] + 1)
        self.assertEqual(str(raised.exception), execution.BUDGET_MESSAGE)
        self.assertIn('budget_exceeded', self._kinds(cap['run_id']))
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'status'), 'Running')

    def test_the_event_names_which_limit_was_hit_and_by_how_much(self):
        cap = self._running_run()
        from dsherp_bridge.run_budget import budget
        plan = budget('query')
        with self.assertRaises(frappe.ValidationError):
            self._call(cap, max_output_tokens=plan['model_max_output_tokens_per_call'] + 1)
        payload = json.loads(frappe.get_all(
            'DS Run Event', filters={'run': cap['run_id'], 'kind': 'budget_exceeded'},
            fields=['payload'], limit_page_length=1)[0]['payload'])
        self.assertEqual(payload['limit'], 'model_max_output_tokens_per_call')
        self.assertEqual(payload['allowed'], plan['model_max_output_tokens_per_call'])
        self.assertEqual(payload['used'], plan['model_max_output_tokens_per_call'] + 1)

    def test_a_broken_runtime_is_still_a_plain_refusal_not_a_budget_stop(self):
        """A wrong provider or a nonsensical size is a broken runtime, not an expense."""
        cap = self._running_run()
        with self.assertRaises(frappe.ValidationError):
            self._call(cap, model='not-the-configured-model')
        self.assertNotIn('budget_exceeded', self._kinds(cap['run_id']))

    def test_the_cumulative_call_budget_is_a_budget_stop(self):
        cap = self._running_run()
        from dsherp_bridge.run_budget import budget
        plan = budget('query')
        frappe.db.set_value('DS Model Run', cap['run_id'], 'model_calls', plan['model_max_calls'])
        with self.assertRaises(frappe.ValidationError):
            self._call(cap)
        self.assertIn('budget_exceeded', self._kinds(cap['run_id']))


class TestBudgetRuling(BudgetCase):
    def _finish(self, cap, **overrides):
        payload = {'status': 'Failed', 'error': '业务运行失败：RuntimeError'}
        payload.update(overrides)
        return execution.finish_run(**cap, **payload)

    def test_finish_turns_failed_into_budget_exceeded_with_the_readable_message(self):
        cap = self._running_run()
        from dsherp_bridge.run_budget import budget
        with self.assertRaises(frappe.ValidationError):
            self._call(cap, input_bytes=budget('query')['model_max_input_bytes_per_call'] + 1)
        self.assertEqual(self._finish(cap)['status'], 'BudgetExceeded')
        stored = frappe.db.get_value('DS Model Run', cap['run_id'], ['status', 'error'], as_dict=True)
        self.assertEqual(stored.status, 'BudgetExceeded')
        self.assertEqual(stored.error, execution.BUDGET_MESSAGE)

    def test_a_loop_ruling_wins_over_a_budget_one_because_it_explains_more(self):
        cap = self._running_run()
        from dsherp_bridge import context_events as events
        events.record(cap['run_id'], 'budget_exceeded', {'limit': 'model_max_calls'})
        events.record(cap['run_id'], 'loop_detected', {'tool': 'erp_read_record'})
        self.assertEqual(self._finish(cap)['status'], 'BudgetExceeded')
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'error'),
                         execution.LOOP_MESSAGE)

    def test_a_plain_failure_is_still_failed(self):
        cap = self._running_run()
        self.assertEqual(self._finish(cap)['status'], 'Failed')
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'error'),
                         '业务运行失败：RuntimeError')

    def test_time_budget_path_requires_both_the_runner_event_and_the_server_clock(self):
        """The one limit only the runner observes, so it is believed only when the server's
        own clock agrees. A runtime that reported a timeout it never hit would otherwise
        relabel an ordinary failure as an expense."""
        cap = self._running_run()
        from dsherp_bridge import context_events as events
        events.record(cap['run_id'], 'runtime_failed', {'reason': 'run_total_exceeded'},
                      source='worker')
        events.record(cap['run_id'], 'claimed', {'domain': 'query'})
        self.assertEqual(self._finish(cap)['status'], 'Failed',
                         'the run was claimed seconds ago; the clock does not agree')
        cap = self._running_run()
        events.record(cap['run_id'], 'runtime_failed', {'reason': 'run_total_exceeded'},
                      source='worker')
        name = events.record(cap['run_id'], 'claimed', {'domain': 'query'})
        from dsherp_bridge.run_budget import budget
        frappe.db.set_value('DS Run Event', name, 'recorded_at',
                            add_to_date(now_datetime(), seconds=-budget('query')['run_total_seconds'] - 10),
                            update_modified=False)
        self.assertEqual(self._finish(cap)['status'], 'BudgetExceeded')
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'error'),
                         execution.TIME_MESSAGE)

    def test_the_runner_alone_cannot_claim_a_timeout(self):
        cap = self._running_run()
        from dsherp_bridge import context_events as events
        events.record(cap['run_id'], 'runtime_failed', {'reason': 'run_total_exceeded'},
                      source='worker')
        self.assertEqual(self._finish(cap)['status'], 'Failed',
                         'no claimed event means no server-side clock to agree with')

    def test_external_caller_cannot_pass_budget_exceeded_directly(self):
        cap = self._running_run()
        with self.assertRaises(frappe.ValidationError):
            execution.finish_run(**cap, status='BudgetExceeded', error='我说的')
        self.assertEqual(frappe.db.get_value('DS Model Run', cap['run_id'], 'status'), 'Running')

    def test_a_runner_cannot_write_the_facts_this_ruling_reads(self):
        """`budget_exceeded` and `loop_detected` are server kinds. A runner able to write them
        could make any failure look like an expense — and, with the time-budget branch, could
        do it without the server's clock ever agreeing."""
        from dsherp_bridge import context_events as events
        for kind in ('budget_exceeded', 'loop_detected'):
            with self.assertRaises(frappe.ValidationError):
                events.record_many(self._running_run()['run_id'],
                                   [{'kind': kind, 'source': 'runner', 'payload': {}}])

    def test_budget_exceeded_runs_are_billed_like_other_finished_runs(self):
        """The most expensive kind of run there is must not be billed as zero.

        `finish_run` used to settle usage for three statuses spelled out by hand; a fourth
        terminal state added later would silently skip metering, and nothing else in the
        system can fill that hole in afterwards — the events are the only record.
        """
        cap = self._running_run()
        from dsherp_bridge import context_events as events
        events.record(cap['run_id'], 'claimed', {'domain': 'query'})
        events.record(cap['run_id'], 'model_response', {
            'usage': {'prompt_tokens': 700, 'completion_tokens': 120}}, source='runner')
        events.record(cap['run_id'], 'budget_exceeded', {'limit': 'model_max_calls'})
        self.assertEqual(self._finish(cap)['status'], 'BudgetExceeded')
        stored = frappe.db.get_value('DS Model Run', cap['run_id'],
                                     ['actual_input_tokens', 'actual_output_tokens', 'duration_ms'],
                                     as_dict=True)
        self.assertEqual(stored.actual_input_tokens, 700)
        self.assertEqual(stored.actual_output_tokens, 120)
        self.assertIsNotNone(stored.duration_ms)
