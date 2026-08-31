# 阶段 2/3 制造闭环与技能升版证据

日期：2026-09-01

检查点 1 后基线：`9611f83`

本证据当前功能 HEAD：`780bb33`；最近一次全仓门 HEAD 仍为 `c1ebb68`，`780bb33` 的全仓门由检查点控制器统一复跑。

## 结论边界

阶段 2 的 make、四段制造闭环与库存影响，以及阶段 3 的查询/操作技能升版已在本地隔离站点完成。当前证据证明代码回归、真实 Frappe/ERPNext 测试站点、固定 DSH Runtime 到 MCP/HTTP/ERP 的模型替身链和运行后清理；不证明真实模型效果、真实浏览器 UI、远端部署或生产可用。

按 R5 硬约束，阶段 2/3 没有在 alpha 运行 operation 领域真实模型，也没有进入 Phase 4。所有提交仅在本地，未 push、未改写历史。

## 任务与提交索引

| 任务 | 主要提交 | 验证重点 | 独立审查 |
| --- | --- | --- | --- |
| T2.1 make 提案 | `8986244`、`bfa4438`、`443091f` | 源单版本、映射冻结、确认重算、受信 adapter、权限 revision | PASS |
| T2.2 自制 | `bcfd9d0`、`651ae87`、`a4b94f1`、`ad7bd0a` | Work Order → 领料/完工 Stock Entry、库存与错误身份反例 | PASS |
| T2.3 采购 | `8d3a981`、`1678304` | Supplier/PO/PR、原生 submit、库存、重复确认幂等 | PASS |
| T2.4 企业供料委外 | `b8d5fb9`、`57ae21b`、`1b94ad3`、`486d31f` | PO→SCO→供料 SE→SCR、fixture 迁移与清理 | PASS |
| T2.5 交付 | `be8e8fa`、`4f266c6`、`4b9ea1a` | SO→DN、库存扣减、交付进度、路由漂移与拒绝边界 | PASS |
| T2.6 库存影响 | `908b40f`、`3de9acf`、`939ebee` | 冻结只读 impact、确认前重算、符号/精度/不支持形状 fastfail、前端展示 | PASS |
| T3.1 查询技能 | `8f05065`、`1bc419f`、`91ca7b6`、`ac20325` | 受限 filters/fields、来源字段和匹配字段复核、erp-query 1.3.0 | PASS，Critical 0 / Important 0 |
| T3.2/T3.3 操作技能 | `252ca20`、`073e1f2`、`05dce5b` | erp-operation 2.0.0、七个受信 route token、manifest 摘要 | PASS，Critical 0 / Important 0 |
| 全量门清理 | `c53cb38`、`de1ecb2` | beta 配置测试正常/异常 finally 精确清理 | PASS，Critical 0 / Important 0 |
| 宽审 make 落库绑定 | `4a996a3`、`e6d9c78`、`7ff4c23`、`780bb33` | 独立 `confirmation_target`、委外供料预生成与完整子表冻结、insert hook 漂移回滚、verify 冻结基准 | 上轮 0 Critical / 1 Important；整改完成待复审 |
| 宽审未知库存影响 | `c1ebb68` | exact stock/no-stock registry、未知 DocType fastfail、字段权限优先 | PASS，Critical 0 / Important 0 |

上述任务在仓库计划与外部权威计划中均已逐项勾选。当前 Phase 2/3 及检查点整改范围 `9611f83..780bb33` 共 37 笔提交；`c1ebb68` 的库存影响修复与 `a810f88` / `3cadae5` 的证据提交位于本次供料修复前，文件集与 `780bb33` 的功能文件不相交，历史保持原样。自身提交均按功能文件集分类，没有重写历史。

## TDD 与审查整改

每项功能均先以行为测试见红，再做最小实现转绿。阶段边界的主要审查整改如下：

