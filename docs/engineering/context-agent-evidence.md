# 原生上下文 Agent 实施证据

## 最新接续：配置领域的固定 Runtime/MCP 已接通

- configuration 领域接入同一个原生 Runtime/stdio 插件、3072 输出上限及既有总预算；工具仅 erp_read_configuration、erp_propose_configuration，无业务记录读取、执行、确认或发布工具。服务端限定当前用户、运行、领域和参数；先读取确切配置目标，再提出绑定来源运行的不可变包。
- 原生配置读取返回可见模块/角色、确切目标存在性及字段，不返回业务记录；字段权限采用原生 DocType/DocField/Custom Field 权限等级，并检查自定义字段记录权限。读取后 Meta 重新暴露隐藏字段的受控元数据故障先复现再修复；该故障注入不修改持久化原生权限。来源重新使用时重新授权，提出配置前比较实际配置快照摘要，旧版本拒绝。配置领域权限摘要覆盖配置 DocType、原生子表、模块/角色，不沿用普通业务领域摘要。
- erp-configuration 1.0.0 固定摘要 `b8617102ad9a358e8043b066667e08714aa06ff9f0be5e58ffe71c31518b819d`，纳入运行内容身份，继续只加载指定领域目录、禁止个人 skill。没有改上游或另写 Agent 循环。
- TDD：MCP/领域缺失、缺少 skill、Runtime 拒绝新领域均先失败。最初真实链测试误用已关闭的 18084 端口，现场确认 beta 无直出端口后改用 preview.localhost:18085；这次连接失败不算功能红测，后续已单独确认 Runtime domain 红测。
- 14 项 MCP/skill/运行摘要及新真实 Runtime 链 / 18.77s；13 项压缩/原生恢复/配置来源/业务权限组合 / 26.10s；5 项原生 LLM 拦截测试通过。字段权限修复后新配置及旧 query（含 Docker 跨进程恢复）4 项真实链 / 113.49s。版本摘要检查新增后配置领域 1 项 / 8.69s；重启 backend 后配置 Runtime/来源/确认 3 项 / 29.81s。
- 新链实际固定 Runtime→MCP→HTTP→beta 原生权限读取→配置提案→finish，三次模型替身请求，来源成功后 execution_ready=true，业务 DocType 未创建；测试会话/运行/配置包已清理，临时容器退出。没有新增付费调用。尚未将配置包挂入侧栏、开放配置领域选择，也未完成跨站预览与目标发布；下一步这些用户路径及真实模型验收。整体目标 active，其他阶段缺口不缩减。

## 最新接续：配置提案绑定来源运行

- 配置包不可变 payload 绑定 model_run；只接受当前用户、同一会话、configuration 领域的 Running 来源。读取确认返回 execution_ready，运行中/取消中/取消/失败不能创建配置执行记录或执行 DDL，成功才开放确认；已有执行记录仍按原规则只读返回，不重放。前端保留提案内容并显示等待原因，无新增审批角色。
- 前端红测复现未成功来源仍能点击；真实 beta 红测首先确认缺少 configuration 领域定义。实现后 75 项前端 / 5.55s、构建通过；配置来源/确认/原生应用组合 4 项 / 26.50s。来源测试随后改为真实事务提交和真实 confirm rollback（不再拦截数据库 rollback），独立重跑 1 项 / 6.15s，通过且清理了本测试会话/运行/配置审计。
- alpha/beta 原生 DS Model Run schema 已加载，backend 重启。未开放前端 configuration 领域选择，MCP、固定 skill、权限摘要和配置来源读取工具还未接通，不能称配置模型链已实现。下一步完成这些接线，再受控跨站预览/独立发布及真实模型验收；整体四阶段目标保持 active。

## 最新接续：配置确认侧栏与真实原生应用 UI

- 会话读取返回经当前用户权限检查的 configuration_confirmations；侧栏复用 ConfigurationProposal，同源 POST 仅发送确认 ID、digest、request_id。前端缺入口/卡片和真实会话缺字段的红测后实现，74 项前端 / 5.54s、构建通过；8 项真实确认及会话测试 / 13.86s。alpha 已加载两个新审计 DocType，alpha/beta backend 已重启。
- beta 合成配置操作员通过原生登录，在会话 `04g1dviui3` 中查看冻结字段/权限/工作流影响并点击一次确认。配置包 `04l2f96rcv`、确认 `04ntb13sph`、执行 `27u5aot33c`：3 个状态、2 个动作、新 DocType、Workflow 共 7 步 Succeeded。独立数据库回读执行数为 1。
- 原生 Desk 刷新元数据后可搜索并打开 DS Preview UI Inspection 列表、新表单。首次保存因标题未进入表单被原生必填校验拒绝；重新填写并核实可见输入后保存成功。记录 `31lbccbdik` 经原生操作菜单批准、取消，数据库 docstatus=2、workflow_state=DS UI 已取消，modified=`2026-08-29 05:44:48.976314`；仅 1 条合成记录。没有绕过原生保存或状态方法。
- 刷新记录、重新打开侧栏后仍显示 7 步成功，确认按钮禁用，无配置重放。仅切换本次创建的 preview.localhost 测试 Cookie，保留用户其他来源会话。合成新应用与记录保留供后续验收，不是临时测试表。
- 本次配置由确定性合成 fixture 提出，不是模型生成；无新增模型调用，未跨站传递或发布到目标企业。下一步配置领域/MCP/固定 skill 与运行来源绑定、受控跨站预览及独立目标发布；Custom Field、过期/并发/未知核实仍待验。阶段四及其他验收缺口保留，整体目标 active。

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

## 接续：业务运行 MCP 与真实 ERP 链路

- 新增 run-bound `context_mcp`，调用方仅配置当前业务 Site、run_id 与短期 capability；模型工具参数没有身份、Site、URL 或凭据入口。三项只读工具目录与旧 task MCP 共用，未扩展旧 Demo。
- MCP 契约测试先失败，最小实现后与旧链路回归 16 项通过。拒绝响应不自动重试、不展示原始服务端异常正文。
- 新增 `dsh-business.yml`，包含原单 stdio 上下文 Runtime 并加载官方 MCP 客户端。真实启动曾因 include 使用错误的 url 键失败；按本仓库已验证固定版 path 格式修正，未升级上游。
- 真实 DSH 0.1.1rc1 → stdio MCP → HTTP capability → Frappe 当前业务用户权限 → 合成 Item 读取通过，工具结果进入下一次模型请求。模型仍是本地 SSE 替身，不算真实模型验收。临时 cap 文件在 finally 删除，合成会话/运行按确切 ID 清理。
- 组合复跑 MCP、旧 Agent、原生恢复/锁、业务会话/执行/拒绝队列及新链路：**33 passed / 33.39s**。无生产写入、无旧 worker 重启。

## 接续：原生 Desk 挂载及首次 UI 检查

- 使用安装版 Frappe `www/app.py` 已读取的 app_include_js 公共钩子，新增独立 context-agent.js bundle。全局 root 挂 body，仅创建一次，不替换原生页面，页面路由切换不主动卸载会话。
- 挂载测试先因模块不存在失败，再实现；前端全量 **52 passed / 4.41s**，两个 bundle 构建成功。真实资源 HTTP 200，alpha clear-cache 后原生 Desk 出现 Agent 按钮。
- 浏览器实际访问应为 `http://127.0.0.1:18082/app`（compose 仅允许 127.0.0.1/localhost；最初使用 Site 域名收到 421，未更改 Host 限制）。复用现有 Administrator 登录，不注销、不切换身份；会话接口沿用已有禁止管理员规则，显示拒绝，未运行模型。
- 首次真实截图发现原生 navbar 遮住抽屉标题/关闭按钮。新增失败断言后使用 Drawer 原生 rootStyle 和 Frappe `--navbar-height`，不提高层级覆盖导航。重建并刷新自建测试 tab 后，截图确认标题/关闭按钮位于顶栏下；实际关闭后抽屉消失、原生首页完整保留。
- 浏览器验证仅覆盖全局入口、打开/关闭、未知上下文提示和拒权；普通用户多轮查询、切页/未保存表单/列表选中项/子表尚未真实 UI 验收。新执行器仍未启动，不能把侧栏出现当成可用 Agent 交付。

下一步：专用普通运行账号、新隔离执行器、取消轮询和原生恢复判定、权限/配置/skills 版本隔离与全模型调用预算，再接真实模型与普通用户 UI。阶段一未完成，阶段二至四仍待实施。

## 接续：运行状态与原生取消联动

- 新增运行 capability 绑定的 run_status HTTP 接口，不返回业务正文。Running 时复查当前用户及会话来源权限；Cancelling 仍允许执行器取得取消状态，结束/过期/错误 cap 拒绝。真实 Frappe 测试先因缺少接口失败，再实现并通过。
- 新增 context_runner.monitored_run，只协调原生 Agent 与业务运行状态，不另写模型循环。开始前检查状态；运行中定期查询并调用项目协议的原生 cancel/whenIdle；撤权或查询失败先取消再抛错，不自动重试。结束时再查取消状态，不把取消后的回答标成成功。
- 真实 Runtime + SSE 替身验证运行前取消零模型调用、开始前拒权零调用、在途取消仅一次调用、运行中撤权中止在途请求。该轮询是停止机制，不代替尚未实现的所有模型调用/压缩前授权与预算检查。
- alpha backend 重启加载接口，确认 ping=pong 后复验。真实 DSH→MCP→HTTP→ERP 链增加 run_status 和 finish_run，回答经来源权限检查后写入业务运行成功记录；不是只看 SDK 返回成功。临时会话/运行仍按确切 ID 清理。
- 相关原生 Runtime、单写者、取消、真实业务执行与 MCP 全链路 **15 passed / 12.27s**。未调用付费模型，未启动新消费者，未改变浏览器登录。下一步容器入口、稳定目录挂载与专用运行账号；全部阶段目标继续 active。

