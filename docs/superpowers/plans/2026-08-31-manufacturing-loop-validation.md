# 制造业务闭环验证实施计划

> **执行者（Codex）开工须知**：先读 `AGENTS.md`、`README.md`、`docs/superpowers/specs/2026-08-31-agent-system-architecture-design.md`（重点"工具扩展"与"实施顺序"节）、`docs/superpowers/plans/2026-08-29-context-agent-sidebar.md` 的"执行边界"节，再执行本计划。任务用 `- [ ]` 复选框跟踪，按序执行、逐任务提交。

**目标**：让既有 Agent/HITL/配置发布底座真正跑通一条 ERPNext 制造闭环——销售订单 → 缺料解释 → 用户选定生产方式 → 采购/自制/委外单据提案与确认 → 收货/入库/交付回读。

**架构**：引入服务端"DocType 策略表 + 通用提案工具"取代逐 DocType 硬编码；新增 make（源单路由生成草稿）提案类型；制造知识并入既有 skills 升版；一切写入复用既有提案-确认（HITL）协议，零新执行机制、零新常驻服务。

**背景**：当前操作工具硬编码 Item/Customer/Sales Order 三个 DocType（分散在 8+ 处白名单）；三个站点均无仓库/供应商/库存物料/BOM；制造单据从未跑通（`docs/engineering/runtime-baseline.md:51`、`docs/engineering/erpnext-integration-evidence.md:88`：相关已提交数量为 0）。

## 已定范围（用户已确认，不得重开）

1. 委外只做"企业供料加工"一种主变体；供应商供料、直接采购成品列为明确缺口，下一计划补齐。
2. 既有 Item/Customer/Sales Order 硬编码迁移到策略表机制并删除旧硬编码路径；既有回归测试全部保留且必须通过（迁移安全网）。
3. 站点：alpha（`dsherp-validation.localhost`，API 18081 / Desk 18082）开发与逐段验收；daily（`dsherp-daily.localhost`，18086）最终端到端验收。
4. BOM 创建不在本阶段（由 fixture 建立），列为缺口。

## 全局约束（每个任务隐含遵守）

- 中文回复与提交说明按仓库现状；TDD 先写行为测试确认失败再最小实现；fastfail，配置缺失/权限不足明确报错，不静默降级。
- controller-owned 提交不得混合计划文件与非计划文件；外部提交交错仅在核验文件集不相交并登记 ledger 索引后允许；不得 reset、rebase、squash 或以其他方式改写历史来隐藏交错。今后每项任务完成时，须在功能提交后由独立计划收尾提交同步勾选复选框。
- **Checkpoint 1 已放行（2026-08-31）**：用户接受已登记的 C1.6 外部交错例外；阶段 1 技术门与三站点零 route 残留证据保持有效，阶段 2/3 可继续，但禁止 operation-domain 真实模型运行，阶段 3 必须先于阶段 4。
- 固定 DSH SDK/Runtime `0.1.1rc1`（源码 ref `528c682e...`）不动；Frappe `15.118.0` / ERPNext `15.119.3`；从实际站点发现字段，不猜 schema，不凭记忆编造 API。
- 模型仅 `deepseek-v4-flash`（`context_execution.py:122` 白名单），项目原模型授权内使用，不扩大生产写入。
- 不新增常驻服务（无 worker/scheduler/websocket，现状为按需一次性容器）；不 fork/修改上游核心。
- 预算等防护常量（如单运行 8 次调用上限 `context_execution.py:133`）不预先放松；撞到后按先例（压缩阈值 0.005→0.02，提交 `19f626a`）以显式失败测试驱动调整。
- 删除被本计划实现取代的旧代码与测试；临时产出用 `work/`；按功能本地提交，不推送远端，不提交密钥/租户数据/日志/运行状态。
- 每阶段分别记录：代码、单测、真实 Runtime、真实模型、真实 ERP、浏览器 UI 证据，互不替代；本地合成验证不表述为生产上线。
- 服务端是安全边界：所有写入走提案-确认；确认绑定企业/操作者/内容/版本；结果不明先核实不重试；部分成功如实记录不承诺回滚。

