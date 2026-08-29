# 原生上下文 Agent 完成度审计

日期：2026-08-29。审计对象为 `main` 当前工作区、本地 validation Runtime 与计划
[`2026-08-29-context-agent-sidebar.md`](../superpowers/plans/2026-08-29-context-agent-sidebar.md)。
本文件区分代码/测试、本地真实 Runtime、真实模型、真实 ERP、浏览器 UI、日常企业与生产上线；任一层不能替代另一层。

## 结论

用户已明确授权使用标识清楚的模拟数据自行创建日常企业和用户映射。四阶段本地交付已完成：阶段一至三既有真实合成链保持成立；阶段四新增 daily 原生初始化、普通成员映射、平台 OAuth、真实模型多轮查询、HITL 写入与原生页面回读，以及验收后四件套备份隔离恢复。它是本地合成可用环境，不是生产上线，也不包含真实企业数据。

## 逐项证据

| 要求 | 当前结论 | 权威证据 |
|---|---|---|
| 原生 Desk 全局 React/Ant Design 右侧栏；打开/关闭、历史、新会话、跨页、取消、有限上下文提示 | 本地实现及 alpha 浏览器验收成立 | `frontend/src/ContextSidebar.jsx`、`frontend/src/desk-context.jsx`；全前端 88 项；真实 alpha 多轮及刷新证据见 [`context-agent-evidence.md`](context-agent-evidence.md) |
| 固定 DSH SDK/Runtime 0.1.1rc1；单 stdio 入口；公开扩展实现显式 create/resume/cancel/close | 成立 | `requirements.lock` 固定两个 0.1.1rc1 包；`runtime/context-plugin.cjs`；原生跨进程恢复和在途取消测试；固定源码 ref `528c682e...` 记录在证据文档 |
| 会话、模型运行、提案、执行分别持久化；幂等、刷新不重跑、单写者、配置/权限变化轮换原生会话 | 成立 | DS Conversation、DS Model Run、DS Operation Proposal、DS Execution Record；会话、claim/cancel、permission revision、session runtime 测试及真实双轮恢复 |
| 页面快照只取路由/业务对象/筛选/选中项/明确允许的未保存字段和子表，不抓 DOM | 成立 | `frontend/src/page-context.js`、`frappe_app/dsherp_bridge/context_api.py`；伪造上下文、字段/子表权限、预算测试 |
| 所有普通与压缩模型调用均受权限、来源、预算和 Runtime/skill 版本约束 | 成立 | `runtime/model-guard.cjs`、`context_execution.reserve_model_call`、原生 compaction 组合测试；固定业务 skill 摘要及 `includeDefaultRoots=false`、`watch=false` |
| 平台登录只提供企业关系；业务 Site 使用明确映射的当前普通用户；撤权即时作用于会话和后台运行 | 成立 | alpha 原有撤权/并发链保持；daily 平台 `member@example.invalid` 显式映射普通 ERP 用户 `daily-operator@example.invalid`，浏览器完成 OAuth 后原生 Desk 显示“日常合成操作员” |
| Item、Customer 创建/修改；Sales Order 草稿创建/修改/提交/取消；确认前零写入、服务端复查、原生业务方法、唯一执行和回读 | 功能与本地真实 ERP 成立 | `operations.py`、操作领域 MCP/skill；真实 Frappe 并发/版本/权限测试；alpha 真实模型与浏览器完成 Item、Customer、Sales Order 生命周期证据 |
| 建议填入当前表单；不自动保存；标量与既有子表行；保存/提交分别确认 | 成立 | `form-fill.js`、fill 提案/授权；真实浏览器填入后 DB 零变化、另点原生保存才持久化；子表逐行原生 `set_value` 验收 |
| Unknown/Partial 不盲重试，当前权限下只读核实，不改写原执行结论 | 成立 | operation/configuration `verify_execution`、对应前端入口和真实 ERP 测试 |
| 新应用/非破坏性字段/布局/工作流配置；禁止任意代码和破坏性变更 | 成立 | `configuration_bundle.py` fastfail 契约；真实原生 DocType、Custom Field、Workflow 状态链及并发插入保护 |
| 隔离预览无业务数据外泄和外部副作用；预览与目标发布分别确认；签名不可变包/基线；逐项记录部分结果 | 本地合成链成立 | beta 独立 Site/DB、无外网网络、邮件/异步关闭；alpha→beta HMAC 交接、回执、独立发布；真实模型生成 `DS Model Quality Inspection` 后两站 UI/ERP 回读 |
| 日常企业使用受控初始化数据；首次数据前和验收后均有备份和隔离恢复 | 本地合成成立 | 用户授权后用原生 setup wizard 方法创建唯一公司“DSHERP 日常合成企业”、Item/Customer fixture 和普通用户；初始化前备份保留，验收后四件套恢复到一次性 Site 并逐项比对后清理 |
| 日常企业普通用户从平台进入原生 Desk，在真实业务页连续问询、确认业务操作并核实结果 | 成立 | daily 为 `Ready` 且唯一成员映射成立；Item 页面真实模型两轮读取成功；HITL 提案 `04pnkv1thg` 确认前零写入，确认后原生 Item 名称及 Frappe Version 回读一致 |
| 旧 Demo 保留独立入口、退出正式导航；旧平台历史只读；被替代服务停止并删除 | 成立 | `dsherp-studio` 页面仍在；Portal 无提问入口；旧 task mutation/worker/MCP/runner 已删，worker 身份禁用，worker/websocket/scheduler 无运行容器 |
| 文档、按功能本地提交、不推送、不改上游/其他项目 | 提交前复核中 | 计划、证据、HITL 和本审计持续维护；未自动 push，未修改上游或其他项目 |

## 当前验证快照

- Python 最终全仓：171 passed / 402.04s；本次受影响 daily/platform 组合另为 16 passed / 22.47s。
- 前端最终：15 files、88 passed；生产构建成功。
- 运行服务：backend、beta-backend、frontend、platform backend/frontend、MariaDB、Redis；无 worker、scheduler 或 websocket。
- 按需 Runtime：daily 三次成功运行后均退出；一次第二轮因没有本轮工具来源被服务端拒绝，三分钟到期后标为 Failed 且未自动重试。固定 query skill 1.2.0 后同会话追问重新读取成功。
- 真实模型：`deepseek-v4-flash` 在 daily 完成两轮只读查询和一次业务修改提案；不是替身测试。
- 真实 ERP/UI：daily 普通用户经平台 OAuth 进入原生 Item 页面；确认前页面/数据库名称未变，确认后重新打开页面显示“日常 Agent 合成物料（HITL 已验收）”及原生 Version 变更。没有生产上线声明。

## 交付边界

当前不再有企业资料或用户映射阻塞。合成 daily 用于本地日常使用和验收；替换为真实企业资料或发布到生产仍是新的外部变更，必须另行授权。本地交付完成不表述为生产上线。
