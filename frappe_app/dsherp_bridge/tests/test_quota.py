"""Tenant quotas: off by default, and a refusal that is a limit rather than a fault.

Both quotas default to 0 = unlimited. That is a decision, not an oversight: without a real
usage distribution, a number switched on for everyone is a gate against ordinary work rather
than against abuse. What is built here is the mechanism and the wording; the values stay off
until a tenant asks for them.

The refusal is 429 and it happens **before a run row exists** — a refused request must not
leave a Queued run behind for the worker to pick up.

Counting and placement are asserted separately, and deliberately so: `send_message` opens
with `frappe.db.rollback()` to take its serialising row lock, which discards anything a test
wrote but did not commit. So the counts are asserted against `_check_quota` itself, with real
rows and the real query, and the placement is asserted through `send_message` with the check
stubbed — rather than committing `DS Model Run` rows that `on_trash` then refuses to let
anyone remove.

Rows written by one test method are still visible to the next one in the same class (the
rollback is per class), so every method that counts brings its own user or sets its limit to
what it seeded.
"""
import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase

from dsherp_bridge import context_api as api
from dsherp_bridge.context_api import QuotaExceededError
from dsherp_bridge.run_budget import QUOTA_DEFAULTS, quota

PAGE = {'schema_version': 1, 'page_type': 'unknown', 'route': []}
PLACEMENT_ACTOR = 'native-quota-placement@example.invalid'


