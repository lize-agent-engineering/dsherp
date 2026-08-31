# 阶段 1 策略迁移工程证据

日期：2026-08-31。证据基线：`5a860fc72ef7fec9525b701a8330960c732b1ef8`。

## 范围与检查点

阶段 0 与阶段 1 的代码任务 T0.1–T1.3 已完成，C1.1–C1.3 的纠正代码也已完成；Checkpoint 1 仍因等待 C1 整体验收而保持阻塞。本记录不解除检查点，也不授权进入 Phase 2。

固定边界为 DSH SDK/Runtime `0.1.1rc1`（源码 ref `528c682e061696f5a160f363f236ecbf53cbd006`）、Frappe `15.118.0`、ERPNext `15.119.3`、Python `3.12.11`。本轮只执行本地代码测试、固定本地 Runtime 的既有自动化以及 alpha/beta/daily 三个合成 Site 的只读回读；没有执行 Phase 2、真实 operation-domain 模型、浏览器 UI、部署或生产操作，也不据此声明用户可见上线。

## 提交索引

### 计划任务 T0.1–T1.3

| 任务 | 功能提交 |
| --- | --- |
| T0.1 | `2a00529ca73f8061b98cc90ccf0173c37287ba3c` — `docs: 落盘制造业务闭环验证计划` |
| T0.2 | `17354c51f0a1fba6974781d16e81b5b6aa8061d8` — `docs: 记录制造映射方法发现`；`ab37988c2de0642266119d5e76001c6227ebf7a9` — `docs: 补齐工单库存路由参数`；`10e89fc7434df1692d75588932ea47e9e3833c66` — `docs: 区分工单签名与调用参数` |
| T0.3 | `c15dc573f511690abc9416c30482565cf9052edb` — `feat: 增加幂等制造合成夹具`；`f1a6b0e852778dac3a0bb1c66935323d880af65f` — `fix: 强化制造夹具首次创建与冲突验证`；`cfe61949ce01dd445a3d3964948cf291e20cc827` — `fix: 验证制造夹具首次创建后的幂等性` |
| T1.1 | `cce4539eacddb2f65ff84226ee7a675a9cf48f46` — `feat: 落地DocType策略与权限版本失效`；`fe1cd58ab33ec347f73faf7a5574cb2a6e722967` — `fix: 让禁用策略变更失效在途提案` |
| T1.2 | `5b1e513cd48e3d2a6da956811a09e2900d9375a4` — `feat: 播种alpha基础DocType策略`；`cff785c6e40ee0e64da928e69d822e896fe1a6d3` — `refactor: 删除业务DocType硬编码边界`；`1666ef83281b53e03d32e69cdfa1ba536b1eb79c` — `fix: 删除页面与平台业务对象边界`；`67d0eb0af3803ed5fa12e8b082ca5e4f794e6148` — `fix: 按服务端策略分类页面上下文` |
| T1.3 | `af38bac25959906b9271a3acec5ed02e4d584cb9` — `test: 锁定治理策略零工具访问`；`4ab11ad26a23456ad1a512287c875502ae21f0f1` — `fix: 阻断治理策略配置工具访问` |

### C1 纠正提交

| 纠正项 | 提交 |
| --- | --- |
| C1.1 | `a3d2089f1cbad0926e2e2aa41552f095633b0f2d` — `恢复日常站点策略读取并统一三站点开通` |
| C1.2 | `f68860feceae4dc7569abe9129e9660efd3ff950` — `策略表缺失时显式失败并保留填表目标` |
| C1.3 | `c94a437e17ba85949d796582e2e672c225a7da7a` — `fix: 阻断治理子表配置工具访问`；`2e4953fe8596f3ad6b29ff0858677e39e1985c7b` — `test: 补强治理子表原生读取证据`；`194454d07536748fa53d87d1d0c4eaa23218c7ea` — `test: 验证治理子表真实读取回滚`；`5a860fc72ef7fec9525b701a8330960c732b1ef8` — `test: 保留治理路由早期失败` |