## 接续：隔离容器、跨容器恢复和真实模型

- 新增 context_container 命令及 context_runner 的业务运行入口。复用原容器资源/安全参数：384MiB、0.1 CPU、只读根、无额外 capabilities，仅挂本项目代码/配置/插件、固定 Runtime 卷、当前 run 凭据文件和当前会话目录。未挂整个状态目录、项目根、宿主凭据或 Docker socket。
- 容器内清除环境后启动 SDK，显式传入 resume 布尔值、当前问题和页面快照；凭据只由 MCP/执行器使用，不拼入提示词。入口和挂载先红后绿，相关旧入口/取消/新容器命令回归 **17 passed / 21.05s**。
- 真实受限 Docker→Runtime→MCP→HTTP→ERP 首次执行成功，但测试错误地只查 *.jsonl，未识别原生 Linux session.jsonl.zstd；查明实际文件后修正测试，未改动原生格式。完整复跑并增加第二独立容器显式 resume，实际第二进程模型请求包含上一轮 assistant 回答；两个入口路径 **2 passed / 58.62s**。这里两轮使用同一合成业务运行检验原生跨容器持久化，不冒称已完成多业务运行协调或权限版本轮换。
- 使用项目 .env 既有授权模型 **deepseek-v4-flash** 实际调用一次业务运行，走正式容器入口，无模型替身。读取 DSHERP-TEST-ITEM，模型回答名称 DSHERP-TEST-ITEM、组 DSHERP-TEST-ITEM-GROUP；finish_run 真实检查读取来源并持久化 Succeeded，原生压缩日志数量 1。未写任何业务单据。
- 真实模型验证采用进程内临时 service-user 绑定领取；尚未开通常驻服务身份或启动队列消费者。合成会话/运行按确切 ID 清理，临时凭据/容器/日志目录及无后续用途验证脚本已删除，保留本节非敏感验证证据。没有退出用户浏览器。

下一步：专用运行账号及主机单消费者、服务端授权的稳定目录命名、原生恢复判定、权限/配置/skills 版本轮换和每次模型调用预算；再做普通用户侧栏真实多轮与 SSO。单次真实模型成功不是阶段一完成，更不是生产上线。

## 接续：取消候选任务与原生恢复判定

- 确定性交错测试在候选查询后、领取行锁前提交 Cancelled，先复现领取将已取消任务复活。实现加锁后重查 Queued；真实数据库测试及执行/拒权回归 **3 passed / 3.27s**。测试模拟确定顺序，不宣称覆盖全部独立事务并发。
- 新增项目协议 dsherp/session/exists，复用固定版公开 sessionPersistence.list，只允许在 open 前查询。内部执行入口 resume='inspect' 在 OS 单写者锁内查询原生持久化，然后向 open 发送明确布尔值，最终仍调用原生 create/resume；不靠宿主目录名或重复 session ID 伪称恢复。
- 新测试先因不支持 inspect 失败，实现后真实 Runtime/恢复/取消 **14 passed / 9.08s**，插件确定性清理两项通过。新进程发出的模型请求实际包含首进程标记。既有显式 true/false 调用保持不变，缺失或未知决策不静默降级。
- 此批没有真实付费调用；领取修复还需下次加载 backend。队列消费者和权限/配置/skills 轮换仍待完成，目标保持原四阶段，不以本批通过代替整体交付。

## 接续：服务身份与业务队列消费者

- claim_run 新返回 scope_id，由服务端 Site、owner、会话、query 领域及原生会话标识生成 SHA-256；测试先因缺字段失败。消费者只接受固定十六进制标识，不允许模型或请求拼目录，挂载单个 scope 目录。权限/配置变化导致原生会话轮换仍待实现，不把当前哈希当成权限版本证明。
- 新增 context_worker：领取→单容器→结果提交，不自建 Agent 循环。与旧 worker 共用同一个 OS 锁，维持一个付费 Runtime 的现有预算；结果提交响应不明直接报错，不重跑模型、不覆写成失败。队列单元测试先红后绿，相关执行/取消共 **5 passed / 2.49s**。
- 一次性脚本 infra/provision_context_worker.py 在 alpha 创建 dsherp-context-runtime@example.invalid，未授 ERP 业务角色，真实确认 Item/Customer read 均无权限；固定版原生 generate_keys 创建凭据，0600 保存本项目 .runtime/context-worker.json，不输出/提交密钥。不旋转已存在账号/配置，避免覆盖其他身份。
- 通过原生 update_site_config 保存本地服务绑定，重启 alpha backend 加载领取修复，ping=pong。确认活动运行 0 后实际执行 context_worker --once，空队列正常返回、零模型调用；未启动常驻进程。
- tests/integration/test_context_worker_chain.py 使用真实普通用户 HTTP 入队、真实专用服务账号领取、真实两个受限容器执行不同业务运行、真实 HTTP 完成及会话回读。服务账号直读 Item 返回403，业务工具仍按会话 owner 读取。两轮结果 Succeeded/DSHERP_OK，一个 scope 目录、一份原生压缩日志，第三次空队列返回 False；**1 passed / 71.52s**。仅模型端为容器内 SSE 替身；合成会话/运行按 ID 清理。
- 当前消费者具备手动单轮与循环入口，但尚不启动常驻：下一步补权限/运行配置/skills 版本轮换、全模型调用及压缩预算与来源授权，再完成普通用户侧栏真实模型多轮。阶段二 HITL、阶段三隔离预览发布、阶段四日常站点与恢复验证仍未完成。

## 接续：LLM 请求授权与预算预占

- 现场读取固定提交 [LLM service](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/llm/llm/src/index.ts) 的公开 llm/stream waterfall 与 [GenerateOptions](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/llm/llm/src/types.ts)。该事件位于普通 stream 和 prepared stream 的共同适配器前，不只 agent/request。最初尝试错误的 core/llm 路径收到404，随后用固定提交 tree 定位正确包路径，没有采用 master。
- 项目 model-guard 插件通过公开事件接入，发送调用元数据而非业务正文；服务端 reserve_model_call 以运行 capability 加行锁，复查 owner 与所有会话来源权限，预占后才允许模型调用。alpha schema 已通过原生 reload_doc 更新，backend 重启、ping 就绪后测试。
- 当前查询组合固定 deepseek-official/deepseek-v4-flash；每运行最多8次调用、单次输入131072 UTF-8字节、累计524288字节、单次输出最多2048 token、累计预留16384输出 token。输入量是字节预算，不宣称精确 token 或货币核算；失败/响应不明不退还预占，不自动重试。
- 任意授权/预算请求失败后，本轮插件锁定后续模型访问。普通与 purpose=compaction 均经过相同逻辑；单元测试模拟压缩调用拒绝被上层捕获，后续普通调用仍拒绝且 adapter 零调用。未启用/验收官方自动压缩策略本身，不能把这个测试表述为完整真实压缩验收。
- 插件与 API 均先红后绿。真实固定版 Runtime 授权HTTP403后没有模型HTTP请求；元数据未包含问题正文。真实普通/跨容器两条链分别产生2/4条模型预算计数，与实际模型请求数一致，**2 passed / 102.16s**（模型替身）。相关回归 **11 passed / 9.54s**，Node插件 **4 passed**。
- 尚需权限指纹和运行配置/skills 版本轮换、业务 skills 固定摘要、原生压缩触发和来源保全，以及普通用户侧栏真实多轮。当前检查逐次复查已有来源权限，但不声称已覆盖所有权限变化后的旧运行上下文废弃。整体仍为阶段一进行中，未启动常驻消费者、无本批付费模型调用。

## 接续：原生权限版本与运行隔离

- 新增 context_permissions，仅读取原生授权输入并生成摘要，不提供权限授予能力。查询领域 Item/Customer 的 DocPerm、Custom DocPerm、DocType/字段/自定义字段/Property Setter，当前用户的启用状态/类型/角色/有效角色定义/User Permission，当前用户或 everyone 的 DocShare，以及严格用户权限设置纳入摘要。现场从安装版 metadata 确认相关字段。
- claim 保存 permission_revision，变化时分配新的原生 runtime_session，从而生成新的 scope_id。旧原生日志不迁移、不重新灌入新模型上下文；业务会话仍保留可重新授权展示的历史。历史来源失去访问权时现有展示仍拒绝，不在界面层假隐藏后继续喂模型。
- 状态轮询、工具调用、模型预占（含 compaction purpose）和成功结束均要求权限版本一致。失败/取消的终止记录仍可写，避免撤权后无法释放运行；没有撤销既有业务结果的承诺。
- 真实合成用户添加一个无业务权限的临时角色，先复现旧运行仍可继续，修复后旧状态/工具/摘要调用拒绝，模型计数仍0；终止旧运行后下一轮原生ID、目录及权限摘要均变化。临时角色通过原生 User/Role 方法创建与清理，用户原有角色保留，测试会话按ID清理。
- alpha 原生 schema 更新并重启 backend；权限版本、执行、会话和实际专用服务账号两轮容器队列组合 **8 passed / 95.32s**。模型仍为替身，未启动常驻消费者。当前摘要是查询领域的原生授权输入快照，不宣称已覆盖未来业务/配置领域、自定义权限钩子或运行配置/skills 变化；这些仍需随领域和版本接入验证。

