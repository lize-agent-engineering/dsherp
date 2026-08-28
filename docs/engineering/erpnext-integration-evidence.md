# ERPNext 隔离验证证据

日期：2026-08-28。用户授权资源占用后完成独立站点与只读工具链验证；不是完整业务系统或生产部署。

后续阶段 1 已补齐 Desk 服务，最新状态见 [原生 Desk 证据](native-desk-evidence.md)。下文保留首次 API 验证时的历史环境，不作为当前服务清单。

## 环境与版本（实际运行）

| 项目 | 核验值 |
| --- | --- |
| Compose project | `dsherp-validation` |
| Site | `dsherp-validation.localhost` |
| HTTP | `http://127.0.0.1:18081`，请求携带固定 `X-Frappe-Site-Name` |
| ERPNext / Frappe | `15.119.3` / `15.118.0` |
| 自定义 App | `dsherp_bridge 0.1.0` |
| 容器 Python | `3.11.6` |
| MariaDB / Redis | `10.6.28` / `6.2.24` |
| 主机 MCP | Python `3.12.11`、`mcp==1.26.0`、`httpx==0.28.1` |

镜像未保留 Git 目录，`bench list-apps` 显示 UNVERSIONED；不虚构其源码提交。固定制品：

- ERPNext：`frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341`
- MariaDB：`mariadb@sha256:92e50059ea0a5965a33ef751970eab37d421b91ebbd01ac909039cffe159e574`
- Redis：`redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015`

配置见 [compose.validation.yml](../../infra/compose.validation.yml)。只使用已存在镜像，没有下载大镜像；没有进入、修改或停止 AgenERP 或其他服务。

## 隔离与资源

- 三个容器内存硬上限依次为 backend 2560 MiB、db 1024 MiB、redis 128 MiB，总计 **3712 MiB**；禁用容器额外 swap。CPU 上限 1.3 + 0.6 + 0.1 = **2**。
- 采样实际内存约 backend 120 MiB、db 216 MiB、redis 9 MiB；数据目录约 318 MiB，低于授权 10 GiB。磁盘为观察预算，不是假定存在的 volume 硬配额。
- 所有持久卷名均为 `dsherp-validation_*`：sites、logs、db-data、redis-data。未挂载任何既有站点卷。App 源码仅只读挂载本仓库 `frappe_app/`。
- DB/Redis 在内部专用网络，无宿主端口。backend 另连接专用 API 网络，仅发布 `127.0.0.1:18081`。Docker Desktop 的纯 internal 网络未实际发布端口，故采用两个网络；不声称 backend 出站网络被完全封禁。
- 开通时临时挂载本项目控制面密钥；常驻 backend 与 MCP 不持有 DB root/admin 密码。MCP 仅收到普通 reader 或 denied 的专用配置文件路径。
- 只启用 ERP HTTP/DB/Redis；无 worker、scheduler、WebSocket、静态资源代理或工作台。站点未完成企业 setup wizard，不能当成可运营企业账套。

## 合成资料与凭证

先通过本 Site metadata 查询字段、Select 值和 Link 对象，发现新站点尚无 UOM/分组。随后通过 Frappe 文档 `insert/save` 创建专用 UOM、Item Group、Customer Group、Territory、两个 Customer 和一个非库存 Item；未直接写 SQL。

- 主记录：`DSHERP-TEST-CUSTOMER`、`DSHERP-TEST-ITEM`。
- 权限反例：`DSHERP-TEST-OTHER-CUSTOMER`。
- 普通用户：`dsherp-reader@example.invalid`，业务读取权限来自自定义只读角色；User Permission 将 Customer 范围限制到主记录。
- 拒绝用户：`dsherp-denied@example.invalid`，没有 Customer/Item read 权限。
- 字段反例：Customer 的合成 `custom_dsherp_restricted`，permlevel=1；普通 reader 仅具有 permlevel=0。
- 两个用户均禁止发送欢迎邮件。控制面使用 Administrator 创建合成资料和权限；所有业务读取测试换用普通 token。
- 本地 `.runtime/erp-reader.json`、`.runtime/erp-denied.json` 为 MCP 专用配置，`.runtime/erp-users.json` 仅供 HTTP 测试；控制面密钥在 `.runtime/control/`。文件权限 0600、控制面目录 0700，全部忽略提交，不在文档记录值。

## TDD 与真实接口

首次 HTTP 回归：10 项失败（项目方法未实现返回 417），原生 ERP 读取/拒绝测试通过。实现薄 App 后 11 项通过；随后增加实际 User Permission 反例。

