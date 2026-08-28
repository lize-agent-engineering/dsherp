# 隔离 ERPNext 只读验证实施计划

当前任务使用 executing-plans 顺序执行，不委派、不增加门禁。设计依据：[首版设计](../specs/2026-08-28-dsherp-design.md)。

## 授权与边界

用户已授权本机独立测试栈，最多 4 GiB RAM、2 CPU、10 GiB 新数据。复用镜像层，不复用任何既有站点、卷、容器或业务凭证。只写合成基础资料与专用权限配置，不提交库存/账务单据。

镜像实际为 ERPNext 15.119.3 + Frappe 15.118.0 + Python 3.11.6；更新原候选 Frappe 15.119.1，以实物为准，满足 ERPNext 的版本约束。镜像不含 Git 元数据，不能声称已核实其中每个源文件的上游提交。

## 1. 隔离环境

- [x] `infra/compose.validation.yml`：仅 db、redis、backend，固定已有 digest，禁止自动 pull；内存总上限 3712 MiB、CPU 总上限 2；仅 HTTP 18081 暴露 loopback。
- [x] `.runtime/control/` 存放新生成开通密钥，权限 0700/0600；开通时临时挂载，常驻 backend 不携带 DB root 或管理员密码。
- [x] 通过官方 `bench new-site` 创建 `dsherp-validation.localhost`，安装 ERPNext；只配置独立 db/redis。
- [x] 回读版本、容器限制、卷归属和实际磁盘用量。10 GiB 为监测预算，不伪称 Docker named volume 有硬配额。

## 2. 先测试再实现只读接口

新增 `frappe_app/dsherp_bridge/api.py`，使用 Frappe 自定义 App 扩展，不改核心。仅开放 Customer/Item 的 schema 和有权限的记录读取；从 `frappe.session.user` 获取身份，拒绝 Guest/Administrator，不接受用户或站点参数。schema 先查 DocType read 权限，再按字段 permlevel 过滤；记录用 Frappe 原生权限方法。

- [x] 先创建专用只读角色、普通 reader、denied 用户及从真实 metadata 得出的合成 Customer/Item。控制面使用 ORM 文档方法，不写 SQL。
- [x] `tests/integration/test_erp_read.py` 通过真实 HTTP 断言 reader 读取真实标记、denied 403、禁止 DocType 403、未登录拒绝。先运行目标接口不存在的失败，再实现接口并重跑。
- [x] 服务端字段权限通过额外 permlevel 字段测试；不把管理端元数据结果当成普通用户 schema 验证。

## 3. 最短 MCP 调用

`dsherp/erp_mcp.py` 使用官方 Python MCP SDK 注册明确的只读工具；服务端配置固定 URL、site 和普通用户 token，不接受模型提供地址或身份。`config/dsh-erp.yml` 复用零工具组合，加官方 MCP bridge；启动失败报错、关闭重连，不开放 Shell/SQL/任意 HTTP。

- [x] 先写 MCP discovery/call 测试，观察失败；再实现并通过真实 ERP API 返回合成记录。
- [x] 用真实 DSH Runtime + 本地模型协议替身强制一次 ERP 工具调用，核对工具列表、返回值和进程关闭。不把它报告为新增真实模型调用。
- [x] 记录此前官方真实 DSH 调用与本轮真实 ERP 工具链是独立证据；未经额外预算不扩大付费模型调用。

## 4. 交付

- [x] 更新分层证据、README、准确版本，编写两个薄开发 skills 与后续身份授权 TDD 计划。
- [x] 分类提交本地 main，不推送；清除 work/ 临时下载与运行产物，保留必要本地配置及合成站点。

实际结果：36 项测试通过，真实 Site 只读权限、MCP 和 Runtime 工具链均通过。没有新增付费模型调用或库存/账务提交。源码 skills 仅作固定契约参考；未委派 Agent 做压力测试。环境中间问题与资源实测见 ERP 证据。