下一步：运行配置摘要及固定业务 skills 版本轮换，原生压缩实际触发与来源保全，再做普通用户侧栏真实模型多轮与登录衔接。其余三个阶段保持原范围未完成。

## 接续：运行配置摘要与下一轮会话轮换

- runtime_revision 使用实际模型/端点/凭据配置及选定的 Runtime 组合、插件、MCP/运行入口、版本锁文件内容生成摘要。密钥仅参与摘要，不写入数据库、日志或提示词；缺文件/必需配置直接失败，不使用默认旧版本。
- 专用消费者每次领取前重新读取模型配置并计算摘要，claim_run 必须收到有效摘要。服务端将权限摘要与运行配置摘要共同绑定原生会话，配置变化后即便权限未变也分配新原生ID和目录；DS Model Run 单独记录 runtime_revision 便于审计。
- 新摘要测试先因模块不存在失败；真实轮换测试先因API不支持配置参数失败。实现并原生更新 alpha schema 后，测试验证模型/端点/文件变更影响摘要、配置缺失拒绝、同权限不同运行配置轮换；相关 **9 passed / 5.95s**，补充消费者元数据断言后单元 **5 passed / 0.23s**。
- 更新现有协议测试的领取参数，保留原有权限/取消回归。alpha backend 已重启并 ping 就绪；未启动消费者、未调用付费模型。本批未重跑完整容器套件。
- 当前实现保证下一轮配置变化不复用旧模型上下文；运行中的文件漂移校验、模型调用与已绑定配置的端到端一致性，以及业务 skills 摘要仍需完成，不把下一轮轮换当作在途配置变化已自动中止。

## 接续：配置绑定一致性与运行期间漂移

- config/runtime-files.json 统一 Python 领取端与 Cordis 插件的文件清单；固定版本锁以单文件只读挂入容器。Python 入口在网络请求前验证实际文件/模型配置摘要，插件启动时独立计算相同摘要，模型预占接口再次对照已领取的 runtime_revision。任何不一致直接拒绝，不以调用方自报版本替代实际文件检查。
- 插件监测当前运行挂载的代码/配置文件及 run 配置内容，在模型授权前后检查；响应完成前再检查，改变后锁定后续调用，不能将变化后的响应标成成功。已发送给模型的数据无法撤回，也不承诺退还已经发生的费用。
- TDD 先复现缺少漂移检测、无绑定入口仍尝试网络、响应期间变化仍放出 finish chunk，再实现。真实 Runtime 测试覆盖授权拒绝零模型请求、授权返回期间配置变化零请求、拒绝后第二次调用仍禁用、正常情况下两轮可完成。最后相关 **7 passed / 4.43s**，Node模型插件 **4 passed**。
- 容器测试改为预先确定的容器内 loopback SSE 地址，领取摘要与实际执行配置一致，不再在测试中用临时模型地址替换已绑定配置。真实专用账号→两轮业务队列→两容器恢复→模型调用预占与结果回读 **7 passed / 98.54s**（该批包含其他相关测试）；随后响应末尾检查补丁已跑 Node 与真实本机 Runtime，未再重跑容器套件。
- 这里监测的是当前运行已挂载的配置。宿主 .env 修改由下一轮重新读取，尚不是在途凭据热更新/宿主配置变动主动中断；固定业务 skills 与真实自动压缩仍待接入。未调用付费模型、未启动常驻消费者，整体四阶段目标保持 active。

## 接续：固定业务 skill 与原生加载

- 固定提交现场核对 [skill-filesystem](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/skill/skill-filesystem/src/index.ts)、[agent-spine 组合](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/examples/agent-spine-demo/src/index.ts) 和 [原生 skill 工具](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/skill/tool-skill/src/index.ts)。业务组合启用官方 registry/filesystem/tool；协议验证组合仍关闭，不引入个人目录。
- 新增 erp-query 1.0.0，限定当前用户 Item/Customer 事实查询、来源标注、未保存上下文区别和只读边界。正文不含可执行脚本，业务权限仍由服务端决定。原生 skill 工具是第四个可见工具，其余三个仍为 ERP 只读工具；没有 Shell/任意 HTTP/数据库工具。
- includeDefaultRoots=false、watch=false、显式 business-skills 目录，容器只读挂载。Python 领取前与插件启动/调用前校验 manifest、完整内容 SHA-256、版本和目录集合；拒绝额外目录、额外文件及符号链接。manifest 与正文都纳入运行配置摘要，变更不能沿用旧原生会话。
- 首轮 Runtime 因未引用 YAML 三元表达式，把 customSkillDirs 解析成对象而失败；按错误修正为字符串表达式后复跑，无上游改动。真实 Runtime 的原生 skill 调用把“业务只读查询”正文加入下一次模型请求；放在会话 .agents/skills 中的合成个人 skill 未进入请求。此测试模型为 SSE 替身，不冒称真实模型主动选 skill。
- 摘要篡改、manifest版本不符、额外目录/符号链接、配置与真实 Runtime 回归 **15 passed / 9.20s**，Node插件 **5 passed**。真实队列两轮容器读取/恢复 **1 passed / 95.76s**，只读 skill 挂载断言通过。业务数据未写入，无本批付费模型调用，测试生成的临时目录按测试生命周期清理。
- 当前仅查询领域业务 skill；阶段二操作领域、阶段三配置领域仍需各自限制工具和版本。下一步接原生压缩并验证摘要来源/权限/预算，再推进普通用户侧栏真实多轮与 SSO。整体四阶段目标未完成。

## 接续：原生自动压缩与冷恢复

- 固定 ref 的 compaction-basic README 和 compaction 服务接口再次核对；业务组合启用官方 token-meter 与 compaction-basic，不编写摘要循环，不启用工具结果裁剪器。大模型原生窗口较大，thresholdRatio=0.005 提前触发压缩以适配当前较小的输入预算；保留区和摘要输出各2048 tokens，自动摘要不重试。实际输入仍由已有模型调用预算检查，不保证每次超长单条输入均可压缩。
- TDD 两项先因未触发原生压缩失败；启用原生插件后允许路径出现真实 compact 请求头、摘要 checkpoint 被下一轮使用。拒绝路径不向模型发送摘要，即使原生自动压缩捕获错误，项目调用锁定仍阻止继续请求。没有额外人工门禁。
- 补验退出原生进程后显式 resume，下一轮仍包含 compacted-summary 与合成来源版本标记。这里 Runtime、压缩插件、持久化和恢复是真实的，模型/授权HTTP为替身；不证明真实模型语义摘要质量或完整 ERP 摘要来源保全。
- 压缩、模型保护、配置版本与实际 ERP 权限/执行组合 **23 passed / 13.43s**；Node保护插件 **5 passed**。未启动常驻消费者或调用付费模型。阶段一仍在进行，下一步普通用户真实模型侧栏多轮与登录衔接；其他三阶段不缩减。

## 接续：读取结果的来源版本

- read_record 返回同一次原生读取的 modified；search_records 在同一次权限过滤查询中返回 name/modified；schema 返回原生元数据 modified。工具来源账本分别保存 record_versions 或 schema_version，不在工具读完后另外查询版本而造成读值/版本错配。
- 真实 ERP 测试先因缺少 record_versions 失败，最小实现后记录、列表及 schema 三类来源均通过。字段权限和用户权限回归保留，未开放业务写入。alpha backend 已重启并 ping 就绪；上述23项组合覆盖本改动。
- schema_version 是原生元数据时间戳，不冒称它单独涵盖全部自定义字段配置；已有权限/配置摘要另外绑定相关变化。历史版本只用于说明当时来源，不替代后续业务确认的当前版本核对。

## 接续：普通用户侧栏真实模型两轮

- 浏览器保留原先 127.0.0.1 管理员会话；新建 localhost 同端口独立会话，使用本项目测试管理员凭据登录，并通过原生 Impersonate 输入测试理由切换 dsherp-reader。未修改密码/角色，未退出原用户。此为原生受审计身份切换验收，不等于平台 SSO 验收。
- 只读用户在原生 Item 表单打开侧栏，当前页面显示 Item / DSHERP-TEST-ITEM，没有业务保存按钮。输入真实查询后由专用服务账号 --once 消费，384MiB/0.1CPU 隔离容器调用既有 deepseek-v4-flash，回答名称、群组、单位和来源修改时间。
- 保持侧栏与会话，点击原生物料导航回列表；旧消息继续显示原表单，新页面显示 Item。追问再次由独立 --once 进程/容器执行，真实模型正确指明上一轮 Form/Item/DSHERP-TEST-ITEM、当前 List/Item/List，并实际重新读取原物料单位。
- 独立数据库回读两条运行均 Succeeded，页面类型依次 form/list，权限和运行配置摘要一致；模型调用计数分别3/4，来源数2/1，记录版本均与读取的 modified 一致。计数是所有模型预占次数，不冒称精确计费或已证明其中摘要语义正确。
- 整页刷新、重新打开 Agent 后，两轮完整历史恢复显示，未发送新问题或重新启动消费者。两个 --once 均退出，无常驻付费进程。该合成验收会话与原生日志保留用于继续 UI/恢复验证，不提交到 Git；没有业务单据写入、库存或账务动作。
- UI 当前按纯文本显示 Markdown 标记，回答可读但格式需改善；轮询完成展示本次由“刷新记录”明确回读确认，不能宣称后台自动刷新体验全部验收。未保存字段选择、子表/筛选选中项完整 UI、SSO，以及阶段二至四仍待实施。整体目标 active。

