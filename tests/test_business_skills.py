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


def test_query_skill_requires_fresh_source_on_every_model_run():
    content=(ROOT/'business-skills/erp-query/SKILL.md').read_text()
    assert '每个模型运行都必须至少调用一次 ERP 只读工具取得本轮新来源' in content
    assert '不能只凭会话历史或上一轮工具结果回答' in content


def test_query_skill_plans_bom_then_batches_warehouse_scoped_bins():
    content=(ROOT/'business-skills/erp-query/SKILL.md').read_text()
    header=content.split('---',2)[1]
    assert 'version: 1.3.0' in header
    description=next(
        line for line in header.splitlines() if line.startswith('description:')
    )
    assert '当前业务用户权限与服务端策略允许的业务对象' in description
    assert all(name not in description for name in ('Item','Customer','Sales Order'))

    sequence=[
        '先规划本轮需要读取的 DocType、字段与调用数',
        '读取确切的销售订单或其他需求来源及其 items',
        '批量搜索有效且已提交的 BOM 候选',
        '汇总实际读取层级中的原料与所需量',
        '批量读取 Bin',
        '分开说明 actual_qty 实际库存与 projected_qty 预计库存',
    ]
    positions=[content.index(step) for step in sequence]
    assert positions==sorted(positions)
    assert '不能跨仓库直接相加' in content
    assert '未展开子装配' in content
    assert '禁止按每个物料分别调用' in content
    assert '固定上限为 8 次模型调用' in content
    assert '资料不足或预算不足时明确说明未完成' in content
    assert 'filters={"item_code":["in",["RM-A","RM-B"]],"warehouse":["in",["原料仓 - ACME"]]}' in content
    assert 'fields=["item_code","warehouse","actual_qty","projected_qty"]' in content
    assert all(argument not in content for argument in ('site=','user=','url=','grant='))