- T3.1 首轮审查发现 filters 或 title 参与匹配却未返回时，其字段没有进入历史来源复核。新增独立 `match_fields` 账本，filters keys、非空 query 的 name/可读 title 均被记录；撤销返回字段、filter-only 字段、query-title 字段三类权限都会拒绝历史来源。第二轮恢复了被误替换的既有返回字段撤权回归。
- T3.2/T3.3 首轮审查发现公开工具要求模型传 route 字符串，但没有 route 枚举接口。技能补齐当前发布版本七个精确 token；它们只是调用词汇表，不是 DocType 能力白名单，实时策略、当前用户权限和固定 adapter 仍最终裁决。
- 最终全仓第一次虽为 Python `243 passed`，但运行态发现 beta 三个配置测试遗留已提交的 Conversation/Bundle/Confirmation/Execution。三个既有 finally 已按精确 ID、依赖顺序补齐，并增加提前失败故障注入；正常与异常路径 `6 passed`，独立复审通过。该整改不修改产品代码。
- 阶段宽审发现 make 仅在 insert 前比较 mapper，Frappe/ERPNext hook 仍可把保存后的确认字段改写；同时 verify 只会拿 actual 与 Execution 中同一份 actual 自证。最终实现将完整 mapper `target` 与摘要绑定的用户 `confirmation_target` 分离：公开 target、保存后精确比较和 verify 都以同一冻结确认投影为准；空值、来源链接、数量、仓库和子表结构均绑定，仅对四条真实链证明的原生派生字段做精确 DocType/child 注册。Delivery Note 五类真实 insert hook 漂移均 Failed、目标草稿回滚，正常链与幂等保留。
- 后续复核发现 SCO/SCR 的 `supplied_items` 曾被按父表整表排除，这会遗漏企业供料的原料、required/consumed qty、reserve/supplier warehouse 与 reference/source links。`780bb33` 在 propose 与 confirm 共用的 mapper 路径上，对精确 SCO/SCR target 纯内存调用 ERPNext v15 `SubcontractingController.create_raw_materials_supplied()`；提案分配且 confirm/insert 复用唯一子行引用，不消费 Series、不插入或提交目标单据。公开 `target`、raw mapper `target` 与 `confirmation_target` 现在都有供料表；父表不再整表排除，仅对 SCR supplied child 真实 insert 派生的 amount、available qty、cost/current/default account 字段做精确登记。SCO/SCR 各自以 before_insert hook 覆盖行数、rm item、qty、warehouse 漂移，均 Failed、草稿回滚、唯一 Execution；管理员外改 required/consumed qty 后 verify 为 false。
- 阶段宽审还发现任意未注册 DocType 会被静默标成无库存影响。现在只有 Sales Order、Work Order、Purchase Order、Subcontracting Order 进入精确 no-stock registry；四类库存单据先复核父/子字段权限再计算；其他 DocType 在提案形成前中文 fastfail，零 Proposal、Execution 和业务写。

## 最终全量门

