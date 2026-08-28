# dsherp

基于 DeepSeek Harness SDK 与 ERPNext 的 Agent 驱动制造业 ERP SaaS。

## 当前状态

项目初始化阶段：已记录产品范围、架构与验证计划。尚无业务代码、依赖安装、ERPNext 站点、DSH 运行实例或线上服务。文档中的能力是设计目标，不是已实现功能。

## 产品方向

- 多家企业注册使用；每个客户企业对应独立 ERPNext Site。
- 服务从零建立 ERP 数据的制造企业，支持自制、委外和混合生产。
- Agent 引导建立基础资料，并推进销售、采购、生产、委外、入库和交付。
- 查询按权限执行；基础资料和草稿按授权创建；正式提交、取消和影响库存或账务的操作需要有权限的人确认。
- 首版不做外部 ERP 接入、历史迁移、自动排产优化和银行支付接入。

## 文档

- [首版设计](docs/superpowers/specs/2026-08-28-dsherp-design.md)
- [首次技术验证计划](docs/superpowers/plans/2026-08-28-foundation-validation.md)
- [项目开发约定](AGENTS.md)

## 技术方向

React + Ant Design 工作台；Python 应用服务通过 DSH SDK 驱动独立 Runtime；Frappe 自定义 App 封装业务操作；ERPNext 承载业务规则和单据。版本需经过首次验证后固定，不把上游 master 当作稳定依赖。
