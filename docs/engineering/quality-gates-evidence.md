# 计划 5 证据：质量门禁（瘦身版）

日期：2026-09-07。环境：本机隔离合成四站加两个一次性测试站，ERPNext `16.33.0` /
Frappe `16.31.0`，容器 Python `3.14.7`，DSH SDK/Runtime `0.1.1rc1`。
全部数字来自合成数据，不代表生产可用；任何环境都未接入真实租户。

## 总判定

**不宣称 G9 通过。** G9 是「CI 配置存在且历史 30 天绿」，只能由 workflow 的运行历史证明，
不能由本文证明。本文证明的是：门禁本身建起来了、每一道都在本机真实跑过、并且被验证过有牙齿。

**G9 起算**：`2026-MM-DD · <run URL>`（待第一个含原生测试步的绿色 nightly；本机尚未 push，
push 与 workflow 首跑是用户检查点）。只有 `ci.yml` 与 `nightly.yml` 计入，`supply-chain.yml` 不计。

门禁要保证的三件事，与它不保证的：
- 学过的失败不能悄悄回来 —— 每个 PR 与每次合入 main 自动跑 ruff、非集成 pytest、vitest、
  dist 一致性、Node runtime、DocType 迁移守卫。
- 每次冲破一天内变成用例 —— 每夜在 GitHub 托管 runner 上从零拉起四站，跑全部集成与
  Frappe 原生测试，红了当天修或删。
- 审查方不用再自己重跑 —— junit、compose 日志、台账、原生测试输出作为工件上传。
- **不保证**生产不出事。门禁不防生产失败，它只保证学过的失败留下用例。

## 切片 0：仓库卫生

- `pytest.ini` 与根 conftest 的 `pytest_ignore_collect`：默认运行不碰 `tests/integration`
  （两套件有 3 对同名文件，同一会话收集会 `import file mismatch`），点名目录才收集，
  并统一打 `integration` marker。
- `ruff.toml` 只开 `F` 与 `E9`：默认 `E,F` 有 892 条，其中 `E701/E702` 就 757 条。
  清理 24 处未用导入、3 处未用变量、2 处未定义名。
- 迁移守卫接受 `DSHERP_GUARD_BASE`，CI 传 PR base 或 push 前提交。

| 门 | 结果 |
|---|---|
| 非集成 pytest | `731 passed / 2:12` |
| 全量集成 | `212 passed / 22:49`（`work/junit-integration-slice0.xml`） |

## 切片 A：PR 门

`.github/workflows/ci.yml`，三个互不依赖的 job：`python`、`frontend`、`runtime`。
全部 action 以 40 位提交 SHA 固定，后跟版本注释。无任何重试。

**未闭合**：push、开 PR、三绿→故意一红→三绿的演练、以及 `main` 分支保护，都是用户检查点，
本机无法代劳。三次 run URL 与 `required_status_checks.contexts` 的输出待补。

## 切片 B：从零开通驱动、每夜与每周

- `infra/dev_stack.py`：27 步步骤表 + 台账 `.runtime/dev-stack.json` + 每步廉价探针。
  探针返回 `list[bool]`，全真＝结果在，全假＝不在，**混合＝半成品即停**，绝不重跑
  fail-closed 的开通脚本。台账里只有步骤名、时间戳和该步产出的凭据文件名，没有任何密钥值。
- `scan-artifacts` 只报密钥名与文件名，从不报值；上传前自检。
- `nightly.yml`（02:00 Asia/Shanghai 从零）、`supply-chain.yml`（每周，允许红，**不计 G9**）。

**未闭合**：nightly 与 supply-chain 的 `workflow_dispatch` 首跑、以及 Task B.7 的本机从零重建，
都需要用户检查点。首跑要记录总时长、各步耗时、`docker-stats.txt` 峰值内存与 `disk.txt`。

## 切片 C：集成清理（登记式残留）

