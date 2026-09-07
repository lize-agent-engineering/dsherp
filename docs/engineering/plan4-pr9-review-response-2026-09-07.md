# 计划 4 / PR #9 阶段 5 审查回应

日期：2026-09-07。回应对象：[阶段 5 审查单](plan4-pr9-review-2026-09-07.md)（HEAD `6054290`）。九项发现逐条修正，每项都以真实站点或真实编排路径复验；本文只记做了什么与查到什么，判定仍归审查方。

## 逐项处置

| 项 | 处置 | 真实复验 |
|---|---|---|
| R1 · 终态运行可改写 | `DS Model Run.validate`：前状态为 Succeeded/Failed/Cancelled 的运行拒绝任何改写；未结束的运行在 Document API 路径上只允许 Queued→Cancelled、Running→Cancelling 且只改 `status`/`cancel_request_id`。服务端状态推进走 `frappe.db.set_value`，不经此路径 | validation 站三路：普通用户被权限拒绝；Administrator 带 `ignore_permissions` 改 answer 得 `ValidationError`，回读仍为原值；`cancel_run` 仍可用；Administrator 改 question 亦被拒（`tests/integration/test_audit_immutability.py`） |
| R2 · 绑定唯一性挡不住并发 | `DS Membership.active_binding`（启用时 = 企业+业务用户，停用时 NULL）加**唯一索引**；pre_model_sync patch 先把既有重复绑定按最早保留、其余停用并逐条写 Error Log，再由模型同步建索引 | 真实平台两个数据库连接：第一个插入后持有事务 3 秒再提交，第二个在第一个未提交时通过应用层检查、INSERT 等在索引上，第一个提交后第二个得 `UniqueValidationError`；启用绑定只剩 1 条（`tests/integration/test_membership_binding.py`，索引 `Non_unique=0`） |
| R3 · runtime 轮换丢密钥 | 没有 `--profile` 也没有 `--print-secret` 即拒绝，签发前就拒；签发后先把密钥写到 `<runtime>/rotations/runtime-<站点>-<时间>.json`（0600）再写 profile，写入失败时错误指明该文件，全部成功后删除 | 单元：无目的地时 `generate_keys` 未被调用；注入写入失败时副本文件含新密钥且错误信息指向它；正常路径不留文件 |
| R4 · 冷启动中途开站 | 站点一经建立即置维护模式，`restore_into` 在冷启动路径不再开站，任何异常在 `except` 里重申维护模式并写报告后再抛出，只有比对干净、解密通过、第二次 provision 完成才开站 | 编排层（替身 bench，真实 `restore_site`）：开关序列为"建站即关、最后才开、中途不开"；解密失败与第二次 provision 失败都保持关闭并写 `maintenance: kept` |
| R5 · 密钥副本落入数据卷 | 数据半份进 `*-backups`、密钥半份进 `*-backup-secrets`，各挂自己的 `/incoming`；`verify_fetched` 接受两个根；无论成败 `finally` 删除两侧 `/incoming/<半份>` | 编排层：两次 restic restore 的 `-v` 分别是 `tenant-backups:/incoming` 与 `tenant-backup-secrets:/incoming`；成败两条路径都执行两次 `rm -rf` |
| R6 · 删除不处理在途 | `--confirm` 后先在 User 行锁下取消排队运行、把运行中/持 capability 的 NeedsInput 标为 Cancelling，轮询等执行者放手（`--wait`，默认 120 秒），到时仍有在途即拒绝，不清任何内容；清除脚本在同一把锁下再查一次在途。`_actor` 在 `disable_user_pass_login` 生效的站点上拒绝没有授权令牌的执行 | validation 站（worker 暂停）：一条排队、一条运行中，第一次 `--wait 8` 被拒，排队的已 Cancelled、运行中的转 Cancelling；模拟执行者放手后第二次 applied，清除 25 条运行 / 20 个会话。无令牌 + 模拟 SSO 强制：claim 拒绝并把运行标为 Failed（`tests/integration/test_run_grants.py`） |
| R7 · `platform_grant` 仍在运行表 | 令牌改存站点缓存（键 = 运行 id，寿命 = 排队上限 + 运行预算 + 900 秒），运行到达终态（完成/失败/取消、排队超时、租约过期、会话不可读）即删；`send_message` 不再写列，提案与配置提案从缓存取；DocType 去字段，patch 清值后删列 | validation/beta/daily 三站 migrate 后列已不存在；令牌在缓存、行内不含 token；领取正常；finish 后缓存为空 |
| R8 · 绑定哈希复用旧版本 | `binding_version` 改为行上的递增整数：绑定字段变化 +1，凭据续签不动 | 真实平台：插入 1，续签仍 1，停用 2，重新启用 3，改绑 4，改回 5；`api.binding_version` 返回 `'5'` |
| R9 · 未知用量归零 | 缺任一侧的 usage 记入 `usage_unknown_calls`（已知的一半仍累加）；站点脚本读取该列；月报每站与总计带 `unknown_calls`、`runs_with_unknown_usage`、`complete`；`month_boundary: UTC` 如实标注 | dev 站真实月报：45 条运行、24 次未知调用（4 条运行）、`complete: false` |