## 接续：显式未保存字段与子表列

- 按 Ant Design 技能核对固定5.27.6 Select API/示例，复用原生 multiple Select。未保存表单显示字段与子表列目录，不预选、不把值放入目录；Password/隐藏/布局字段不列出。选定后发送当时值，子表只带行名与选定列，不提交或刷新原生表单。
- 选择绑定路由，点击发送时再次对照；若用户在轮询更新前切到另一记录，明确拒绝沿用旧选择并清空选择，新建会话也清空。服务端始终独立检查，不把前端目录当权限证明。
- 真实 Frappe 新测试先因 uoms 不在普通标量字段集合而失败。读取安装版 Meta.get_permitted_fieldnames 与 Document.apply_fieldlevel_read_permissions 后，按原生父表 permlevel 和子表字段权限处理；已保存行必须属于当前单据，新行只作为 new- 前缀的未保存上下文，拒绝重复/外来行、系统归属字段和嵌套对象。没有保存业务内容。
- 子表 DocType/字段/自定义字段/Property Setter 纳入权限版本摘要，避免子表配置变化后继续使用旧上下文。新增真实检查先失败于摘要只有 Item/Customer，补入原生子表后通过。
- 全前端 **54 passed / 4.61s**，构建成功；真实会话/权限版本 **8 passed / 8.27s**。alpha backend 重启加载修改。此批尚未验收真实可编辑用户的未保存子表 UI，不声称 Sales Order 已接入；后端查询领域仍仅 Item/Customer。无付费模型调用，整体四阶段目标继续。

## 接续：平台 OAuth 身份映射端点

- 现场确认现有平台只有成员凭据代理，不是浏览器SSO。读取安装版 frappe/utils/oauth.py 与 integrations/oauth2_logins.py：原生提供 Social Login Key、授权码交换、一次性 state 和 LoginManager；原生通用登录还会更新/创建用户，因此后续业务回调必须限制到已明确绑定且已存在的业务身份，不按邮箱相同推断或自动开通用户。
- 新增 desk_identity 作为后续原生 OAuth user-info：从已认证平台用户读取现有 Membership/Enterprise，使用绑定的普通业务凭据核实实际业务用户，再结束读快照重查绑定版本。返回明确的业务用户、平台subject、目标Site/企业及绑定版本；不输出API凭据，不授予业务角色。
- 与旧只读接口共用已核实的业务连接上下文，保留仍有效回归；没有改变旧Demo功能范围。新测试先因缺少方法失败；实现后验证alpha/beta不同业务用户、非成员拒绝、撤销成员/错绑用户拒绝。
- 首次组合测试因平台backend重启未就绪出现11项502失败；确认ping就绪后完整重跑 **13 passed / 35.11s**。这只是实际身份映射与权限验证，尚未配置OAuth客户端、业务回调/登录态成员撤销检查，也未验收浏览器SSO。无付费模型调用或业务写入。

## 接续：业务 OAuth 回调与会话授权检查

- 新增 sso 适配，使用安装版公开 get_oauth2_flow/get_oauth2_authorize_url/consume_oauth_state 与 LoginManager.login_as，不使用自动注册/更新用户的通用 social-login helper。明确校验平台subject、目标企业/Site、版本及已存在启用的普通 System User；拒绝 Guest/Administrator、跨企业与未开通用户，不新增角色。
- 授权码交换复用原生 rauth flow，核对 get_auth_session/get_raw_access_token 实际参数；平台信息请求使用 Bearer header、固定配置端点/企业、15秒超时和禁止重定向。回调只接受原生state中固定/app目标，先消费state再交换授权码。访问令牌使用原生站点加密后存入原生会话，不返回浏览器或模型。
- auth_hooks 在最终身份验证后重查平台信息及绑定版本，撤权/版本变化拒绝当前SSO请求。只豁免重新登录回调/发起和退出入口以便恢复登录；本地原生登录无SSO grant，不改变其权限流程。尚未配置实际OAuth客户端，故没有建立新的真实SSO会话。
- TDD 两项先分别因模块/回调缺失失败；真实Frappe身份数据、Redis原生state、原生加密验证通过。授权码交换和登录动作在回调测试中为替身，不能等同真实OAuth浏览器链路。alpha重启、clear-cache加载hook后重跑会话/读取/SSO相关20项；无业务写入和付费模型调用。
- 下一步必须把SSO授权绑定到已提交模型运行，确保离开浏览器后仍重查成员，再配置原生OAuth Client/Social Login Key、正式平台入口和真实跨站登录验证。当前HTTP hook不等于后台运行已完成成员撤销检查，阶段一及后续三个阶段仍未完成。

## 接续：后台运行的成员授权

- DS Model Run 保存提交时已核实的加密 platform_grant，属于内部会话审计数据，不是业务写入。加密授权不会出现在会话公开响应、claim响应或容器配置中；平台OAuth令牌留在业务Site服务端。
- 领取、运行状态、工具、所有模型预占和成功结束通过现有_actor重查该运行的授权，不依赖当前浏览器cookie仍存在。失败/取消仍可结束释放资源，不把撤权变成无法终止的运行。
- 成员身份/企业/版本信息参与原生会话版本组合；重新授权后绑定版本变化即分配新原生会话，即使本地业务权限和Runtime版本未变，也不继续读取旧模型上下文。
- 新行为测试先因运行未保存grant失败。补字段并原生reload_doc后，测试移除浏览器grant，模拟平台成员版本变化：状态/工具/摘要预占均拒绝，模型计数0；新授权下一轮原生ID变化，本地权限摘要不变。数据库/权限/加密真实，平台userinfo为替身，尚不等于真实OAuth撤权链验收。
- 相关初轮6项通过，扩展重新授权轮换后SSO3项通过；alpha重启并ping就绪后会话/SSO/执行组合10项通过。未调用付费模型或新增常驻服务。下一步原生OAuth配置和真实登录/撤权链，再继续业务HITL与后续阶段。

## 接续：真实原生 OAuth HTTP 链路

- 从安装版实际metadata核对 OAuth Client / Social Login Key 字段与validate，配置合成alpha专用原生Client及Social Login Key。仅授权码模式、确切localhost回调、原生consent，不启用自动注册，也不启用通用social-login按钮；自定义start进入已限定身份的回调。客户端密钥只在配置脚本进程内传递，平台原生Client和业务Password字段保存，不落Git/输出。
- infra/provision_desk_oauth.py 为现有独立容器的本地配置脚本，不新增常驻服务。预检已有配置时拒绝覆盖，分阶段提示而不自动重跑已创建Client。原生OAuth scope与用户权限沿用Frappe，未宣称scope名称构成额外API隔离。
- start测试先403未配置，配置后302到平台原生authorize，回调固定 http://localhost:18082/api/method/dsherp_bridge.sso.callback。真实交换最初因内部Host被当Site而404；诊断对比明确Site头后，平台token与userinfo两处补固定平台Site头，无权限降级。
- 完整HTTP测试使用真实平台普通账号登录、原生授权表单/CSRF、原生授权码及token交换和业务Cookie。登录身份精确为dsherp-reader，业务会话列表200；重复callback403。随后通过原生成员DocType实际停用成员，已有业务SSO的身份/会话接口403，finally恢复成员；测试独立会话正常退出，不操作用户浏览器Cookie。
- **5 passed / 7.00s**，包含前述身份、后台grant和真实OAuth测试。没有模型调用或业务单据写入。已完成的是HTTP协议与原生登录状态，不冒称浏览器点击体验已验收；正式平台Desk入口、beta接入、前端历史只读兼容和后续业务HITL/预览发布/日常Site仍待完成。

## 接续：平台首页切换为企业 Desk 入口

- Portal 删除提问框、提交、运行轮询及相关旧交互状态，首页只承担企业选择/管理入口和旧平台历史查阅。新的问题从业务Desk侧栏发起，不建立普通/Agent双模式。旧合成Studio独立入口及实现未改动。
- 旧历史仍按原企业和当前成员重新读取，保留回答、状态、错误及原始工具记录；不复制到业务Site或新企业。切换企业和拒权清除旧显示，迟到历史不会串回当前企业。
- 新增 desk_entry 服务端接口，复用成员/业务用户实际核实链路，返回站点配置的明确Desk OAuth入口。平台客户端限制为context/desk_entry/list_tasks/get_task四个只读请求，删除提交代码。当前仅配置alpha，beta明确未配置；旧后端submit_task及消费者代码尚未整体退役，旧付费进程仍停止，不能将前端移除当成后端端点已删除。
- TDD 先复现首页仍有提问框、客户端路由错误/仍可写、后端入口方法缺失；替换后全前端 **47 passed / 4.55s**，构建及固定Antd5.27.6检查通过。删除旧提交测试，保留身份、企业隔离和历史再授权回归。真实Desk入口与OAuth身份映射 **2 passed / 4.39s**。
- 本次无付费模型调用或业务数据写入，平台backend已重启并ping就绪。下一步浏览器实际平台入口→OAuth→业务Desk验收及beta接入，然后业务HITL/应用预览发布/日常Site；全部目标仍active。

