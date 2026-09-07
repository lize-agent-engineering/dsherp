"""Rotating a credential is an event with a version, a time, and a fingerprint - never a value.

The ledger exists so an operator can answer "when was this last changed, and is what runs now
the thing I rotated?" without anyone having to keep the secret around to compare."""
import datetime
import json
import os
import stat
from pathlib import Path

import pytest

from dsherp import rotation

NOW = datetime.datetime(2026, 9, 7, 12, 0, 0)


def test_an_entry_records_when_what_and_which_value_but_never_the_value():
    entry = rotation.entry(kind='provider', target='host', value='sk-super-secret',
                           previous='sk-old', version=2, at=NOW)
    body = json.dumps(entry)
    assert 'sk-super-secret' not in body and 'sk-old' not in body
    assert entry['fingerprint'] == rotation.fingerprint('sk-super-secret')
    assert entry['previous_fingerprint'] == rotation.fingerprint('sk-old')
    assert entry['version'] == 2 and entry['effective_at'] == '2026-09-07 12:00:00'
    assert entry['kind'] == 'provider' and entry['target'] == 'host'


def test_a_fingerprint_identifies_a_value_without_carrying_it():
    one = rotation.fingerprint('sk-a')
    assert one != rotation.fingerprint('sk-b') and len(one) == 16
    assert 'sk-a' not in one


def test_the_next_version_follows_the_last_entry_for_that_target_only():
    rows = [rotation.entry(kind='runtime', target='a.example', value='1', previous=None, version=1, at=NOW),
            rotation.entry(kind='runtime', target='a.example', value='2', previous='1', version=2, at=NOW),
            rotation.entry(kind='runtime', target='b.example', value='9', previous=None, version=1, at=NOW)]
    assert rotation.next_version(rows, 'runtime', 'a.example') == 3
    assert rotation.next_version(rows, 'runtime', 'b.example') == 2
    assert rotation.next_version(rows, 'provider', 'host') == 1
    assert rotation.latest(rows, 'runtime', 'a.example')['version'] == 2


def test_a_credential_is_named_fresh_ageing_or_overdue_by_its_own_window():
    rows = [rotation.entry(kind='provider', target='host', value='v', previous=None, version=1,
                           at=NOW - datetime.timedelta(days=10))]
    assert rotation.state(rows, 'provider', 'host', NOW) == 'fresh'
    old = [rotation.entry(kind='provider', target='host', value='v', previous=None, version=1,
                          at=NOW - datetime.timedelta(days=rotation.MAX_AGE_DAYS['provider'] - 5))]
    assert rotation.state(old, 'provider', 'host', NOW) == 'ageing'
    stale = [rotation.entry(kind='provider', target='host', value='v', previous=None, version=1,
                            at=NOW - datetime.timedelta(days=rotation.MAX_AGE_DAYS['provider'] + 1))]
    assert rotation.state(stale, 'provider', 'host', NOW) == 'overdue'


def test_a_credential_nobody_ever_rotated_says_so_instead_of_looking_fresh():
    assert rotation.state([], 'provider', 'host', NOW) == 'never'
    findings = rotation.findings([], {'provider': ['host'], 'runtime': ['a.example']}, NOW)
    assert len(findings) == 2 and all('从未登记' in item['note'] for item in findings)
    assert {item['state'] for item in findings} == {'never'}


def test_findings_name_the_overdue_ones_and_stay_quiet_about_the_fresh_ones():
    rows = [rotation.entry(kind='provider', target='host', value='v', previous=None, version=1, at=NOW),
            rotation.entry(kind='runtime', target='a.example', value='v', previous=None, version=1,
                           at=NOW - datetime.timedelta(days=rotation.MAX_AGE_DAYS['runtime'] + 3))]
    findings = rotation.findings(rows, {'provider': ['host'], 'runtime': ['a.example']}, NOW)
    assert [item['target'] for item in findings] == ['a.example']
    assert findings[0]['state'] == 'overdue' and findings[0]['age_days'] >= rotation.MAX_AGE_DAYS['runtime']