| 接口/检查 | 证据 |
| --- | --- |
| `GET /api/method/dsherp_bridge.api.read_schema` | 普通用户获得真实 Customer/Item 字段；过滤 permlevel=1 字段 |
| `GET /api/method/dsherp_bridge.api.read_record` | 回读合成记录；先 DocType read，再单据权限与字段权限 |
| denied / Guest | 两个项目接口均返回 403；传入 `user=Administrator` 不改变身份 |
| User 等范围外 DocType | 普通 reader 返回 403 |
| 普通用户访问第二个 Customer | User Permission 拒绝，返回 403 |
| 原生 `/api/resource/Item/DSHERP-TEST-ITEM` | reader 200，denied 403 |
| MCP 启动身份错配 | `get_logged_user` 与预期普通用户不同即失败 |

自定义 API 拒绝 Administrator 与 Guest；普通用户来自 `frappe.session.user`，不存在模型提供身份的参数。Frappe 原生 v2 meta 只要求登录，故没有直接把它暴露为受限工具。这不是对所有原生 Frappe 入口的权限改造。

## DSH → MCP → 真实 ERP 链

1. Python DSH SDK `0.1.1rc1` 启动真实 Runtime。
2. [dsh-erp.yml](../../config/dsh-erp.yml) 使用官方 `cordis:include` 复用零工具组合，再挂载官方 `dsh-mcp-client`。
3. bridge 通过 stdio 启动 Python MCP SDK `1.26.0` 的 [erp_mcp.py](../../dsherp/erp_mcp.py)。
4. 本地模型协议替身发出明确 tool call；MCP 使用普通 token 调用真实 Frappe API。
5. 第二次模型请求的 tool message 包含真实 `DSHERP-TEST-ITEM` 的 `item_code`；模型可见工具恰好只有 `mcp__erp__erp_read_schema` 和 `mcp__erp__erp_read_record`。
6. Runtime 关闭后，其 MCP 子进程也已退出。工具错误以 MCP `isError` 返回；未授权和范围外 DocType 均拒绝。

MCP readonly annotations 仅为工具元数据，实际边界仍是服务端权限。HTTP client 禁止自动重定向、不继承代理环境、不自动重试；业务请求不会携带控制面凭证。

**模型侧在本链测试中是替身，不是新增真实模型调用。** 此前 DeepSeek 官方最小调用成功的证据见 [DSH 验证记录](dsh-validation-evidence.md)；两类证据不相互替代。本轮没有额外付费模型调用。

后续用户单独授权后，已完成真实 DeepSeek 官方模型→DSH→MCP→本 Site 的只读闭环：实际调用一次 `erp_read_record`，合成物料的四个回答字段与工具结果及独立 ERP 回读一致，耗时 4.09 秒。详情见 [真实官方模型闭环记录](dsh-validation-evidence.md#后续真实官方模型与-erp-只读闭环)。前述替身测试继续保留作为不付费的回归，不替代此次真实验证。

## 制造方法核对（未执行业务）

已从安装镜像导入并读取签名：

- `erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry(work_order_id, purpose, qty=None, target_warehouse=None, source_stock_entry=None)`。
- `erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order(source_name, target_doc=None, save=False, submit=False, notify=False)`。
- `erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order.make_subcontracting_receipt(source_name, target_doc=None)`。

这些是实际可导入方法，不是已经跑通的制造链。回读 Stock Entry、Purchase Receipt、Purchase Invoice、Sales Invoice、Subcontracting Receipt 的已提交数量均为 **0**。

## 复验与运行方式

已初始化的本地环境：

```sh
docker compose -f infra/compose.validation.yml up -d
.venv/bin/python -m pytest tests -q
```

最终回归为 **36 项通过（24.67 秒）**，其中 18 项为无 ERP 环境的 SDK/配置测试，18 项需要真实隔离 Site；没有 skip。对空卷运行 `up` 不会自动建站或生成资料，不宣称已经实现平台开通服务。

首次开通执行了官方 `bench set-config`、`bench new-site ... --install-app erpnext --set-default`、`disable-scheduler`；App 经独立 PYTHONPATH、站点 apps.txt 和 `bench install-app dsherp_bridge` 安装。该方法是本机验证用扩展挂载，不是可分发生产镜像。新机器需要先按本计划重新进行控制面建站、metadata 发现与合成资料初始化，不能复制现有业务卷。

如不再需要运行，可执行 `docker compose -f infra/compose.validation.yml stop`，只停止本测试项目并保留数据；本轮未停止它。删除卷是另一项明确操作，不作为默认清理。

## 尚未覆盖

UI、生产发布、真实模型自主选择 ERP 工具、多企业 SaaS、Runtime 容器级租户隔离、企业开通流程、制造库存/账务链都未完成。开发 skills 只核验了固定 API、路径及对应运行测试，没有宣称经过独立 Agent 压力测试。

官方依据：[Frappe v15.118.0 API](https://github.com/frappe/frappe/blob/v15.118.0/frappe/api/v2.py)、[字段权限](https://github.com/frappe/frappe/blob/v15.118.0/frappe/model/meta.py)、[MCP Python v1.26.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0)、[DSH 固定 bridge](https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/mcp/mcp-client/README.md)。
