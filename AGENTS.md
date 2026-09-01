# dsherp 开发约定

## 开工入口

- 使用中文回答。先读 README.md、首版设计和当前阶段计划。
- 本仓库是独立项目；不修改现有 AgenERP，不自动复制其实现或业务数据。
- 按功能分类提交 Git；不自动推送远端。不提交密钥、租户数据、运行日志和本地运行状态。
- 开发资料、单元测试通过、真实 DSH 调用、真实 ERPNext 运行、UI 验证和部署分别报告，不能相互替代。
- 报告中的"已完成/已勾选/已更新"类状态声明，发出前必须对仓库文件和站点实际状态核验；工作副本或台账的状态不能替代仓库事实。

## 实现原则

- 使用 TDD：先写行为测试并确认失败，再实现最小代码，再重跑相关测试。不要默认添加门禁。
- 遵循 fastfail；配置缺失、权限不足、业务校验失败时明确报错，不静默降级或伪造结果。
- 优先复用框架原生组件和业务方法；前端使用 React、Ant Design，保持一致风格。
- 不 fork 或修改 DSH、Frappe、ERPNext 核心；优先使用 SDK、公开扩展点和自定义 App。
- 删除被当前实现取代的旧代码、组件和测试；保留仍有效的回归测试。临时产出使用 work/，无后续用途时清理。
- 不预建迁移平台、连接器市场、通用工作流引擎或其他首版无直接价值的系统。

## DSH 使用

- 从固定版本的官方文档、类型、源码和示例确认 SDK 方法、事件、工具扩展及启动方式，禁止凭记忆编造 API。
- Python SDK 驱动独立 Harness Runtime；区分应用服务、SDK 客户端和运行进程的职责。
- 首次技术验证后记录 SDK、Runtime、Node 和 Python 的准确版本或提交。不得声明尚未执行的兼容性验证。
- 业务 Runtime 仅开放必要 ERP 工具；不得默认开放 Shell、任意代码执行、任意 HTTP 或数据库访问。

## ERP 与租户

- ERPNext 是业务事实来源；从实际站点发现 DocType、字段、关联和可选值，不猜字段。
- 业务变更走 ERPNext 业务方法；不能通过修改状态字段或直接 SQL 代替提交、取消等动作。
- 租户和用户来自服务端已验证的身份，不能由模型自由指定。业务请求不使用共享管理员凭证。
- Site、数据库、文件、会话、记忆、缓存及凭证均按租户隔离。开通站点的控制面凭证不得进入业务 Agent。
- 服务端检查权限和确认凭据；提示词和 skills 不是安全边界。
- 确认绑定操作内容、单据版本、企业和操作者。内容变化后重新确认，执行时复查权限。
- 写入必须考虑幂等性；响应不明时先核实结果，不盲目重试。部分成功要如实记录，不承诺跨单据自动回滚。
- 所有业务验证先在隔离测试站点使用合成数据；真实生产变更需明确授权。

## 官方资料入口

- Frappe/ERPNext 官方主站与版本动态：https://frappe.io/ （发布页、The Frappe Times、发布说明）
- DSH SDK：https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/sdk
- DSH Python SDK：https://github.com/deepseek-ai/deepseek-harness/tree/master/python/sdk
- DSH 架构：https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md
- Frappe REST API：https://docs.frappe.io/framework/user/en/api/rest
- Frappe Site：https://docs.frappe.io/framework/user/en/tutorial/create-a-site
- ERPNext 制造：https://docs.frappe.io/erpnext/work-order
- ERPNext 委外：https://docs.frappe.io/erpnext/subcontracting

这些链接用于发现资料；实现时必须与所选版本对应。开发 skills 在版本核验后建立，不把仓库贡献规范直接当成下游项目规范。