def test_the_ledger_is_appended_to_and_kept_private(tmp_path):
    path = tmp_path / 'rotations.json'
    rotation.record(path, rotation.entry(kind='provider', target='host', value='a', previous=None,
                                         version=1, at=NOW))
    rotation.record(path, rotation.entry(kind='provider', target='host', value='b', previous='a',
                                         version=2, at=NOW))
    rows = rotation.read(path)
    assert [row['version'] for row in rows] == [1, 2]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'a' not in {row.get('value') for row in rows}


def test_a_corrupt_ledger_is_refused_rather_than_silently_restarted(tmp_path):
    path = tmp_path / 'rotations.json'
    path.write_text('{ not json')
    with pytest.raises(ValueError):
        rotation.read(path)


def test_rewriting_the_provider_file_replaces_one_line_and_keeps_the_rest(tmp_path):
    path = tmp_path / '.env'
    path.write_text('# comment\nDEEPSEEK_BASE_URL=https://api.example.invalid\nDEEPSEEK_API_KEY=old-key\nOTHER=1\n')
    os.chmod(path, 0o600)
    rotation.replace_value(path, 'DEEPSEEK_API_KEY', 'new-key')
    text = path.read_text()
    assert 'DEEPSEEK_API_KEY=new-key' in text and 'old-key' not in text
    assert text.startswith('# comment\n') and 'OTHER=1' in text
    assert 'DEEPSEEK_BASE_URL=https://api.example.invalid' in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_provider_file_must_already_have_the_key_so_a_typo_does_not_create_a_second_one(tmp_path):
    path = tmp_path / '.env'
    path.write_text('DEEPSEEK_BASE_URL=https://api.example.invalid\n')
    with pytest.raises(ValueError):
        rotation.replace_value(path, 'DEEPSEEK_API_KEY', 'new-key')


from tests.test_admin_cli import RELEASE, SnapshotBench, _tenant_row   # noqa: E402
from tests.test_admin_cli import host as host_runtime  # noqa: F401,E402


class RotateBench(SnapshotBench):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scripts = []

    def python(self, site, body, timeout=900):
        self.scripts.append((site, body))
        if 'dsherp_runtime_user' in body:
            return 'runtime@acme.tenant.example.com\n'
        if 'DS Business Credential' in body and 'DSHERP_CREDENTIALS' in body:
            return super().python(site, body, timeout=timeout)
        if 'generate_keys' in body:
            return json.dumps({'user': 'runtime@acme.tenant.example.com', 'state': 'rotated',
                               'api_key': 'new-runtime-key', 'api_secret': 'new-runtime-secret'}) + '\n'
        if 'OAuth Client' in body:
            return json.dumps({'state': 'rotated', 'client_id': 'client-1',
                               'client_secret': 'new-client-secret'}) + '\n'
        if 'Social Login Key' in body:
            return json.dumps({'state': 'updated'}) + '\n'
        return super().python(site, body, timeout=timeout)


def test_rotating_the_provider_key_rewrites_the_file_and_records_the_change(host_runtime, monkeypatch):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    provider = Path(host_runtime) / 'provider.env'
    provider.write_text('DEEPSEEK_API_KEY=old-key\nDEEPSEEK_BASE_URL=https://api.example.invalid\n')
    os.chmod(provider, 0o600)
    report = admin.rotate(RELEASE, 'provider', value='brand-new-key', file=provider)
    assert report['version'] == 1 and report['kind'] == 'provider'
    assert 'brand-new-key' not in json.dumps(report), 'a rotation report never carries the value'
    assert 'DEEPSEEK_API_KEY=brand-new-key' in provider.read_text()
    rows = rotation.read(Path(admin.runtime_dir(RELEASE)) / 'rotations.json')
    assert rows[-1]['fingerprint'] == rotation.fingerprint('brand-new-key')
    assert rows[-1]['previous_fingerprint'] == rotation.fingerprint('old-key')
    assert report['next'] and '重启' in report['next']


