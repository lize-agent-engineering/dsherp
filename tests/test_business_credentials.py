"""A member's business credential is short-lived, and the two sides agree on when it dies.

The platform holds the credential it was given; the business Site decides whether that
credential is still alive. Both read the same policy module, mirrored the way usage.py is,
so "expired" can never mean two different things on the two Sites."""
import datetime
import json
from pathlib import Path

import pytest

from dsherp import business_credentials as policy

ROOT = Path(__file__).resolve().parents[1]
MIRROR = ROOT / "frappe_app/dsherp_bridge/business_credentials.py"
BRIDGE = ROOT / "frappe_app/dsherp_bridge"
DEFINITION = BRIDGE / "dsherp_bridge/doctype/ds_business_credential/ds_business_credential.json"
MEMBERSHIP = ROOT / "frappe_app/dsherp_platform/platform/doctype/ds_membership/ds_membership.json"

NOW = datetime.datetime(2026, 9, 7, 12, 0, 0)


def _row(**overrides):
    row = {"api_key": "abc", "issued_at": NOW - datetime.timedelta(hours=1),
           "expires_at": NOW + datetime.timedelta(hours=11), "revoked": 0, "version": 3}
    row.update(overrides)
    return row


def test_a_credential_inside_its_window_is_usable_and_needs_no_renewal():
    assert policy.usable(_row(), NOW) is True
    assert policy.renewal_due(_row(), NOW) is False
    assert policy.state(_row(), NOW) == "fresh"


def test_a_credential_close_to_expiry_is_still_usable_but_renewed_on_the_next_login():
    """Renewal happens while the old one still works, so a login never leaves a member
    holding a credential that dies mid-request."""
    row = _row(expires_at=NOW + datetime.timedelta(hours=1))
    assert policy.usable(row, NOW) is True
    assert policy.renewal_due(row, NOW) is True
    assert policy.state(row, NOW) == "renewable"


def test_an_expired_or_revoked_credential_is_refused_rather_than_renewed_silently():
    expired = _row(expires_at=NOW - datetime.timedelta(seconds=1))
    revoked = _row(revoked=1)
    assert policy.usable(expired, NOW) is False and policy.state(expired, NOW) == "expired"
    assert policy.usable(revoked, NOW) is False and policy.state(revoked, NOW) == "revoked"
    assert policy.renewal_due(expired, NOW) is True and policy.renewal_due(revoked, NOW) is True


def test_no_credential_at_all_is_named_missing_rather_than_treated_as_valid():
    assert policy.usable(None, NOW) is False
    assert policy.state(None, NOW) == "missing"
    assert policy.renewal_due(None, NOW) is True


def test_times_arriving_as_strings_are_read_the_same_way_as_datetimes():
    """Frappe hands back strings over HTTP and datetimes in process; both sides read one policy."""
    row = _row(expires_at="2026-09-07 23:00:00", issued_at="2026-09-07 11:00:00")
    assert policy.usable(row, NOW) is True and policy.state(row, NOW) == "fresh"


def test_the_window_is_a_stated_number_of_hours_not_a_forever_credential():
    assert 1 <= policy.TTL_HOURS <= 24 and 0 < policy.RENEW_WITHIN_HOURS < policy.TTL_HOURS
    issued = NOW
    assert policy.expiry(issued) == issued + datetime.timedelta(hours=policy.TTL_HOURS)


def test_the_two_sides_run_the_same_policy_file():
    ours = (ROOT / "dsherp/business_credentials.py").read_text()
    theirs = MIRROR.read_text()
    assert ours.split('"""', 2)[2] == theirs.split('"""', 2)[2], "policy drifted between host and Site"


def test_the_business_site_records_which_credential_it_issued_and_keeps_that_record():
    definition = json.loads(DEFINITION.read_text())
    fields = {field["fieldname"]: field for field in definition["fields"]}
    assert definition["track_changes"] == 1
    assert fields["user"]["fieldtype"] == "Link" and fields["user"]["unique"] == 1
    for name in ("api_key", "issued_at", "expires_at", "version", "revoked"):
        assert name in fields, name
    assert "api_secret" not in fields, "the Site already stores the secret on User; not twice"
    for permission in definition.get("permissions", []):
        assert not permission.get("delete")


def test_the_platform_stores_when_the_credential_it_holds_dies():
    fields = {field["fieldname"]: field for field in json.loads(MEMBERSHIP.read_text())["fields"]}
    assert fields["credential_expires_at"]["fieldtype"] == "Datetime"
    assert fields["credential_version"]["fieldtype"] == "Int"
    assert fields["api_secret"]["fieldtype"] == "Password"


