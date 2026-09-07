import pytest
import json
from dsherp.context_runner import run_business
from dsherp.runtime_host import load_settings
from dsherp.runtime_revision import configuration_revision,FILES,ROOT


def test_revision_tracks_provider_settings_and_exact_runtime_files(tmp_path):
    for name in FILES:
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes((ROOT/name).read_bytes() if name.startswith('business-skills/') or name=='config/business-skills.json' else b'version-one')
    settings={'DEEPSEEK_API_KEY':'synthetic','DEEPSEEK_BASE_URL':'https://provider.invalid',
              'deployment_digest':'a1'*32}
    first=configuration_revision(settings,tmp_path)
    assert configuration_revision({**settings,'deployment_digest':'b2'*32},tmp_path)!=first
    assert len(first)==64 and configuration_revision(dict(reversed(list(settings.items()))),tmp_path)==first
    assert configuration_revision({**settings,'DSH_MODEL':'model'},tmp_path)==first
    assert configuration_revision({**settings,'DSH_MODEL':'changed'},tmp_path)==first
    # Rotating the provider key must not change the revision: the revision decides whether a
    # conversation keeps its native session, and a routine rotation would end every one of
    # them (S9). The endpoint still counts - talking to a different provider is a different
    # runtime - and the key is still required to be present.
    assert configuration_revision({**settings,'DEEPSEEK_API_KEY':'other'},tmp_path)==first
    assert configuration_revision({**settings,'DEEPSEEK_BASE_URL':'https://other.invalid'},tmp_path)!=first
    (tmp_path/FILES[0]).write_text('version-two')
    assert configuration_revision(settings,tmp_path)!=first


@pytest.mark.parametrize('missing',['DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL'])
def test_missing_provider_setting_fails(missing):
    settings={'DEEPSEEK_API_KEY':'key','DEEPSEEK_BASE_URL':'url','deployment_digest':'a1'*32}
    del settings[missing]
    with pytest.raises(ValueError,match=missing):configuration_revision(settings)


def test_missing_runtime_file_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        configuration_revision({'DEEPSEEK_API_KEY':'key','DEEPSEEK_BASE_URL':'url',
                                'deployment_digest':'a1'*32},tmp_path)


def test_load_settings_requires_only_key_and_base_and_ignores_model(tmp_path):
    path=tmp_path/'provider.env'
    path.write_text('DEEPSEEK_API_KEY=synthetic\nDEEPSEEK_BASE_URL=https://provider.invalid\n')
    path.chmod(0o600)
    expected={'DEEPSEEK_API_KEY':'synthetic','DEEPSEEK_BASE_URL':'https://provider.invalid'}
    assert load_settings(path)==expected
    path.write_text('DEEPSEEK_API_KEY=synthetic\nDEEPSEEK_BASE_URL=https://provider.invalid\nDSH_MODEL=ignored\n')
    path.chmod(0o600)
    assert load_settings(path)==expected


def test_business_entry_rejects_unbound_runtime_before_network(tmp_path):
    config={'run_id':'r','capability':'c','native_session_id':'n','question':'q','context':{},'resume':False,
        'business_url':'http://127.0.0.1:1','site':'synthetic','runtime_revision':'0'*64,
        'DEEPSEEK_API_KEY':'synthetic','DEEPSEEK_BASE_URL':'http://127.0.0.1:1','deployment_digest':'a1'*32}
    path=tmp_path/'run.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='revision'):run_business(path,tmp_path/'native')
