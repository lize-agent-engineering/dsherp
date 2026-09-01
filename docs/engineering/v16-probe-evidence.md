# ERPNext / Frappe v16 迁移探针证据

日期：2026-09-01。范围：仅 `dsherp` 本地合成环境与独立 throwaway probe；不代表生产部署。

## T0 目标基线

### 官方发布与镜像

| 项目 | 执行时核验结果 | 依据 | 判定 |
| --- | --- | --- | --- |
| ERPNext 最新 v16 release | `v16.33.0`，发布于 `2026-08-25T17:04:34Z` | `https://api.github.com/repos/frappe/erpnext/releases` 与 `https://github.com/frappe/erpnext/releases/tag/v16.33.0` | 目标 ERPNext 版本 |
| Frappe 最新 v16 release | `v16.32.0`，发布于 `2026-08-26T05:43:47Z` | `https://api.github.com/repos/frappe/frappe/releases` 与 `https://github.com/frappe/frappe/releases/tag/v16.32.0` | 仅作为发布动态；实际配套版本以镜像内结果为准 |
| ERPNext 镜像 | `frappe/erpnext:v16.33.0`，manifest / RepoDigest `sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd` | Docker Hub tag API、`docker pull`、`docker image inspect` 三方一致 | 固定此 RepoDigest |
| 本机镜像架构 | `linux/arm64`；本地 image ID `sha256:a4fd94c1de264ebaa83713ac663ef1ab7252809f5f8dee84b41065067c53f1e9` | `docker image inspect` | 通过 |
| 镜像内实际应用 | ERPNext `16.33.0`、Frappe `16.31.0` | `docker run --rm --entrypoint bench ... version` | 后续源码与运行契约以这一实际配套为准，不用 Frappe 最新 release 代替 |
| 镜像内运行时 | Python `3.14.7`、Node `v24.19.0` | 容器内 `python3 -V` / `node -v` | 满足 v16 的 Python 3.14 / Node 24 要求 |

`docker pull frappe/erpnext:v16.33.0` fresh 输出的 digest 与 Docker Hub manifest digest 均为 `sha256:493cec…f1bd`；不是用标签推断实际内容。

### 数据库与 Redis

Frappe v16 当前官方安装页要求 MariaDB `11.8`、Redis / Valkey `6+`；官方 `frappe_docker` 的 `overrides/compose.mariadb.yaml` 同样使用 `mariadb:11.8`。因此现有 MariaDB 10.6 不作为 v16 新站基线，T2.1 必须同时翻转数据库 digest。

本机已拉取并核验：

- MariaDB `11.8.9` arm64，RepoDigest `mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3`。
- 现有 Redis `6.2.24` arm64，RepoDigest `redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015`；满足 `6+`，本迁移不升级 Redis。

### v15 历史运行基线

切换前实际容器重新核验：ERPNext `15.119.3`、Frappe `15.118.0`、Python `3.11.6`。v15 compose 仍在 `dsherp-validation` 项目运行，C1 使用不同 project、端口和卷，不触碰现有 v15 站点。

## 可重跑命令

```sh
curl -fsSL 'https://api.github.com/repos/frappe/erpnext/releases?per_page=100' | jq '[.[] | select(.tag_name | startswith("v16."))][0] | {tag_name,published_at,html_url}'
curl -fsSL 'https://api.github.com/repos/frappe/frappe/releases?per_page=100' | jq '[.[] | select(.tag_name | startswith("v16."))][0] | {tag_name,published_at,html_url}'
docker pull frappe/erpnext:v16.33.0
docker image inspect frappe/erpnext:v16.33.0 --format '{{json .RepoDigests}} {{.Architecture}} {{.Id}}'
docker run --rm --entrypoint bench frappe/erpnext:v16.33.0 version
docker run --rm --entrypoint python3 frappe/erpnext:v16.33.0 -V
docker run --rm --entrypoint node frappe/erpnext:v16.33.0 -v
docker pull mariadb:11.8
docker image inspect mariadb:11.8 --format '{{json .RepoDigests}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}}'
docker run --rm redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015 redis-server --version
docker compose -p dsherp-validation -f infra/compose.validation.yml exec -T backend bench version
docker compose -p dsherp-validation -f infra/compose.validation.yml exec -T backend python3 -V
```

## C1 探针结论表

