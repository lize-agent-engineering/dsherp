# 阶段 2/3 制造闭环与技能升版证据

日期：2026-09-01

检查点 1 后基线：`9611f83`

阶段 3 功能 HEAD：`0a9f3a9`；C2 功能 HEAD：`2c572b0`。C2 全仓门在该功能 HEAD 与本证据/计划/README 文档工作树上执行，文档不改变运行行为。

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
| 宽审 make 落库绑定 | `4a996a3`、`e6d9c78`、`7ff4c23`、`780bb33`、`0a9f3a9` | 独立 `confirmation_target`、委外供料预生成、完整子表冻结与唯一引用、insert hook 漂移回滚、verify 冻结基准 | PASS，Critical 0 / Important 0 |
| 宽审未知库存影响 | `c1ebb68` | exact stock/no-stock registry、未知 DocType fastfail、字段权限优先 | PASS，Critical 0 / Important 0 |

上述任务在仓库计划与外部权威计划中均已逐项勾选。当前 Phase 2/3 及检查点整改范围 `9611f83..0a9f3a9` 共 39 笔提交；`c1ebb68` 的库存影响修复与 `a810f88` / `3cadae5` 的证据提交位于本次供料修复前，文件集与 `780bb33` / `0a9f3a9` 的功能文件不相交，历史保持原样。自身提交均按功能文件集分类，没有重写历史。

## TDD 与审查整改

每项功能均先以行为测试见红，再做最小实现转绿。阶段边界的主要审查整改如下：

- T3.1 首轮审查发现 filters 或 title 参与匹配却未返回时，其字段没有进入历史来源复核。新增独立 `match_fields` 账本，filters keys、非空 query 的 name/可读 title 均被记录；撤销返回字段、filter-only 字段、query-title 字段三类权限都会拒绝历史来源。第二轮恢复了被误替换的既有返回字段撤权回归。
- T3.2/T3.3 首轮审查发现公开工具要求模型传 route 字符串，但没有 route 枚举接口。技能补齐当前发布版本七个精确 token；它们只是调用词汇表，不是 DocType 能力白名单，实时策略、当前用户权限和固定 adapter 仍最终裁决。
- 最终全仓第一次虽为 Python `243 passed`，但运行态发现 beta 三个配置测试遗留已提交的 Conversation/Bundle/Confirmation/Execution。三个既有 finally 已按精确 ID、依赖顺序补齐，并增加提前失败故障注入；正常与异常路径 `6 passed`，独立复审通过。该整改不修改产品代码。
- 阶段宽审发现 make 仅在 insert 前比较 mapper，Frappe/ERPNext hook 仍可把保存后的确认字段改写；同时 verify 只会拿 actual 与 Execution 中同一份 actual 自证。最终实现将完整 mapper `target` 与摘要绑定的用户 `confirmation_target` 分离：公开 target、保存后精确比较和 verify 都以同一冻结确认投影为准；空值、来源链接、数量、仓库和子表结构均绑定，仅对四条真实链证明的原生派生字段做精确 DocType/child 注册。Delivery Note 五类真实 insert hook 漂移均 Failed、目标草稿回滚，正常链与幂等保留。
- 后续复核发现 SCO/SCR 的 `supplied_items` 曾被按父表整表排除，这会遗漏企业供料的原料、required/consumed qty、reserve/supplier warehouse 与 reference/source links。`780bb33` 在 propose 与 confirm 共用的 mapper 路径上，对精确 SCO/SCR target 纯内存调用 ERPNext v15 `SubcontractingController.create_raw_materials_supplied()`；提案分配且 confirm/insert 复用唯一子行引用，不消费 Series、不插入或提交目标单据。公开 `target`、raw mapper `target` 与 `confirmation_target` 现在都有供料表；父表不再整表排除，仅对 SCR supplied child 真实 insert 派生的 amount、available qty、cost/current/default account 字段做精确登记。SCO/SCR 各自以 before_insert hook 覆盖行数、rm item、qty、warehouse 漂移，均 Failed、草稿回滚、唯一 Execution；管理员外改 required/consumed qty 后 verify 为 false。
- 唯一引用复审进一步指出随机引用没有证明本提案内及数据库内唯一。`0a9f3a9` 将引用扩为 20 位：propose 对 used 集合与每行 exact child DocType 做至多 8 次碰撞检查，耗尽中文 fastfail；confirm 在绑定 naming 前按冻结引用与 exact child DocType 复查占用。只有错误携带的 child DocType 和 name 同时命中本 make 冻结引用时，`DuplicateEntryError` 才按明确 `Failed` 处理，其他重复错误仍保持 Unknown。可控 hash 序列锁定同提案重复、现存 child 碰撞、最终唯一值及 Series/目标/来源不变；确认前占用和 before_insert 插入竞争均回滚目标草稿、唯一 Execution，且不是 Unknown。
- 阶段宽审还发现任意未注册 DocType 会被静默标成无库存影响。现在只有 Sales Order、Work Order、Purchase Order、Subcontracting Order 进入精确 no-stock registry；四类库存单据先复核父/子字段权限再计算；其他 DocType 在提案形成前中文 fastfail，零 Proposal、Execution 和业务写。