## 关键现状事实（探索已核实，直接采用）

**DocType 硬编码点（阶段 1 全部改为策略表驱动）**：
`dsherp/read_tools.py:12,17,22`（Literal 白名单）、`dsherp/context_mcp.py:48,52,56,60`、`frappe_app/dsherp_bridge/api.py:7,18,43`、`frappe_app/dsherp_bridge/context_api.py:46`、`frappe_app/dsherp_bridge/operations.py:201,267,277,323`、`frappe_app/dsherp_bridge/context_execution.py:16-18`（TOOLS 表）、`frappe_app/dsherp_bridge/context_permissions.py:6`（DOCTYPES 权限版本取样）。

**来源门禁（新工具必须沿用同一模式）**：update/action/fill 提案要求本轮存在参数完全相等且版本匹配的 `erp_read_record` 来源（`context_execution.py:186-189`）；create 要求 `erp_read_schema` 来源（`:197-199`）；`finish_run` 拒绝无来源的 Succeeded（`:239-249`）。

**权限版本**：提案 `authorization_revision` 对 DOCTYPES 全量取快照（`operations.py:10-16`）；策略表引入后取样范围必须同步为"全部启用策略行的 DocType + 策略行本身"，否则漏检或误伤。

**领域标识**：`domain` 为三值 Select，6 处硬校验（`context_api.py:297`、`context_execution.py:90`、`context_permissions.py:38`、`context_mcp.py:31`、`session_runtime.py:57`、`runtime/model-guard.cjs:59`）；本阶段不改名不加值。

**Skill 机制**：1 运行 = 1 skill 目录（`config/dsh-context.yml:17`）；目录集合必须等于 manifest（`dsherp/runtime_revision.py:21`、`runtime/model-guard.cjs:40`）；升级 = 改 `SKILL.md` + `config/business-skills.json` 升版本+sha256。本阶段不建多 skill 装载。

**测试模板**：`tests/integration/test_sales_order_operations.py`（copy 参考单必填字段 → propose → 断言零写入 → confirm → 子表增删改 → submit/cancel 分别确认 → finally 先 cancel 再删清理）。

**环境**：工作区干净（仅 2 个未推送文档提交，正常）；daily 数据数量断言写死在 `tests/integration/test_daily_site.py:25-32`；alpha/daily 各有 `.runtime/context-worker*.json` 运行身份 profile。

## 设计定案

### DS Doctype Policy（新原生 DocType，dsherp_bridge 内部）

字段：`target_doctype`（Data，唯一）、`enabled`（Check）、`allow_read` / `allow_create` / `allow_update` / `allow_submit` / `allow_cancel` / `allow_fill`（Check）、`company_scope`（Data，预留，空=全站）、子表 `routes`（child DocType `DS Doctype Policy Route`：`route_name`、`method_path`（冻结的 ERPNext mapped-doc 方法完整导入路径）、`target_doctype`）。

约束：仅 System Manager 原生读写；**不进任何 MCP 工具，agent 零访问零提案**（spec 明确）；策略行内容（含 routes）进入 `context_permissions.revision()` 取样——改策略立即使在飞提案/运行失效。

### 通用工具演化

- 保留现有工具名：`erp_read_schema` / `erp_read_record` / `erp_search_records` / `erp_propose_create` / `erp_propose_update` / `erp_propose_action` / `erp_propose_fill`。`doctype` 参数校验从 Literal 白名单改为"策略表存在启用行且允许对应动作"；MCP 端参数 schema 的 doctype 改为 string，服务端 fastfail。
- 新增 `erp_propose_make(source_doctype, source_name, source_version, route)`：服务端查策略路由 → 运行 mapped-doc 方法生成目标草稿字段集（不落库）→ 冻结为 `proposal_type='make'` 提案（含 route、source、source_version、完整目标字段与子表）。来源门禁：本轮须有对 source 的 `erp_read_record` 且版本匹配（沿用 `:186-189` 模式）。确认时服务端重跑该方法与冻结结果比对，不一致即拒绝重提（决策 D3），不静默采用新结果。
- action（submit/cancel）提案 payload 增只读 `impact` 块：冻结单据的物料/数量/仓库摘要，前端确认卡明示"将变动库存：物料×数量@仓库"（任务 T2.6，硬性要求，不能只靠 skill 文本）。