从 C1 基线 `774d7619c2b4b52c11cbd9220de462e28df9b0f7` 之后，`git log --reverse 774d761..5a860fc` 的结果恰好依次为 `a3d2089`、`f68860f`、`c94a437`、`2e4953f`、`194454d`、`5a860fc`。C1 提交连续，无插入提交；未 rebase、squash、改写或 push。

### 计划外 terminology / skill 提交

以下提交保留在原历史位置，不归入 T0.1–T1.3 或 C1 功能索引，也不改写历史：

- `00da0adec0a6d36fb2f5185aaf5adcb8638a38bf` — `docs(terminology): define Chinese correction pack`
- `5716407b41a491cad74c313a2d7aa95137b46eda` — `feat(terminology): add deterministic translation glossary`
- `c2a7ceeaa79bf2090b983ac074857c814185e059` — `feat(terminology): package generated Chinese translations`
- `bd037b32d144653c7a4da733af05731e58619bf2` — `test(terminology): verify native translation precedence`
- `5da389a330c4a862b0f24eb245499ad194896043` — `docs(terminology): record activation and recovery evidence`
- `efa75e52255ce14c929dd53ab57065f786a3e548` — `feat(terminology): add second batch of Chinese term fixes`
- `4b3b824766d7c41d70d6fde780241d6dd11f4dc3` — `feat(skills): 新增 ERPNext/Frappe 官方文档检索技能`
- `774d7619c2b4b52c11cbd9220de462e28df9b0f7` — `fix(skills): 文档技能补充中文来源误区并经二轮评测`

## TDD RED/GREEN 索引

精确上下文与完整命令见 `.superpowers/sdd/codex-5-6-sol-structured-origami/task-7-report.md` 和 `.superpowers/sdd/codex-5-6-sol-structured-origami/task-8-report.md`。

| 项目 | RED | GREEN |
| --- | --- | --- |
| C1.1 daily HTTP 恢复 | `test_membership_read_uses_distinct_business_users_and_sites` 返回 HTTP 403，`assert 403 == 200`，`1 failed in 1.70s`。 | 备份、恢复校验、migrate 与固定策略开通后，同一用例 `1 passed in 1.60s`。 |
| C1.1 三站点开通器 | `test_policy_seed... -k creates_or_verifies -x` 因旧 CLI 只接受 `[--verify-conflict]` 而失败，`1 failed, 11 deselected in 0.07s`。 | 固定三 Site/service allowlist 后，完整 seed 回归 `14 passed in 64.74s`。 |
| C1.2 缺 schema fastfail | 故障注入下，`require_action`、revision 与 boot 均错误返回，confirm 仍为 `Authorized`；`2 failed in 4.32s`。 | 共享 schema guard 后 focused `2 passed in 2.38s`；相关策略/HITL/平台回归 `47 passed in 121.30s`。 |
| C1.3 父子治理零工具访问 | 初始 RED 为 `governance tool access allowed: configuration/erp_read_configuration/DS Doctype Policy Route`，`1 failed`。 | 父/子目标共用既有 policy gate，相关回归 `18 passed in 19.86s`。 |
| C1.3 原生父/子读证据 | Round 1 RED：普通 value-field 枚举不含 `routes`；Round 2 RED：真实父文档空 routes 不含 transaction-only probe；均为 `1 failed`。 | Round 1 focused `1 passed in 6.16s`；Round 2 focused `1 passed in 6.76s`，rollback 后 fresh connection 与原快照相同。 |
| C1.3 teardown 早期失败 | 受控 RED 显示原始 `AssertionError('原始早期失败')` 被旧 teardown 的 `(None, [])` 比较覆盖。 | 前置快照未建立时不做二次比较；原始异常保留，focused `1 passed in 5.80s`，最终相关回归 `18 passed in 19.19s`。 |

## 全量门禁

以下数字均在 C1 最终代码树 `5a860fc` 上于本记录写入前重新运行，不复制旧的 205/158 结果。

```sh
PYTHONPATH=. .venv/bin/python -m pytest
```

```text
collected 218 items
======================= 218 passed in 565.12s (0:09:25) ========================
```

```sh
cd frontend && npm test
```