## 最终全量门

全仓门 HEAD `e8afb97` fresh 执行：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests -q --tb=short
249 passed in 778.10s (0:12:58)
```

```text
cd frontend && npm test
Test Files  20 passed (20)
Tests       160 passed (160)
Duration    13.56s
```

`git diff --check 9611f83..e8afb97` 退出 0；全仓门执行时工作区干净。

固定 DSH Runtime → stdio MCP → HTTP/Frappe → alpha ERP 的容器链在最终技能内容上以本地 SSE 模型替身通过：`1 passed in 87.01s`。全仓最终门也包含 Runtime/MCP/ERP 集成回归。没有使用真实 provider、真实模型或真实模型凭证。

`780bb33` 的 fresh 聚焦验证没有冒充全仓门：旧实现公开 SCO target 缺 `supplied_items`，真实 RED 为 `1 failed` / `KeyError: supplied_items`；修复后 make 专项与四段真实链 `7 passed in 90.47s`，operations 聚焦门 `28 passed in 155.15s`，最终委外专项 `1 passed in 31.01s`，`git diff --check` 与 `py_compile` 退出 0。alpha fresh 回读中 `subcontract-*` User、Conversation、PO、SCO、Stock Entry、SCR 均为 0；测试内还逐次锁定目标 DocType 名称集合、全量 Series、来源单 docstatus/modified/status 在提案前后不变。

`0a9f3a9` 的唯一引用 RED 在旧实现首先命中公开引用仍为 10 位（委外专项 `1 failed`），从而阻止后续碰撞断言；最小实现后，委外链 + make 专项 `3 passed in 52.28s`，最终 operations 聚焦门 `18 passed in 126.47s`，`py_compile` 与 `git diff --check` 退出 0。该门保留 SCO/SCR 八类供料 hook、四段正常链、幂等、verify 反例与 fresh 清理，并新增 hash monkey 与 before_insert hook 的 finally 恢复断言。

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
6. **R1-2026-09-01-T4.3**：alpha 采购链首个真实 operation 运行 `41dd8ca…` 在第 6 次模型调用后的第二次原生压缩触发 `summarization truncated at the token cap (incomplete checkpoint)`，运行明确 Failed，预留输出恰为 `16384`，且提案、执行记录、Purchase Order、Purchase Receipt 均为 0。先新增操作运行累计预留 `19456` 应放行、下一次预约应明确失败的行为测试并确认旧常量红灯，再把**单运行累计输出预留**从 `16384` 调整为 `20480`；8 次调用、`524288` 输入字节、query/operation/compaction 单次输出上限及容器资源均不变。失败运行保留审计，不恢复、不静默放松、不对同一运行重试；本 ruling 落盘并提交后才允许在新会话发起下一次真实采购请求。

### Deferred，不阻塞本检查点

- confirm 阶段策略拒绝的具体文案透传。
- `get_proposal` / `verify_execution` 的策略门禁定位。
- R2：权限 revision 全量重算成本量测；仅在真实瓶颈出现后以测试驱动请求内记忆化。

### 当前明确不支持

- 供应商自带料委外、把直接采购成品包装成制造变体、BOM 创建、发票与付款。
- 采购收货内部调拨、交付 Product Bundle/目标仓、相关退货等未纳入本阶段的库存影响形状；服务端明确 fastfail，不生成 Unknown 执行记录，不建议绕过。

## 下一检查点

阶段 3 已结束，Phase 4 尚未开始。等待人工检查点明确放行后，才可在 alpha 使用计划指定的真实模型执行查询轮与 operation 领域分段 UI 验收。

## C2 检查点整改（2026-09-01）

- C2.1：对 `de1ecb2`、`780bb33`、`0a9f3a9`、`c1ebb68` 补做聚焦复审，并把 SDD 台账的 `review pending` 回写为 PASS。复审覆盖配置异常清理、委外供料冻结/唯一引用、未知库存影响 fastfail；聚焦测试 `16 passed in 118.38s`，静态核对为 Critical 0 / Important 0。原任务表的 PASS 结论自此与台账同步，不再超前。
- C2.2：新增真实委外链故障注入。供料 Stock Entry 提交成功后，Subcontracting Receipt 原生 `validate` 抛出确定校验失败；断言供料执行仍为 Succeeded、Stock Entry 与库存分录保留，SCR 执行为 Failed、SCR 不提交且无库存分录，重复确认返回同一执行且校验只调用一次。当前实现的初始特征测试直接为绿；受控反向变异“Failed 执行允许再次进入确认”后测试为红，恢复去重逻辑后 `1 passed in 37.50s`。
- C2.3：旧实现禁用 Delivery Note 目标策略后仍接受 make，真实 RED 为 `disabled make target policy accepted`。新增仅要求策略行存在且 enabled 的 `require_enabled()`；不读取 `allow_create`，保持“Delivery Note 仅经 make”语义。恢复目标策略后同链放行，make 专项 `1 passed in 17.10s`。
- C2.4：旧实现撤销 `Delivery Note.po_no` 当前读权限后返回 `matches_proposal=True`。修复后返回 `matches_proposal=None`、`comparison_status=部分字段不可比对` 和明确说明；未撤权的完整比对仍返回原有布尔结果。实现复用 `_shape_mismatch_path()`，删除未被消费的 projected 递归结果。
- C2.6：README 已加入本证据链接；`operations.py` 原 `_project_frozen_shape()` 的 projected 构造没有消费者，已删除，匹配判断统一走既有冻结形状比较。

### 验收矩阵未覆盖行的明确归属

- 跨租户制造具体化：归属 **T5.2 daily 端到端终验**，届时以 daily 会话/身份对 alpha 绑定内容的拒绝作为多站点反例；本检查点不宣称覆盖。
- Unknown 核实落到 Stock Entry submit：归属 **T4.2 自制链真实验收**，在提交响应中断场景只读核对 Stock Ledger Entry/单据状态，不重放提交；本检查点仅保留既有通用 Unknown 契约。
- 预算耗尽文案：归属 **T4.1 缺料解释查询轮**，真实模型达到 8 次调用上限时验收明确“预算已用尽”失败，不把截断结果冒充成功。

### C2 涉改测试与最终门

直接新增或收紧的行为断言位于：

- `tests/integration/test_subcontracting_operations.py::test_supplied_material_subcontracting_chain`：SE 成功、SCR 校验失败、前一步不回滚、失败步骤不重试。
- `tests/integration/test_make_proposal.py::test_make_freezes_mapped_result_and_rejects_drift`：目标策略 disabled 拒绝/恢复放行；verify 当前读权限缺字段时返回部分不可比对。

C2.1 聚焦复审运行配置 apply/confirmation/verification、make、subcontracting、stock-impact-fastfail 六个文件组：`16 passed in 118.38s`。三站策略播种完整组复跑：`23 passed in 113.91s`。

首轮全仓为 `240 passed, 9 failed in 1323.42s`；九项全部是 `test_policy_seed.py` 调用 provision 脚本的外层 60 秒超时，没有策略内容断言失败。排查时三个后端无遗留 provision/bench 进程，数据库容器没有 OOM 或重启；单例 `1 passed`，原相邻测试顺序 `4 passed`，完整策略播种组 `23 passed`，未复现确定性代码缺陷，因此没有修改超时或加入重试。随后 fresh 权威全仓复跑：

```text
PYTHONPATH=. .venv/bin/python -m pytest tests -q --tb=short
249 passed in 765.94s (0:12:45)
```

```text
cd frontend && npm test
Test Files  20 passed (20)
Tests       160 passed (160)
Duration    13.16s
```

最终 `python3 -m py_compile` 与 `git diff --check` 均退出 0。C2 未运行真实 provider/模型，未进入 Phase 4。

全量门后 fresh 回读：alpha 仍为 14 条 enabled 策略和 7 条 route，beta/daily 各为 Customer、Item、Sales Order 三条 enabled 基础策略且 0 route；Delivery Note 目标策略已恢复 enabled。alpha 的 `make-` / `subcontract-` 临时用户、六类制造单据及四类 Agent 记录均为 0；合成库存恢复为原料仓 RM `100/100`、委外仓 RM `0/0`、成品仓 FG `0/0`（actual/projected）。
