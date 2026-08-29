# 原生上下文 Agent 完成度审计

日期：2026-08-29。审计对象为 `main` 当前工作区、本地 validation Runtime 与计划
[`2026-08-29-context-agent-sidebar.md`](../superpowers/plans/2026-08-29-context-agent-sidebar.md)。
本文件区分代码/测试、本地真实 Runtime、真实模型、真实 ERP、浏览器 UI、日常企业与生产上线；任一层不能替代另一层。

## 结论

当前尚不能完成持续目标。阶段一至三的项目代码和本地合成真实链已完成核心闭环；阶段四的空日常 Site、OAuth/Runtime 准备及备份恢复已完成，但日常 Site 仍为 `setup_complete=0`、Company=0、普通业务用户=0，平台 daily 企业仍为 `Provisioning`、成员=0。因此真实日常企业初始化、普通成员登录和该身份下的 Agent 浏览器闭环没有证据，不能以 alpha/beta 合成身份代替。

## 逐项证据

| 要求 | 当前结论 | 权威证据 |
|---|---|---|
| 原生 Desk 全局 React/Ant Design 右侧栏；打开/关闭、历史、新会话、跨页、取消、有限上下文提示 | 本地实现及 alpha 浏览器验收成立 | `frontend/src/ContextSidebar.jsx`、`frontend/src/desk-context.jsx`；全前端 88 项；真实 alpha 多轮及刷新证据见 [`context-agent-evidence.md`](context-agent-evidence.md) |
| 固定 DSH SDK/Runtime 0.1.1rc1；单 stdio 入口；公开扩展实现显式 create/resume/cancel/close | 成立 | `requirements.lock` 固定两个 0.1.1rc1 包；`runtime/context-plugin.cjs`；原生跨进程恢复和在途取消测试；固定源码 ref `528c682e...` 记录在证据文档 |
| 会话、模型运行、提案、执行分别持久化；幂等、刷新不重跑、单写者、配置/权限变化轮换原生会话 | 成立 | DS Conversation、DS Model Run、DS Operation Proposal、DS Execution Record；会话、claim/cancel、permission revision、session runtime 测试及真实双轮恢复 |
| 页面快照只取路由/业务对象/筛选/选中项/明确允许的未保存字段和子表，不抓 DOM | 成立 | `frontend/src/page-context.js`、`frappe_app/dsherp_bridge/context_api.py`；伪造上下文、字段/子表权限、预算测试 |
| 所有普通与压缩模型调用均受权限、来源、预算和 Runtime/skill 版本约束 | 成立 | `runtime/model-guard.cjs`、`context_execution.reserve_model_call`、原生 compaction 组合测试；固定业务 skill 摘要及 `includeDefaultRoots=false`、`watch=false` |
| 平台登录只提供企业关系；业务 Site 使用明确映射的当前普通用户；撤权即时作用于会话和后台运行 | alpha 真实链成立；daily 未完成 | alpha 原生 OAuth 授权码→业务 Cookie、并发请求及撤权测试/浏览器证据成立。daily OAuth 已配置，但无普通成员与 ERP 用户映射，不能验收 |
| Item、Customer 创建/修改；Sales Order 草稿创建/修改/提交/取消；确认前零写入、服务端复查、原生业务方法、唯一执行和回读 | 功能与本地真实 ERP 成立 | `operations.py`、操作领域 MCP/skill；真实 Frappe 并发/版本/权限测试；alpha 真实模型与浏览器完成 Item、Customer、Sales Order 生命周期证据 |
| 建议填入当前表单；不自动保存；标量与既有子表行；保存/提交分别确认 | 成立 | `form-fill.js`、fill 提案/授权；真实浏览器填入后 DB 零变化、另点原生保存才持久化；子表逐行原生 `set_value` 验收 |
| Unknown/Partial 不盲重试，当前权限下只读核实，不改写原执行结论 | 成立 | operation/configuration `verify_execution`、对应前端入口和真实 ERP 测试 |
| 新应用/非破坏性字段/布局/工作流配置；禁止任意代码和破坏性变更 | 成立 | `configuration_bundle.py` fastfail 契约；真实原生 DocType、Custom Field、Workflow 状态链及并发插入保护 |
| 隔离预览无业务数据外泄和外部副作用；预览与目标发布分别确认；签名不可变包/基线；逐项记录部分结果 | 本地合成链成立 | beta 独立 Site/DB、无外网网络、邮件/异步关闭；alpha→beta HMAC 交接、回执、独立发布；真实模型生成 `DS Model Quality Inspection` 后两站 UI/ERP 回读 |
| 日常企业 Site 不含合成业务数据；真实信息由用户填写；首次真实数据前备份和隔离恢复 | 准备成立，真实初始化待输入 | `dsherp-daily.localhost` 三 App、`setup_complete=0`，Company/Item/Customer/Sales Order=0；四件套备份真实恢复并回读后清理临时 Site |
| 日常企业普通用户从平台进入原生 Desk，在真实业务页连续问询、确认业务操作并核实结果 | **缺失/阻塞完成** | daily 平台企业 `Provisioning`、membership=0；Site 只有 Administrator 和无业务角色 Runtime 身份。缺少用户提供的企业资料、普通平台成员与普通 ERP 用户角色映射 |
| 旧 Demo 保留独立入口、退出正式导航；旧平台历史只读；被替代服务停止并删除 | 成立 | `dsherp-studio` 页面仍在；Portal 无提问入口；旧 task mutation/worker/MCP/runner 已删，worker 身份禁用，worker/websocket/scheduler 无运行容器 |
| 文档、按功能本地提交、不推送、不改上游/其他项目 | 当前成立 | 计划、证据、HITL 和本审计；2026-08-29 功能提交链；工作区干净。未观察到自动 push 或上游/其他项目修改 |

## 当前验证快照

- Python 全仓：170 passed / 426.13s。
- 前端：15 files、88 passed；生产构建成功。
- 运行服务：backend、beta-backend、frontend、platform backend/frontend、MariaDB、Redis；无 worker、scheduler 或 websocket。
- 按需 Runtime：alpha 真实多轮、业务操作和配置模型调用后均退出；daily 空队列运行成功，未产生模型调用。
- 真实模型：`deepseek-v4-flash` 已在 alpha 完成查询、HITL 业务操作及配置生成；尚未在未初始化的 daily Site 运行。
- 真实 ERP/UI：alpha/beta 为本地合成验收；daily 只有原生登录/设置向导入口和恢复验证。没有生产上线声明。

## 完成目标仍需的外部事实

1. 用户在 daily 原生向导提供公司名称、简称、国家、时区、默认币种和财年起始信息。
2. 明确一个普通平台成员，以及 daily 中对应的普通 System User 与实际业务角色；不得映射 Guest、Administrator 或内部 Runtime 用户。
3. 在该普通身份下完成平台→daily OAuth、原生业务页面多轮查询、至少一个明确 HITL 操作及 ERP 回读，并再次备份。
4. 重跑与受影响范围相称的回归，更新证据；只有届时才能把持续目标标为完成。
