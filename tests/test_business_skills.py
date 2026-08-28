import shutil
import json
import hashlib
from pathlib import Path
import pytest
from dsherp.runtime_revision import verify_business_skills,ROOT


def bundle(tmp_path):
    (tmp_path/'config').mkdir()
    shutil.copyfile(ROOT/'config/business-skills.json',tmp_path/'config/business-skills.json')
    shutil.copytree(ROOT/'business-skills',tmp_path/'business-skills')
    return tmp_path


def test_only_pinned_business_skills_are_accepted(tmp_path):
    root=bundle(tmp_path)
    verify_business_skills(root)
    skill=root/'business-skills/erp-query/SKILL.md'
    skill.write_text(skill.read_text()+'\nchanged')
    with pytest.raises(ValueError,match='digest'):verify_business_skills(root)


def test_configuration_skill_is_pinned_and_in_runtime_identity():
    from dsherp.runtime_revision import FILES
    manifest=json.loads((ROOT/'config/business-skills.json').read_text())
    row=next(row for row in manifest['skills'] if row['name']=='erp-configuration')
    assert row['version']=='1.0.0'
    assert 'business-skills/erp-configuration/SKILL.md' in FILES
    verify_business_skills()


def test_extra_skill_or_symlink_is_rejected(tmp_path):
    root=bundle(tmp_path);extra=root/'business-skills/personal';extra.mkdir()
    with pytest.raises(ValueError,match='catalog'):verify_business_skills(root)
    extra.rmdir()
    skill=root/'business-skills/erp-query/SKILL.md'
    skill.unlink();skill.symlink_to(ROOT/'business-skills/erp-query/SKILL.md')
    with pytest.raises(ValueError,match='symbolic'):verify_business_skills(root)


def test_manifest_version_must_match_skill_body(tmp_path):
    root=bundle(tmp_path)
    path=root/'config/business-skills.json';manifest=json.loads(path.read_text())
    manifest['skills'][0]['version']='999.0.0'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='version'):verify_business_skills(root)
