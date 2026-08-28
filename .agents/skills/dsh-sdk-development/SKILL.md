---
name: dsh-sdk-development
description: Use when developing dsherp DSH SDK sessions, runtime composition, model protocol configuration, MCP tools, or process shutdown behavior.
---

# dsherp DSH SDK 开发

这是开发参考，不是业务运行时 skill，不挂载给业务 Agent。

## 阅读顺序

1. [项目约定](../../../AGENTS.md) 和 [版本基线](../../../docs/engineering/runtime-baseline.md)。
2. 按问题读取 [最小验证器](../../../dsherp/dsh_probe.py)、[零工具组合](../../../config/dsh-no-tools.yml) 或 [ERP 组合](../../../config/dsh-erp.yml)。
3. 运行前核对 [DSH 证据](../../../docs/engineering/dsh-validation-evidence.md)，涉及 ERP 时再读 [ERP 证据](../../../docs/engineering/erpnext-integration-evidence.md)。

## 固定契约

SDK/Runtime 均为 `0.1.1rc1`，不是 master。导入 `DeepSeekHarness`，使用 `cordis`、`session_root`；这个版本没有 master 的 `profile` / `dsh_home` 构造契约。以 `requirements.lock` 和已安装签名为准。

Python SDK 只管理子进程与 stdio JSON-RPC；Cordis 组合决定模型适配器和工具。使用 `try/finally: harness.close()` 覆盖初始化、事件回调和运行错误。`RunResult.events` 仅根事件，`notifications` 可含后代；截断不能算成功。

ERP 工具走官方 MCP bridge → Python MCP server → Frappe HTTP，不存在本项目自造的 SDK Python 回调注册。新增工具前先写真实协议测试；本阶段只有 `erp_read_schema`、`erp_read_record`。

## 模型协议与身份

DSH 不绑定 DeepSeek 模型。当前已测组合使用 `deepseek-official`；自定义协议网关应核对固定版本 `llm-pi-ai` 的 `api/baseURL/apiKeyEnv/models`，不要只替换 DeepSeek 专用适配器 URL 并宣称兼容。当前未实际验证 pi-ai 组合。

SDK 的 `env` 会继承父环境，独立目录不是 OS 租户隔离。ERP MCP 只拿普通用户配置路径；不得传开通密钥、管理员密码、任意 URL/方法/用户选择工具。凭证必须显式提供，不扫描其他项目。

## 验证

无 ERP 环境：`.venv/bin/python -m pytest tests/test_dsh_probe.py tests/test_erp_mcp_config.py -q`。

已有隔离 Site：`.venv/bin/python -m pytest tests/integration/test_dsh_erp_chain.py -q`。此测试使用真实 Runtime/MCP/ERP，但模型是本地 SSE 替身；它不替代付费模型验证。

官方固定源码：[SDK](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/python/sdk/README.md)、[MCP bridge](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/mcp/mcp-client/README.md)、[协议适配器](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/llm/llm-pi-ai/README.md)。
