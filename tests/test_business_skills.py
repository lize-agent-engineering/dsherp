import shutil
import json
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
    assert row['version']=='1.1.0'
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
    assert 'version: 1.4.0' in header
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


def test_operation_skill_discovers_dynamic_capabilities_and_manufacturing_routes():
    content=(ROOT/'business-skills/erp-operation/SKILL.md').read_text()
    header=content.split('---',2)[1]
    assert 'version: 2.2.0' in header
    description=next(
        line for line in header.splitlines() if line.startswith('description:')
    )
    assert '当前业务用户权限与服务端策略允许的业务对象' in description
    assert all(name not in description for name in ('Item','Customer','Sales Order'))

    assert '能力以 erp_read_schema、服务端启用的 DS DocType 策略及工具或路由返回为准' in content
    assert '工具目录固定，但 doctype 由服务端策略动态裁决' in content
    assert 'schema、动作和 make 路由错误是最终权威' in content
    assert '不能凭旧清单判定某个 DocType 不支持' in content

    prerequisites={
        'create':'create 前先调用 erp_read_schema',
        'update':'update 前先用 erp_read_record 读取确切记录与版本',
        'fill':'fill 前先用 erp_read_record 读取确切记录与版本',
        'action':'action 前先用 erp_read_record 读取确切记录与版本',
        'make':'make 前先用 erp_read_record 读取确切源单与版本',
    }
    for step in prerequisites.values():
        assert step in content
    assert '只能使用服务端已启用策略返回或允许的精确 route' in content
    assert '不传 options，不发明映射' in content
    assert 'make 确认只保存映射后的草稿' in content
    assert '提交或取消必须另起 action 提案并单独确认' in content
    assert '草稿保存与提交不能合并为一次确认' in content

    assert 'impact 是服务端冻结且只读' in content
    assert '有符号的“物料 × 数量 @ 仓库”' in content
    assert '模型不得编辑、重算或替换' in content
    assert '采购收货内部调拨' in content
    assert '交付 Product Bundle 或目标仓' in content
    assert '退货' in content
    assert '只如实转述 fastfail' in content

    chains=[
        'Work Order → Material Transfer for Manufacture Stock Entry → Manufacture Stock Entry',
        'Purchase Order → Purchase Receipt',
        'is_subcontracted Purchase Order → Subcontracting Order → Send to Subcontractor Stock Entry → Subcontracting Receipt',
        'Sales Order → Delivery Note',
    ]
    for chain in chains:
        assert chain in content
    assert '每个 make 都只产生草稿' in content
    assert '每次保存和提交分别产生自己的提案与侧栏确认' in content

    assert 'Purchase Order 普通收货看 per_received' in content
    assert '委外供料进度看明细 subcontracted_qty' in content
    assert 'Subcontracting Order 看 per_received 与 status' in content
    assert 'Sales Order 完成交付但未开票时可为 To Bill' in content
    assert '不能误报为业务失败' in content

    for gap in ('供应商自带料委外','将直接采购成品包装成制造变体','BOM 创建','发票与付款'):
        assert gap in content
    assert '不能发明工具、路由，也不能用直接数据库或字段修改替代' in content
    assert '结果不明先核实，不重跑' in content
    assert '模型没有确认、保存、提交、取消或发布工具' in content
    assert '当前工具支持 Item、Customer' not in content


def test_operation_skill_supplies_exact_trusted_make_route_tokens():
    content=(ROOT/'business-skills/erp-operation/SKILL.md').read_text()
    mappings={
        'work_order_material_transfer':'Work Order → Material Transfer for Manufacture Stock Entry',
        'work_order_manufacture':'Work Order → Manufacture Stock Entry',
        'purchase_order_to_purchase_receipt':'Purchase Order → Purchase Receipt',
        'purchase_order_to_subcontracting_order':'is_subcontracted Purchase Order → Subcontracting Order',
        'subcontracting_order_to_supply_stock_entry':'Subcontracting Order → Send to Subcontractor Stock Entry',
        'subcontracting_order_to_subcontracting_receipt':'Subcontracting Order → Subcontracting Receipt',
        'sales_order_to_delivery_note':'Sales Order → Delivery Note',
    }
    for token, mapping in mappings.items():
        assert f'`{token}`：{mapping}' in content
        assert content.count(f'`{token}`')==1
    assert '当前发布版本可提交给 erp_propose_make 的精确 route token' in content
    assert '这些 token 只是 make 调用词汇表，不是 DocType 能力白名单' in content
    assert '服务端当前策略、用户权限和固定 adapter 仍是最终裁决' in content
    assert '服务端拒绝时立即停止，不能尝试或发明其他 token' in content
    assert 'route 内容变化会轮换权限版本，使在飞运行和提案失效' in content


ERROR_EXIT_HEADING='## 工具错误与做不了的出口'
ERROR_EXIT_SKILLS=(
    ('erp-query','1.4.0'),
    ('erp-operation','2.2.0'),
    ('erp-configuration','1.1.0'),
)


def error_exit_section(content):
    start=content.index(ERROR_EXIT_HEADING)
    rest=content[start+len(ERROR_EXIT_HEADING):]
    nxt=rest.find('\n## ')
    return content[start:] if nxt<0 else content[start:start+len(ERROR_EXIT_HEADING)+nxt]


def test_business_skills_share_tool_error_and_impossible_exit_rules():
    sections=[]
    for name,version in ERROR_EXIT_SKILLS:
        content=(ROOT/f'business-skills/{name}/SKILL.md').read_text()
        header=content.split('---',2)[1]
        assert f'version: {version}' in header
        section=error_exit_section(content)
        assert 'error_class=validation' in section
        assert '修正参数最多重试一次' in section
        assert '仍失败则用 `erp_request_input` 向用户说明' in section
        assert 'error_class=permission' in section
        assert '不得重试' in section
        assert '直接告知用户无权并结束' in section
        assert 'error_class=transient' in section
        assert '原样重试一次' in section
        assert '再失败则结束并说明' in section
        assert '不能自行猜测缺失信息' in section
        assert '用 `erp_request_input` 索取' in section
        sections.append(section)
    assert sections[0]==sections[1]==sections[2]