## 接续：浏览器 SSO 验收未通过与敏感日志处置

- 浏览器已有平台member登录，打开新企业入口，选择alpha、点击进入Desk、确认原生OAuth允许，进入业务Site并显示DSHERP reader。侧栏最初恢复此前两轮历史，原生搜索可进入Item列表。未退出已有用户账号。
- 持续使用后出现原生权限弹窗，侧栏清除历史并显示权限失效，发送按钮禁用。本次SSO只读验收问题未发送，也未启动模型消费者。平台令牌元数据仍Active且未过期，成员enabled=1且版本未变化；用该本地OAuth授权核对平台userinfo仍200。实际浏览器后续拒绝点尚未确定，不声称浏览器SSO已验收。
- 排查原生Error Log时发现其“traceback with variables”记录了早前OAuth交换失败中的客户端密钥；读取该日志意外将旧密钥带入工具输出。已立即只轮换本次合成alpha OAuth Client与对应Social Login Key两端密钥，未动其他账号/服务凭据。工具历史无法回收，旧密钥已失效。精确删除唯一确认含旧密钥的Error Log（创建时间2026-08-29 02:45:11.253981），没有批量删除日志；该删除不提供恢复副本以免继续保留旧凭据。
- 新增TDD：合成敏感内容的交换异常先直接逃逸，修复后交换层在原异常处理结束后抛出明确PermissionError，不链入含凭据的provider调用栈。不是失败重试或降级。相关非真实OAuth重登录组合 **5 passed**；尚未再次跑会修改成员的真实撤销测试，避免掩盖当前浏览器问题。
- 下一步先定位浏览器权限失效，并补全OAuth传输异常的凭据安全处理；其后仍需beta、业务HITL、配置预览发布、日常Site。当前没有新付费模型调用或业务写入，完整目标保持active。

## 接续：SSO 并发循环调用修复与真实模型 UI 闭环

- 根因已用真实并发测试复现：业务HTTP请求的SSO检查→平台desk_identity→业务get_logged_user，四个并发请求占住业务进程后等待平台回调，最终四个403（23.61s）。不是用户角色不足或令牌过期，不能靠增加权限解决。
- 新增平台desk_membership，已建立grant的后续验证只核实当前成员/企业状态与版本，不回调等待中的业务服务；grant仍比较初次验证的完整身份版本。本地用户启用及业务权限照常检查。首次登录仍使用完整凭据映射验证；并发首次登录回调尚未单独压测。
- 真正的OAuth授权码、四并发业务请求、成员停用后旧会话拒绝等组合 **6 passed / 7.59s**。原生撤权测试恢复成员后，浏览器重新走平台入口、原生consent并进入完整Desk；此前权限弹窗消失，列表和侧栏均正常。
- 另修复路由尚未初始化时快照抛出route-not-iterable：明确返回未就绪的unknown上下文，不读旧表单。侧栏对少量明确列举的登录错误展示固定文案，未知服务器异常仍不展示回溯。两项先失败后通过，全前端 **49 passed / 4.62s**，构建成功。
- 浏览器在SSO只读用户的Item列表新建会话并发送实际查询，单次受限容器使用已授权deepseek-v4-flash完成，侧栏自动轮询显示DSHERP-TEST-UNIT及实际来源修改时间，不需手动刷新。独立业务数据库核对唯一运行Succeeded且有加密platform_grant和实际来源；未创建/修改/保存/提交业务单据，消费者已退出。合成会话留作后续验收，不入Git。
- 这次证明alpha浏览器SSO→页面上下文→后台授权→真实模型/ERP只读→自动展示链；不代表beta、并发首次登录、未保存子表可编辑用户、业务HITL、配置预览发布或日常Site已完成。整体目标保持active，接下来推进业务HITL并补剩余阶段一兼容验收。

## 阶段二接续：不可变操作提案基础

- 前端 OperationProposal 使用原生 Antd Table 展示冻结对象、版本及逐字段前后差异，只提供一次确认；发送提案ID/摘要/请求ID而非可改写操作内容。重复点击、过期、异常后不自动重试；组件测试从缺模块失败到2项通过。尚未接入侧栏/执行API，不称业务闭环已交付。
- 新增独立 DS Operation Proposal，业务Site原生迁移已应用于alpha。十分钟有效期绑定Site、操作者、会话、动作、对象、版本和差异；控制器阻止后续修改正文/归属/摘要/有效期。读取重新检查记录及字段权限。时间按站点时区输出带偏移ISO，不伪装UTC。
- update_diff 使用原生read/write和字段权限，禁止系统字段、只读字段、非明确标量、空变化。真实Frappe测试从缺方法失败到 **2 passed / 2.15s**：只读拒绝、未确认零业务写入、版本冲突、不可变和跨用户拒绝。临时普通测试账号使用现场已存在Item Manager角色，所有测试数据事务回滚；未修改角色权限。现场Stock Manager实际只有Item读权限，未假设其可写。
- 当前只实现提案基础，未实现确认执行/执行记录/模型操作工具、Sales Order、填表及配置发布。下一步先实现确认原生save+回读+幂等记录并接侧栏，再逐项扩展。无新增模型调用、无业务单据写入、无新常驻服务，旧消费者保持停止。

## 阶段二接续：确认执行、回读和并发去重

- 新增独立 DS Execution Record（每个提案唯一），确认接口只接收提案ID/摘要/请求ID；以当前业务身份检查归属、权限/成员版本、有效期、单据版本及冻结差异。先持久化Running意图，再调用原生save并逐字段读取实际结果；成功业务写入与成功证据同事务提交。未声明任意业务钩子外部副作用可事务回滚。
- 重复确认，包括新的HTTP请求ID，只读既有执行；中断遗留Running返回Unknown，不重跑。权限/版本/过期导致失败有独立记录，非预期异常保留结果不明。当前Unknown尚未实现专门的自动核实工具，不能将“不重跑”说成“已核实业务结果”。
- 新增真实Item普通用户保存/回读、错摘要、重复确认、版本冲突、十分钟过期、权限版本变化、中断记录测试。Customer使用现场原有Sales User检查实际permlevel1字段：可改普通字段、明确拒绝高权限字段，不依赖原生静默还原。尚未真实执行Customer保存。
- 两个真实认证HTTP请求最初得到200/417 UniqueValidationError。安装源码确认原生metadata加载可重新打开一致性快照，单独rollback不足；提案行锁后，执行记录查询也必须使用原生for_update当前读。修复后并发只产生一份执行记录和一次保存。四项真实Frappe测试 **4 passed / 6.34s**；测试账号、物料、会话、提案及执行记录均精确删除/回滚，无生产数据操作。
- 会话读取包含重新授权后的提案及执行结果，侧栏展示冻结差异并调用同源CSRF确认接口；切页不改变确认目标，刷新恢复结果不触发写入。全前端 **54 passed / 4.43s**，构建及Antd5.27.6检查通过。前一轮会话/权限/执行组合11项通过，随后又完成上述并发修复；不可将该前一轮等同修复后的全套回归。
- alpha已原生reload执行DocType并重启本项目backend，ping正常；现场无agent_worker/context_worker消费者。无付费模型调用。模型操作领域和提案工具尚未接通，尚未真实浏览器确认操作；下一步完成领域/技能/工具和真实模型→侧栏HITL链，继续Sales Order、填表及阶段三/四。

## 阶段二接续：操作领域、固定技能与模型提案工具

- 侧栏增加本次任务领域选择（只读查询/业务操作），不是另建业务页面或双工作台。领域保存在每个Model Run并绑定请求摘要；领取时纳入原生会话版本及日志scope。切换领域不复用旧Agent上下文，所有模型调用（含摘要）校验所属领域。
- operation MCP目录在三个原有读工具外仅开放erp_propose_update，没有确认/save/提交接口。服务端从运行取当前操作者、会话和平台grant；只能对本轮实际读取过的确切记录/版本提出修改。query能力即使伪造工具名也不能提出操作。模型不接收用户、Site、授权或会话选择参数。
- 新增固定erp-operation 1.0.0及SHA256清单，限定当前实际支持的Item/Customer标量修改；明确形成提案即结束本轮、等待侧栏确认，不假称创建/Sales Order/填表已支持。官方固定ref的[技能目录发现实现](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/skill/skill-filesystem/src/index.ts)确认单技能文件夹也可作为根目录。query只加载erp-query，operation只加载erp-operation；默认个人目录继续禁用。
- 真实固定Runtime测试先复现erp-operation未知，配置后原生skill调用将业务操作提案正文送入下一次模型请求，工具目录含提案、不含确认，查询技能不进入operation首轮上下文。Runtime/技能/MCP组合 **13 passed / 6.77s**；运行器/配置/压缩组合 **12 passed / 6.39s**。这些模型均是SSE替身，未调用付费模型。
- 真实Runtime→MCP→ERP原有只读/恢复回归 **2 passed / 78.49s**。此时另一组共享站点领取测试并行运行，权限轮换因claim返回None失败；两组结束后串行重跑业务领域/会话/权限组合 **14 passed / 15.85s**。这不是放宽领取约束，后续共享站点任务套件串行执行。
- 提案另绑定其生成Model Run，原生不可变字段保护。确认要求来源运行Succeeded（worker在容器退出后才finish），运行中/失败/取消的提案不能提前执行。新行为先因缺model_run接口失败，补齐后操作真实Frappe **5 passed / 8.98s**；当前提案原生模型运行状态在该测试中为合成设置，不能当作真实模型容器释放验收。
- 全前端 **55 passed / 4.60s**、构建及Antd5.27.6检查通过；alpha迁移Model Run/domain与提案model_run并重启backend。尚未真实模型生成业务提案、尚未浏览器确认写入；下一步普通写权限账号完整链路，同时继续Customer实际保存/创建、Item创建、Sales Order、填表、Unknown核实和后续阶段。旧付费消费者未启动，无新常驻服务。

