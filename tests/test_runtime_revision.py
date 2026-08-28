import pytest
from dsherp.runtime_revision import configuration_revision,FILES


def test_revision_tracks_model_and_exact_runtime_files(tmp_path):
    for name in FILES:
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('version-one')
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