### 做了什么
- `tests/integration/site_exec.py`：容器脚本的唯一入口，站点映射到 compose 服务，
  保证容器侧 `frappe.destroy()`；没有测试再自己写容器名。
- `tests/integration/credentials_check.py`：凭据三态。只有站点**拒绝**密钥（401/403 或
  把密钥认作别人）才重发；站点不可达是坏栈，直接失败并点名站点与端口——往一个可能带着旧密钥
  回来的站点里重发，只会把凭据文件写坏。
- `tests/integration/residue.py`：先登记后创建的残留台账。登记在创建之前落盘（0600、fsync），
  teardown 按控制器要求的顺序清扫并核验零残留，清不掉的留在台账里等人处理，
  被中断的会话留下的登记在下一次会话开头清。持久夹具前缀与固定演员邮箱在登记时就被拒绝。
- 九个高风险测试迁移；`test_doctype_policy.py` 的策略轮转测试定点加固（见下）。

### 数字

| 项 | 值 |
|---|---|
| 容器名硬编码（`tests/integration/*.py` 全套） | 101 → 93 |
| 九个迁移文件合计 | 9 → 1 |
| 九个文件单独跑 | `47 passed / 7:29`，跑完台账为空 |
| 结束门全量集成 | `214 passed / 24:03`（`work/junit-integration-slice-c-final.xml`） |
| 非集成 | `745 passed / 2:05` |

保留的那 1 处在 `test_agent_boundary.py`：`dsherp-validation-backend-1` 在那里是可达性探测的
目标主机名，不是执行入口，必须留。

### C.11：`test_policy_seed` 60 秒超时的决策
判据用例 `test_policy_seed_creates_or_verifies_exact_legacy_rows` 三条，扣掉 30 秒上限的读
（实测 2–3 秒，按 3 秒扣）之后样本 `[1.14, 1.34, 5.16]` 秒。n=3 < 20，按规则用 max = **5.16 秒**，
远低于 45 秒门槛。**保留 `timeout=60`，不改代码。** 数据来自本机；每夜 runner 更慢，
首夜 junit 是第二份样本。

### 一次真实事故与它教会的事

第一次全量集成：`1 failed, 94 passed, 120 errors / 11:44`。

| 事实 | 值 |
|---|---|
| 集成开始 | 15:10:54 +08:00 |
| `dsherp-validation-db-1` 被杀 | 15:21:44 +08:00（第 650 秒） |
| 退出码 / 标志 | 137 / `OOMKilled=true` |
| 容器上限 / 重启策略 | `mem_limit: 1g` / `restart: "no"` |
| 该实例已连续运行 | 约 25 小时 |
| 第一个失败用例 | 第 94/214 条，`MySQLdb.OperationalError: (2013, Lost connection)` |

排除项：`scheduler` 的 137 是 `compose stop` 的 SIGKILL（`OOMKilled=false`）；
`mariadb-check` 对四个站库全部通过，无损坏表；重启后四站 ping 均 200。

**由此产生的两处改进：**

1. `infra/v16_integration_queue.py` 原来用 `check=True`，栈死掉时每条测试抛一个一模一样的
   `CalledProcessError`，一百多条栈没有一条指向数据库。改为共享的 `_inspect(run)` 自判退出码，
   抛一条指名 `docker ps -a --filter name=dsherp-validation-`、137 代表内存上限、上限写在
   compose 的 `mem_limit` 的消息，并保留守护进程的原话。

2. 第二次全量集成 `14 failed, 200 passed`，14 条全部源于 alpha 站 `DS Doctype Policy` 的
   `Item` 一行漂移（`allow_create` 1→0、`allow_fill` 1→0、多出一条 `delivery_note_changed` 路由），
   其余 13 行与开通脚本期望完全一致、无多余行。来源是 `test_doctype_policy::test_policy_change_rotates_revision`：
   数据库死掉时它注入脚本的 `finally` 只跑到删会话就断在 `Lost connection`，没走到策略恢复；
   下一次运行的同一条测试把已损坏的行当作 `original_policy` 快照又原样写回，损坏因此自我延续。
   修法是把该行的恢复登记到宿主侧。