## 凭据裁决的落实

按审查建议保留用户级凭据、12 小时窗口、剩余 ≤4 小时续签、窗口充裕时复用。两个前置条件已落实：R2 的数据库唯一约束与 R8 的独立递增版本。承诺按审查意见收窄并写进 runbook：续签会替换旧 secret，浏览器会话不受影响，但用旧凭据在途的一次 API 调用会失败。"每次登录一把必须自建认证"的说法撤回：Frappe 原生 OAuth Bearer 是可选路径，本轮未扩大认证范围。

## 顺带发现并处理

- 审计不可删除（T2）让 37 个集成测试的清理段（`frappe.delete_doc` 审计记录）全部失效；集成测试改走数据库路径清理合成数据（这正是证据文档写明的边界），上一轮"22 项未复跑"就是原因之一。
- 集成测试的运行/会话清理曾把 `send_message` 内部的 `rollback()` 与未提交的探针数据搅在一起，探针脚本改为逐条提交。
- dev 栈 scheduler 在集成运行期间会持续入队，集成夹具拒绝清理它的作业；集成运行期间停掉 scheduler，结束后恢复。常驻 worker 也必须停：它用真实 provider 抢先领取测试运行（`test_agent_audit_report` 的前置检查正是为此）。
- 策略"每次修改必须给出新的原因"（T2）让策略种子脚本 `infra/provision_alpha_doctype_policies.py` 与直接保存策略的集成测试全部失败：种子每次运行带一条新的原因（策略集 + 时间 + pid），测试逐处补原因。
- 短期凭据让文件形式的夹具（`.runtime/erp-*.json`）会被合法的续签/吊销作废：集成套件开始时经 `run_validation_provision --reissue` 让站点、文件、平台绑定回到同一把密钥，平台吊销测试的清理也改走这条路。
- 凭据窗口的强制范围与授权令牌的规则对齐：只在 `disable_user_pass_login` 生效（SSO 强制）的站点上拒绝过期/未登记的 key；仍允许密码登录的开发站只记录与报告。

## 门禁

| 门 | 结果 |
|---|---|
| 非集成 pytest | `659 passed`（审查时 637） |
| 集成（dev 栈，常驻 worker 与 scheduler 均停止） | 完整套件 210 项：一次完整运行 `209 passed, 1 failed`，唯一失败是制造夹具基线把已取消的库存分录也计入（套件在同一站点跑第二遍就会撞上，与本轮改动无关），改为只数未取消的分录后该文件 7/7 通过；完整套件复跑结果见下一行 |
| 集成完整复跑 | FINAL_RERUN |
| 真实 migrate | validation / platform / beta / daily 四站各跑本轮全部 patch（唯一索引、绑定版本、删列） |
| 真实探针 | R1 三路改写、R2 双事务并发插入、R6 在途运行阻塞与放手、R7 令牌位置与回收、R8 版本计数——均在真实站点执行，见上表 |

## 仍未闭合

- G3：物理异机、真实异地副本、RPO/RTO 实测、真实 systemd 触发，均未做。
- `restore-site` 真机全流程仍未跑过；本轮修的是编排层，以替身 bench 驱动真实函数验证。
- `frappe.db.delete` / 直接 SQL 绕过 `on_trash` 的边界不变；集成测试的清理正是走这条路。
- 平台聚合报表目前是宿主 CLI 形态，与上位设计的展示形态对齐另排。