def test_a_business_user_answers_to_one_platform_member_and_the_database_is_what_guarantees_it():
    """Frappe keeps one api_secret per User. Two platform members bound to the same business
    user would take turns invalidating each other. A read-then-write check in the controller
    lets two concurrent transactions both through (R2), so the guarantee is a unique index on
    a key the controller fills only while the binding is enabled. The concurrent proof against
    the real Site is tests/integration/test_membership_binding.py."""
    fields = {field["fieldname"]: field for field in json.loads(MEMBERSHIP.read_text())["fields"]}
    assert fields["active_binding"]["unique"] == 1 and fields["active_binding"]["fieldtype"] == "Data"
    controller = (ROOT / "frappe_app/dsherp_platform/platform/doctype/ds_membership/ds_membership.py").read_text()
    assert "active_binding" in controller and "else None" in controller, "disabled rows must not collide on an empty key"
    patches = (ROOT / "frappe_app/dsherp_platform/patches.txt").read_text()
    pre = patches.split("[pre_model_sync]", 1)[1].split("[post_model_sync]", 1)[0]
    assert "binding_uniqueness" in pre, "duplicates must be resolved before the index is created"


def test_the_binding_version_is_a_counter_not_a_hash_of_the_row():
    """A grant carries the binding version. A content hash would give a disabled-then-re-enabled
    binding its old number back, and an old grant with it (R8); the repository rule is also
    plain: no hash stands in for a version number."""
    fields = {field["fieldname"]: field for field in json.loads(MEMBERSHIP.read_text())["fields"]}
    assert fields["binding_version"]["fieldtype"] == "Int"
    api = PLATFORM_API.read_text()
    helper = api.split("def binding_version", 1)[1].split("\ndef ", 1)[0]
    assert "hashlib" not in helper and "binding_version" in helper
    controller = (ROOT / "frappe_app/dsherp_platform/platform/doctype/ds_membership/ds_membership.py").read_text()
    counting = controller.split("def _count_the_binding", 1)[1]
    assert "+ 1" in counting and "BINDING_FIELDS" in counting


def test_the_business_site_refuses_a_key_whose_window_has_passed_where_sso_is_enforced():
    """The window governs credentials the platform borrows, which only exist where every
    session comes through the platform. A Site that still takes passwords (development)
    records windows and reports them, but does not turn keys away on them."""
    source = (BRIDGE / "sso.py").read_text()
    body = source.split("def validate_session", 1)[1]
    assert "_machine_authenticated()" in body
    gate = body.split("_machine_authenticated():", 1)[1].split("return", 1)[0]
    assert "_password_login_disabled()" in gate and "credentials.require(user)" in gate


def test_the_login_hands_the_platform_a_current_credential_instead_of_a_stored_forever_one():
    source = (BRIDGE / "sso.py").read_text()
    callback = source.split("def callback", 1)[1].split("\ndef ", 1)[0]
    assert "credentials" in callback and "accept_credential" in source


PLATFORM_API = ROOT / "frappe_app/dsherp_platform/api.py"


def test_the_platform_refuses_to_read_with_a_credential_whose_window_has_passed():
    source = PLATFORM_API.read_text()
    binding = source.split("def _binding", 1)[1].split("\n@", 1)[0]
    assert "credential_expires_at" in binding or "business_credentials" in binding
    assert "usable" in binding or "state" in binding


def test_the_platform_accepts_a_credential_only_after_proving_it_belongs_to_the_bound_user():
    """A pushed credential is checked against the business Site itself before it is stored,
    so a wrong or stale pair fails at delivery rather than on the member's next read."""
    source = PLATFORM_API.read_text()
    assert "def accept_credential" in source
    body = source.split("def accept_credential", 1)[1].split("\n@frappe.whitelist", 1)[0]
    assert "get_logged_user" in body and "erp_user" in body
    assert "methods=['POST']" in source.split("def accept_credential", 1)[0][-120:]


def test_a_stale_credential_is_named_as_such_rather_than_reported_as_a_permission_problem():
    source = PLATFORM_API.read_text()
    business = source.split("def _business", 1)[1].split("\n@", 1)[0]
    assert "401" in business and "重新登录" in business


def test_disabling_a_membership_kills_the_credential_it_lent_out():
    source = PLATFORM_API.read_text() + (ROOT / "frappe_app/dsherp_platform/platform/doctype/ds_membership/ds_membership.py").read_text()
    assert "revoke_credential" in source