**双向演练**（不是只声称）：

| 组合 | 脚本内恢复 | 宿主侧登记 | 结果 |
|---|---|---|---|
| 对照 | 停用 | 无 | 精确复现那三处漂移 |
| 实验 | 停用 | 有 | 漂移为空 |
| 正常 | 有 | 有 | 7 passed，漂移与台账均为空 |

**顺带纠正了计划里的一个假设**：宿主 `subprocess.run(timeout=)` 只杀 docker 客户端，
容器里的解释器会继续跑完自己的 `finally`——所以单纯的宿主超时**并不留残留**。
真正留下残留的是容器侧数据库或进程本身死掉，让 `finally` 中途抛错。

### 数据库内存：第一次判断被第二次事故推翻

第一次 OOM 之后我的结论是**不改 `mem_limit`**，理由是：

| 时刻 | 值 |
|---|---|
| 数据库重启后 | 468.6 MiB / 1 GiB（45.8%） |
| 一次全量集成之后 | 608.7 MiB / 1 GiB（59.4%） |
| 连续运行 25 小时、跨多次集成 | 撞上 1 GiB 被杀 |

当时的推理是「每夜每次从零、只跑一轮，1 GiB 有 40% 余量」。**这条推理错了**，
证据是切片 D、E 做完之后同一个数据库在 **3 小时 14 分**内第二次被 OOM 杀掉
（07:28:09 → 10:42:48，期间两次建站、12 次 `bench migrate`、四轮全量集成）。
第一次的 609 MiB 是刚重启、缓存还没填满时的读数，不代表稳态。

量清楚之后（2026-09-07）：

| 事实 | 值 |
|---|---|
| 站库数 / 表总数 | **6 / 3516**（四个 ERPNext 站各 755，两个平台站各 248） |
| 切片 D 加进来的表 | 1003 张，表数增长 **40%**——而 1 GiB 是按四站定的 |
| `Open_tables` / `table_open_cache` | 2000 / 2000（已打满），`Opened_tables=3534` |
| MyISAM | 18 张表 / 2.5 MB，`Key_blocks_used=21`（约 21 KiB） |
| Aria | `Aria_pagecache_blocks_used=26`（约 208 KiB） |
| 镜像默认 `key_buffer_size` / `aria_pagecache_buffer_size` | **各 128 MiB**，都没写在命令行上 |

所以真正的成因有两条，都不是「环境抖动」：
1. **表缓存随站点数增长**，切片 D 把它推高了 40%，而上限没跟着动——这是我自己的改动带来的后果。
2. **256 MiB 花在两个几乎没被用到的缓存上**，因为它们是镜像默认值、没有写在 `command` 里，
   在上限背后被分配掉了。

修法（`infra/compose.validation.yml`）：`mem_limit`/`memswap_limit` 提到 `2g`；
`key-buffer-size` 与 `aria-pagecache-buffer-size` 显式写成 `32M`（仍是实测用量的百倍以上），
把那 192 MiB 还给真正会增长的部分。常驻内存合计从 3040 MiB 变为 4064 MiB，
GitHub runner 16 GiB、本机 Docker VM 8.3 GiB 都容得下。

`tests/test_deployment_contract.py` 钉住的是这次的真实教训，不是一个具体数字：
`memswap_limit` 必须等于 `mem_limit`（否则上限形同虚设）、默认值很大的缓冲区必须写在命令行上、
它们的合计要给数据字典与打开表留出至少三分之二的空间。

**2g 这个数字目前来自推算，不是实测。** 从零重建（B.7）会给出六个站建完加一轮集成加原生测试
之后的真实峰值，届时按数据修正。

## 切片 D：原生测试站与 G7 权限矩阵

### 两个一次性测试站（真实建站实测）

