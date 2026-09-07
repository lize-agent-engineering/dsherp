# 计划 4 / PR #9 阶段 5 审查

日期：2026-09-07。审查对象：`plan4/governance`，HEAD `605429047237714f95a3901d98dc8b18a87648bd`。

结论：**暂不建议合并 PR #9，也不能将计划 4 标为全部完成。** 用户级短期凭据的方向可以保留；以下实现问题应先修正。G3 的物理异机恢复和正式 RTO 继续保持未验收。本次只做审查、隔离探针和记录，没有修改业务实现、提交、推送、合并或部署。

## 核验范围与证据

| 项目 | 本轮独立核验 |
| --- | --- |
| PR 状态 | [PR #9](https://github.com/lize-agent-engineering/dsherp/pull/9) OPEN；35 个提交；远端 HEAD 与本地一致；base 为 `30cf6fb`；没有返回 CI check 记录 |
| G2 前一切片 | [PR #7](https://github.com/lize-agent-engineering/dsherp/pull/7) 已合并，merge commit `30cf6fb`；本次没有重新执行其完整升级/回滚验收 |
| 非集成 | `.venv/bin/python -m pytest tests --ignore=tests/integration -q`：`637 passed in 94.87s` |
| 四站 patch | 从当前各站 `Patch Log` 只读核实：validation / beta / daily 的四个 bridge patch，以及 platform 的凭据 patch，均有未跳过的执行记录。这不等同于本轮重新执行 migrate |
| 真实 Frappe | 当前容器 `16.31.0`；已核对安装源码中的 `generate_keys`、`validate_oauth` 和 API key 校验 |
| 审计改写 | 在 validation 对一条已成功结束的合成运行调用 `Document.save(ignore_permissions=True)` 修改 answer，成功；随后 rollback，回读确认恢复原值 |
| 并发绑定 | 在 platform 创建临时合成企业，两个独立数据库事务通过真实 DocType 控制器插入同一 erp_user 的两条启用绑定；两次插入都在任一事务 rollback 前完成。两条绑定均 rollback，临时企业已删除并验证清理。首次探针超时，不计入成功证据；增加阶段输出后的探针完成 |
| 故障路径 | 宿主命令使用现有测试替身隔离外部 I/O，调用真实 `rotate` / `restore_site` / `delete_user_data`；另直接调用真实 `_actor` 验证清空 grant 前后的分支。没有轮换既有运行身份或改动已有成员绑定 |
| 本轮未执行 | `test_desk_sso` / `test_platform_identity` 的完整 22 项、真实 provider、浏览器验收、完整备份与恢复、异机部署、systemd 定时触发。执行方原报告保留，但不转记为本轮独立通过 |

## 需修正的发现

### R1 · P1：完成的运行记录仍可通过 Document API 改写，G7 不成立

位置：[DSModelRun](../../frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_model_run/ds_model_run.py)、[G7 判据](../superpowers/specs/2026-09-03-production-hardening-design.md)。

控制器只新增 `on_trash`，没有对已经形成的运行事实限制改写。真实验证站中，Administrator 对 `status=Succeeded` 的运行修改 answer 并 `save(ignore_permissions=True)`，保存成功。这个反例没有使用直接 SQL 或 `frappe.db.delete`，因此不属于证据文档已说明的数据库直连边界。`track_changes` 可以记录变更，但不能兑现“任何角色不可删改”。

需要保留受信服务端的合法运行状态转换，同时拒绝普通文档修改路径改写终态运行事实；再以普通角色、管理员和受信运行流程分别验证。不能只增加删除测试。

### R2 · P1：一个业务用户一个启用绑定，当前并发下无法保证

位置：[DSMembership._sole_holder_of_the_business_user](../../frappe_app/dsherp_platform/platform/doctype/ds_membership/ds_membership.py)，第 23–29 行。

实现为查询是否冲突，然后分别写入。当前真实站的唯一索引只有 `PRIMARY(name)`，没有保障同企业、同业务用户的启用绑定唯一性。真实并发探针的两个事务均完成插入：插入时间分别为 `294225.484`、`294225.587`，rollback 分别为 `294228.490`、`294228.590`（同一容器 monotonic 秒）。两个合法平台成员因此可以同时通过这条规则。

应使用数据库唯一约束或串行化的事务写入保证不变量，同时处理升级前已经存在的重复绑定。相关验收应实际同时提交两个绑定；现有测试中对 `erp_user` / `frappe.throw` 源码字符串的断言不能证明这一行为。

### R3 · P1：不带 profile 的 runtime 轮换会丢失新 secret

位置：[admin.rotate](../../dsherp/admin.py)，第 1681–1699 行。

`paths=[]` 仍调用 `ensure_runtime_identity(..., rotate=True)`；随后既不保存新 secret，也不在返回值中提供它，却提示“密钥只在本次输出中出现过”。对真实编排函数使用合成 Bench 的探针结果：

```json
{"rotation_executed":true,"profile_written":false,"secret_returned":false,"result_says_secret_was_output":true}
```

这会使现有 worker 持有已经失效的凭据。最短修正是：在签发前要求有可交付的目的地；没有目的地就拒绝。文件可解析的预检还不足以覆盖实际写入失败，需要保留失败后的可恢复交付路径。

### R4 · P1：冷启动恢复在验证完成前退出维护状态，失败后未恢复封闭

位置：[restore_into / restore_site](../../dsherp/restore_drill.py)，第 223–225、382–402 行。

共享的 `restore_into` 在恢复后写 `maintenance_mode=0`，而冷启动 `restore_site` 使用的是正常部署的 bench，并已执行 provision。后续只有“比较结果不一致”分支重新设置维护状态；抽样解密异常、快照读取异常或第二次 provision 异常并没有相同处理。

使用真实 `restore_site` 和 `restore_into`、替换外部 I/O 并在 `decrypt_check` 注入异常，结果为：`maintenance_mode_after_failure=0`。本次没有在真实站点执行故障恢复；这是命令编排的确定性复现。

冷启动路径应从建立站点起保持不可服务，所有验证和本机配置完成后才开放；任意中间异常都保持关闭。隔离演练栈不对外提供服务的条件不能自动套用到正常生产栈。

### R5 · P1：冷启动恢复把密钥副本落入数据备份卷

位置：[restore_site](../../dsherp/restore_drill.py)，第 370–378 行。

`data` 和 `secrets` 两次 restic 恢复使用完全相同的 `*-backups:/incoming` 挂载。探针捕获的两个挂载均为 `dsherp_tenant-backups:/incoming`，只是卷内目录不同。函数完成后也没有清除此处的密钥副本。数据侧备份容器可以读取整个数据备份卷，恢复一次便破坏了原本的数据/密钥隔离。

应复用已有数据卷与密钥卷的权限边界，分别接收两半；可交付的失败诊断也不能把 secret 留在数据卷中。`restore-drill` 已经有两只独立 fetch 卷，可作为复用依据。

### R6 · P1：个人数据删除可清除仍在运行任务的授权，且任务随后还可写回内容

位置：[delete_user_data](../../dsherp/admin.py)，第 1443–1470 行；[删除字段](../../dsherp/user_data.py)，第 31–38 行；[_actor](../../frappe_app/dsherp_bridge/context_execution.py)，第 36–47 行。

删除没有排空、取消或拒绝该用户的在途运行，直接对所有选中运行清空 `platform_grant`、question、answer 等，并删除原生会话目录。以 `Running` 行调用实际编排函数仍返回 `applied=true`。真实 `_actor` 的分支探针表明：模拟已撤销授权时，清空前拒绝；清空 `platform_grant` 后跳过平台授权校验并允许进入 actor 上下文。该探针没有执行业务写入，也没有把它扩大表述为已完成整条越权链。

需要先形成没有在途执行者、不会再写回这些内容的删除窗口；缺失授权也不能成为后台执行的放行条件。清除字段后保留行和 capability，并不能构成删除完成的证明。

### R7 · 计划缺项：platform_grant 仍长期保存在 DS Model Run

位置：[send_message](../../frappe_app/dsherp_bridge/context_api.py)，第 312、354 行；[运行字段](../../frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_model_run/ds_model_run.json)；[工作流 B](../superpowers/specs/2026-09-03-production-hardening-design.md)，第 87 行。

上位设计明确要求从运行行移除 `platform_grant`、只保留在 session。当前新运行仍写入该字段，后台执行继续读取它；当前 validation 站 70 条运行中有 4 条非空。正常结束路径没有清空它。`delete-user-data` 在用户申请删除时清除，不能替代普通运行的凭据生命周期。

这是本轮宣称覆盖 S2/S4/S9 时遗漏的既定要求。应完成保留后台撤权能力的替代链路及存量迁移；不能直接删列而破坏后台权限检查，也不能仅修改完成状态文案后算实现通过。

### R8 · P2：绑定内容哈希不能替代撤销版本

位置：[binding_version](../../frappe_app/dsherp_platform/api.py)，第 13–21 行；[validate_grant](../../frappe_app/dsherp_bridge/sso.py)。

排除凭据轮换引起的 `modified` 变化是正确的，但当前改成字段内容哈希。对实际 helper 的探针显示：启用 → 停用 → 再启用，得到原来的 `binding_version`。根据 `validate_grant` 比较身份内容的实现，只要原 OAuth token 仍有效，恢复原绑定内容会使旧授权再次匹配；本次没有重新跑完整浏览器会话的撤销/重启用链路。

使用独立递增的绑定版本即可区分真实绑定变更和凭据续签，无需新增哈希。这也符合仓库“禁止用哈希代替版本号”的明确约束。验收需要覆盖停用/重新启用、改绑后改回，以及单纯续签不改变绑定版本。

### R9 · P2：未知用量在月报中重新变成零

位置：[USAGE_SCRIPT](../../dsherp/admin.py)，第 1236–1241 行；[summarise / monthly](../../dsherp/usage.py)，第 76–88、147–162 行。

运行行虽保存 `usage_unknown_calls`，月报取数却不读取它，聚合结果也不暴露未知量。只有 input、缺失 output 的部分 usage 还会被当作完整已知值。直接调用实际聚合函数，输入一条 usage 缺失事件和一条只有 `prompt_tokens=12` 的事件，月报显示 input=12、output=0，完全没有未知标记。

应保留已知 token 合计，并在站点及总计中带出缺失/不完整量，不能让消费者把它理解成完整账单。另外，代码目前把站点本地时间转换到 UTC 后归月；“按站点时区出月报”的文案需要与这个实际口径统一。

## 凭据规则的裁决建议

建议保留用户级凭据：当前原生 User 确实只有一份 api_secret，12 小时有效、剩余不超过 4 小时才续签，可作为当前试点参数；同一成员窗口充裕时复用，避免每次登录都更换密钥。一个业务用户只有一个启用平台绑定，也符合操作者可追责要求，但必须先修正 R2，不能把应用层预检查当作并发保证。

同时要限定承诺：续签实际会替换旧 secret，不能据此承诺任何在途 API 请求都不受影响。应对续签竞争、交付失败和使用旧凭据的在途请求进行行为验证；浏览器会话保持和一次 API 调用成功是不同的结果。绑定版本采用独立递增字段，续签不增加它，真实绑定变化才增加。

“每次登录一把、互不影响就必须自建令牌校验”这一推论过强。Frappe 已有原生 OAuth Bearer Token 和校验路径，官方 [REST API 的 Access Token 章节](https://docs.frappe.io/framework/user/en/api/rest#3-access-token)明确支持 Bearer access_token，当前安装源码也包含 `validate_oauth`。将业务站改为该模型仍需要单独设计接入及撤销链路，本轮没有验证，也不建议为修当前问题立即扩大认证范围。

## 整个计划的阶段 5 判定

| 范围 | 本次判断 |
| --- | --- |
| G2 前一切片 | 已合并；保留已有验收记录，不在本次重复宣称通过 |
| 备份、异地同步、恢复工具 | 实现和本机证据已存在；冷启动路径有 R4/R5，不能用隔离 restore-drill 的成功替代 |
| G3 | 未验收：仍需真正异地副本、另一台主机恢复全部站点、RPO/RTO 实测、真实 systemd 触发 |
| T2 / G7 | R1 真实反例推翻“不可删改”完成结论 |
| T3 | Dynamic Link 与不阻塞业务单据处置的实现存在；本轮非集成测试通过，没有新做真实取消/删除验收 |
| T4 | 导出/删除实现存在；R6 阻塞完成；已有删除与保留边界不能代替在途任务处理 |
| T5 / T8 | 计量、分页和索引实现存在；R9 需修正；平台聚合报表展示目前交付形态主要是宿主 CLI，需与上位设计对齐 |
| S2 / S4 / S9 | 方向可保留；R2/R3/R7/R8 需处理，随后验证续签并发与失败交付 |

本次建议继续停在阶段 5，先按上述真实失败行为修正并独立复验，再讨论 PR 合并。G1/ACME 与 G3 的外部验收仍按既有裁决保留，不能在本地研发通过后自动勾选。