```text
Test Files  20 passed (20)
Tests       158 passed (158)
Duration    14.48s
```

```sh
git diff --check
```

退出码 0，无输出。文档编辑完成后还须再次运行同一检查，结果记录在 Task 9 报告。

## 三站点只读回读

回读使用既有 validation compose 的容器 Python，只执行 `table_exists`、版本读取、`frappe.get_all` 与 `frappe.db.count`；没有 migrate、seed、route 创建、commit 或业务写入。三站均实测 Frappe `15.118.0`、ERPNext `15.119.3`，父表与子表都存在，汇总 `schema=true`。

| 环境 | Site | schema | parent self-policy | child self-policy | route rows |
| --- | --- | --- | ---: | ---: | ---: |
| alpha | `dsherp-validation.localhost` | `true` | 0 | 0 | 0 |
| beta | `dsherp-beta.localhost` | `true` | 0 | 0 | 0 |
| daily | `dsherp-daily.localhost` | `true` | 0 | 0 | 0 |

每个 Site 的当前精确三行完全相同：

| target_doctype | enabled | read | create | update | submit | cancel | fill | company_scope | routes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Customer | 1 | 1 | 1 | 1 | 0 | 0 | 1 | empty (`null`) | `[]` |
| Item | 1 | 1 | 1 | 1 | 0 | 0 | 1 | empty (`null`) | `[]` |
| Sales Order | 1 | 1 | 1 | 1 | 1 | 1 | 1 | empty (`null`) | `[]` |

`parent self-policy` 是 `target_doctype='DS Doctype Policy'` 的策略行计数，`child self-policy` 是 `target_doctype='DS Doctype Policy Route'` 的策略行计数；三站两者均为 0。`route rows` 是子表总行数，三站均为 0，而非只检查上述三条策略的可见 routes。

## daily 恢复证据

1. C1.1 在任何备份、迁移或播种前先恢复 daily 的真实 HTTP 200/name 断言；daily 返回 403，形成 `1 failed in 1.70s` 的 RED。
2. 使用原生 `bench --site dsherp-daily.localhost backup --with-files --compress` 生成四个非空、未入 Git 的私有备份制品：
   - `20260831_162906-dsherp-daily_localhost-site_config_backup.json`
   - `20260831_162906-dsherp-daily_localhost-database.sql.gz`
   - `20260831_162906-dsherp-daily_localhost-files.tgz`
   - `20260831_162906-dsherp-daily_localhost-private-files.tgz`
3. `infra/verify_daily_backup.py` 将备份恢复到一次性 `dsherp-daily-restore.localhost`，确认源/恢复快照一致且三个 App 齐全；验证退出码 0，随后原生 `drop-site --no-backup` 删除一次性恢复 Site。这里只记录名称与状态，不包含 payload、凭证或租户数据。
4. 原生 `bench --site dsherp-daily.localhost migrate` 退出码 0；Frappe、ERPNext、dsherp_bridge DocType、Dashboard 与 `after_migrate` hooks 完成。
5. 同一个固定开通器分别对 alpha、beta、daily 执行，并在三站完成相同结果的二次幂等重放与 conflict rollback；daily 不使用手工 SQL 或单独修复路径。
6. 原 HTTP 用例在修复后返回 200/name，`1 passed in 1.60s`；本轮只读回读再次确认 daily `schema=true`、精确三行、零 routes、零父/子 self-policy。

## 偏差与裁定