class QuotaCase(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self._original = frappe.conf.get('dsherp_quota')
        self._had = 'dsherp_quota' in frappe.conf
        self.actor = self._member()
        # A live worker heartbeat, so a refusal here is the quota and never 503.
        frappe.cache().set_value('dsherp_worker_heartbeat', frappe.utils.now_datetime().isoformat(),
                                 expires_in_sec=600)

    def tearDown(self):
        if self._had:
            frappe.conf.dsherp_quota = self._original
        else:
            frappe.conf.pop('dsherp_quota', None)
        frappe.set_user('Administrator')
        super().tearDown()

    def _member(self):
        """A member of this method's own, so one method's rows never count towards another's."""
        email = f'native-quota-{uuid.uuid4().hex[:10]}@example.invalid'
        frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': 'Native Quota',
                        'user_type': 'System User', 'send_welcome_email': 0,
                        'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)
        return email

    def _run_row(self, user, *, calls=0, input_tokens=0, output_tokens=0, status='Succeeded'):
        conversation = frappe.get_doc({'doctype': 'DS Conversation',
                                       'title': 'quota'}).insert(ignore_permissions=True)
        frappe.db.set_value('DS Conversation', conversation.name, 'owner', user)
        run = frappe.get_doc({
            'doctype': 'DS Model Run', 'conversation': conversation.name, 'domain': 'query',
            'status': status, 'question': '额度', 'page_context': json.dumps(PAGE), 'sources': '[]',
        }).insert(ignore_permissions=True)
        frappe.db.set_value('DS Model Run', run.name,
                            {'owner': user, 'model_calls': calls,
                             'actual_input_tokens': input_tokens,
                             'actual_output_tokens': output_tokens})
        return run.name

    def _check(self, user=None):
        return api._check_quota(user or self.actor)


class TestQuotaConfiguration(QuotaCase):
    def test_quota_is_off_by_default(self):
        frappe.conf.pop('dsherp_quota', None)
        self.assertEqual(quota(), QUOTA_DEFAULTS)
        self.assertEqual(set(QUOTA_DEFAULTS.values()), {0})

    def test_zero_is_accepted_as_unlimited_while_negative_is_rejected(self):
        frappe.conf.dsherp_quota = {'user_daily_model_calls': 0}
        self.assertEqual(quota()['user_daily_model_calls'], 0)
        for invalid in ({'user_daily_model_calls': -1}, {'user_daily_model_calls': '5'},
                        {'user_daily_model_calls': True}, {'nosuch': 1}, ['not a dict']):
            frappe.conf.dsherp_quota = invalid
            with self.assertRaises(frappe.ValidationError):
                quota()

    def test_an_unlimited_quota_counts_nothing_at_all(self):
        self._run_row(self.actor, calls=5000, input_tokens=10 ** 9)
        frappe.conf.pop('dsherp_quota', None)
        self._check()

    def test_the_quota_keys_stay_out_of_the_container_plan(self):
        """`reserve_model_call` compares the claimed budget against `budget(domain)` key by
        key. A tenant quota in there would have to be copied into every hand-written plan
        fixture, and the container has no use for it."""
        from dsherp_bridge.run_budget import budget
        self.assertFalse(set(QUOTA_DEFAULTS) & set(budget('query')))


class TestDailyCallQuota(QuotaCase):
    def test_daily_call_quota_refuses_with_429_and_a_readable_reason(self):
        self._run_row(self.actor, calls=5)
        frappe.conf.dsherp_quota = {'user_daily_model_calls': 5}
        with self.assertRaises(QuotaExceededError) as raised:
            self._check()
        self.assertEqual(QuotaExceededError.http_status_code, 429)
        self.assertIn('今日模型调用已达上限', str(raised.exception))
        self.assertIn('5/5', str(raised.exception))

    def test_daily_quota_counts_model_calls_not_runs(self):
        """`model_calls` is charged by `reserve_model_call` and never refunded, so runs still
        in flight count — a user cannot get around the limit by leaving runs open."""
        for _ in range(4):
            self._run_row(self.actor, calls=1, status='Running')
        frappe.conf.dsherp_quota = {'user_daily_model_calls': 5}
        self._check()
        self.assertEqual(frappe.db.count('DS Model Run', {'owner': self.actor}), 4,
                         'four runs, four calls: the limit is on calls, not on runs')
        self._run_row(self.actor, calls=1, status='Running')
        with self.assertRaises(QuotaExceededError):
            self._check()

    def test_the_daily_quota_is_per_user(self):
        self._run_row(self._member(), calls=50)
        frappe.conf.dsherp_quota = {'user_daily_model_calls': 5}
        self._check()

    def test_yesterdays_calls_do_not_count_against_today(self):
        name = self._run_row(self.actor, calls=50)
        frappe.db.set_value('DS Model Run', name, 'creation',
                            frappe.utils.add_days(frappe.utils.now_datetime(), -1),
                            update_modified=False)
        frappe.conf.dsherp_quota = {'user_daily_model_calls': 5}
        self._check()


class TestQuotaPlacement(QuotaCase):
    """These go through `send_message`, whose opening `frappe.db.rollback()` discards
    anything this test wrote and did not commit — including a member created in `setUp`. So
    this class brings a committed one; a `User` row, unlike a `DS Model Run`, may be kept."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists('User', PLACEMENT_ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': PLACEMENT_ACTOR,
                            'first_name': 'Native Quota Placement', 'user_type': 'System User',
                            'send_welcome_email': 0,
                            'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        self.actor = PLACEMENT_ACTOR

    def test_quota_refusal_creates_no_run_row(self):
        """Placement, not counting: the check sits after the page context is validated and
        before the run is created, so a refused request leaves nothing for the worker."""
        before = frappe.db.count('DS Model Run')
        original = api._check_quota

        def refuse(user):
            raise QuotaExceededError('今日模型调用已达上限（5/5 次）')

        api._check_quota = refuse
        try:
            frappe.set_user(self.actor)
            with self.assertRaises(QuotaExceededError):
                api.send_message('额度测试问题', PAGE, uuid.uuid4().hex)
        finally:
            api._check_quota = original
        self.assertEqual(frappe.db.count('DS Model Run'), before)
        self.assertEqual(frappe.db.count('DS Model Run', {'owner': self.actor}), 0)

    def test_without_a_quota_the_same_message_creates_its_run(self):
        """The other half of the previous test: nothing else about `send_message` changed."""
        frappe.conf.pop('dsherp_quota', None)
        frappe.set_user(self.actor)
        api.send_message('额度测试问题', PAGE, uuid.uuid4().hex)
        frappe.set_user('Administrator')
        self.assertEqual(frappe.db.count('DS Model Run', {'owner': self.actor}), 1)


class TestMonthlyTokenQuota(QuotaCase):
    def test_monthly_token_quota_uses_the_recorded_usage_fields(self):
        self._run_row(self.actor, input_tokens=900, output_tokens=100)
        frappe.conf.dsherp_quota = {'site_monthly_tokens': 1000}
        with self.assertRaises(QuotaExceededError) as raised:
            self._check()
        self.assertIn('本站本月 token 已达上限', str(raised.exception))
        self.assertIn('/1000', str(raised.exception))

    def test_the_monthly_message_says_the_number_is_a_lower_bound(self):
        """Only finished runs have written their tokens, so the figure understates the month.
        A person told "you have used X" needs to know X is not the whole story."""
        self._run_row(self.actor, input_tokens=2000)
        frappe.conf.dsherp_quota = {'site_monthly_tokens': 1000}
        with self.assertRaises(QuotaExceededError) as raised:
            self._check()
        self.assertIn('在飞运行尚未计入', str(raised.exception))


class TestMonthlyQuotaIsSiteWide(QuotaCase):
    def test_another_members_tokens_count_towards_the_same_monthly_quota(self):
        """The tenant is what is capped, so the count is not filtered by user."""
        self._run_row(self._member(), input_tokens=1000)
        frappe.conf.dsherp_quota = {'site_monthly_tokens': 1000}
        with self.assertRaises(QuotaExceededError):
            self._check()
