# 首次运行基线

核验日期：2026-08-28。本文区分源码核验、安装和运行；不代表 SaaS 或制造业务完成。

## 本机只读盘点

- macOS arm64，32 GiB RAM；盘点时 swap 使用约 3.83 GiB，压缩页约 9 GiB，可用磁盘 112 GiB。
- 系统 Python 3.12.9；项目 uv 创建的 `.venv` 使用 Python 3.12.11。Node v26.7.0，npm 11.19.0，pnpm 9.12.2，uv 0.9.27。
- Docker 23 个运行容器；8080、8001、18080、6379 等已被占用。未进入、修改或停止现有容器，未访问 AgenERP 的站点、数据库或凭证。
- 首次 DSH 阶段仅安装项目 Python 环境，Runtime wheel 约 52.6 MiB；后续用户授权后启动了独立 ERP 测试栈，未拉取 ERP 镜像。

## DSH 固定选择

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

## ERP 基线（已运行）

- ERPNext `v15.119.3`；实际镜像 Frappe `v15.118.0`（替代原候选 v15.119.1）。ERPNext [pyproject](https://github.com/frappe/erpnext/blob/v15.119.3/pyproject.toml) 声明 Frappe `>=15.111.0,<16.0.0`；该组合已完成独立站点只读接口验证；不代表制造业务链已验证。
- 本机已有官方镜像 `frappe/erpnext:v15.119.3`，arm64，约 2.55 GB，RepoDigest `frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341`。已在独立容器核实内部 Frappe 15.118.0、Python 3.11.6；镜像没有 Git 元数据，不虚构其内部源码提交。
- 未来部署必须使用该 digest 或经核验的新 digest、独立 compose project/网络/卷；不能挂载现有站点。用户已授权并启动独立测试栈；具体资源边界见 ERP 证据。
- [Work Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/manufacturing/doctype/work_order/work_order.py)：`make_stock_entry(work_order_id, purpose, qty=None, target_warehouse=None, source_stock_entry=None)` 生成 Stock Entry 字典，不等于保存或提交。
- [Purchase Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/buying/doctype/purchase_order/purchase_order.py)：`make_subcontracting_order(source_name, target_doc=None, save=False, submit=False, notify=False)`；此方法内部捕获部分提交错误，不可凭返回文档报告提交成功。
- [Subcontracting Order 源码](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/subcontracting/doctype/subcontracting_order/subcontracting_order.py)：`make_subcontracting_receipt(source_name, target_doc=None)`。供料加工、供应商供料和直接采购不能直接合并为一种动作；基础资料 schema 已验证；BOM 与制造单据业务运行尚未验证。
- [Frappe v2](https://github.com/frappe/frappe/blob/v15.118.0/frappe/api/v2.py) 提供 `/api/v2/doctype/<doctype>/meta`，仅 `only_for("All")`；自定义接口须另行检查业务 DocType read 权限。客户/物料 v1 路径为 `/api/resource/Customer`、`/api/resource/Item`，调用 [frappe.client.get_list](https://github.com/frappe/frappe/blob/v15.118.0/frappe/client.py) 受权限约束。本项目通过普通用户验证原生记录读取，并新增带业务权限检查的薄 schema API，字段均从实际 Site 发现。

## 当前限制

已在用户授权下使用项目密钥完成一次 DeepSeek 官方真实最小调用。独立 ERP Site 的普通用户读取/拒绝、字段/单据权限和 DSH→MCP→ERP 工具链已验证（模型为本地替身）；多租户业务尚未实施。后续状态见 [DSH 证据](dsh-validation-evidence.md) 和 [ERP 证据](erpnext-integration-evidence.md)。

## 本轮依赖与环境补充

官方 MCP Python SDK 固定 `1.26.0`，httpx `0.28.1`；`pydantic-settings` 从自动解析的 2.15.0 固定到该 MCP tag 上游锁文件中的 2.10.1，解决 lifespan 前向引用警告；未修改第三方源码。重新锁定并验证共 36 个 Python 包。Docker 测试栈固定 MariaDB 10.6.28、Redis 6.2.24；Compose 5.0.2。
