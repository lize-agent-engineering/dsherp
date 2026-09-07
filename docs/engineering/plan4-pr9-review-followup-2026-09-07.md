# PR #9 第二轮复核：19db846

日期：2026-09-07。对象：`plan4/governance` / `19db84664422a5c24f117cb6f518466a6d95bed8`；本地与 [PR #9](https://github.com/lize-agent-engineering/dsherp/pull/9) HEAD 一致，44 个提交，PR 仍为 OPEN。

结论：**原九项中六项可关闭，R3、R4、R6 部分修复，仍不建议放行 PR。** 这次审查没有更改业务实现或提交、推送、合并。原审查单与[执行方回应](plan4-pr9-review-response-2026-09-07.md)保留，本文件记录独立复核结果。

## 仍需修正

### R6 · P1：数据库清除和会话目录删除之间仍有竞态

位置：[admin._user_clear_script / delete_user_data](../../dsherp/admin.py)，清除脚本的 `frappe.db.commit()`，以及宿主随后执行 `sessions_module.remove` 的位置；[sessions.remove](../../dsherp/sessions.py)。

现有修复可以等待先前任务结束，也能在数据库清除前重新检查在途任务；SSO 强制条件下缺少授权时拒绝执行的修复亦通过集成验证。但 User 行锁在数据库清除脚本提交时已经释放，宿主删除目录发生在此后。中间没有继续阻止新提交/领取的保持状态。

因此允许这样的次序：清除提交 → 用户在原会话中发起新运行 → 新执行者创建原会话下的新 scope 目录 → 宿主按原会话名删除整个目录。原删除计划没有包含新目录，结果仍报告 `applied=true`。

对真实 `delete_user_data` 和 `sessions.remove` 调用链使用替身 Bench，在清除提交返回点模拟新执行者落盘，结果为：

```json
{"applied":true,"new_session_survived":false,"new_session_in_original_export":false}
```

这是编排层的确定性复现，没有真实模型调用，也没有将它描述为已跑通 HTTP/worker 全并发链。修正应让阻止新执行者的窗口覆盖到目录处理完成，或仅处理冻结范围内的旧目录并保证不与执行者共用。可复用已有站点保持/领取闸门，不能只在文件删除前再做一次无锁查询。

### R3 · P1：恢复副本本身不可写时，新 secret 仍会丢失

位置：[admin.rotate](../../dsherp/admin.py)，第 1778–1786 行附近。

无目的地先拒绝、profile 写失败后保留副本这两条已修好。但远端 `ensure_runtime_identity(..., rotate=True)` 仍先执行，随后才创建恢复目录、写入恢复文件；副本写入也在处理 profile 写入异常的 `try` 之外。

使用实际 `rotate`、现有合成 RotateBench 和真实临时文件系统，令 `<runtime>/rotations` 为文件而不是目录，得到：

```json
{"error":"FileExistsError","remote_key_rotated_before_refusal":true,"profile_still_old":true,"recovery_files":0}
```

这与“profile 写失败但副本已经成功落盘”不同：本次失败发生时，旧凭据已失效，两个交付位置都没有新 secret。恢复目录创建失败应在签发前发现；副本本身写入失败时也必须给出可取回新凭据的恢复路径。不能把写入副本当作不会失败的步骤。

### R4 · P2：第一次 provision 仍先公开空站，再设置维护模式

位置：[restore_site](../../dsherp/restore_drill.py)，第 395–400 行附近；[provision_tenant](../../dsherp/admin.py)，创建站点、`ensure_enterprise`、保存租户清单和渲染入口的调用次序。

解密失败与第二次 provision 失败保持关闭的原反例已修好；`restore_into(reopen=False)` 也正确。不过“从建站起关闭”的承诺仍不成立：第一次 `provision()` 完整返回之后，才设置 `maintenance_mode=1`。实际 `provision_tenant` 此前已经创建站点，将企业设为 Ready，并更新入口。

本轮将真实 `provision_tenant` 接回真实 `restore_site`，仅替换外部 I/O，观察到：

```json
[{"stage":"site_created","maintenance":0},
 {"stage":"enterprise_ready","maintenance":0},
 {"stage":"site_reused","maintenance":1},
 {"stage":"enterprise_ready","maintenance":1}]
```

这不是说旧备份中的业务数据在解密失败后仍开放——该原问题已经修好；残余是首次建站/发布入口的窗口。恢复专用的建站准备应保持 Provisioning/维护状态，全部验证成功后才标为 Ready 并发布入口。现有测试把 `provision` 替换为 no-op 或直接抛错，因而不能覆盖这一组合行为。

## 九项对照

| 原项 | 本轮判断 | 独立依据 |
| --- | --- | --- |
| R1 运行不可改写 | 关闭 | 相关真实集成测试通过；额外尝试取消时夹带 owner、creation 修改，均被 Frappe 原生 `CannotChangeConstantError` 拒绝，普通取消仍可用。探针均 rollback |
| R2 并发唯一绑定 | 关闭 | 真实并发插入集成测试通过；平台真实 `active_binding` 唯一索引存在，启用绑定缺键数为 0，两个迁移 patch 已执行 |
| R3 凭据交付 | 部分修复 | 无目的地/普通 profile 失败路径已通过；恢复副本自身不可写反例仍在 |
| R4 恢复保持关闭 | 部分修复 | 原解密和第二次 provision 失败路径已通过；第一次 provision 的窗口仍在 |
| R5 数据/密钥分卷 | 关闭，限编排层 | 修复代码和非集成测试已分别验证两侧卷、根目录与清理路径；未替代真机恢复验收 |
| R6 删除与在途执行 | 部分修复 | 取消/等待及缺授权拒绝已有证据；数据库提交到文件清理之间仍可误删新执行者目录 |
| R7 运行令牌位置 | 关闭 | 真实集成验证缓存持有与 finish 回收；validation/beta/daily 三站列均已消失，drop patch 均有未跳过执行记录 |
| R8 递增绑定版本 | 关闭 | 真实平台集成验证 `1 → 1 → 2 → 3 → 4 → 5`，对应插入、续签、停用、重启用、改绑、改回 |
| R9 未知用量 | 关闭原反例 | 原缺失及半份 usage 行为、月报未知计数与完整性标记通过非集成复跑；UTC 月份口径已显式标注 |

## 本轮验证及环境

- 非集成完整复跑：`.venv/bin/python -m pytest tests --ignore=tests/integration -q`，**659 passed in 111.76s**。
- 定向真实集成：`test_audit_immutability.py`、`test_membership_binding.py`、`test_run_grants.py`、`test_sso_machine_auth.py`，**8 passed in 64.92s**。停常驻 worker 和 scheduler 后执行，使用原有 conftest，没有绕过测试。
- 首轮定向集成在 fixture 的队列保护处得到 8 个 setup error，未执行行为断言。没有删除非许可队列任务；恢复消费者处理队列后，改为先停生产者、确认队列为空，再停消费者，随后以同一测试命令通过。
- 210 项完整集成是执行方报告；本轮没有重跑完整 210 项，也没有将它记为本轮独立通过。没有进行真实 provider、浏览器或真机恢复/部署验收。
- 四站相关迁移状态已只读核实；本轮没有重新 migrate。复核结束时 scheduler 两容器运行、常驻 worker 为 running；validation/daily 心跳距核验约 0.6 秒，二站在途运行均为 0。
- 额外事务探针的临时用户、运行、会话及所生成联系人已清理并回读确认。一次探索中的事务竞争探针未产出有效判定，不作为发现依据；对应临时脚本已删除。保留下面的可复现脚本。

三个残余问题的编排层探针：

```sh
.venv/bin/python work/plan4-pr9-review2/local_probes.py
```

[复现脚本](../../work/plan4-pr9-review2/local_probes.py)使用真实宿主编排函数，替换外部 I/O，所有文件均在临时目录中并自动清理。它是审查证据，不是新增交付门禁。

仍停在阶段 5。G3 异机、真实 RPO/RTO、systemd 实际触发、`restore-site` 真机全流程，以及数据库直接访问绕过控制器的既有边界均保持原状态；平台报表 UI 另排的范围差异也没有在本轮被默认为完成。