| 项 | 结果 |
|---|---|
| `dsherp-test.localhost` | frappe 16.31.0 / erpnext 16.33.0 / dsherp_bridge 0.1.0 |
| `dsherp-platform-test.localhost` | frappe 16.31.0 / dsherp_platform 0.1.0（无 erpnext） |
| site_config | `allow_tests`、`disable_scheduler`、`mute_emails`、`pause_scheduler` 均为 1 |
| `default_site` | 仍为 `dsherp-validation.localhost`（未被改动） |
| 重复建站 | 打印 `already exists; inspect before retrying`，退出码 1 |
| nginx 对两站 | 均 **421**，不对外暴露 |
| `_new_site` 关键字 | 与容器内 `frappe/installer.py` 第 39-57 行逐个核对 |

`bench run-tests` 会清表，所以原生测试有自己的站，绝不碰四个开发站。每个建站服务只挂它那台
bench 的 sites 卷与它需要的两个密钥，看不到另一台 bench 的卷，也看不到四个开发站的管理员口令。
口令走进程内 `frappe.installer._new_site`，不上 argv。`frappe_app/*/tests` 进 `.dockerignore`。

### 原生测试的判定（容器内实测，推翻了计划的 v15 假设）

| 行 | 原文 | 意义 |
|---|---|---|
| `testing.py:165` | `f"\nRunning {suite.countTestCases()} {category} tests for {app}"` | 唯一的「跑了多少」证据 |
| `testing.py:171-173`、`224` | `sys.exit(1)` | 失败时退出码**确实**非零（与计划按 v15 的假设相反） |
| `testing.py:239` | `print("xmlrunner not found. ...")` | 镜像里没有 xmlrunner，`--junit-xml-output` 不写文件 |
| `testing.py:349-351` | 未开 `allow_tests` 时打印 `Testing is disabled for the site!` 然后 `return` | **实测退出码 0、零条 Running 行** |

所以判定是两条一起看：退出码非零即失败，**且**必须有 `Running N ... tests for <app>` 且 N>0。
产出是每个 App 一份 `.log` 加一份解析后的 `.json`。

| 运行 | 结果 |
|---|---|
| 骨架金丝雀 | bridge 3 / platform 1，ok=true |
| 故意改坏一条金丝雀 | bench 退出 1 → 驱动 Fault → CLI 退出码 2，第二个 App 仍跑完 |
| 加矩阵后 | bridge 7 / platform 5 |
| 加交接窗口后（切片 E） | bridge 12 / platform 5 |
| 工件 `scan-artifacts` | 退出码 0，无密钥 |

### G7 权限矩阵

16 个 DocType 一份 JSON，四类身份 × 四个动作的期望**从 permission 行推导后比对**，
不是手写自然语言——授权位变了而期望没变就是不匹配，不是静默放行。
游客端点磁盘实测 9 个，与声明一致；审计报表由 System Manager 单独限权。

宿主静态 52 条通过。变异测试五处各自都变红：

| 改动 | 结果 |
|---|---|
| `DS Model Run` 的 manager.read 改 false | 1 failed |
| `DS Membership` 的 `controller_refuses_delete` 改 never | 2 failed |
| 删掉一个游客端点声明 | 1 failed |
| `DS Enterprise` 的 `permlevel_1.member` 改 `["read"]` | 1 failed |
| 报表角色改 All | 1 failed |

容器内：把 `manager.read` 改 false → 原生测试退出 1 并指名
`('DS Model Run', 'manager', 'read', True, False)`。

`DS Agent Task` 去掉 `delete`（README 说旧任务记录只读保留，而它没有 `on_trash`，
角色授权位就是全部的门）：只改一行，`git diff --stat` 为 1 insertion / 2 deletions；
迁移守卫在 `no-patch:` 说明下退出 0；六站已 `bench migrate`。未 migrate 时平台原生测试
正确报 `('DS Agent Task', 'manager', 'delete', True, False)`——「定义改了、站没迁」被抓住。

