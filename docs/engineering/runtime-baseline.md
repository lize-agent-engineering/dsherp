# 运行基线

首次核验日期：2026-08-28；当前 ERP 运行基线核验日期：2026-09-02。本文区分源码核验、安装和运行；不代表 SaaS、生产部署或 v16 迁移整体完成。

## 本机只读盘点

- macOS arm64，32 GiB RAM；盘点时 swap 使用约 3.83 GiB，压缩页约 9 GiB，可用磁盘 112 GiB。
- 系统 Python 3.12.9；项目 uv 创建的 `.venv` 使用 Python 3.12.11。Node v26.7.0，npm 11.19.0，pnpm 9.12.2，uv 0.9.27。
- Docker 23 个运行容器；8080、8001、18080、6379 等已被占用。未进入、修改或停止现有容器，未访问 AgenERP 的站点、数据库或凭证。
- 首次 DSH 阶段仅安装项目 Python 环境，Runtime wheel 约 52.6 MiB；后续用户授权后启动了独立 ERP 测试栈，未拉取 ERP 镜像。

## DSH 固定选择（v15 → v16 未变）

ERPNext/Frappe 切换到 v16 没有改动本节的 DSH 链：SDK/Runtime 仍为 `0.1.1rc1`，两个 wheel SHA256、Runtime 内置 Node `v24.19.0`、独立 Cordis 组合与 MCP bridge 均保持不变。

| 项目 | 固定标识与证据 |
| --- | --- |
| SDK | `deepseek-harness-sdk==0.1.1rc1`，Python >=3.10 |
| Runtime | SDK 强制依赖 `deepseek-harness-runtime-bin==0.1.1rc1` |
| 对照源码 | tag `dsh-v0.1.1-rc.1`，提交 `528c682e061696f5a160f363f236ecbf53cbd006` |
| SDK wheel SHA256 | `2113aec229039da435bc44b275b487216d2b1c308d850521b88cea6ce3c1b762` |
| macOS arm64 Runtime wheel SHA256 | `2707cd666ba49ee0963228873abf7850ca7ec5e782cca61e3603793bace0d1cf` |
| Node | wheel 内置 `v24.19.0`，通过临时 Cordis persona 求值 `process.version` 并在本地模型替身收到的 system message 中读取；不使用系统 v26.7.0 |

