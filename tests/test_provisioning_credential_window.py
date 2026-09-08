"""A key handed out by provisioning must come with a recorded window, on both sides.

The business Site refuses a key it has no record of (S2), and the platform refuses a binding
whose credential window it cannot see: `desk_identity` finds `credential_expires_at` empty,
calls it expired, and its fallback then refuses because `credential_erp_user` is empty too.
Both refusals are correct. What was wrong is that provisioning handed out keys through
`generate_keys`, which writes no window anywhere - so a Site built from zero could never
complete an SSO round trip, and the failure surfaced as a bare 403 on the callback.

`infra/run_validation_provision.py` already said this in its own comment ("a key with no
recorded window is refused by the Site (S2)"); the creation path had not caught up."""
from pathlib import Path

from infra import bind_identity

ROOT = Path(__file__).resolve().parents[1]
WINDOW_FIELDS = ('credential_issued_at', 'credential_expires_at', 'credential_erp_user', 'credential_version')


def _profile(**extra):
    return {'user': 'dsherp-reader@example.invalid', 'site': 'dsherp-validation.localhost',
            'api_key': 'k', 'api_secret': 's', **extra}


def test_a_membership_carries_the_window_the_business_site_recorded():
    alpha = _profile(expires_at='2026-09-08 07:20:46', version=1)
    beta = _profile(user='beta-reader@example.invalid', site='dsherp-beta.localhost',
                    expires_at='2026-09-08 07:20:47', version=1)
    rows = bind_identity.memberships(alpha, beta)
    assert [row['enterprise'] for row in rows] == ['alpha', 'beta']
    for row, profile in zip(rows, (alpha, beta)):
        assert row['erp_user'] == profile['user']
        assert row['credential_erp_user'] == profile['user'], '窗口属于这个业务用户，不是别人'
        assert row['credential_expires_at'] == profile['expires_at'], '两边记同一个窗口，不是各写各的'
        assert row['credential_version'] == profile['version']
        assert row['credential_issued_at']


def test_a_profile_without_a_window_is_refused_instead_of_written_as_empty():
    """Empty window fields are exactly what made a from-zero stack unable to log in. Writing
    them as empty again would reproduce that silently, so the binding refuses to be built."""
    for missing in ('expires_at', 'version'):
        profile = _profile(expires_at='2026-09-08 07:20:46', version=1)
        del profile[missing]
        try:
            bind_identity.memberships(profile, _profile(expires_at='x', version=1))
        except ValueError as error:
            assert missing in str(error)
        else:
            raise AssertionError(f'缺少 {missing} 的凭据档案被接受了')


def test_every_business_credential_provisioning_writes_a_window():
    """The three scripts that create a business user for the platform to borrow from all have
    to go through the Site's credential module; `generate_keys` writes no window."""
    for name in ('provision_validation_site.py', 'initialize_daily_synthetic.py', 'seed_identity.py'):
        source = (ROOT / 'infra' / name).read_text()
        assert 'credentials.issue' in source or 'credential_window' in source, name