## 阶段二：真实模型与浏览器 Item HITL 闭环

- 完整证据见 [原生侧栏HITL验收](context-agent-hitl-acceptance.md)。普通writer在独立本地域名原生登录，真实deepseek-v4-flash生成唯一提案，容器退出后浏览器确认，原生save回读一致。独立数据库记录实际操作者、版本及唯一Succeeded执行；确认前未业务写入，确认后原生未保存草稿没有被覆盖。
- 随后真实模型在同一旧版本/未保存表单连续读取已保存结果；新标签页恢复历史不重复运行或写入。两轮分别4次真实模型调用，各自受原单容器预算限制，无常驻付费消费者。这个证据只证明Item标量修改链，不涵盖Customer保存、创建、Sales Order、填表或配置发布。
- 真实UI发现只读上下文的旧版本409阻碍连续使用，已移除该不必要阻断：保留页面version并由服务端独立填写server_version，显式说明差异。写操作确认仍严格匹配读取/确认版本，未放松权限或HITL。前端56项/构建通过，真实会话/执行/域名13项通过；列表伪造server_version边界另补红绿验证。
- 收尾串行重跑（包含列表伪造版本拒绝）**13 passed / 15.09s**，运行器/配置回归 **7 passed / 3.02s**。真实Item写入记录保留，负面测试产出已清理，未推送远端。

## 阶段二：创建能力与 Customer 真实模型 UI

- Item/Customer创建已接入同一原生HITL链，创建提案不占编号或写业务记录；结构版本、权限与实际字段重新检查后原生insert并回读。真实两种对象均验证创建、重复确认去重和继续修改；Date按规范字符串比对，仍要求实际值一致。
- MCP仅增加提案工具erp_propose_create，必须先实际读取schema，确认工具不对模型开放；固定操作skill升级1.1.0并校验摘要。前端显示创建后的原生记录名。57项前端、7项真实业务测试、13项Runtime/技能/MCP通过。
- 真实deepseek-v4-flash（5次调用）→Customer创建提案→浏览器一次确认→原生列表/表单回读通过，确认前客户0/执行0，确认后客户1/执行1，无关联联系人/地址。详细ID与字段证据见[HITL验收](context-agent-hitl-acceptance.md)。没有生产数据、上游修改或新增常驻消费者。
- 下一步Sales Order草稿创建/修改/提交/取消和填表；不能将当前Item/Customer链表述为全部阶段二完成。其余阶段一缺口、Unknown核实、配置预览发布和日常Site仍保留。

## 阶段二：Sales Order 原生确定性执行

- 先以真实Frappe测试复现缺少状态提案入口，随后实现分别确认的原生submit/cancel；新增草稿create、items数量修改、新增/移除明细测试。普通Sales User全链通过，未改已有SAL-ORD-2026-00001；测试订单及临时身份/提案/执行记录清理。
- 明细名单冻结结果行及顺序，已有行仅补丁指定字段，新行由原生方法生成；拒绝其他订单行、重复行、只读amount/conversion_factor。根Table按原生permlevel检查，历史提案读取也复查子表字段权限。没有给用户增加角色或确认步骤。
- 红测发现Frappe构造子文档会修改传入字典，以及ERPNext混合既有date对象/新增JSON日期字符串时TypeError；采用独立JSON值和原生表单一致的日期表示解决，不忽略原生错误。补全后增删行和状态链 **1 passed / 3.31s**。
- 组合回归首次因旧HTTP进程权限摘要未加载Sales Order及新增行日期问题失败；修复日期并只重启本项目alpha backend后，ping正常，串行Item/Customer/Sales Order业务回归 **8 passed / 16.17s**。均真实Frappe、合成数据；本次没有真实模型或Sales Order浏览器验收。
- 下一步将Sales Order及可读子表结构接入读API、页面上下文、操作MCP和固定skill，再做模型/UI链。当前提交只是服务端确定性执行能力，不代表销售订单已在侧栏可用；填表、Unknown核实、阶段三/四仍待完成。

## 阶段二：Sales Order 上下文与模型工具接入

- Sales Order读schema显式提供items可读列、原生read_only/required/default；read_record提供当前docstatus及可读明细。表单快照支持该对象和用户明确选择的未保存列，不保存数据；真实既有订单数量仍为2，外来明细行被拒绝。
- 工具来源账本新增child_fields，schema/record两类结果均记录实际列，历史重用复查子表权限；先用不可读历史列复现失败后实现拒绝。不是仅在界面隐藏旧内容。
- operation MCP新增erp_propose_action，仅Sales Order submit/cancel；业务服务要求本轮已读取精确记录及版本。真实工具测试先因未知工具失败，接入后生成Pending提案且原单据仍为草稿。query工具目录仍只有读取，模型无确认执行入口。
- erp-query升级1.1.0、erp-operation升级1.2.0并更新固定摘要；操作技能明确明细数组完整名单、删除影响、只读计算字段、保存与提交/取消分开确认。运行配置变化继续轮换原生上下文，没有升级DSH。
- 状态提案UI先红测数字状态不清楚，随后显示中文状态与提交/取消业务影响。全前端 **58 passed / 4.87s**，构建通过；固定Runtime/技能/MCP **13 passed / 6.74s**（模型SSE替身）；alpha重启后业务读取/上下文/操作/执行 **19 passed / 13.58s**。原版技能测试曾硬编码1.0.0导致失配测试无效，改为直接改变清单版本，仍验证实际正文/清单不匹配会拒绝。
- 尚未新增真实模型Sales Order调用或浏览器确认。下一步原预算单次worker真实Sales Order链、明细差异可读展示及填表；其它阶段仍进行中。

## Sales Order 真实模型诊断（尚未通过）

- 原生销售订单列表普通writer新会话5egn7nc1e5发出创建请求。第一次真实模型运行fd5650b47f150476fde24444883ab83fff98861033e885840577f7b36ba8f737失败，4次调用/8192预留输出；原生日志明确max-tokens，最后一次2048输出全部用于推理。独立查询确认该会话提案0、销售订单总数仍1，没有创建/确认写入。
- TDD调整operation单次上限3072、query仍2048，每轮总预留16384、输入524288、8次上限及容器预算均未增加。固定Runtime红测2048→绿测3072，5项通过（6.89s）。这不是本次销售订单链已成功的证明。
- 同会话明确核实后发起第二轮d56fb6a54268e1cb28efbad881c748ff9488f586b40e5774171a89d098e6080f，运行配置变更产生新原生会话fcc84a38229b4de692ffbc4bee64d395。仍失败，2次调用/5120预留输出；原生日志为step1工具调用后compaction/start，compaction/end报告caller abort，turn/end user-aborted。根因尚未判定，不将其直接归因模型额度；仍提案0、订单1。
- 需接续诊断容器运行错误传播/状态轮询与压缩路径：context_worker目前丢弃容器stderr，只保存RuntimeError类型，信息不足。建议先保留不含凭证、正文或异常消息的错误类型/栈位置诊断，再有证据地修复，不能盲目重试或扩大总预算。当前无常驻付费worker。
- 真实UI另发现已成功旧提案同时显示“过期需重提”，红测后修正仅Pending且无执行结果时显示过期。59项前端（4.92s）及构建通过；未强制刷新现有标签页。Sales Order真实模型/UI仍未验收，完整目标继续。

## Sales Order 真实链后续与 HTTP 诊断

- 新增仅错误类型/栈位置的容器诊断，不含异常消息、局部变量、请求正文或凭证，替换丢弃stderr后无法定位的问题。脱敏测试先红后绿；后续真实运行实际捕获RemoteProtocolError。
- 状态轮询HTTP不再复用旧连接，仍不重试失败请求；真实HTTP服务先证明原实现复用同一端口，调整后两次请求用不同连接。运行器/worker组合9项通过（3.44s）。只改变业务状态连接管理，不增加资源预算或权限门禁。
- 真实模型Sales Order创建与明细qty修改均在浏览器确认后成功，独立ERP回读和执行记录一致；详见[HITL记录](context-agent-hitl-acceptance.md)。之前失败保留，不能用后续成功抹掉尚未完整复现解释的压缩caller-abort/网络异常。下一步真实提交/取消、状态轮询持续验证、明细差异展示及填表，完整四阶段目标保持。

## 销售订单状态链与压缩频率

