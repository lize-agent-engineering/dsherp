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
