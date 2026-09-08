"""How the evaluation identity is issued, checked without touching a Site.

The two things that matter: the password never reaches argv, and re-provisioning reuses
what exists rather than rotating a key a running evaluation is holding.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infra import provision_eval_identity as provision  # noqa: E402


class _Run:
    def __init__(self, payload, returncode=0):
        self.payload, self.returncode, self.calls = payload, returncode, []

    def __call__(self, command, **kwargs):
        self.calls.append({'command': command, 'input': kwargs.get('input', '')})
        import types
        return types.SimpleNamespace(returncode=self.returncode,
                                     stdout=json.dumps(self.payload, ensure_ascii=False),
                                     stderr='')


ISSUED = {'user': 'daily-operator@example.invalid', 'api_key': 'k-synthetic',
          'api_secret': 's-synthetic', 'version': 1, 'reused': False}


def test_no_secret_ever_reaches_argv(tmp_path):
    run = _Run(ISSUED)
    provision.main(['--out', str(tmp_path.relative_to(ROOT) / 'eval-users.json')]
                   if str(tmp_path).startswith(str(ROOT)) else
                   ['--out', 'work/test-eval-users.json'], run=run)
    joined = ' '.join(run.calls[0]['command'])
    for secret in ('k-synthetic', 's-synthetic', 'password', 'api_secret'):
        assert secret not in joined, joined
    # the body goes in on stdin, where a process listing cannot see it
    assert 'credentials' in run.calls[0]['input']
    (ROOT / 'work/test-eval-users.json').unlink(missing_ok=True)


def test_the_profile_has_a_fixed_key_set_and_no_extra_fields():
    run = _Run(ISSUED)
    profile = provision.main(['--out', 'work/test-eval-users.json'], run=run)
    assert set(profile) == {'site', 'business_url', 'operator'}
    assert set(profile['operator']) == {'user', 'api_key', 'api_secret', 'site', 'base_url'}
    written = json.loads((ROOT / 'work/test-eval-users.json').read_text())
    assert written == profile
    assert oct((ROOT / 'work/test-eval-users.json').stat().st_mode)[-3:] == '600'
    (ROOT / 'work/test-eval-users.json').unlink()


def test_an_existing_profile_is_reused_and_no_key_is_rotated():
    target = ROOT / 'work/test-eval-users.json'
    target.parent.mkdir(exist_ok=True)
    existing = {'site': 'dsherp-daily.localhost', 'business_url': 'http://b',
                'operator': {'user': 'u', 'api_key': 'existing', 'api_secret': 'x',
                             'site': 'dsherp-daily.localhost', 'base_url': 'http://a'}}
    target.write_text(json.dumps(existing))
    run = _Run(ISSUED)
    profile = provision.main(['--out', 'work/test-eval-users.json'], run=run)
    assert profile == existing
    assert run.calls == [], '复用分支不该再去站上签发'
    target.unlink()


def test_a_failed_provisioning_prints_no_credentials():
    run = _Run(ISSUED, returncode=1)
    with pytest.raises(RuntimeError, match='未打印任何凭据'):
        provision.main(['--out', 'work/test-eval-users-missing.json'], run=run)
    assert not (ROOT / 'work/test-eval-users-missing.json').exists()


def test_the_site_script_uses_the_credential_module_not_generate_keys():
    body = provision._script('dsherp-daily.localhost', 'daily-operator@example.invalid')
    code = '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('#'))
    assert 'credentials.current' in code
    assert 'generate_keys' not in code, '一把没有登记窗口的 key 会被站点拒绝（S2）'
    assert '__SITE__' not in body and '__USER__' not in body
