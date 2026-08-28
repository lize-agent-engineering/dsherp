# 原生上下文 Agent 实施证据

## 2026-08-29：入口与第一批前端 TDD

持续目标 active，main 本地实施，不推送。完整范围见 [计划](../superpowers/plans/2026-08-29-context-agent-sidebar.md)。

- 现场 HEAD 起点 ffb7d52，工作区干净；计划及旧方向替代标记提交 b459297。
- 固定 Frappe 列表源码实读：get_filters_for_args()、get_checked_items(true)、cur_list 由 list_factory 切页设置；表单 is_dirty() 检查 __unsaved。
- 页面快照 7 项先失败（新适配器缺失），最小实现后全部通过。包括深冻结、发送时身份/版本、默认不含表单正文、显式字段/子表列、选中名称/筛选、旧表单不串页、未知页面及超预算拒绝。
- 侧栏 6 项先失败（未实现），实现后通过。打开仅恢复、无蒙层、关闭保留输入、发送时取快照、新会话屏蔽迟到结果、拒权清正文、取消按运行 ID、已完成历史仍重新授权轮询。测试 API 是边界替身，不能据此声称真实恢复/取消完成。
- 首次 UI 测试发现 Ant Design loading 图标影响按钮可访问名称；添加固定 aria-label，恢复期间禁用发送。重开测试等待真实异步恢复完成。
- `npm test`：7 files / 48 tests passed；`antd lint src/ContextSidebar.jsx --format json` 无问题。未新增 CI、发布门禁、角色体系。
- 尚未挂载侧栏到 Desk，未实现新业务 Site API、DSH 插件或跨进程恢复，未运行新真实模型/ERP/UI 验收；阶段一不标完成。

## 旧服务收敛（用户明确要求）

用户要求“不要加太多的门禁，之前旧的服务该停停”。执行前只读查询旧平台 DS Agent Task：2 条 Succeeded，无 Queued/Running。向已核实的旧付费 worker PID 39450 发送 SIGTERM；随后 pgrep 无进程、dsherp-agent 容器列表为空。没有丢弃运行任务、没有新付费调用。

保留原生 ERP、平台身份、数据库、Redis、原生 worker/scheduler/WebSocket，供后续会话、SSO 和配置验证；未动其他项目。旧付费工作台已不再接受实际执行，不再以该入口作为新功能可用性说明。不删除旧历史、凭证和数据卷。

下一步：接原生全局侧栏及受限同源 API；业务 Site 持久会话和权限来源；固定版原生 resume/cancel 插件协议测试。资源仍沿用现有授权，不默认增加常驻服务。

## 2026-08-29：固定版协议基础闭环

用户追加明确授权：按计划使用项目既有模型配置，不再逐次询问。保持资源预算、隔离验证及非生产写入边界；本批自动化仍仅使用本地模型替身，没有新增付费调用。

`runtime/context-plugin.cjs` 通过公开 `ctx.loader.import()` 引用打包 Runtime 内的 JsonRpcLineTransport、HarnessSdkJsonRpcServer 和 createUserMessage。只创建一个 stdio transport；复用官方握手/事件，项目显式 open/cancel 方法独立命名。直接外部 require 无法解析打包依赖；固定版 Loader 的公开 import 已实测可用，没有改上游私有字段或核心。

`config/dsh-context.yml` 为独立最小组合，目前无工具、skills 暂未接入，不能用于正式业务执行；接领域工具和校验后的业务 skills 前不得当作阶段一完成。固定版插件 name 不支持配置表达式，因此使用相对配置文件的静态模块路径。

真实 Runtime + 本地 SSE 模型的 5 项测试：

1. 首进程创建、完成消息、退出码 0；第二进程显式 `ctx.agents.resume`，实际发往模型的第二轮请求包含此前用户消息及 assistant 消息，第二进程退出码 0。
2. 缺失持久会话的 resume 报错，零模型调用。
3. prompt 前必须 open，重复 open 拒绝，零模型调用。
4. 固定版原生 create 不自行拒绝已持久化 ID（测试先失败）；插件通过原生 persistence.list 检查后明确要求 resume，不覆盖日志。
5. 取消实际在途 HTTP 请求，原生 whenIdle 收敛，`turn/end.reason.kind=aborted`，仅一次模型请求，无后续请求。最初使用可变取消原因对象，真实日志出现 non-JSON-serializable data、缺少 turn/end；冻结传入的 readonly cause 后修复。没有伪造/补写原生日志，没有修改 Runtime。临时错误观察插件已删除。

官方固定来源：[SDK server](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/sdk/server/src/server.ts)、[Agent 类型](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/core/agent/src/index.ts)、[公开 Loader.import](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/vendor/loader/src/config/tree.ts)、[原生取消](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/core/agent-loop/src/agent.ts)。实现前已读取相关源码。

本批复验：`.venv/bin/python -m pytest tests/test_*.py -q` **35 passed / 44.26s**；前端 **51 passed / 4.84s**；原有 bundle 构建通过，Ant Design 检查无问题。新侧栏尚未加入 Desk bundle，因此不把该构建当新侧栏实际 UI 验证。未重跑全 ERP 集成套件。

## 当前接续清单（全部尚未完成）