- 真实提交首轮形成提案后因过频摘要耗尽预算，原生日志明确compaction HTTP417与guard停止。通过TDD把普通多轮不必要摘要降下来，thresholdRatio 0.005→0.02；更大压力仍触发官方摘要且权限拒绝不继续。未增总额度或绕过摘要授权。
- 随后真实提交4次模型调用、独立取消3次均成功；确认前后原生docstatus0→1→2，两个不同提案/执行/版本，普通writer身份；新原生页面已取消且明细只读。完整ID见[HITL记录](context-agent-hitl-acceptance.md)。
- 最终压缩/模型guard/运行器14项通过（14.75s），无付费worker遗留。下一步填表与可读明细差异，保留未知结果核实、其余阶段一验收、配置预览发布和日常Site工作，不把当前业务闭环扩大为整个目标完成。

## 阶段二：当前表单标量填入链

- 新增fill提案和erp_propose_fill，必须先实际读取记录，模型目标还须等于本条消息的Form快照。server复用原生字段/写权限与版本检查，仍只创建提案。固定operation skill1.3.0明确填入不保存、目前标量且不自动合并目标字段的已有草稿。
- 确认fill只持久化Authorized/browser-draft授权记录，不调用save/submit。该状态不冒充ERP业务成功，也不证明浏览器已应用。真实Frappe红绿验证确认前后Item字段和modified完全不变、重复请求只回同一授权记录。
- 前端收到匹配的授权后调用原生frm.set_value，一次应用前检查所有字段、页面/记录/版本及目标原值；不保存/刷新、不覆盖无关草稿。应用后显示Applied“尚未保存或提交”；刷新只显示服务端Authorized，不重放填入。切页、字段变化、无效后续字段均有零修改测试。
- alpha已reload两个原生审计DocType的Authorized选项并重启backend。63项前端（4.94s）/构建通过，13项Runtime/技能/MCP（7.05s），更新技能精确摘要后额外3项摘要测试通过；fill/操作/Sales Order真实Frappe7项（13.78s）。没有本次付费调用，尚未真实浏览器填入。
- 下一步真实模型→浏览器填入与原生手动保存分开验收；补用户明确提供的目标未保存值作为差异基线、子表填入、可读明细差异。当前只证明标量链及服务端零业务写入，不将其称为完整填表完成。Unknown核实、剩余阶段一与阶段三/四仍保留。

## 标量填入真实UI及已提供草稿基线

- 真实模型4次→fill提案→侧栏确认→原生输入变更，数据库字段/modified不变；再单独点击原生保存才更新数据库。授权记录保持Authorized/browser-draft，不以客户端填入冒充业务执行。ID与字段详见[HITL验收](context-agent-hitl-acceptance.md)。
- form_before扩展先红测：原生提案缺字段、前端拒绝已提供草稿；实现后保存数据库before并另绑定用户明确提供的表单值，确认复查数据库基线，前端验证草稿基线。填入允许建议值等于数据库值（例如恢复草稿），不把它误判为没有业务改动。
- 65项前端/4.94s与构建通过，7项真实Frappe/14.97s、8项固定Runtime/技能7.10s。alpha backend已更新；无常驻付费消费者。下一步子表填入、可读明细差异、Unknown核实以及剩余阶段一、配置预览发布和日常Site，不收缩整体范围。

## 子表填入及可读明细差异

- 明细提案不再整块显示JSON：按物料/字段展开变更、新增、删除及行位置；确认仍绑定原始完整提案，不以展示行重新拼装操作。服务端从原生metadata附带可读列标签和item_code/item_name/qty行标识，删除影响不只显示opaque行名。真实订单提案红测缺item_code后补齐，确认回归通过。
- 现场固定Frappe form.js证明frm.set_value(Table)会clear_table/add_child重建行，故现有行填入使用原生frappe.model.set_value(childtype,rowname,values)，保留行名和未修改草稿。服务端只允许相同行名单/顺序的fill；增删/重排已有业务修改提案支持，不伪装成原生整表填入。
- 前端先校验所有目标行/列及原值（合并用户明确提供的form_before），再原生更新并回读；不调用save或reload。服务端授权真实测试确认后Sales Order原modified及qty2未变，并拒绝以无name新行替换现有行。历史提案读取再检查form_before子表列权限。
- operation技能1.5.0及摘要/MCP说明已同步；68项前端/4.75s、13项Runtime技能MCP/7.60s、8项真实ERP组合/17.54s通过，构建与alpha更新完成。本次无付费调用。
- 下一步真实模型/浏览器验证现有子表列填入和可读差异；注意00002已经取消，不再修改它；00001参考订单不得保存业务变更。之后继续Unknown结果核实、其余阶段一验收、配置隔离预览发布及日常Site，不将这些测试等同全部完成。
# 子表现实填入与结果只读核实（2026-08-29）

- alpha 普通 writer 在参考订单 SAL-ORD-2026-00001 上真实模型运行 df597741782d766aad46596bfce7424a49dfc90e18f99c4df8ad972e5d71e5d0（operation，4 次调用）成功；会话 s2lg7sbghr、提案 sh3avpt8tk。侧栏按物料展示 Quantity 2→5，用户确认填入产生授权 sr86dn8k8l，状态 Authorized，不是业务保存成功。
- 原生表单显示未保存、qty5、金额6172.80；独立数据库回读仍 qty2、总额2469.12、行 q0cg5p3jnk、版本2026-08-28 21:17:27.589587。未点击保存，参考单据没有持久化变更。
- Unknown/Failed 执行增加当前权限下的只读核实入口，仅读取确定记录的当前字段与版本，不重放、不改执行记录；字段一致也不宣称本次执行成功。创建未记录确定名称时明确无法靠列表猜测。
- TDD：新增真实 Frappe 测试先因缺少 verify_execution 失败；前端先因接口及入口缺失失败。实现后全前端70项通过（4.52s），构建通过；真实 Frappe 核实/提案/填表组合8项通过（13.02s）。核实功能尚无浏览器 Unknown 场景验收，不能替代真实未知结果的最终判断。
- 下一步阶段三配置包、隔离预览与原生发布；阶段二剩余边界验收及阶段一缺口继续保留，整体目标未完成。
# 阶段三开始：配置确认与数据包（2026-08-29）

- 前端先红测缺少 ConfigurationProposal，再实现独立预览/发布确认卡，绑定包摘要、目标、基线；显示逐项结果，部分成功/Unknown不重放，不宣称DDL回滚。组件尚未接入侧栏或真实配置服务，不能声称预览可用。
- 从alpha安装的真实Frappe metadata核对DocField、Custom Field、Workflow、状态/转换、DocPerm。Custom Field与Workflow沿用原生System Manager权限，无新增发布角色；工作流原生condition是代码字段，不进入数据包。
- 新增无Frappe副作用的configuration_bundle解析器：新DocType、字段布局、非必填非破坏性扩展、新应用自己的工作流；拒绝未知属性、表达式、默认值、fetch_from、字段改名、既有工作流覆盖、重复字段。稳定JSON摘要并复制输入；这是数据契约，不替代服务端身份、权限、Link存在性、基线或发布并发检查。
- 先确认模块缺失红测，再8项包单测通过（0.01s）；全前端72项通过（4.98s），构建与diff检查通过。本切片没有新真实模型调用、配置DDL、预览站点创建或生产发布。
- 本项目无agent_worker/context_worker消费者；未停止其他项目服务，未新增常驻服务。下一步在业务Site持久化不可变配置包/独立确认与逐项执行，复用现有预算建立隔离合成预览；仍需原生权限/并发钩子、真实保存及工作流验证。阶段一二剩余验收及阶段四保持未完成。
# 阶段三：业务Site配置包与原生配置并发（2026-08-29）

- 新增 DS Configuration Bundle 与当前用户 propose/get，内部持久化绑定Site、操作者、会话、完整数据包、权限摘要和配置基线；不可修改，不执行DDL。alpha安装了内部审计DocType，未创建待发布业务DocType。
- 真实Frappe先红测缺少模块/复查入口，再验证原生System Manager测试用户提案、无业务DocType/表、不可变、普通writer拒绝、跨用户读取拒绝。测试用户、会话、包与临时Property Setter均事务回滚，Item元数据缓存清理。
- 基线包含既有DocType、Custom Field、Property Setter，以及关联结构；原生配置变更后拒绝旧包。最初测试细化后发现复用了业务权限摘要，Item标签变更先触发业务权限版本；已把配置授权摘要限定到原生DocType/Custom Field/Workflow及其子表元数据和权限，保留SSO成员复核，业务领域默认摘要不变。
- 自定义App doc_events对DocType/Custom Field/Property Setter/Workflow的保存、删除、重命名使用按Site/目标隔离的MariaDB连接锁；after_request释放，CLI使用finally或连接关闭。安装版Frappe updatedb内部会commit，因此没有用事务回调提前释放。真实独立第二连接持锁时原生Property Setter保存被明确拒绝，commit后锁仍有效，释放后可获取；不是新的审批/发布角色。
- 包单测与真实配置/锁/业务回归13项通过（6.34s）；原有权限摘要、会话、执行10项通过（12.75s）；diff检查通过。本项目alpha backend已重启加载扩展。未新增真实模型调用、预览站点、应用DDL或生产发布。
- 下一步建立隔离合成预览（原Compose validation网络internal可复用，仅按需使用共享384MiB/.1CPU槽，不与Agent并发），接独立30分钟确认、逐项执行记录、原生保存/工作流与目标发布。当前check_bundle仅复查，调用方仍须持锁；不能将本切片称为发布闭环。阶段一二剩余验收与阶段四未完成。
# 阶段三：真实隔离预览入口（2026-08-29）