| 行 ID | 风险 | 脚本 / 命令 | 预期 | 实测 | 判定 | 阶段 2 关联任务 |
| --- | --- | --- | --- | --- | --- | --- |
| C1-R13 | bind-mount 装 app | `t1_1_bind_mount.py` | 两 app 可安装，hooks 与 boot 生效 | fresh Site 安装四 app；原生 boot dispatch 与真实 doc_events 均执行 | **绿** | T2.1 保留 bind-mount 方案 |
| C1-R14 | agent-runtime venv | `t1_2_agent_runtime.sh` | Python 3.14 下锁文件可安装 | `--require-hashes` 安装成功；`deepseek_harness`、`mcp` import 成功 | **绿** | T2.9 不触发，不改锁文件 |
| C1-R1 | commit 语义 | `t1_3_commit.py`、`t1_3_configuration_chain.py` | hook 内 no-op 可观测；业务链无 warning | doc_events 内 commit warning+no-op；真实配置 `_confirm` 在 warning→error 下成功；SSO 在 commit 前因 R3 新红项停止 | **混合：框架与配置绿，SSO 红** | T2.12；operations 在 T2.13 后连同制造 fixture 复跑 |
| C1-R2-R3 | Desk / SSO | `t1_4_sso_provision.py`、`t1_4_sso_http.py` | `/desk` 与 OAuth 全链成立 | `/app/home` 301→`/desk/home`；授权码和 Bearer Token 成功签发；原生 callback 在 `login_as` 因 `tabUser` 记录变化/savepoint 失败；OAuth Client 默认角色另有不兼容 | **红** | T2.2、T2.5、T2.11、T2.12 |
| C1-R6 | mapper / 委外 | `t1_framework_contracts.py` | 七条 route 与委外内部方法可用 | 七个 mapper 可 import、签名保持；首个必要委外调用 fastfail：`create_raw_materials_supplied` 已移除，替代方法带 `raw_material_table` 参数 | **红** | T2.13；修复后必须跑 7 route + 10 类派生字段 + autoname fixture |
| C1-R4 | Page / sidebar | `t1_http_contracts.py` + in-app Browser | Page、全局资源、Workspace 可加载 | 4 Page transport、6 个 app 资源、全局 JS/CSS 通过；正式工作台渲染且隐藏全局 Agent，Item 页显示 Agent；Workspace Sidebar 同时存在；fresh setup 后无侧栏冲突异常 | **绿** | T2.4 仅切 `/desk`、重建 dist；不另造侧栏系统 |
| C1-R15 | 镜像入口 | compose `entrypoints` profile + logs | backend/worker/scheduler/websocket 路径有效 | `start.sh`、worker 三队列、scheduler、`apps/frappe/socketio.js`、`env/bin/python` 均实际运行；websocket 监听 9000 | **绿** | T2.1 按价值裁决 legacy profile，而非兼容性删除 |
| C1-R9-R11 | provision / boot | `provision.py`、`t1_8_setup.py`、`t1_framework_contracts.py` | 内部导入、参数与 boot 语义有效 | new-site 参数、5 个内部导入、`setup_complete` 合成载荷、boot 属性、insert 前 `get_doc_before_save() is None` 均通过 | **绿** | T2.8 添加 `add_to_apps_screen` 与 boot 回归断言 |
| C1-R5-R7-R10-R12-R17 | 其余框架边界 | framework、database、HTTP、compileall 探针 | 排序、翻译、锁、CSRF、Python import 可用 | runtime meta 默认 `creation DESC`；context 翻译为“总账”；MariaDB advisory lock 与 `tabUser FOR UPDATE` 执行；CSRF POST 200；Python 3.14 compileall 通过 | **绿** | T2.6、T2.7、T2.8；13 JSON 仍显式化排序 |
| C1-API | API 面 | `t1_http_contracts.py` | v1、resource 与 v2 权限事实明确 | guest ping 200、guest v2 meta 拒绝；Administrator 的 resource/v2 meta 200；CSRF POST 200 | **绿** | T5.1 runtime-baseline |

## 红项原始事实与阶段 2 转化

### R3-A：OAuth Client 默认角色变化

v16 新建 OAuth Client 自动写入 `allowed_roles = Desk User`；项目现有平台成员使用 `DSHERP Member` 自定义 Desk 角色。未显式绑定时，授权端返回 `Invalid client_id parameter value`，但数据库中的 `client_id` 完全一致。探针显式将 OAuth Client 允许角色设为专用成员角色后，授权码与 Bearer Token 均成功签发。

- **T2.11**：`infra/provision_desk_oauth.py` 创建 OAuth Client 时显式写入 `allowed_roles = DSHERP Member`，先补 v16 行为测试；不得给成员扩成 `System Manager` 或共享管理员。

### R3-B：callback 登录事务冲突

原生全链达到业务 callback 后，`exchange()` 已取得平台身份；随后 `frappe.local.login_manager.login_as(user)` 报：

```text
MySQLdb.OperationalError: (1020, "Record has changed since last read in table 'tabUser'; try restarting transaction")
MySQLdb.OperationalError: (1305, 'SAVEPOINT ... does not exist')
```

