import pytest
import json
from dsherp.context_runner import run_business
from dsherp.runtime_revision import configuration_revision,FILES,ROOT


def test_revision_tracks_model_and_exact_runtime_files(tmp_path):
    for name in FILES:
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes((ROOT/name).read_bytes() if name.startswith('business-skills/') or name=='config/business-skills.json' else b'version-one')
    settings={'DEEPSEEK_API_KEY':'synthetic','DSH_MODEL':'model','DEEPSEEK_BASE_URL':'https://provider.invalid'}
    first=configuration_revision(settings,tmp_path)
    assert len(first)==64 and configuration_revision(dict(reversed(list(settings.items()))),tmp_path)==first
    assert configuration_revision({**settings,'DSH_MODEL':'changed'},tmp_path)!=first
    assert configuration_revision({**settings,'DEEPSEEK_BASE_URL':'https://other.invalid'},tmp_path)!=first
    (tmp_path/FILES[0]).write_text('version-two')
    assert configuration_revision(settings,tmp_path)!=first


def test_missing_runtime_file_or_model_setting_fails(tmp_path):
    with pytest.raises(ValueError):configuration_revision({},tmp_path)
    with pytest.raises(FileNotFoundError):
        configuration_revision({'DEEPSEEK_API_KEY':'key','DSH_MODEL':'model','DEEPSEEK_BASE_URL':'url'},tmp_path)


def test_business_entry_rejects_unbound_runtime_before_network(tmp_path):
    config={'run_id':'r','capability':'c','native_session_id':'n','question':'q','context':{},'resume':False,
        'business_url':'http://127.0.0.1:1','site':'synthetic','runtime_revision':'0'*64,
        'DEEPSEEK_API_KEY':'synthetic','DSH_MODEL':'deepseek-v4-flash','DEEPSEEK_BASE_URL':'http://127.0.0.1:1'}
    path=tmp_path/'run.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='revision'):run_business(path,tmp_path/'native')