- 先完成侧栏独立 bundle 与原生 app_include_js 接入、真实业务 Site 会话/模型运行 DocType/API；现有 `frontend/src/context-api.js` 已定义同源 GET/POST/CSRF 接口，3 项 TDD 通过，服务端还不存在。
- 完善插件并发 open/shutdown 的清理（当前 open 时 Map 有临时 null 预留，关闭必须等待创建，不能对预留项 dispose）；实现跨进程单写者锁及异常退出恢复，不以进程内 Map 声称全局互斥。
- 加入业务上下文来源授权、权限/配置版本轮换、全部模型调用预算及压缩路径保护、固定摘要业务 skills；当前组合只用于协议验证。
- 复用现有单容器 worker 的隔离与资源限制，改为业务 Site 的会话/运行队列与短期 capability；不重启已停止的旧付费 worker，不把旧平台任务搬入新企业。
- 接真实模型/ERP及真实原生页面验收，再做 SSO、阶段二 HITL、阶段三配置预览发布、阶段四日常 Site 和备份恢复。
- 旧 Demo 独立入口保留，正式导航移除尚需实施；旧平台历史只读兼容尚需实现。

整体持续目标 active，阶段一仍进行中。以上不是生产部署或完整阶段交付。

## 2026-08-29 接续：创建/关闭竞态与业务 Site 会话

- 插件创建期间关闭的两项确定性交错测试先失败（对 null 预留执行 dispose），改为等待在途原生创建收敛，再释放实际 handle；创建失败仍回报原请求，但不破坏清理。`node --test runtime/context-plugin.test.cjs` 两项通过，真实 Runtime 五项复跑通过。尚不能据此宣称跨进程单写者已实现。
- 新增业务 Site `DS Conversation` 与 `DS Model Run` 两个原生 DocType，平台旧任务不搬迁。经原生 `bench --site dsherp-validation.localhost migrate` 仅同步 alpha 隔离测试站点。没有修改其他项目或生产数据。
- `dsherp_bridge.context_api` 的 send/get/list/cancel 接口使用当前 Site 身份，普通用户不能通用 REST 读取内部运行表。原生 User 行锁串行化该用户提交，request_id 与内容摘要校验防重复。会话消息绑定服务端校验过的页面快照及真实 modified，当前支持 Item/Customer 标量上下文及列表读取权限；Sales Order、新未保存单据与子表服务端能力仍待接入。
- 入队前检查真实 DocType、记录和字段权限，历史展示重新检查快照来源权限；后续工具结果/摘要来源授权和权限指纹尚未实现，当前没有任何模型回答或工具结果写入接口。
- 新队列尚无消费执行器：send 返回真实 Queued，排队取消返回 Cancelled；不伪造模型回答，不把排队当作服务已可使用。暂未挂入 Desk，用户现有页面不变。
- `tests/integration/test_context_sessions.py` 首次因接口缺失返回 417 而失败；实现和原生迁移后 **5 passed / 4.90s**。覆盖归属、真实 DB 持久化/重复请求、刷新不执行、越权记录/字段、取消后继续、活动运行冲突、伪造上下文及旧版本拒绝。测试创建的会话和运行由 fixture 按确切 ID 删除，没有保留合成任务进入后续真实执行。

下一步：业务 Site 运行领取/短期凭据/当前用户工具回读，隔离容器执行器与稳定目录单写者锁；然后将侧栏 bundle 挂到 Desk。新会话后端还需并发事务、撤权、来源摘要、断线和模型预算的真实验收。旧 worker 保持停止，不自动恢复旧聊天入口。

## 2026-08-29 接续：单写者与运行能力凭据

- `dsherp/session_runtime.py` 使用非阻塞 OS flock，稳定目录和 writer.lock 不随单轮删除。真实独立进程持锁时第二进程被拒；杀死持锁进程后可重新取得，异常退出释放、拒绝符号链接锁文件。`open_runtime` 持锁启动固定 Runtime，经公开 client.request 显式 open/resume，finally 关闭进程后才释放锁。它是容器内调用入口，不宣称本身完成宿主环境或租户授权隔离。
- 四项新测试先失败，再实现；与原协议测试共同 **9 passed / 5.37s**。真实进程恢复请求包含上一轮信息；模型仍是本地替身。
- `context_execution.py` 新增 claim_run/run_tool/finish_run。领取身份为 Site 配置指定的普通服务用户，不新增业务角色；生产配置尚未开通。运行 cap 随机生成仅存摘要、180 秒、绑定运行，结束失效；模型只收到该 cap，不接触服务身份密钥。
- 工具仍仅 Item/Customer 的 schema/record/search，服务端切换到 run.owner 校验真实权限，退出恢复原身份。来源保存所读字段和记录，历史显示及成功结束重新校验来源。缺实际读取时拒绝成功；结束后工具凭据不能继续用。
- 实际权限拒绝的排队任务会记 Failed 并停止阻塞后续领取：该回归先失败再修复，未修改测试用户权限，只修改并清理本次合成运行的上下文。
- 真实站点函数/DB 能力测试使用进程内合成 service-user 绑定，不保存服务账号配置；没有付费调用，不等于实际 Docker/MCP/HTTP 能力链验收。
- 更新 alpha DS Model Run 原生 schema，并重启本项目 backend 加载新代码。首次紧接 restart 的 HTTP 回归遇到连接断开（服务未就绪）；随后 ping=pong，再完整复跑会话/执行/拒绝队列三文件：**7 passed / 5.59s**。未伪装第一次为通过。旧付费 worker 检查仍不存在。

仍需接续：开通专用运行服务账号、受限 HTTP/MCP、新容器执行器及 stable session 目录挂载；明确原生恢复判定、失败和取消状态轮询、跨进程权限/运行配置/skills 版本轮换；领取与取消的数据库并发再检查；覆盖所有 LLM/压缩调用的授权预算。当前无消费新队列的进程，侧栏未挂 Desk。未到阶段一验收，更未到整体完成。