def test_rotating_to_the_same_value_is_refused_as_a_rotation_that_did_not_happen(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    provider = Path(host_runtime) / 'provider.env'
    provider.write_text('DEEPSEEK_API_KEY=same-key\nDEEPSEEK_BASE_URL=https://api.example.invalid\n')
    with pytest.raises(admin.Fault):
        admin.rotate(RELEASE, 'provider', value='same-key', file=provider)
    assert rotation.read(Path(admin.runtime_dir(RELEASE)) / 'rotations.json') == []


def test_rotating_a_sites_runtime_identity_writes_the_new_pair_into_the_worker_profile(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    profile = Path(host_runtime) / 'worker.json'
    profile.write_text(json.dumps({'sites': [
        {'site': 'acme.tenant.example.com', 'base_url': 'http://backend:8000',
         'business_url': 'http://backend:8000', 'api_key': 'old', 'api_secret': 'older'}]}))
    os.chmod(profile, 0o600)
    bench = RotateBench([])
    report = admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=profile,
                          bench_factory=lambda kind: bench)
    written = json.loads(profile.read_text())['sites'][0]
    assert written['api_key'] == 'new-runtime-key' and written['api_secret'] == 'new-runtime-secret'
    assert 'new-runtime-secret' not in json.dumps(report)
    assert report['profile_updated'] is True and report['version'] == 1
    assert report['profile_sites'] == ['acme.tenant.example.com']

    second = Path(host_runtime) / 'worker-2.json'
    second.write_text(profile.read_text())
    report = admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com',
                          profile=[profile, second], bench_factory=lambda kind: bench)
    assert report['profile_sites'] == ['acme.tenant.example.com'] * 2, 'both profiles, or one dies'
    assert json.loads(second.read_text())['sites'][0]['api_secret'] == 'new-runtime-secret'
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600


def test_rotating_the_oauth_client_secret_reaches_both_sides_or_says_it_did_not(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = RotateBench([])
    report = admin.rotate(RELEASE, 'oauth-client', target='acme', bench_factory=lambda kind: bench)
    assert report['client'] == 'rotated' and report['business_site'] == 'updated'
    assert 'new-client-secret' not in json.dumps(report)
    sites = [site for site, _ in bench.scripts]
    assert RELEASE['platform_site'] in sites and 'acme.tenant.example.com' in sites


def test_doctor_reports_a_credential_nobody_has_rotated(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    findings = admin.doctor(RELEASE)
    assert any('从未登记' in item for item in findings), findings
    assert any('provider' in item for item in findings)


def test_a_profile_that_cannot_take_the_new_pair_stops_the_rotation_before_it_happens(host_runtime):
    """A secret is issued once. If a profile write fails after the key has changed, nobody
    holds the credential the Site now expects - so every file is checked first."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    good = Path(host_runtime) / 'worker.json'
    good.write_text(json.dumps({'sites': [{'site': 'acme.tenant.example.com', 'api_key': 'old',
                                           'api_secret': 'older'}]}))
    missing = Path(host_runtime) / 'other-worker.json'
    missing.write_text(json.dumps({'sites': [{'site': 'somewhere.else', 'api_key': 'x', 'api_secret': 'y'}]}))
    bench = RotateBench([])
    with pytest.raises(admin.Fault):
        admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[good, missing],
                     bench_factory=lambda kind: bench)
    assert not any('generate_keys' in body for _, body in bench.scripts), 'the key must not have changed'
    assert json.loads(good.read_text())['sites'][0]['api_secret'] == 'older'


def test_the_older_flat_worker_profile_is_understood_too(host_runtime):
    """The worker accepts a single-site profile with no `sites` list; a rotation that only
    knew the newer shape refused it, after already changing the key."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    flat = Path(host_runtime) / 'flat.json'
    flat.write_text(json.dumps({'site': 'acme.tenant.example.com', 'base_url': 'http://backend:8000',
                                'api_key': 'old', 'api_secret': 'older'}))
    bench = RotateBench([])
    report = admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[flat],
                          bench_factory=lambda kind: bench)
    assert report['profile_sites'] == ['acme.tenant.example.com']
    assert json.loads(flat.read_text())['api_secret'] == 'new-runtime-secret'


def test_a_runtime_rotation_with_nowhere_to_deliver_the_secret_is_refused_before_it_happens(host_runtime):
    """Issuing without a destination kills the old key and gives the new one to nobody (R3)."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = RotateBench([])
    with pytest.raises(admin.Fault, match="交付目的地"):
        admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', bench_factory=lambda kind: bench)
    assert not any('generate_keys' in body for _, body in bench.scripts), 'the key must not have changed'


def test_the_operator_may_take_the_secret_themselves_and_it_is_also_kept_privately_on_disk(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = RotateBench([])
    report = admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', print_secret=True,
                          bench_factory=lambda kind: bench)
    assert report['api_secret'] == 'new-runtime-secret'
    kept = Path(report['secret_file'])
    assert json.loads(kept.read_text())['api_secret'] == 'new-runtime-secret'
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600


def test_a_profile_write_that_fails_after_issuance_leaves_the_secret_recoverable(host_runtime, monkeypatch):
    """The precheck cannot see a disk that fills or a file that changes underneath; if the
    write fails once the key has changed, the pair is already on disk, privately, and the
    error says where."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    profile = Path(host_runtime) / 'worker.json'
    profile.write_text(json.dumps({'sites': [{'site': 'acme.tenant.example.com', 'api_key': 'old',
                                              'api_secret': 'older'}]}))
    bench = RotateBench([])
    real = admin._profile_pair

    def failing(path, site, pair):
        raise OSError('No space left on device')
    monkeypatch.setattr(admin, '_profile_pair', failing)
    with pytest.raises(admin.Fault, match="保存在") as caught:
        admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[profile],
                     bench_factory=lambda kind: bench)
    kept = sorted(Path(admin.runtime_dir(RELEASE)).glob('rotations/runtime-*.json'))
    assert kept and json.loads(kept[-1].read_text())['api_secret'] == 'new-runtime-secret'
    assert str(kept[-1]) in str(caught.value)
    monkeypatch.setattr(admin, '_profile_pair', real)