from tests.test_admin_cli import RELEASE, SnapshotBench, _tenant_row   # noqa: E402
from tests.test_admin_cli import host as host_runtime  # noqa: F401,E402


class CredentialBench(SnapshotBench):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scripts = []
        self.report = {
            "now": "2026-09-07 12:00:00",
            "service": ["Administrator", "runtime@acme.tenant.example.com"],
            "rows": [{"user": "alice@example.invalid", "api_key": "K1", "issued_at": "2026-09-07 11:00:00",
                      "expires_at": "2026-09-07 23:00:00", "version": 2, "revoked": 0, "legacy": 0},
                     {"user": "bob@example.invalid", "api_key": "K2", "issued_at": "2026-09-06 11:00:00",
                      "expires_at": "2026-09-06 23:00:00", "version": 1, "revoked": 0, "legacy": 1}],
            "unrecorded": ["carol@example.invalid"],
        }

    def python(self, site, body, timeout=900):
        self.scripts.append((site, body))
        if "DSHERP_CREDENTIALS " in body:
            return "DSHERP_CREDENTIALS " + json.dumps(self.report) + "\n"
        if "DSHERP_CREDENTIAL_ISSUED " in body:
            return "DSHERP_CREDENTIAL_ISSUED " + json.dumps(
                {"api_key": "K3", "api_secret": "S3", "expires_at": "2026-09-08 00:00:00", "version": 3}) + "\n"
        if "DSHERP_CREDENTIAL_STORED " in body:
            return "DSHERP_CREDENTIAL_STORED " + json.dumps({"membership": "m1", "enterprise": "acme"}) + "\n"
        return super().python(site, body, timeout=timeout)


def test_the_report_says_which_credentials_are_alive_and_which_keys_nobody_recorded(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = CredentialBench([])
    report = admin.credentials_report(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench)
    states = {row["user"]: row["state"] for row in report["credentials"]}
    assert states == {"alice@example.invalid": "fresh", "bob@example.invalid": "expired"}
    assert report["unrecorded"] == ["carol@example.invalid"], "an API key with no window is a finding"
    assert report["stale"] == 1


def test_the_report_judges_expiry_by_the_sites_clock_not_the_hosts(host_runtime):
    """The Site stores naive local times; judging them against the host's clock would call
    a live credential dead whenever the two disagree."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = CredentialBench([])
    bench.report["now"] = "2026-09-08 12:00:00"
    report = admin.credentials_report(RELEASE, "acme.tenant.example.com", bench_factory=lambda kind: bench)
    assert {row["state"] for row in report["credentials"]} == {"expired"}


def test_issuing_hands_the_pair_to_the_platform_and_never_prints_the_secret(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = CredentialBench([])
    report = admin.issue_credential(RELEASE, "acme.tenant.example.com", "alice@example.invalid",
                                    bench_factory=lambda kind: bench)
    assert report["version"] == 3 and report["expires_at"] == "2026-09-08 00:00:00"
    assert "S3" not in json.dumps(report), "a secret never travels through a report"
    assert report["stored"]["membership"] == "m1"
    sites = [site for site, _ in bench.scripts]
    assert RELEASE["platform_site"] in sites, "the platform is told, or it keeps calling with a dead key"


def test_a_login_on_an_expired_credential_still_refuses_a_binding_that_names_someone_new():
    """The round trip proves the platform holds a credential for the user it claims. When the
    credential has expired that proof is unavailable, so the last proof stands in - and only
    for the same business user. Otherwise an edited erp_user would log in unproved."""
    source = PLATFORM_API.read_text()
    body = source.split("def desk_identity", 1)[1].split("\n@frappe.whitelist", 1)[0]
    assert "credential_erp_user" in body and "PermissionError" in body
    fields = {field["fieldname"] for field in json.loads(MEMBERSHIP.read_text())["fields"]}
    assert "credential_erp_user" in fields
    stored = source.split("def accept_credential", 1)[1].split("\n@frappe.whitelist", 1)[0]
    assert "credential_erp_user=member.erp_user" in stored.replace(" ", "")


def test_a_finished_credential_reads_as_an_authentication_failure_not_a_permission_one():
    """401 is what tells the platform to renew; 403 reads as "this member may not", and made
    an expired login impossible to repair on the real stack."""
    source = (BRIDGE / "credentials.py").read_text()
    body = source.split("def require", 1)[1].split("\ndef ", 1)[0]
    assert "frappe.AuthenticationError" in body and "frappe.PermissionError" not in body
    platform = PLATFORM_API.read_text().split("def _business", 1)[1].split("\n@", 1)[0]
    assert "401" in platform and "CredentialStale" in platform