### 首批策略表条目（fixture/迁移脚本写入 alpha，daily 终验同步）

| DocType | read | create | update | submit | cancel | fill | make 路由 |
|---|---|---|---|---|---|---|---|
| Item | ✓ | ✓ | ✓ | – | – | ✓ | – |
| Customer | ✓ | ✓ | ✓ | – | – | ✓ | – |
| Supplier | ✓ | ✓ | ✓ | – | – | – | – |
| Sales Order | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | → Delivery Note |
| BOM | ✓ | – | – | – | – | – | – |
| Warehouse | ✓ | – | – | – | – | – | – |
| Bin | ✓ | – | – | – | – | – | – |
| Work Order | ✓ | ✓ | ✓ | ✓ | ✓ | – | → Stock Entry（领料 / 完工） |
| Stock Entry | ✓ | – | – | ✓ | ✓ | – | 仅经 make 产生草稿 |
| Purchase Order | ✓ | ✓ | ✓ | ✓ | ✓ | – | → Purchase Receipt；→ Subcontracting Order |
| Purchase Receipt | ✓ | – | – | ✓ | ✓ | – | 仅经 make |
| Subcontracting Order | ✓ | – | – | ✓ | – | – | → 供料 Stock Entry；→ Subcontracting Receipt |
| Subcontracting Receipt | ✓ | – | – | ✓ | – | – | 仅经 make |
| Delivery Note | ✓ | – | – | ✓ | ✓ | – | 仅经 make |

缺料解释的只读入口：BOM（`erp_read_record` 读明细子表展开）、Bin（`erp_search_records` 按物料+仓库查 `actual_qty` / `projected_qty`）、Warehouse。

make 路由候选方法名（`make_delivery_note`、`make_stock_entry`、`make_purchase_receipt`、`make_subcontracting_order`、`make_subcontracting_receipt`、供料 SE 方法）**不得直接写入代码**——阶段 0 在真实站点核验确切导入路径后才进策略行（决策 D2）。

## 阶段 0：落盘与真实发现

- [x] **T0.1 计划落盘**：把本计划全文存为 `docs/superpowers/plans/2026-08-31-manufacturing-loop-validation.md`，`README.md` 文档清单加链接，单独提交（`docs:` 前缀）。
- [x] **T0.2 make 方法与委外链真实发现**：在 alpha 用只读脚本（`work/` 下，事后清理）核验 ERPNext v15 实际 mapped-doc 方法导入路径，以及"企业供料加工"链的确切单据序列（`is_subcontracted` 采购订单 → Subcontracting Order → Send to Subcontractor 供料 Stock Entry → Subcontracting Receipt）。确切签名与一次 dry-run 输出摘要记入 `docs/engineering/erpnext-integration-evidence.md` 附录。判据：每条路由有确切 import path；零遗留写入。
- [x] **T0.3 alpha 制造 fixture**：新建幂等脚本 `infra/provision_manufacturing_fixture.py`（数据命名明确标注合成）：仓库组（原料/在制/成品/委外仓）、Supplier、`is_stock_item=1` 的成品+原料、成品 BOM、期初库存（Stock Reconciliation）。先写失败测试 `tests/integration/test_manufacturing_fixture.py`（断言 BOM 可读、Bin 有期初量、重复执行幂等），确认红 → 实现 → 绿 → 提交。

## 阶段 1：策略表机制与旧 DocType 迁移