发布元数据：[SDK PyPI](https://pypi.org/pypi/deepseek-harness-sdk/0.1.1rc1/json)、[Runtime PyPI](https://pypi.org/pypi/deepseek-harness-runtime-bin/0.1.1rc1/json)。安装后的 `api.py`、`client.py`、`models.py` 与上述提交逐文件 SHA256 相同。Runtime 包内部 `deepseek-harness-runtime.json` 写的是 `0.0.0-dev`，不能用它证明发布版本或构建提交；以 wheel 版本和哈希固定制品，不声称已证明二进制全部源码来源。

核验时 GitHub master/tag `dsh-v0.1.2-alpha.1` 指向 `cd5ef8148158c3a752a658978873241fdf8e2bbc`，它的 `profile`、`dsh_home` 接口不同。**不得混用 master 文档与当前 wheel**。选择已有匹配 wheel 的 rc1，尚非生产稳定性承诺。

## 已核对的实际接口

依据固定提交的 [SDK README](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/python/sdk/README.md)、[完整示例](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/examples/jsonrpc-agent/minimal.py)、[api.py](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/python/sdk/src/deepseek_harness/api.py)、[client.py](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/python/sdk/src/deepseek_harness/client.py)：

- `from deepseek_harness import DeepSeekHarness`；应用 Python 持有 SDK，SDK 启动独立的 `dsh-jsonrpc-agent-pkg-macos-arm64` 子进程，经 stdio JSON-RPC 通信。
- 构造参数：`provider`、`model`、`max_tokens`、`cwd`、`runtime_cwd`、`session_root`、`cordis`、`api_key`、`base_url`、`request_timeout_seconds`、`shutdown_timeout_seconds`。
- `start()` 完成 initialize；`start_session(session_id)` 返回 Session；`run(input, session_id=..., on_notification=...)` 是最短调用。
- 通知为 `Notification(method, payload)`。`RunResult.events` 仅根会话事件；`notifications` 可含后代。最终响应来自已提交 assistant 消息，结束原因来自最后一个根 `turn/end` 的 `data.reason.kind`；仅 `completed` 计作本验证成功。
- `close()` 请求 shutdown、关闭 stdin、terminate/wait，必要时 kill/wait。项目用 `try/finally` 覆盖初始化异常及回调异常；单独验证进程退出，不凭 Python 返回宣称清理成功。
- `env` 合并父进程环境，不是替换。生产租户必须使用隔离容器/受控启动环境；本阶段目录隔离仅用于合成会话，不是 OS 安全边界。

## 工具与扩展

固定版 [agent-spine](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/examples/agent-spine-demo/src/index.ts) 默认加载 Bash、jobs 和 skills。使用完整独立 Cordis 组合，显式 `toolBash: false`、`toolJobs: false`、`skills.enabled: false`、`workspaceContext: false`，不挂载 Shell、FS、HTTP、数据库、代码执行或子 Agent 插件。零工具是最小模型调用阶段的预期，已通过真实 Runtime 请求验证；ERP 组合通过测试确认只有两个只读 MCP 工具，配置文本本身不是证明。

固定版 [MCP bridge](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/mcp/mcp-client/README.md) 已包含在 [Runtime wheel](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/python/sdk-runtime/README.md)。可用 stdio 或 Streamable HTTP 连接仅发布必要 ERP 工具的 Python MCP server；不是 SDK Python 回调注册。模型工具名为 `mcp__<serverName>__<rawName>`。须设置 `failOnStartupError: true`，本阶段关闭自动 reconnect；MCP Resources/Prompts 不支持。服务端绑定身份、企业、固定站点并校验参数及权限，不把任意 URL、方法或身份交给模型。

## ERP v16 当前运行基线（2026-09-02）

| 项目 | 当前隔离合成环境实测 |
| --- | --- |
| ERPNext | `16.33.0` |
| Frappe | `16.31.0` |
| 容器 Python | `3.14.7` |
| 镜像 | `frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd` |

镜像版本由容器内 `bench version`、Python 解释器和 RepoDigest 分别核验；标签、最新主线文档或工作副本不能代替运行事实。当前四站为 fresh provision 的隔离合成站点，证据见 [v16 迁移证据](v16-migration-evidence.md)。C4 与 C5 已完成：冷静期、最终独立审计通过，`main` 已切换到 v16，v15 九卷已归档并逐名删除。本节仍不声明可上线或生产可用——四站为隔离合成环境，不含生产租户数据。

### v16.33.0 制造 mapper 固定契约

下表按当前七条受信 route 逐条列出；两条 Work Order route 复用同一个上游 `make_stock_entry`。签名由当前 v16 容器安装源码和 C1/C2 固定 tag 探针核对，不凭文档猜测。所有 mapper 返回的目标文档仍需保存、校验及按风险确认；返回草稿不等于保存或提交。

| 受信 route | v16.33.0 固定源码与签名 |
| --- | --- |
| Sales Order → Delivery Note | [`make_delivery_note`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/selling/doctype/sales_order/sales_order.py#L1151-L1320)`(source_name, target_doc=None, kwargs=None)` |
| Work Order → Material Transfer for Manufacture | [`make_stock_entry`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/manufacturing/doctype/work_order/work_order.py#L2664-L2722)`(work_order_id: str, purpose: str, qty: float \| None = None, target_warehouse: str \| None = None, is_additional_transfer_entry: bool = False, source_stock_entry: str \| None = None)` |
| Work Order → Manufacture | [`make_stock_entry`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/manufacturing/doctype/work_order/work_order.py#L2664-L2722)`(work_order_id: str, purpose: str, qty: float \| None = None, target_warehouse: str \| None = None, is_additional_transfer_entry: bool = False, source_stock_entry: str \| None = None)` |
| Purchase Order → Purchase Receipt | [`make_purchase_receipt`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/buying/doctype/purchase_order/purchase_order.py#L761-L821)`(source_name, target_doc=None, args=None)` |
| Purchase Order → Subcontracting Order | [`make_subcontracting_order`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/buying/doctype/purchase_order/purchase_order.py#L962-L986)`(source_name, target_doc=None, save=False, submit=False, notify=False)` |
| Subcontracting Order → Send to Subcontractor | [`make_rm_stock_entry`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/controllers/subcontracting_controller.py#L1384-L1501)`(subcontract_order, rm_items=None, order_doctype='Subcontracting Order', target_doc=None)` |
| Subcontracting Order → Subcontracting Receipt | [`make_subcontracting_receipt`](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/subcontracting/doctype/subcontracting_order/subcontracting_order.py#L433-L435)`(source_name, target_doc=None)` |

### Frappe 16.31.0 API 权限事实

- [`get_meta`](https://github.com/frappe/frappe/blob/v16.31.0/frappe/api/v2.py#L216-L218) 为 `/api/v2/doctype/<doctype>/meta` 调用 `frappe.only_for("All")`：它要求已登录身份，但没有替本项目执行目标业务 DocType 的 `read` 权限检查。因此受限 schema 工具仍须自行检查 DocType read 权限并过滤字段。
- `/api/resource/Customer`、`/api/resource/Item` 等 v1 列表调用 [`frappe.client.get_list`](https://github.com/frappe/frappe/blob/v16.31.0/frappe/client.py)，由 Frappe 应用用户权限；本项目另以普通用户实测读取和拒绝，不把 meta 接口当作业务授权边界。

## ERP v15 历史基线（已退役运行栈证据，原文加注保留）

以下只记录 v16 切换前的 v15 时期历史事实，不能作为当前运行版本、当前源码签名或部署指令。

- **v15 时期历史**：ERPNext `v15.119.3`；实际镜像 Frappe `v15.118.0`（替代原候选 v15.119.1）。ERPNext [pyproject](https://github.com/frappe/erpnext/blob/v15.119.3/pyproject.toml) 声明 Frappe `>=15.111.0,<16.0.0`；该组合已完成独立站点只读接口验证；不代表制造业务链已验证。
- **v15 时期历史**：本机曾使用官方镜像 `frappe/erpnext:v15.119.3`，arm64，约 2.55 GB，RepoDigest `frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341`。曾在独立容器核实内部 Frappe 15.118.0、Python 3.11.6；镜像没有 Git 元数据，不虚构其内部源码提交。
- **v15 时期历史**：当时的部署要求使用该 digest 或经核验的新 digest、独立 compose project/网络/卷，不能挂载既有站点；具体资源边界见 ERP 证据。
- **v15 时期历史**：[Work Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/manufacturing/doctype/work_order/work_order.py)：`make_stock_entry(work_order_id, purpose, qty=None, target_warehouse=None, source_stock_entry=None)` 生成 Stock Entry 字典，不等于保存或提交。
- **v15 时期历史**：[Purchase Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/buying/doctype/purchase_order/purchase_order.py)：`make_subcontracting_order(source_name, target_doc=None, save=False, submit=False, notify=False)`；此方法内部捕获部分提交错误，不可凭返回文档报告提交成功。
- **v15 时期历史**：[Subcontracting Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/subcontracting/doctype/subcontracting_order/subcontracting_order.py)：`make_subcontracting_receipt(source_name, target_doc=None)`。供料加工、供应商供料和直接采购不能直接合并为一种动作；制造闭环曾在 v15 本地合成环境验证（alpha 分段 + daily 端到端），证据见[阶段 2/3 制造闭环与技能升版证据](stage-2-3-manufacturing-evidence.md)与[原生侧栏 HITL 真实验收](context-agent-hitl-acceptance.md)；不代表生产验证或生产上线。
- **v15 时期历史**：[Frappe v2](https://github.com/frappe/frappe/blob/v15.118.0/frappe/api/v2.py) 提供 `/api/v2/doctype/<doctype>/meta`，仅 `only_for("All")`；自定义接口须另行检查业务 DocType read 权限。客户/物料 v1 路径为 `/api/resource/Customer`、`/api/resource/Item`，调用 [frappe.client.get_list](https://github.com/frappe/frappe/blob/v15.118.0/frappe/client.py) 受权限约束。本项目通过普通用户验证原生记录读取，并新增带业务权限检查的薄 schema API，字段均从实际 Site 发现。

## 当前 v16 验证边界

当前 v16 隔离合成环境已分别取得自动化、真实 ERP、固定 DSH Runtime、本地模型替身、真实浏览器和经授权真实 DeepSeek 只读证据；这些证据不能互相替代，也不代表生产租户部署。C4/C5 完成情况与剩余边界见 [v16 迁移证据](v16-migration-evidence.md)；DSH 与 ERP 分层证据另见 [DSH 证据](dsh-validation-evidence.md) 和 [ERP 证据](erpnext-integration-evidence.md)。

### C3 worker 配置加载边界

`dsherp.context_worker` 的告警阈值定义在进程启动时导入的 `dsherp/alerts.py`，Prometheus 指标注册表也在模块导入时创建。因此 `alerts.py`、`metrics.py` 或其阈值发生版本变更后，必须重启唯一的 LaunchAgent worker 才能生效；不能把工作副本已更新当作运行进程已加载。provider `.env` 仍按每轮重读，其行为与代码/阈值加载边界不同。

## 本轮依赖与环境补充

官方 MCP Python SDK 固定 `1.26.0`，httpx `0.28.1`；`pydantic-settings` 从自动解析的 2.15.0 固定到该 MCP tag 上游锁文件中的 2.10.1，解决 lifespan 前向引用警告；未修改第三方源码。重新锁定并验证共 36 个 Python 包。Docker 测试栈固定 MariaDB 10.6.28、Redis 6.2.24；Compose 5.0.2。
