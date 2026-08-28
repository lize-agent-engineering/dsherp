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

实测 ERPNext `15.119.3`、Frappe `15.118.0`、容器 Python `3.11.6`；主机 SDK 环境是 Python `3.12.11`。以 Compose 中 digest 固定镜像，不将标签或候选版本当作镜像实际版本。

只操作 `dsherp-validation` 项目和合成站点。不复制 AgenERP 的卷、数据或凭证。当前只有 API/DB/Redis，没有前端、worker、scheduler，也没有完整企业建账。

## API 契约

`GET /api/method/dsherp_bridge.api.read_schema?doctype=Customer`：先检查当前会话用户和 DocType read，再用 `get_permitted_fieldnames` 过滤字段；原生 v2 meta 仅要求登录，不能直接作为受限 schema 工具。

`GET /api/method/dsherp_bridge.api.read_record?doctype=Item&name=...`：调用 `doc.check_permission('read')` 和 `apply_fieldlevel_read_permissions()`。模型不能指定企业、用户、凭证或 URL。只允许 Customer/Item，禁止 Guest/Administrator。角色权限、User Permission、字段 permlevel 都需要真实测试。

新增字段参数前，从目标 Site 的 metadata 和 Link 记录发现，不按文档猜值。测试采用合成资料；开通/种子控制面可以用管理员，但业务读取必须换普通用户。

## 制造业务

自制 `work_order.make_stock_entry` 返回草稿字典；委外 `purchase_order.make_subcontracting_order` 与 `subcontracting_order.make_subcontracting_receipt` 走对应业务方法。已核对安装版本的签名，尚未执行制造流程。不能改 docstatus 或 SQL 冒充提交；返回草稿不等于保存/提交，尤其委外 factory 内部会捕获部分提交异常。

## 验证与报告

`.venv/bin/python -m pytest tests/integration -q` 需要已配置独立 Site 和本地测试凭证；缺失就报错，不跳过后称全部通过。

分别报告文档、单元测试、真实 ERP HTTP、真实 DSH 工具链、真实模型和 UI/部署。自动化 DSH→ERP 链使用本地模型替身；官方模型最小调用及后续真实模型→ERP 只读闭环另有证据，见 DSH 验证记录，不混称付费自动化测试。

固定来源：[Frappe v2](https://github.com/frappe/frappe/blob/v15.118.0/frappe/api/v2.py)、[Frappe 字段权限](https://github.com/frappe/frappe/blob/v15.118.0/frappe/model/meta.py)、[ERPNext 工单](https://github.com/frappe/erpnext/blob/v15.119.3/erpnext/manufacturing/doctype/work_order/work_order.py)。