- [x] **T1.1 DS Doctype Policy 落地**：按"设计定案"建 DocType 与子表；`context_permissions.revision()` 取样扩展为"全部启用策略行的 DocType + 策略行内容"。先写失败测试 `tests/integration/test_doctype_policy.py`：`test_policy_rows_gate_tool_doctypes`（未启用 DocType 的 read/propose 被拒，启用后放行）、`test_policy_change_rotates_revision`（改策略行 → revision 变化 → 在飞提案 confirm 被拒）。红 → 实现 → 绿 → 提交。
- [x] **T1.2 迁移与删除硬编码**：把 Item/Customer/Sales Order 三条策略行写入（迁移脚本或 fixture），随后逐点删除"关键现状事实"列出的 8+ 处白名单，全部改查策略表。安全网：既有全仓 Python 回归与前端 88+ 项必须全绿后才提交。分两个 commit：先接线（新旧并行读策略）、后删除。
- [x] **T1.3 治理零访问校验**：测试断言 `DS Doctype Policy` 自身对 `erp_read_*` / `erp_propose_*` 全部被拒（策略表里没有它自己的行即天然被拒，用测试锁死这一事实）。

## 阶段 2：制造 DocType 接入（每段先红后绿，段内 finally 先 cancel 再删清理）

- [x] **T2.1 make 提案机制**：`erp_propose_make` 工具、`proposal_type='make'`、确认时重跑比对（D3）。失败测试 `tests/integration/test_make_proposal.py::test_make_freezes_mapped_result_and_rejects_drift`：读源单 → make 提案 → 他人改源单 → confirm 拒绝；反例：未读源单版本直接 make 被拒。
- [x] **T2.2 自制段**：Work Order/Stock Entry/BOM/Bin/Warehouse 策略行 + Work Order→Stock Entry 两条路由（领料 Material Transfer for Manufacture、完工 Manufacture）。失败测试 `tests/integration/test_work_order_operations.py::test_work_order_chain_updates_stock`：WO 创建确认 → submit 确认 → 领料 SE make+submit → 完工 SE make+submit → Bin 原料减、成品增回读；反例：options 带非法键被拒。
- [x] **T2.3 采购段**：Supplier/PO/PR 策略行 + `purchase_order_to_purchase_receipt` 路由。失败测试 `tests/integration/test_purchase_operations.py::test_purchase_order_to_receipt_updates_stock`：Supplier 创建确认 → PO 创建（Purchase User 角色）→ submit 确认 → make PR 草稿 → submit 确认 → Bin `actual_qty` 增、PO `per_received` 回读；同一 PR submit 提案二次 confirm 返回同一执行（去重断言）。
- [x] **T2.4 委外段（企业供料加工）**：`is_subcontracted` PO / SCO / SCR 策略行 + 三条路由（PO→SCO、SCO→供料 SE、SCO→SCR）。失败测试 `tests/integration/test_subcontracting_operations.py::test_supplied_material_subcontracting_chain`：委外 PO 提交 → make SCO → submit → make 供料 SE（Send to Subcontractor）→ submit → 委外仓 Bin 增 → make SCR → submit → 成品入库、供料消耗回读；反例：SCO 直接 propose_create 被策略拒。供料方法以 T0.2 发现的确切签名接入。
- [x] **T2.5 交付段**：Delivery Note 策略行 + `sales_order_to_delivery_note` 路由。失败测试 `tests/integration/test_delivery_operations.py::test_delivery_note_from_sales_order_updates_delivery_status`：已提交 SO → make DN → submit 确认 → SO `per_delivered`/status 回读、库存扣减断言。
- [x] **T2.6 库存影响展示**：action 提案 `impact` 只读块（冻结物料/数量/仓库摘要）+ 前端 `frontend/src/OperationProposal.jsx` 展示。失败测试：前端组件测试（确认卡渲染"将变动库存：物料×数量@仓库"）+ `tests/integration/test_operation_proposals.py::test_stock_action_proposal_carries_impact_summary`。