最终功能 HEAD `c1ebb68` fresh 执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests -q --tb=short
249 passed in 768.43s (0:12:48)
```

```text
cd frontend && npm test
Test Files  20 passed (20)
Tests       160 passed (160)
Duration    13.38s
```

`git diff --check 9611f83..c1ebb68` 退出 0；最终功能树工作区干净。

固定 DSH Runtime → stdio MCP → HTTP/Frappe → alpha ERP 的容器链在最终技能内容上以本地 SSE 模型替身通过：`1 passed in 87.01s`。全仓最终门也包含 Runtime/MCP/ERP 集成回归。没有使用真实 provider、真实模型或真实模型凭证。

`780bb33` 的 fresh 聚焦验证没有冒充全仓门：旧实现公开 SCO target 缺 `supplied_items`，真实 RED 为 `1 failed` / `KeyError: supplied_items`；修复后 make 专项与四段真实链 `7 passed in 90.47s`，operations 聚焦门 `28 passed in 155.15s`，最终委外专项 `1 passed in 31.01s`，`git diff --check` 与 `py_compile` 退出 0。alpha fresh 回读中 `subcontract-*` User、Conversation、PO、SCO、Stock Entry、SCR 均为 0；测试内还逐次锁定目标 DocType 名称集合、全量 Series、来源单 docstatus/modified/status 在提案前后不变。

## 三站策略与 route 回读

最终全量门之后重新从数据库未过滤回读：

- alpha `dsherp-validation.localhost`：14 条启用策略，7 条 route。
  - 策略：Bin、BOM、Customer、Delivery Note、Item、Purchase Order、Purchase Receipt、Sales Order、Stock Entry、Subcontracting Order、Subcontracting Receipt、Supplier、Warehouse、Work Order。
  - Work Order：`work_order_material_transfer`、`work_order_manufacture`。
  - Purchase Order：`purchase_order_to_purchase_receipt`、`purchase_order_to_subcontracting_order`。
  - Subcontracting Order：`subcontracting_order_to_supply_stock_entry`、`subcontracting_order_to_subcontracting_receipt`。
  - Sales Order：`sales_order_to_delivery_note`。
- beta `dsherp-beta.localhost`：精确 3 条基础策略 Customer、Item、Sales Order；0 route。
- daily `dsherp-daily.localhost`：精确 3 条基础策略 Customer、Item、Sales Order；0 route。

治理父表 `DS Doctype Policy` 和子表 `DS Doctype Policy Route` 均不在任何业务策略中，读取和配置提案回归锁定为 Agent 零访问。route 全量内容参与权限 revision；任一 route 内容变化会使在飞运行与提案的版本复核失败。

## 清理与库存基线

最终全量门之后 fresh 回读：

- alpha、beta、daily 在 2026-09-01 新建的 DS Conversation、DS Model Run、DS Operation Proposal、DS Execution Record 均为 0。
- beta 六个配置测试正常/故障注入标题对应的 Conversation、Bundle、Confirmation、Execution 均为空。
- alpha 以 `impact-`、`work-order-`、`purchase-`、`subcontract-`、`delivery-` 开头的 Work Order、Stock Entry、Purchase Order、Purchase Receipt、Subcontracting Order、Subcontracting Receipt、Sales Order、Delivery Note 均为 0。
- alpha 的 `make-`、`unknown-impact-`、`impact-permission-` 临时用户，`DS Unknown Impact *` 临时 DocType/策略，以及本轮 Delivery Note/Stock Entry 权限 Property Setter 均为 0。
- alpha 合成制造库存恢复为：原料仓 RM `actual_qty=100`、`projected_qty=100`；成品仓 FG、在制仓 RM、委外仓 RM 均为 0。额外普通仓 RM 行也为 0。
- 各制造用例在 finally 中对已提交单据先 cancel 再删；本轮技能替身链没有创建 ERP 业务单据。

## 技能与 manifest

- `erp-query`：1.3.0，SHA-256 `deb10c60556b70c3ab30ce57c7536e4dbec0c9d47370b78987083161f0021971`。
- `erp-operation`：2.0.0，SHA-256 `e1686e9f66311684a39b6bd4609908dc4588831bab8636ae73644ec72df4093c`。
- `erp-configuration`：1.0.0，SHA-256 `b8617102ad9a358e8043b066667e08714aa06ff9f0be5e58ffe71c31518b819d`。

T3.3 按计划取得两层证据：旧 1.5.0 对新行为测试为红；只改 SKILL.md 后行为测试转绿，但 manifest/runtime revision 以 `Business skill digest mismatch` 变红；同步版本和真实摘要后转绿。route 词汇修复再次重复了 skill-only digest 红与 manifest 同步绿。

## 偏离决定与后续台账

### 本阶段已裁定偏离

1. T3.1 开始前发现原 `erp_search_records` 只有模糊 query 且只返回 name/modified，无法真实执行计划要求的 BOM/Bin filters 批量读取。最小增加了受限、权限复核的 filters/fields 契约；否则发布查询技能会形成虚假能力声明。
2. T3.2 审查发现没有模型可见的 route 枚举接口。在不新增服务端能力的任务边界内，选择在 2.0.0 技能列出当前七个受信 token，并保留服务端策略为最终裁决。
3. 阶段边界全量门发现既有 beta 配置测试残留。按“每段 finally 清理”硬约束单独修复测试清理，没有混入制造或技能提交。
4. 阶段宽审把 make 的“mapper 重算相等”扩展为“落库后用户确认投影仍相等”；完整 mapper target 继续用于插入，只有用户实际看到的稳定业务投影承担确认语义，原生派生/default 字段通过真实四段链逐项精确登记，不使用通用忽略。复核后进一步禁止对 SCO/SCR `supplied_items` 整表排除：公共 ERPNext 方法在内存预生成供料表，供料核心字段与完整子表结构均进入冻结确认。
5. 阶段宽审把库存影响从“未注册即 none”收紧为 exact stock/no-stock registry；新增策略若没有相应影响实现会 fastfail，不能借动态策略静默绕过确认解释。

### Deferred，不阻塞本检查点

- confirm 阶段策略拒绝的具体文案透传。
- `get_proposal` / `verify_execution` 的策略门禁定位。
- R2：权限 revision 全量重算成本量测；仅在真实瓶颈出现后以测试驱动请求内记忆化。

### 当前明确不支持

- 供应商自带料委外、把直接采购成品包装成制造变体、BOM 创建、发票与付款。
- 采购收货内部调拨、交付 Product Bundle/目标仓、相关退货等未纳入本阶段的库存影响形状；服务端明确 fastfail，不生成 Unknown 执行记录，不建议绕过。

## 下一检查点

阶段 3 已结束，Phase 4 尚未开始。等待人工检查点明确放行后，才可在 alpha 使用计划指定的真实模型执行查询轮与 operation 领域分段 UI 验收。