- **T0.2 只读 draft fastfail**：当时没有可用的已提交源单；唯一 dry-run 对既有草稿 Sales Order 调用 `make_delivery_note`，因 mapper 要求 `docstatus=1` 而稳定 `ValidationError`。目标 DocType 计数 rollback 前后均为 0。这只证明精确 callable 与只读 fastfail，不冒充成功 mapped draft。
- **T1.2 beta 条件播种与可复现修复**：beta 先只迁移 schema 且策略行数为 0；现有平台读取真实返回 403 后，按计划条件使用固定控制面播种同三条行。C1.1 又把原 alpha 专用开通器收敛为三 Site/service 固定 allowlist，使 beta 的手工部署状态可由同一脚本精确复现、幂等回放并冲突 fastfail。
- **daily schema/403 回归断言缺陷**：T1.2 将 daily 缺 schema 的 403 当作阶段边界，但 Checkpoint 1 要求三 supplied Sites 同步；C1.1 先用恢复后的 200/name 断言重现 403，再完成可恢复备份、恢复验证、migrate、统一开通和 200 GREEN。C1.2 用 fault injection 保留缺 schema 的明确失败覆盖，不再保留一个真实破损 Site。
- **前端/平台硬编码审查修复**：T1.2 初版服务端已 policy-only，但审查发现前端三元页面白名单和平台 Item/Customer 代理边界；`1666ef8` 删除它们。随后审查发现任意 Form/List 会误分类配置页；`67d0eb0` 改由 boot 提供“策略允许 ∩ 原生可读”的候选，客户端列表不构成授权，服务端仍实时复查。
- **configuration-domain 父治理例外**：配置元数据路径原本绕过业务 policy gate，导致非 Administrator System Manager 可经 Agent 读取父治理 schema；`4ab11ad` 对精确父目标复用现有 `require_action(read)`，不存在 self-policy 因而拒绝，原生 Desk 控制面权限保留。
- **configuration-domain 子治理例外**：同类路径仍允许 `DS Doctype Policy Route`；`c94a437` 将固定治理目标扩为父表和子表，二者共用同一 generic gate，不新增第二套授权系统。
- **transaction-only 子表读取探针**：仅凭 child meta/空 routes 不能证明真实子表值可经父文档读出。C1.3 获准在 alpha 隔离测试事务内临时 append 一条字面 route，以非 Administrator System Manager 读取真实值，随后 rollback，并用 fresh connection 对比测试前快照；最终三站 route 总行数仍为 0。
- **terminology/skill 插入与 C1.6 串行规则**：既往计划任务之间出现的 terminology/skill 提交全部保留并单独索引，不重写历史。从 C1 基线 `774d761` 起，C1 功能与修复提交严格连续。今后本计划 active 期间，计划外工作累计到阶段边界，或经明确指令排在计划提交序列之外；任务复选框在同一功能提交或紧随其后的收尾文档提交更新。

## 延后非阻塞项

以下三项不阻塞 C1 验收，也不得在本记录中宣称已解决：

1. confirm-stage policy denial message passthrough；
2. `get_proposal` / `verify_execution` policy-gate placement；
3. R2 revision cost measurement。

## 模型硬约束

当前固定 `erp-operation` 为 `1.5.0`，其文本仍逐 DocType 列举能力，与服务端已经 policy-driven 的动态边界不一致。风险 R5 已成立：Phase 3 必须先把 skill 升级为以 schema + 策略枚举为准，再进入 Phase 4 的真实模型验收；否则模型可能拒用已开放的新单据或误述能力。因此 Phase 2 与 Phase 3 均禁止真实 operation-domain 模型运行，本阶段也没有运行真实 operation-domain 模型。

## 证据分层

| 证据层 | 本记录状态 | 精确边界 |
| --- | --- | --- |
| 代码 / 测试 | 已验证 | 最终 C1 代码树上 Python 218/218、前端 20 files / 158 tests，编辑前 `git diff --check` 退出 0。 |
| 真实 ERP Sites | 已验证 | 本地合成 alpha/beta/daily 真实 Site 只读回读；三站 schema、版本、策略行与零残留治理数据如上。不是生产企业数据。 |
| 真实 Runtime | 已验证（本地） | 固定 DSH Runtime 的既有真实进程/容器回归包含在 218 项全量门中；模型响应测试可使用替身，本记录不把 Runtime 通过外推为真实模型通过。 |
| 真实模型 | 未验证 | 本轮未调用真实模型；尤其禁止 operation-domain 真实模型运行。 |
| 浏览器 UI | 未验证 | 本轮未进行浏览器操作或视觉验收。 |
| 部署 / 用户可见可用性 | 未验证 | 未部署、未推送、未改生产；Checkpoint 1 仍等待 C1 验收。 |
