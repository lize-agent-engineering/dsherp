---
name: erpnext-integration
description: Use when developing dsherp ERPNext or Frappe integration, schema discovery, ordinary-user permissions, synthetic site validation, or manufacturing document operations.
---

# dsherp ERPNext 集成开发

仅用于开发，不是业务 Agent 的权限来源。

## 按需阅读

先读 [AGENTS](../../../AGENTS.md)、[真实 ERP 证据](../../../docs/engineering/erpnext-integration-evidence.md)。

- 站点与资源：[独立 Compose](../../../infra/compose.validation.yml)。
- 读取和权限：[自定义 App](../../../frappe_app/dsherp_bridge/api.py)、[真实 HTTP 测试](../../../tests/integration/test_erp_read.py)。
- MCP 边界：[受限工具](../../../dsherp/erp_mcp.py)、[真实工具链测试](../../../tests/integration/test_dsh_erp_chain.py)。

## 版本与边界

当前隔离合成环境实测 ERPNext `16.33.0`、Frappe `16.31.0`、容器 Python `3.14.7`；主机 SDK 环境仍是 Python `3.12.11`。Compose 固定镜像为 `frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd`，不将标签、滚动主线文档或候选版本当作镜像实际版本。C4 冷静期/最终独立审计与 C5 尚未完成，不得写成迁移整体完成或可上线。

v15 时期历史基线为 ERPNext `15.119.3`、Frappe `15.118.0`、容器 Python `3.11.6`；只用于解释旧证据，不作为当前实现依据。DSH SDK/Runtime `0.1.1rc1`、两个 wheel SHA256、Runtime 内置 Node `v24.19.0`、Cordis 组合和 MCP bridge 未随 ERP 版本切换改变。

只操作 `dsherp-validation` 项目和合成站点。不复制 AgenERP 的卷、数据或凭证。当前 v16 本地端口为 alpha API `18081`、alpha Desk `18082`、platform `18083`、beta 隔离预览 `18085`、daily `18086`；Desk 路由统一使用 `/desk/...`。四站 fresh provision、普通用户认证和浏览器矩阵已有独立证据，但不等于完整企业业务建账或生产部署。见 [v16 迁移证据](../../../docs/engineering/v16-migration-evidence.md)；原 v15 行为见 [原生 Desk 历史证据](../../../docs/engineering/native-desk-evidence.md)。

## API 契约

`GET /api/method/dsherp_bridge.api.read_schema?doctype=Customer`：先检查当前会话用户和 DocType read，再用 `get_permitted_fieldnames` 过滤字段；Frappe 16.31.0 的原生 `/api/v2/doctype/<doctype>/meta` 仍只调用 `only_for("All")`，即仅要求已登录身份，不能直接作为受限 schema 工具。

`GET /api/method/dsherp_bridge.api.read_record?doctype=Item&name=...`：调用 `doc.check_permission('read')` 和 `apply_fieldlevel_read_permissions()`。模型不能指定企业、用户、凭证或 URL。只允许 Customer/Item，禁止 Guest/Administrator。角色权限、User Permission、字段 permlevel 都需要真实测试。

新增字段参数前，从目标 Site 的 metadata 和 Link 记录发现，不按文档猜值。测试采用合成资料；开通/种子控制面可以用管理员，但业务读取必须换普通用户。

## 制造业务

当前七条受信 route 使用 ERPNext `v16.33.0` 固定源码的 `make_delivery_note`、两条不同 purpose 的 `make_stock_entry`、`make_purchase_receipt`、`make_subcontracting_order`、`make_rm_stock_entry` 和 `make_subcontracting_receipt`。签名及固定 permalink 见[运行基线](../../../docs/engineering/runtime-baseline.md)；v16 alpha/daily 隔离合成制造行为已重验。不能改 docstatus 或 SQL 冒充提交；mapper 返回草稿不等于保存/提交，尤其委外 factory 内部会捕获部分提交异常。本地合成验证不代表生产制造可用。

## 验证与报告

`.venv/bin/python -m pytest tests/integration -q` 需要已配置独立 Site 和本地测试凭证；缺失就报错，不跳过后称全部通过。

分别报告文档、单元测试、真实 ERP HTTP、真实 DSH 工具链、真实模型和 UI/部署。自动化 DSH→ERP 链使用本地模型替身；官方模型最小调用及后续真实模型→ERP 只读闭环另有证据，见 DSH 验证记录，不混称付费自动化测试。

固定来源：[Frappe v2](https://github.com/frappe/frappe/blob/v16.31.0/frappe/api/v2.py#L216-L218)、[Frappe 字段权限](https://github.com/frappe/frappe/blob/v16.31.0/frappe/model/meta.py)、[ERPNext 工单](https://github.com/frappe/erpnext/blob/v16.33.0/erpnext/manufacturing/doctype/work_order/work_order.py#L2664-L2722)。
