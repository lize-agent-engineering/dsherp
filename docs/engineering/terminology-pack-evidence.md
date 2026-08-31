# ERPNext 中文术语纠正包实施与验收证据

日期：2026-08-31。范围：本地 validation 合成环境；不代表生产部署。

## 结论

两批共 59 行简体中文术语（首批 50 行 + 第二批 9 行）已通过 `dsherp_bridge/translations/zh.csv` 接入 Frappe 原生翻译链。新站点安装 App 并使用 `zh` 后自动加载；存量站点更新制品后逐站执行 `bench clear-cache`，再由用户硬刷新浏览器即可激活。没有新增运行时翻译代码、API、hook、worker、同步服务或企业词表 UI。

Alpha、daily、beta 三个本地业务站点均已清理服务端缓存并回读新译文。Alpha 真实 Desk 管理员会话硬刷新后，四个高频页面可见标题为“生产工单”“采购入库单”“记账凭证”“科目余额表”。企业 `Translation` 记录仍以原生最高优先级覆盖平台 CSV。

## 固定版本与 T0 原生机制

- Frappe `15.118.0`，ERPNext `15.119.3`。
- 三个业务站点实际 App 顺序均为 `frappe → erpnext → dsherp_bridge`，后装 App 的同键翻译覆盖前项。
- Frappe 从各 App 的 `translations/<language>.csv` 加载 `source,translated[,context]`；简体中文代码为 `zh`。
- 翻译查询先检查 `source:context`，再回退裸 `source`。首批唯一需双写的 context 键是 `General Ledger:Warehouse`，因此 49 个源串生成 50 行。
- App CSV 合并完成后，Frappe 再合并原生 `Translation` DocType，企业热修优先级最高。
- `frappe.translate.clear_cache()` 清理服务端翻译、企业翻译和 bootinfo 缓存；浏览器已加载的翻译包仍需硬刷新。
- 结论与设计一致，T0 未发现需要触发刹车的机制冲突。

## TDD 红绿证据

### T2：主词表与生成器

先增加行为测试并确认因 `infra/build_translation_packs.py` 不存在而 **6 failed**；随后实现最小生成器与 50 行主词表，相关测试 **6 passed**。覆盖：固定表头、必填字段、周围空白拒绝、重复 `source + context` fast-fail、UTF-8、稳定排序、字节级幂等，以及禁止 `Submit`、`Cancel` 状态机动词。

### T3：生成制品与打包声明

先增加防漂移测试并确认因已提交的 `zh.csv` 不存在而失败；生成文件并声明 `translations/*.csv` package data 后，相关测试 **7 passed**。生成器连续执行的 SHA-256 不变；当前词表为 50 行数据，生成文件为 50 行。

### T4：原生集成与缓存

集成测试首次运行时 Alpha 仍从已缓存的上游包返回“试算平衡表 / 采购收货单 / 手工凭证 / 总帐”，证明仅修改文件不会伪装成已激活。执行 Alpha `clear-cache` 后，集成测试 **3 passed**；单元与集成组合最新结果为 **10 passed**。

集成测试同时证明：

- `Trial Balance → 科目余额表`
- `Purchase Receipt → 采购入库单`
- `Journal Entry → 记账凭证`
- `General Ledger → 总账`
- `General Ledger` + `Warehouse` context → `总账`
- 管理员可插入核心 `Translation` 记录，配置锁未阻断；临时企业译文“企业科目余额表”压过 App CSV，测试结束后删除记录并清缓存。
- 临时改动 App CSV 后，未清缓存仍返回旧值；清缓存后返回临时值；`finally` 恢复原字节并再次清缓存。
- Frappe 与 ERPNext 上游 `zh.csv` 未修改，其固定 SHA-256 分别为 `9e9dbd64f0965853e6be131b6335efdbba906a1a8e2908bfdd9f8888a2f0fdc8`、`233ab506626683446fb137dd8aab3fb6c28f78b1b6a55d803bc3cd537af00be9`。

## 检查点 2、备份与恢复

动 daily 前先生成带文件的压缩备份 `20260831_134841-dsherp-daily_localhost`：站点配置 382 B、数据库 854226 B、公共文件 141 B、私有文件 141 B。

首次误用宿主机 Python 调用恢复验证器，在导入阶段因缺少 `frappe` 立即失败，未创建恢复站点。改用控制 profile 的 Bench 虚拟环境后，`infra/verify_daily_backup.py` 成功恢复到一次性站点并核对：

- Apps：`dsherp_bridge`、`erpnext`、`frappe`
- Company：`DSHERP 日常合成企业`
- Customer：`日常 Agent 合成客户`
- Item：`DAILY-AGENT-ITEM`
- 日常操作员存在
- Sales Order 为空
- `setup_complete = 1`

验证结束后一次性恢复站点已删除。用户随后明确通过检查点 2。

## T5 激活与真实 UI

严格按 `daily → beta → alpha` 顺序执行：

```sh
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-daily.localhost clear-cache
docker compose -f infra/compose.validation.yml exec -T beta-backend bench --site dsherp-beta.localhost clear-cache
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-validation.localhost clear-cache
```