## 阶段 3：skills 升版（排在阶段 4 之前，见风险 R5）

- [x] **T3.1 erp-query → 1.3.0**：补缺料解释读取策略（先 BOM 后 Bin、filters 批量读、先规划后读取的预算意识）。
- [x] **T3.2 erp-operation → 2.0.0**：改为"能力以 schema + 策略枚举为准"（删除逐 DocType 列举）、make 路由用法、草稿与提交分别确认、库存影响措辞、委外链步骤、明确缺口声明（供应商供料/直接采购成品/BOM 创建/发票不支持）。
- [x] **T3.3 manifest 同步**：`config/business-skills.json` 升版本+sha256；先改 SKILL.md 不改 manifest 观察 `tests/test_runtime_revision.py` 校验变红，再同步转绿。容器链回归通过后提交。

## 阶段 4：alpha 真实模型 + 浏览器 UI 分段验收（真实 deepseek-v4-flash，非替身）

- [x] **T4.1 缺料解释查询轮**：真实业务页侧栏问"这张销售订单缺什么料"，模型经 BOM+Bin 只读给出有来源的缺料依据。
- [x] **T4.2 自制链**、**T4.3 采购链**、**T4.4 委外+交付链**：各段模型提案 → 侧栏确认 → 原生回读。每段判据：执行记录唯一、零意外写入、预算未爆（爆则按 R1 处置）。证据逐段追加 `docs/engineering/context-agent-hitl-acceptance.md`。

## 阶段 5：daily 终验

- [x] **T5.1 daily 角色与 fixture**：先改 `tests/integration/test_daily_site.py:25-32`（数量断言改为包含制造 fixture 集合；角色断言加 Manufacturing/Purchase 类角色）确认红；再对 daily 执行 fixture 与角色扩充，转绿。变更前按既有惯例做 daily 备份。
- [x] **T5.2 daily 端到端终验**：普通成员经平台 SSO 登录 daily，真实模型完整叙事：销售订单 → 缺料解释 → 用户聊天中选定方式（至少自制+采购+委外主变体各一次）→ 逐个确认 → 收货/入库 → 交付 → SO 状态回读。验收后四件套备份恢复比对，证据落档，按功能提交。判据：全仓 Python + 前端回归全绿、证据数字更新。

## 风险与显式决策

- **R1 预算封顶**（8 次调用 / 524288 输入 / 16384 输出，`context_execution.py:117-138`）：缺料解释多轮读取与制造链多步 make 可能撞顶，大 schema（Work Order/PO）吃输入预算。不预先放松；撞到后以显式失败测试驱动改常量（先例：压缩阈值 `19f626a`）。skills 文本先行降低轮次。
- **R2 revision 取样成本**：`require_revision` 每次工具调用全量重算，DocType 从 3 扩到 ~14 后 DocField/DocPerm 取样量大增。先量测；若成瓶颈，允许的最小修复是请求内 `frappe.local` 记忆化（单请求一致性不变），仍需失败测试驱动。
- **R3 Stock Entry 确认措辞**：docstatus 0→1 的 diff 不足以让用户理解库存后果，T2.6 的 impact 块是硬性要求。
- **R4 daily 断言写死**：T5.1 必须"先改断言见红、再补 fixture 转绿"，防 fixture 与断言双向漂移。
- **R5 skill 与工具能力一致性**：erp-operation 1.5.0 文本逐 DocType 列举，与策略枚举矛盾；阶段 3 必须在阶段 4 真实模型验收前完成，否则模型会拒用新单据或误述能力。
- **D1（已决）旧只读验证链去留**：`dsherp/erp_mcp.py` + `config/dsh-erp.yml` 是 README"最小验证"依赖的首次技术验证基线（`tests/test_erp_mcp_config.py`），**不迁移、不删除**，业务运行不使用；在 runtime-baseline 文档标注其历史基线身份即可。
- **D2 委外供料方法**：一切 mapped-doc 方法路径以 T0.2 真实站点发现为准，候选名不得直接写入代码。
- **D3 make 冻结比对语义**：confirm 重跑 adapter 结果与冻结不一致 → 拒绝并要求重新提出，不静默采用新结果（与 update 版本门禁同一措辞）。

