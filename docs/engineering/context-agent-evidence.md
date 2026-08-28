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