三站随后都由各自真实 Frappe 运行上下文返回“科目余额表 / 采购入库单 / 记账凭证 / 生产工单”，`General Ledger:Warehouse` 返回“总账”。

应用内浏览器复用 Alpha 现有 Administrator 登录，不退出、不切换身份、不提交表单、不写业务数据。访问 `/app/work-order` 后执行硬刷新，再只读抽查：

| 路由 | 页面可见标题 |
| --- | --- |
| `/app/work-order` | 生产工单 |
| `/app/purchase-receipt` | 采购入库单 |
| `/app/journal-entry` | 记账凭证 |
| `/app/query-report/Trial%20Balance` | 科目余额表 |

Daily 现有会话识别为“日常合成操作员”，但该合成普通用户访问 `/app` 与 `/app/work-order` 均由原生权限返回 403；未切换身份或提升权限。此项是该用户的 Desk 权限事实，不作为翻译失败，也不冒称 daily 普通用户页面验收成功。

## 第二批补充（2026-08-31）

对 35 个候选高频词在 Alpha 真实站点逐一回读现状后，追加 9 个源串（均无 context）：

- 语义级修正：`Lead → 线索`、`Opportunity → 商机`（上游 CRM 漏斗错档：Lead 误译“商机”、Opportunity 译“机会”）；`Outstanding Amount → 未清金额`（上游“未付金额”在应收方向语义相反）；`Write Off → 核销`。
- 机翻与漏翻：`To Bill → 待开票`（上游“待开费用清单”）、`To Deliver → 待发货`、`To Deliver and Bill → 待发货和开票`（“出货”改大陆惯用）、`Stock Reposting → 库存重算`（上游漏翻）。
- 规范字：`Period Closing Voucher → 期末结账凭证`。

`To Deliver`/`To Bill` 系列为单据状态标签，不属于 Submit/Cancel 状态机动词红线范围，词表 note 已注明。候选词中其余 26 项（成本中心、会计科目表、科目、批次、计量单位等）上游翻译合格，未重复覆盖。

红绿证据：先将单测行数断言 50→59 确认 **1 failed**；补词表并重新生成后单测 **7 passed**、集成 **3 passed**。按 `daily → beta → alpha` 逐站 `clear-cache` 后，三站回读 9 个新词条均返回预期译文。

1. 只编辑 `config/terminology/glossary.csv`，逐项确认固定版本精确 msgid 与 context；不得手改生成文件。
2. 生成翻译包：

   ```sh
   PYTHONPATH=. .venv/bin/python infra/build_translation_packs.py
   ```

3. 验证生成器、制品防漂移与集成行为：

   ```sh
   PYTHONPATH=. .venv/bin/python -m pytest tests/test_translation_packs.py -q
   PYTHONPATH=. .venv/bin/python -m pytest tests/integration/test_translation_pack.py -q
   ```

4. 更新 App 制品后，按上文顺序逐站执行 `bench clear-cache`。命令幂等，可重复执行；失败时立即停止并报告具体站点。
5. 用户在浏览器硬刷新，并在实际 Desk 高频页面核对译文。服务端通过不替代页面可见性验收。
6. ERPNext/Frappe 升级后重新核对精确 msgid、context、App 顺序与上游文件哈希；不自动生成近似键。

新站点无需额外推送 API：安装 `dsherp_bridge` 且语言为 `zh` 时，Frappe 原生加载随 App 分发的 `zh.csv`。

## 回滚

1. 对需撤销的术语功能提交执行 `git revert`，恢复上一版主词表、生成器或生成 CSV；不要直接修改上游文件。
2. 若回滚涉及主词表，重新运行生成脚本并确认防漂移测试通过。
3. 按 `daily → beta → alpha` 顺序逐站执行 `bench clear-cache`。
4. 浏览器硬刷新并复核高频页面。

企业 `Translation` 记录独立于 App 提交，不在平台回滚中创建、覆盖或删除。

## 最终回归与共享环境争用

- 重新生成 `zh.csv` 后字节未变化，SHA-256 为 `e30c1802a400321ff28166047c01cd6a101820a9d4738b1a0f11d8ff2fabc83d`。
- 术语单元与集成组合：**10 passed / 8.41s**。
- README 最小验证：**18 passed / 21.17s**。
- 完整 Python 回归最终结果：**202 passed / 475.23s**。

完整回归前两次曾分别出现 1 项行锁超时和 6 项 run 被抢先领取；术语 10 项始终全绿。现场进程清单显示另一个本地任务正同时通过 `docker exec` 使用同一 validation backend，失败用例单独及与前序用例组合复跑均通过。待并发进程结束后原命令完整重跑 202 项全绿，因此没有通过跳过测试或修改业务代码来制造结果。

## 验收边界

- 已验证：开发资料、TDD 单元测试、容器内真实 Frappe/ERPNext 翻译链、企业 Translation 覆盖、三站本地激活、Alpha 真实 Desk 可见标题、daily 备份恢复。
- 未执行：生产企业部署、真实生产业务写入、付费模型或 DSH 调用、wheel 构建验证、`zh-TW` 或其他语言包。
- 本功能只有本地合成环境可见，不代表远端、生产或最终用户已经获得该更新。