## 验收矩阵（既有边界在制造单据上的具体化）

| 边界 | 制造场景具体化 |
|---|---|
| 跨租户 | daily 会话确认 alpha 的 Work Order 提案 → `payload['site']` 不匹配拒绝（`operations.py:235`） |
| 权限 | 无 Manufacturing 角色 propose_create Work Order 被拒；无 Stock 权限用户对 SE submit 提案被拒；撤角色后在飞运行 `require_revision` 失效 |
| 版本 | 读 PO 后他人改单 → make SCO 提案被拒；提案后源单变化 → confirm 拒"记录版本已变化" |
| 策略 | 运行中禁用 Stock Entry 策略行 → 下一次工具调用即失效；未确认 SE 提案 confirm 被拒 |
| 部分成功 | 供料 SE 成功后 SCR 提交校验失败 → SE 保持 Succeeded、SCR Failed，不承诺回滚、不自动重试 |
| Unknown 核实 | SE submit 执行中断 → `verify_execution` 只读核对库存分录存在性，不重放（沿用 Running→Unknown 语义） |
| 重复确认去重 | 同一 SE/PR submit 提案并发/重复 confirm → 单一 DS Execution Record、库存只动一次（防双重入账，最高优先级用例） |
| 预算 | 缺料解释运行耗尽 8 次调用 → 显式"预算已用尽"失败，不静默截断 |

## 检查点 2/3 审计整改 C2（Phase 4 前）

- [x] **C2.1 复审台账对齐**：四笔 review pending 提交完成聚焦复审，SDD 台账与阶段证据同步为 PASS。
- [x] **C2.2 部分成功不回滚**：供料 SE Succeeded 后 SCR 校验 Failed，保留前一步且不自动重试。
- [x] **C2.3 make 目标策略**：目标 DocType 必须存在启用策略行，不要求 allow_create。
- [x] **C2.4 verify 撤权语义**：当前读权限导致字段跳过时返回“部分字段不可比对”，不得报告完全一致。
- [x] **C2.5 验收矩阵归属**：跨租户、SE Unknown 核实、预算文案分别明确归属 T5.2、T4.2、T4.1。
- [x] **C2.6 文档与死代码**：README 收录阶段证据，删除未消费的 projected 构造。

## 人工检查点（执行者在此暂停，等用户放行）

1. **阶段 1 结束后**：硬编码全部删除、全仓 Python + 前端回归全绿——请用户过目迁移结果再进入阶段 2。
2. **阶段 3 结束后**：skills 升版与 manifest 同步完成——请用户确认知识文本再进入真实验收。
3. **阶段 4 开始前**：此后每段验收消耗真实 DeepSeek 调用费——明确请用户放行后再执行 T4.1。

## 验证与收尾

- 每任务：相关测试先红后绿；每阶段末跑全仓 `PYTHONPATH=. .venv/bin/python -m pytest`（含 `tests/integration`，需本地容器与 `.runtime/` 配置）与前端 `npm test`（frontend/），全绿才进下一阶段。
- 真实验收（阶段 4/5）逐段记录到 `docs/engineering/context-agent-hitl-acceptance.md` 与 `docs/engineering/context-agent-evidence.md`；`runtime-baseline.md:51` 与 `erpnext-integration-evidence.md:88` 的"制造未验证"陈述在闭环通过后更新。
- 计划完成定义：阶段 0-5 全部勾选、全仓+前端回归绿、证据文档更新、按功能本地提交完毕、不推送远端。委外另两种变体、BOM 创建、发票/收款为明确遗留缺口，写入证据文档"下一步"。
