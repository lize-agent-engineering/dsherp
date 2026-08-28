# dsherp

基于 DeepSeek Harness SDK 与 ERPNext 的 Agent 驱动制造业 ERP SaaS。

## 当前状态

首次技术验证完成：固定 DSH SDK/Runtime `0.1.1rc1`；DeepSeek 官方最小调用及真实模型→DSH→MCP→ERPNext 只读闭环均已通过。独立 ERPNext `15.119.3` / Frappe `15.118.0` 站点已运行，普通用户读取、权限拒绝已验证。41 项自动化测试通过（其中工具链测试使用本地模型替身，付费真实闭环单独验证）。已完成本地原生初始化，验证中文 Workspace、物料录入搜索和管理员角色页；明细输入及普通角色 UI 尚未完全验收，亦无 SaaS 身份系统、制造业务链或生产部署。

当前正执行新计划阶段 1；[原生 Desk 证据与剩余事项](docs/engineering/native-desk-evidence.md)。本地入口：[原生登录页](http://127.0.0.1:18082/login)。

## 产品方向

- 多家企业注册使用；每个客户企业对应独立 ERPNext Site。
- 服务从零建立 ERP 数据的制造企业，支持自制、委外和混合生产。
- Agent 生成包含数据、页面、规则、权限和测试的业务应用，经隔离预览与管理员确认发布。
- Agent 引导建立基础资料，并推进销售、采购、生产、委外、入库和交付。
- 查询按权限执行；基础资料和草稿按授权创建；正式提交、取消和影响库存或账务的操作需要有权限的人确认。
- 首版不做外部 ERP 接入、历史迁移、自动排产优化和银行支付接入。

## 文档

- [首版设计](docs/superpowers/specs/2026-08-28-dsherp-design.md)
- [首次技术验证计划](docs/superpowers/plans/2026-08-28-foundation-validation.md)
- [项目开发约定](AGENTS.md)
- [准确版本与运行契约](docs/engineering/runtime-baseline.md)
- [DSH 分层验证证据](docs/engineering/dsh-validation-evidence.md)
- [ERPNext 真实验证证据](docs/engineering/erpnext-integration-evidence.md)
- [当前前端优先与应用生成计划](docs/superpowers/plans/2026-08-28-frontend-agent-app-generation.md)
- [阶段 3 身份绑定参考](docs/superpowers/plans/2026-08-28-identity-bound-read-execution.md)

## 最小验证

已在 macOS arm64、Python 3.12.11 验证以下命令；未声明其他平台兼容：

```sh
uv venv --python 3.12.11 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
.venv/bin/python -m pytest tests/test_dsh_probe.py tests/test_erp_mcp_config.py -q
```

真实模型调用需事先授权费用，并通过进程环境提供 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`，随后运行 `.venv/bin/python -m dsherp.dsh_probe`。脚本不自动加载 `.env`，不读取其他项目凭证；只执行固定合成提示，不访问 ERP。会话临时目录执行后清理，只输出脱敏摘要。

## 隔离 ERP 验证

已初始化环境的 API 仅监听 `127.0.0.1:18081`，Site 为 `dsherp-validation.localhost`。

```sh
docker compose -f infra/compose.validation.yml up -d
.venv/bin/python -m pytest tests/integration -q
```

集成测试需本地 `.runtime/` 普通测试用户配置；缺失会明确失败，不自动跳过。此命令不自动建站或生成资料，首次开通记录和资源边界见 ERP 证据。

开发参考 skills 位于 `.agents/skills/dsh-sdk-development/` 和 `.agents/skills/erpnext-integration/`；不作为业务运行时 skills。

## 技术方向

原生 Frappe Desk 承载业务页面与基础管理；自定义 App 通过扩展入口加载 React + Ant Design 的 Agent、构建及发布页面；Python 应用服务通过 DSH SDK 驱动独立 Runtime；Frappe 自定义 App 封装业务操作；ERPNext 承载业务规则和单据。版本需经过首次验证后固定，不把上游 master 当作稳定依赖。