- 现场发现已保留的beta合成Site可复用，无需新建常驻容器。beta-backend从validation+api改为仅internal validation网络；既有nginx进程新增18085/preview.localhost虚拟入口，使用独立beta文件卷和固定Site头，原alpha入口不变。没有增加容器或内存/CPU配额，不需要占用Agent按需槽；这替代此前准备新临时预览容器的设想。
- beta设置dsherp_preview、mute_emails、disable_scheduler、pause_scheduler=1。配置前确认Webhook/Email Account均0。自定义App在预览Site拒绝Webhook/Email Account/Notification原生保存，boot使用Frappe原生disable_async关闭实时通信。仅预览禁用，不改变alpha原生按钮或权限。
- 红测先证实beta仍连外网及缺少预览模块；实现后真实独立入口、资产、错误Host、外部Origin拒绝、native Webhook拒绝和连接1.1.1.1:443失败通过。首次重建后nginx先于Gunicorn就绪出现一次502，待实际启动后重跑；未添加自动重试伪造成功。
- 组合预览/alpha原入口/配置锁/配置包5项通过（17.92s），diff检查通过。真实浏览器新标签访问http://preview.localhost:18085/login显示原生中英文登录表单，未注销已有会话。alpha原生socketio及Origin回归通过。
- beta现状：Company=0、setup_complete=0、仅既有beta-reader测试身份。尚无配置发布、预览应用或工作流验收；下一步创建独立合成配置操作身份、完成原生初始化向导（不直接设置setup_complete），再接30分钟确认、逐项执行/回读及UI验证。完整目标继续active。
# 阶段三：原生预览初始化与运行配置修正（2026-08-29）

- 独立合成身份dsherp-preview@example.invalid仅在beta创建，原生System Manager角色；本地0600 profile，不复用生产凭证。先验证用户不存在的红测，创建后DocType/Custom Field/Workflow原生create权限通过。
- 真实浏览器使用该身份登录原生向导，选择简体中文、China、Asia/Shanghai、CNY；提交合成公司“DSHERP 隔离预览合成公司”/DPR、标准科目表、2026财年，不勾选演示数据。只提交一次，之后只读核实。原生流程完成并进入Desk，setup_complete=1、Company属性正确、Sales Order=0。
- 原生向导自动选取首个既有用户beta-reader，账户页保持原姓名、密码留空。源码确认既有用户不会追加角色；完成后原生流程将本次新预览登录切换到beta-reader。独立回读其角色仍仅DSHERP Beta Reader；未退出alpha或用户已有其他站点会话。后续配置UI需重新使用合成配置身份，不假定当前预览标签仍是System Manager。
- 真实浏览器发现extend_bootinfo设置disable_async无效：固定版frappe.sessions.get在扩展钩子之后用frappe.conf覆盖该值。原直接调用helper的测试不足以证明实际启动行为，已替换成完整原生sessions.get红测；改用原生Site配置disable_async=1并删除无效钩子，重启相关后端后通过。
- 冷启动曾跑满beta原0.15CPU并触发入口超时。停掉本地合成验收不需要的scheduler（Compose profile scheduled），把其128MiB/.1CPU配额转给beta：448MiB/.25CPU。运行中项目仍10容器、3712MiB/1.9CPU，保留Agent384MiB/.1CPU；未扩大总预算。日常Site阶段需要重新核对scheduler资源安排，不能默认同时启用scheduled profile而超预算。
- beta重建换IP后nginx刚发reload的过渡请求曾502；reload就绪后4项真实身份/原生启动信息/预览隔离/alpha入口通过（15.54s），预览login复测200/0.458741s。不是声称所有冷启动延迟已消除。无新模型调用、应用发布或生产部署。
- 后续重点：30分钟不可变预览/发布确认、逐项执行与原生保存/工作流。已核对Workflow.on_update会自动创建状态字段并对空状态记录UPDATE；发布计划必须显式包含字段并保证新应用在工作流应用前无业务记录，防止隐式回填及并发窗口，不能将原生DDL视为整体事务。
# 阶段三：独立预览确认与真实原生应用首链（2026-08-29）

- 新增DS Configuration Confirmation与DS Configuration Execution，分别存储不可变30分钟确认和独立逐项执行记录；确认绑定完整编译后原生文档、包摘要、Site与基线，显示字段、模块、编号方式、可提交/子表及角色权限。不是只绑定可被后续代码改变的抽象配置。
- prepare_preview限定隔离Site并复查原生权限/基线；确认前无业务DocType或表。confirm_preview单次执行意图先提交，再原生insert与回读；沿用连接锁跨DDL提交，逐项记录，不把Running/未知结果当成功。重复确认返回唯一执行记录，不重放。
- 真实beta普通合成配置身份确认创建DS Preview Native Apply Test（custom=1），随后原生insert/save将合成记录从Synthetic initial改为Synthetic changed并回读；重复确认换request_id仍同一execution_id。测试先因缺少入口失败，实现后通过；完整原生文档冻结与权限可见展示另经红测补齐。
- 确认/原生应用/包单测10项通过（12.66s）；预览隔离、配置锁与包4项通过（22.09s）。beta执行标准bench migrate同步当前自定义App审计结构，没有修改上游文件。测试结构/业务记录/确认/执行已清理：独立回读test_doctype=False、test_table=False、confirmation/execution=0。Frappe删除DocType保留空表，测试清理仅对明确自建的固定空表执行DROP，不向Agent开放删除配置或SQL工具。
- 本切片没有真实模型配置调用、配置确认浏览器UI、目标发布或工作流验收。当前执行器在开始任何DDL前明确拒绝尚未接通的workflow包；这只是待实现部分，不能缩减计划。Custom Field执行路径已写但尚未真实应用验收；新DocType循环关联当前明确拒绝。
- 下一步：补工作流显式状态字段/原生状态与动作、部分失败和逐步权限变化测试、配置确认前端/API接线与业务Site→预览的受控传递，再独立目标发布。尤其Workflow原生UPDATE不能对已有或并发插入记录隐式回填；每步重新核实身份/权限，不能只依赖开始时检查。整体目标active，阶段一二剩余验收与阶段四不变。
# 阶段三：配置执行中撤权与部分结果（2026-08-29）

- 新增真实Frappe故障注入：第一项原生DocType创建及回读后，通过原生User.save禁用合成配置用户。红测发现原实现仍成功创建第二项，证明仅开始时核查身份不够。
- 配置包权限复查拆出check_authorization，每一原生执行步骤开始前重新核实当前身份、原生配置权限摘要与SSO成员关系；不重新用已变化的新建结构做原始基线比较。尚未开始原生调用即失败的步骤标Failed，已开始且结果不明才标Unknown，先前成功保留，整体Partial。
- 修复后第一项Succeeded、第二项Failed，第二项DocType与表均不存在。恢复合成用户访问后再次确认同一提案，仅返回同一Partial执行记录，不补做第二项。测试恢复用户enabled=1，并清理所有自建测试结构和记录。
- 原生应用/撤权/确认/包/预览身份组合5项通过（27.16s），diff检查通过。故障由测试在真实步骤间注入，未声明真实浏览器撤权或生产验证。无新模型调用或发布。
- 下一步仍是Workflow显式状态字段、原生状态/动作及工作流执行、并发新记录保护、前端与跨站预览发布接线；当前workflow执行尚未接通，整体目标active。
# 阶段三：原生Workflow应用与防回填（2026-08-29）

- 移除workflow尚未接通的临时拒绝。确认包现在包含必要的原生Workflow State、Workflow Action Master、新DocType和Workflow文档；状态/动作已有时只读复用，原生权限及基线包含相关定义，不修改既有工作流。
- 新应用必须显式提供workflow_state Link→Workflow State，包含提交/取消状态时必须显式可提交；避免Workflow.on_update偷偷创建字段。确认展示原生草稿/已提交/已取消、可编辑角色、自行审批、关闭邮件提醒，以及字段必填/列表/布局信息。
- 真正执行Workflow前检查新表无记录；配置锁覆盖关联结构及状态/动作定义。自定义DocType原生before_insert检查其他连接是否持有配置锁，防止DDL提交后、Workflow安装前的插入窗口；普通ERPNext既有标准DocType不增加此检查，原生按钮不增加审批。
- 真实beta普通配置用户：确认创建状态/动作/可提交DocType/Workflow→原生insert草稿→apply_workflow提交→apply_workflow取消→数据库docstatus2回读，重复确认同一执行记录。未生成隐式Custom Field。独立第二数据库连接持锁时，原生新记录insert明确拒绝；释放后正常创建。
- 另以同连接故障注入在DocType创建后插入记录，Workflow步骤在执行前停止，整体Partial、末步Failed，记录状态保持空，未创建Workflow，也不因重复确认而重放。测试自建记录、配置、状态/动作、确认/执行及明确的空表清理。
- 工作流/应用/撤权/确认/包6项组合通过（38.35s）；原生锁/预览隔离/填表/核实/包14项通过（20.84s）；展示信息补齐后工作流与确认3项重跑通过（24.07s）。alpha/beta后端已重启加载并发钩子；未增加资源或新模型调用。
- 当前仍是服务端真实预览验证，不是配置Agent模型/UI或目标发布验收。下一步配置领域技能/MCP与侧栏接线、受控跨站预览传递和目标单独发布；补Custom Field真实应用、过期/并发确认/未知结果核实。其余阶段及完整目标继续active。