顺带修 `tests/test_audit_immutability.py:14` 的平台 doctype 路径（写成了
`dsherp_platform/dsherp_platform`，真实是 `dsherp_platform/platform`）。影响范围核实过：
八个审计 DocType 全在 bridge 下，两个查找函数先查 BRIDGE 就命中，所以**未造成误判**，
但它是失败关闭的隐患，仍按计划修。

### 切片 D 自己带出的队列问题

两个测试站建在同两台 bench 上，队列是同一批 redis 队列；`bench migrate` 排下的
`build_index_for_all_routes` 与建站向导排下的 `set_timezone` 都是以裸函数对象入队的，
队列报的是 `repr(f)` 而不是点号路径，都不在白名单，队列检查直接拒绝，18 条集成测试错在夹具上。
修法：清扫目标加这两站；按**函数名**放行这两个（不是按 `<function ...>` 形状放行，
形状对但名字不认识的仍算意外任务）。时区实测在建站时已同步写好（`Asia/Shanghai`），
这条异步任务是多余的。现场清了一次：测试站积了 **125** 条。

### 结束门

| 门 | 结果 |
|---|---|
| ruff | All checks passed |
| 非集成 | `802 passed` |
| 全量集成 | `214 passed / 22:44`（`work/junit-integration-slice-d.xml`） |
| 原生测试 | bridge 7 / platform 5，ok=true |
| vitest | 22 文件 `203 passed` |
| node runtime | 10 pass |
| 迁移守卫 | 对 main 退出 0；DocType JSON 只动 `ds_agent_task.json` 一行 |

## 切片 E：交接令牌落表收口

### 做了什么
交接行是谁也删不掉的审计记录（裁决 #3），它过去逐字保存成员的平台 OAuth 令牌密文。
现在按 R7 的做法：授权存进站点缓存、键是交接 id、寿命恰好等于窗口；行里只记
`expires_at`（必填、只读、冻结）。窗口是一处常量 `TRANSFER_WINDOW_SECONDS=7200`
（用户 2026-09-07 裁决 2 小时）。

`export_transfer` 的顺序是身份/站点 → 窗口 → 租约 → 授权，后两者各自具名拒绝。
`_confirm` 的发布分支把「谁的交接」「还能不能用」「回执变没变」拆成三条拒绝——
原来三者合并报「回执已变化」，且回执要花一次对端调用。
`_request` 把对端 `frappe.throw` 的原话中继给用户，异常类与 traceback 一概不传。

### 全仓第一个 `EXPECTED_CHANGES`
`expire_transfer_grant` 声明 `columns_removed: ['platform_grant']`，判据是真实的
`release_compare.compare`：不声明时报 `removed_columns` drift，声明后 `clean`。
`import frappe` 放在 `execute()` 里，所以没有 Frappe 也能读到声明。

**与计划不符、按事实修正的三处：**
1. `compare()` 对这张表只会报告删列一个差异。快照对小表保留字段值，而「只在升级后存在的列」
   算 schema 新增、不逐行报。所以声明的实质是 `columns_removed`，`fields: ['expires_at']`
   记录 patch 写了什么、给人读。
2. `of_transfer` 必须读穿 redis。容器实测：`set_value` 无条件写进程内缓存并忽略
   `expires_in_sec`，`get_value` 默认先读本地，1 秒 TTL 过后仍返回旧值，
   `use_local_cache=False` 才返回 None。不读穿的话，写过租约的进程在窗口关闭后还会拿它作数。
3. alpha 上只有 `dsherp-preview@example.invalid` 能提配置包（`configuration.authorize`
   需要 `DocType.create`）；计划里写的 `dsherp-reader` 是只读角色，跑不通。

### 迁移实测
六站 `bench migrate` 全部 OK。alpha：存量 4 行全部拿到窗口、`platform_grant` 列已删、
`rows_without_window: 0`、Patch Log 已记；beta 同（0 行）。未 migrate 时原生测试如实报
`Unknown column 'expires_at' in 'SELECT'`。