def test_a_clean_profile_rotation_leaves_no_secret_file_behind(host_runtime):
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    profile = Path(host_runtime) / 'worker.json'
    profile.write_text(json.dumps({'sites': [{'site': 'acme.tenant.example.com', 'api_key': 'old',
                                              'api_secret': 'older'}]}))
    bench = RotateBench([])
    admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[profile],
                 bench_factory=lambda kind: bench)
    assert not list(Path(admin.runtime_dir(RELEASE)).glob('rotations/runtime-*.json'))


def test_a_recovery_destination_that_cannot_be_written_is_found_before_the_key_changes(host_runtime):
    """The reviewer's probe: <runtime>/rotations is a file, so no recovery copy can be made.
    That has to be discovered while the old key still works - nothing rotated, profile
    untouched, no half-delivered secret (R3)."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    profile = Path(host_runtime) / 'worker.json'
    profile.write_text(json.dumps({'sites': [{'site': 'acme.tenant.example.com', 'api_key': 'old',
                                              'api_secret': 'synthetic-old'}]}))
    (Path(admin.runtime_dir(RELEASE)) / 'rotations').write_text('synthetic path collision')
    bench = RotateBench([])
    with pytest.raises(admin.Fault, match='未签发新密钥'):
        admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[profile],
                     bench_factory=lambda kind: bench)
    assert not any('generate_keys' in body for _, body in bench.scripts), 'the key must not have changed'
    assert json.loads(profile.read_text())['sites'][0]['api_secret'] == 'synthetic-old'


def test_a_recovery_copy_that_fails_to_write_after_issuance_hands_the_secret_to_the_operator(host_runtime, monkeypatch):
    """The disk can still fail between the check and the write. Then the only place the new
    secret can go is the operator's screen, once, with the failure named."""
    from dsherp import admin

    admin.ensure_secrets(RELEASE)
    _tenant_row()
    profile = Path(host_runtime) / 'worker.json'
    profile.write_text(json.dumps({'sites': [{'site': 'acme.tenant.example.com', 'api_key': 'old',
                                              'api_secret': 'older'}]}))
    bench = RotateBench([])

    def failing_fsync(descriptor):
        raise OSError('Input/output error')
    monkeypatch.setattr(admin.os, 'fsync', failing_fsync)
    with pytest.raises(admin.Fault) as caught:
        admin.rotate(RELEASE, 'runtime', target='acme.tenant.example.com', profile=[profile],
                     bench_factory=lambda kind: bench)
    assert 'new-runtime-secret' in str(caught.value) and 'Input/output error' in str(caught.value)