同一 Bearer Token 对 `desk_identity`、`desk_membership` 的独立请求均为 200，返回的企业、Site 和普通业务用户一致，因此失败点不是 OAuth 签发或成员绑定，而是业务 callback 的本地事务快照与 `login_as` 会话元数据更新相撞。

- **T2.12**：先建立能稳定复现上述堆栈的 SSO HTTP 行为测试，再在 callback 中结束只读身份复核快照，保留 state 单次消费、成员二次校验、普通用户限制和 grant 加密；以完整授权码链路转绿为验收，不能吞掉数据库异常。

### R6：委外内部方法更名

未修改业务代码时，真实 import/call 边界为：

```text
AttributeError: SubcontractingController has no attribute create_raw_materials_supplied
Did you mean: create_raw_materials_supplied_or_received?
```

v16 实际签名是 `create_raw_materials_supplied_or_received(self, raw_material_table='supplied_items')`；`set_items_conversion_factor(self)` 与七个 mapper 函数仍可 import，公开参数面未发生影响本项目的变化。按 fastfail，旧方法缺失后没有伪造“七链均已调用”的绿结论。

- **T2.13**：先让委外 make 行为测试在 v16 因旧方法失败，再最小切换新方法并显式传 `supplied_items`；复跑 7 route、10 个 `_MAKE_INSERT_DERIVED_FIELDS` 目标/子表、实例 autoname、完整 operations `confirm()`，任何派生字段差异单独形成测试和修正。

## 其余运行事实

- v16 nginx entrypoint 会重建 `sites/assets` 软链接，因此 frontend 的 sites volume 必须可写；首次只读挂载报错后仅修正 probe compose，app 源码仍只读。
- `frappe.sessions.get()` 的原生 boot path 确实触发 `dsherp_bridge.boot.boot_session`；无 request 的 CLI 探针显式设置 `frappe.local.request = None`，没有以直接函数调用代替原生 dispatch。
- v16 runtime meta 对 11 个非子表自定义 DocType 都返回 `creation DESC`；另外 2 个 JSON 是子表。T2.6 仍对全部 13 个 JSON 显式写出排序，避免依赖未来默认值。
- Browser fresh setup 后复跑：`#dsherp-context-root` 在 `/desk/dsherp-agent` 为 hidden，在 `/desk/item` 显示“Agent”；原生 `.layout-side-section` 只在普通业务页存在。Page 容器为 `page-dsherp-agent`，React 工作台可见。
- probe frontend 未把 socket.io 接到 entrypoints profile，Desk console 的 `xhr poll error` 属于 probe 接线噪声；独立 websocket 容器已证明 v16 路径与 9000 监听有效，不能把该噪声当成产品兼容性结论。

## C1 资源清理

已销毁。`docker compose -p dsherp-v16probe ... ps --format json` 输出为空；`docker volume ls --format '{{.Name}}' | rg '^dsherp-v16probe'` 无命中。清理使用明确 project 与卷名，没有使用 prune 或通配；既有 `dsherp-validation` v15 backend、beta、platform、frontend、db、redis 仍保持运行。

## C2 部署契约切换

- TDD 红：新增 `tests/test_v16_deployment_contract.py` 后，旧配置在镜像、卷、退役服务、agent-runtime 与指纹清单五项全部失败（`5 failed`）。
- 最小实现：ERPNext 切到 `sha256:493cecf…f1bd`，MariaDB 切到 `sha256:2439dcd…43c3`；7 个保留 compose 服务加 2 个宿主入口共 9 处 ERP digest。Redis digest未变。
- 全新卷：compose 的 sites/logs/db/redis/platform/beta 共 8 个逻辑卷全部增加 `v16-` 前缀；隔离 Runtime 使用 `dsherp-v16-agent-runtime`。没有创建或删除卷，切换前 v15 仍挂载旧卷运行。
- 价值裁决：仓库已证明 Context Agent 使用 HTTP 轮询且 `/socket.io` 明确返回 404，因此删除 `websocket`、`worker`、`platform-websocket` 三个退役 `legacy` 服务；保留有调度验收价值的 `scheduler` profile。
- 指纹：新增 compose、镜像准备、Runtime 镜像及两处 agent-runtime 卷消费者，共 5 个部署控制文件。目标契约与相关运行测试 `14 passed`，`docker compose ... config --quiet` 退出码 0；非历史源码 v15 ERP/MariaDB digest 与旧 agent-runtime 卷名均为 0 命中。
- 无运行切换：核验时 `docker compose ... ps` 仍显示原 v15 ERPNext digest 与 MariaDB digest，6 个既有服务保持运行。C3 前不会执行 down/up。