**如实说明**：存量交接行迁移后全部过期——升级窗口内在途的交接要重新发起。
交接是分钟级操作，代价可接受。

### 前端
`ConfigurationBundle` 被 `context-agent.js` 与 `agent-workbench.js` 两个入口打包，
所以两个 dist 都要重建（计划预期只有一个）；`studio.js` 不含它、未变。
产物是纯 ASCII + **大写**十六进制 `\uXXXX` 转义（按小写查会漏）。重建两次 md5 一致。

### 台账的一次真实兜底
两站 HTTP 测试里，登记的恢复脚本占位我只替换了第一处，teardown 报 `NameError`。
台账没有静默通过：抛 `ResidueError`、把条目留在台账里、并在下一次会话开头挡住运行，
直到人工删掉那条租约并清理条目。这是设计意图，也是它第一次在真实失误上生效。

### 结束门

| 门 | 结果 |
|---|---|
| ruff | All checks passed |
| 非集成 | `810 passed / 1:54` |
| 全量集成 | `215 passed / 22:45`（`work/junit-integration-slice-e.xml`） |
| 原生测试 | bridge 12 / platform 5，ok=true |
| vitest | 22 文件 `203 passed` |
| node runtime | 10 pass |
| 迁移守卫 | 对 main 退出 0 |

## 门禁一览

| 门 | 在哪跑 | 判据 | 重试 |
|---|---|---|---|
| ruff（F、E9） | `ci.yml` 的 python job；每切片末 | 零告警 | 无 |
| 非集成 pytest + 迁移守卫 | 同上 | 全绿 | 无 |
| vitest + dist 一致性 | `ci.yml` 的 frontend job | `git diff --exit-code -- dist` | 无 |
| Node runtime | `ci.yml` 的 runtime job | 全绿 | 无 |
| 全量集成（从零四站） | `nightly.yml` | 全绿，且跑完台账为空 | 无 |
| Frappe 原生测试 | `nightly.yml` | 退出码 0 **且** 每个 App 报了 N>0 条 | 无 |
| SBOM / CVE（high） | `supply-chain.yml`，每周 | 允许红 | 无 |

唯一的退避重试是 Docker Hub 拉镜像的 shell 循环——那是取件，不是测试。

## 未闭合与如实说明

- **G9 未起算**：需要第一个含原生测试步的绿色 nightly。push、开 PR、`main` 分支保护、
  两个 workflow 的首跑都是用户检查点，本机无法代劳。
- **切片 A 的红/绿演练未做**：需要真实 PR。
- **切片 B 的本机从零重建（Task B.7）未做**：会销毁本机合成数据，需用户确认。
- **Playwright 五路径、mypy、存量 57 个注入脚本整体改写**：明确不做，理由见偏离表。
- **`dsherp/admin.py` 体量拆分**：推迟（复盘 Q5）。
- **G1、G3 仍未闭合**：与本计划无关，沿用计划 3、4 的结论。
- **发布进行中窗口恰好到期**：下一步 `read_receipt` 会被源站拒绝、该步 Failed、执行记录 Partial，
  与「任何变化即停止发布」一致。2 小时内实际不会碰到，但形态如实记在这里。
- **Redis 不可达时租约静默未写成**：`stash_transfer` 走 `frappe.cache()`，连接失败被 Frappe 吞掉；
  导出时会以「授权已失效」拒绝（fail-closed），但 `prepare_transfer` 当时不会暴露这一点。
- **存量交接行迁移后全部过期**：见切片 E。
- **数据库长期运行会累积内存**：见切片 C，决定不改上限而是让失败可查。
- **本机开发栈上仍有早期中断留下的合成用户**（`policy-revision-*`、`impact-*`、`execute-*`、
  `delivery-*` 等 `@example.invalid`），是登记式清理落地之前的遗留；新写的测试不会再产生它们。

## 偏离表

与上位设计的逐项偏离见
[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md)的「计划 5 偏离表」，
两处同一份内容，不另抄。
