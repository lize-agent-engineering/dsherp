# ERPNext/Frappe v16 迁移证据

日期：2026-09-01

分支：`codex/v16-migration`

## 结论边界

本记录只证明本机隔离合成环境中的版本切换、四站 fresh provision、自动化回归、固定 Runtime 本地模型替身链、制造行为重验、应用内真实浏览器 UI 验收和经授权的真实 DeepSeek 只读页面矩阵。它不证明生产部署或生产租户可用。

截至本记录，C0–C3、T4.1、T4.2 和 T4.3 均已完成并落档。Claude 第三轮独立审计实际重跑自动化、操作真实浏览器并回读既有真实模型证据，在发现两项并发确认恢复身份校验缺口后没有放行；执行方以 TDD 修复，第四轮聚焦复审确认两项阻断解除且无新的 Critical 或阻断级 Important。C4 核心技术与浏览器审计已经通过，但整体 C4 仍须等待 daily 调度、备份与恢复冷静期证据。T5.1 文档更新、v15 九卷归档和精确 dry-run 已产生待审增量，但 C5 整体尚未放行，v15 卷与镜像仍全部在位；用户已明确授权后续移除 v15，该授权不允许跳过 C4 最终独立审计或按通配删除。

## 固定运行基线

| 项目 | 当前现场证据 |
| --- | --- |
| ERPNext | 容器内 `bench version`：`16.33.0` |
| Frappe | 容器内 `bench version`：`16.31.0` |
| Python | ERPNext 镜像内 `3.14.7` |
| ERPNext 镜像 | `frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd`；运行容器 image ID `sha256:a4fd94c1de264ebaa83713ac663ef1ab7252809f5f8dee84b41065067c53f1e9` |
| MariaDB | `mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3` |
| Redis | `redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015` |
| DSH | `deepseek-harness-sdk==0.1.1rc1`、`deepseek-harness-runtime-bin==0.1.1rc1`，锁文件未因迁移改动 |
| Agent Runtime | 新卷 `dsherp-v16-agent-runtime`；单容器限制仍为 384 MiB、0.1 CPU、96 PIDs、只读根、无额外 capabilities |

ERPNext 镜像本身没有 Node 可执行文件；固定 DSH Runtime 的 Node 版本仍应以 `runtime-baseline.md` 中 wheel 内 Runtime 的独立证据为准，不能用业务后端容器推断。

## C0–C2：探针与迁移实现

- `46496ac` 启动完整迁移并固化 C0 清单；v15 栈在新卷切换前没有原地升级。
- `0bf9893` 建立隔离 v16 probe，随后 `5b45934`、`ee4aaf2`、`5c0b022`、`85ad131`、`35f41c6`、`5a6e4fc` 完成 v16 框架、Desk 路由、翻译/制造基线、部署卷、前端制品和制造方法适配。
- `cf9b4cb` 记录 C2 复跑；迁移实现没有修改 DSH 固定版本，没有把 v16-only API 混回旧 v15 运行卷。

详细探针矩阵、红绿结果与方法签名见迁移计划的 C1/C2 已勾选条目。本节不把 probe 通过外推为 fresh 四站通过，后者由 C3 单独证明。

## C3：四站 fresh provision 与全量门

### 站点和凭证

- fresh 创建 `dsherp-validation.localhost`、`dsherp-platform.localhost`、`dsherp-beta.localhost`、`dsherp-daily.localhost`；beta 只经 18085 隔离预览入口公开，18084 不监听。
- 四站 `/api/method/ping` 均为 HTTP 200；alpha、daily、platform、beta 的新本地 0600 profile 分别实调 `frappe.auth.get_logged_user`，均为 HTTP 200。
- beta 原生 setup 使用公司 `DSHERP 隔离预览合成公司`，alpha 使用 `DSHERP 原生验收测试公司`；daily 使用 `DSHERP 日常合成企业`。这些均为明确合成数据，不是生产租户。
- 常驻协调进程通过用户 LaunchAgent `com.dsherp.agent-worker-v16` 启动；`.runtime/agent-worker.pid` 当前对应存活的 `dsherp.context_worker`。启动日志因 macOS launchd 不能打开 Documents 下的重定向目标，改存用户标准 `~/Library/Logs/dsherp-agent-worker-v16.log`，权限为 0600。

### 自动化门

| 门 | 结果 |
| --- | --- |
| 集成测试 | `163 passed in 701.64s` |
| 非集成 Python | `117 passed in 44.19s` |
| 前端 Vitest | `21 files / 162 tests passed in 14.11s` |
| 前端生产构建 | `npm run build` 退出 0 |
| 格式 | `git diff --check` 退出 0 |

全量集成开始前在三站活跃运行均为 0、无 Frappe/Agent worker 的条件下，用 Frappe 原生命令清理了合成测试积压的默认后台队列；没有清业务数据库或会话缓存。全量门结束后同样清理其测试用户产生的 455 条可重建任务，再启动正式协调 worker。

### 备份、profile 与回退

- `--profile control` 和 `--profile scheduled` 在 C3 各实际拉起一次；scheduler 随后保持停止，不作为常驻服务。
- `verify_daily_backup.py` 退出 0。当前 daily 私有备份四件套前缀为 `20260901_163426-dsherp-daily_localhost`：数据库 `942896` 字节，public/private tar 各 `10240` 字节，Site 配置备份 `483` 字节；恢复比对完成后一次性恢复 Site 已删除。
- v16 八个站点/数据库/Redis/日志卷和 `dsherp-v16-agent-runtime` 均存在。
- v15 的 validation/platform/beta sites/logs、db、redis 以及 `dsherp-agent-runtime` 仍逐名存在；未执行 `down -v`、volume rm、image rm 或 prune。
- fresh 重建前的 `.runtime` 完整备份位于忽略提交的 `work/v16-rollback-20260901/runtime`，旧现场运行状态位于 `work/v16-rollback-20260901/retired-live-runtime`。它们在 C4 放行、v15 归档和精确 dry-run 完成前不得清理。

## v15 卷归档与精确 dry-run

2026-09-02 对九个已退役 v15 卷逐名执行了九条独立归档命令；每条命令均将对应卷只读挂载到 `/src:ro`，用 Alpine `tar czf` 写入 `work/v16-rollback-20260901/v15-volumes/`，九条退出码均为 0。没有使用通配目标，没有启动 v15 应用栈，也没有执行 `docker volume rm`、`docker image rm`、`prune` 或 `down -v`。

旧数据库卷的逻辑备份使用 MariaDB 10.6 临时容器、`--network none` 和 Unix socket。首次错误地只读挂载当前 v16 控制面 root secret，`mariadb-dump` 以鉴权拒绝退出 2；凭据值没有输出，精确确认其失败产物为 0 字节后已清理，没有覆盖重试。随后只读挂载迁移前备份 `work/v16-rollback-20260901/runtime/control/db_root_password`，以全新的 `.partial` 目标执行一次 `mariadb-dump --all-databases --single-transaction --routines --events --triggers`，退出 0；产物非空且含 5 条实际行首 `CREATE DATABASE` 语句，验证后原子改为最终文件名。临时数据库运行时可用空间由约 5.8 GiB 降到 4.4 GiB，停止并由 `--rm` 清理临时容器后恢复到约 5.6 GiB；旧数据库卷仍为 673.9 MB、无运行中挂载，额外占用来自已回收的临时容器写层。

精确 dry-run 清单位于 `work/v16-rollback-20260901/v15-removal-dryrun.txt`，共 10 行，只包含九个卷名和镜像 `frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341`，不包含可执行删除命令。本次只完成非破坏性归档和清单登记，不代表已删除 v15。

| 归档文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `work/v16-rollback-20260901/v15-volumes/dsherp-agent-runtime.tar.gz` | 83745778 | `b1afd4c6085249ec83db59e53bba6a0ebd81916ef07668017bc87c2b63137876` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_beta-logs.tar.gz` | 73252 | `064ad5fc6210c00b1fa41db0068d1cf6715deed9f7a48a921ab84fab29f72706` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_beta-sites.tar.gz` | 73477 | `df171f8c5673fb3fd14e2ba8525e0156e1b915c2df103e8a0853c953405c0f40` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_db-data.tar.gz` | 67479901 | `72d9c7a071467937e2f3f8c5432b9a4e9f22668ab00b24ea4773011aa0c836a8` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_logs.tar.gz` | 193172 | `6a13e2fa8cb180969b00b0ca2b4f991f104a0ad0ec80d08cd5589d93c80c98e3` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_platform-logs.tar.gz` | 19214 | `17355700d1c8ccee46d3ac5808f80797bd157b66106c36da183a7a243c93fc21` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_platform-sites.tar.gz` | 19274 | `f9a0fd4d87b0043692a4847641b09045e5e74a3b7e732b28a39702519a7650d6` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_redis-data.tar.gz` | 93 | `c77250a5691262fd11f9ad2d2a30c74f6d56cf4c4228f3cc86dd93b7fd3baa97` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_sites.tar.gz` | 4501928 | `7136f701099c5af81376735047b597aa8bd19ed5d6fc3818fa12d84a080c4ef9` |
| `work/v16-rollback-20260901/v15-volumes/dsherp-validation_db-data-all-databases.sql` | 123568901 | `5b44a6c7643f31119f9397b4c46df12f2ef5f3c9a6f9a33f75fba823d5f302fb` |

## T4.1：本地模型替身与制造重验

### 固定 Runtime 工具链

- alpha：固定 Runtime→本地 SSE 模型→MCP→HTTP→ERP 的宿主与隔离容器两种路径均通过。
- daily：机械切换到 `dsherp-daily.localhost`、`daily-operator@example.invalid`、`DAILY-AGENT-ITEM` 后，两种路径均通过。
- daily 临时验收副本先暴露两项夹具错误：嵌套目录把仓库根算成 `/dsherp/tests`；复用 alpha 清理 fixture 留下 3 个合成会话。修正临时路径和本站清理后重跑通过；三个会话按精确 ID 删除，worker 全程停止，因此没有真实 provider 请求。

### 原生制造行为

复用既有自制、采购收货、企业供料委外和交付四个文件组。alpha 共 5 个唯一用例通过；daily 机械切换本站后，先因 alpha 固定公司/客户名出现 2 项预期红灯，改用现场回读的 `DSHERP 日常合成企业` 与 `日常 Agent 合成客户` 后 5 个唯一用例全部通过。临时 daily 源文件和 pytest pyc 均已删除，未提交为新测试基建。

最终独立数据库回读：

| Site | 活跃运行 | 七类制造单据 | 原料仓 | 在制仓 | 委外仓 | 成品仓 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| alpha | 0 | 全部 0 | `100/100` | `0/0` | `0/0` | `0/0` |
| daily | 0 | 全部 0 | `100/100` | `0/0` | `0/0` | `0/0` |

七类单据为 Work Order、Stock Entry、Purchase Order、Purchase Receipt、Subcontracting Order、Subcontracting Receipt、Delivery Note。T4.1 后清理 303 条合成测试后台任务并重启同一 worker；没有真实 DeepSeek 调用。

## T4.2：真实浏览器 UI 与四站登录链

用户明确授权使用此前的本地 `0600` 合成账号登录，并授权 Codex 控制应用内真实浏览器完成验收。本轮不是静态 DOM 单测或 headless 截图替代：页面在当前浏览器实际渲染并经原生登录/OAuth 表单交互完成。

### React、生命周期与互斥

- alpha 以 `dsherp-writer@example.invalid` 原生登录后，`/desk/dsherp-agent` 渲染会话栏、对话区、来源入口和只读输入区，React 工作台可见。
- 打开原生 `Item / DSHERP-HITL-ITEM` 后，`on_page_show` 注入的“打开 Agent”按钮为 1；打开侧栏后当前页面上下文精确显示 `Item / DSHERP-HITL-ITEM`。
- 切回 `/desk/dsherp-agent` 后，全局“打开 Agent”按钮为 0、工作台 main 为 1；再回到 Item 页按钮恢复为 1。由此验证全局侧栏生命周期和正式工作台互斥，没有同页双挂载。

截图：[`alpha 工作台`](evidence/v16/t4.2-alpha-workbench.jpg)、[`Item 上下文侧栏`](evidence/v16/t4.2-alpha-record-sidebar.jpg)。

### 四站登录与 SSO

| 站点 | 登录链 | 浏览器最终身份 | 结果 |
| --- | --- | --- | --- |
| platform | 原生账号密码 → `/desk/dsherp-home` | `member@example.invalid` | 三个 Ready 企业均可见 |
| alpha | platform → 原生 OAuth consent → callback | `dsherp-reader@example.invalid` | 直接落到 `/desk/dsherp-agent` |
| beta | platform → 原生 OAuth consent → preview callback | `beta-reader@example.invalid` | 直接落到 `/desk/dsherp-agent` |
| daily | platform → 原生 OAuth consent → daily callback | `daily-operator@example.invalid` | SSO 身份成立，工作台可访问 |

截图：[`平台三企业入口`](evidence/v16/t4.2-platform-enterprises.jpg)、[`alpha SSO 工作台`](evidence/v16/t4.2-alpha-sso-workbench.jpg)、[`beta SSO 工作台`](evidence/v16/t4.2-beta-sso-workbench.jpg)、[`daily SSO 工作台`](evidence/v16/t4.2-daily-sso-workbench.jpg)。

浏览器首轮真实暴露 beta 入口 HTTP 417（“该企业 Desk 登录尚未配置”），没有盲目重复点击。先把三企业 Desk URL 行为写入集成测试并确认红，再为 fresh provision 补 beta 原生 OAuth Client/Social Login Key 和 `18085` 回调；复验又发现普通用户回跳 `/desk/home` 会触发 Page 权限弹窗，于是先把 OAuth 回跳行为测试改为核心 `/desk/dsherp-agent` 并确认红，再做最小实现。修复提交为 `d94efce`，相关 v16/平台/SSO 回归为 **34 passed in 14.01s**。T4.2 全程没有点击发送、没有创建 Agent run，也没有真实 provider 调用。

## T4.3：真实 DeepSeek 中文查询、记录读取与企业隔离

费用授权先由用户明确给出，并在提交 `390438a` 中落盘；随后才开始真实 provider 请求。协调进程从本项目权限为 `0600` 的 `.env` 读取既有 DeepSeek 配置，实际模型为 `deepseek-v4-flash`。密钥、token 和 provider 原始响应没有进入仓库或证据输出。

| Site / 用例 | Run | 状态 | 模型调用 | 输入字节 | 预留输出 token | ERP 读取结论 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| alpha 无匹配校准 | `b857f9613c9921638372a20dcae5c3aac86b4a414cd76de4f148df2ccf9162dc` | Succeeded | 6 | 194332 | 12288 | 合成中文名称精确查询两次均 0 条 |
| alpha 正向记录读取 | `54f2c17d9f318fa2d2292faf2f4f1a6d42a3acfff84413d0d6ffc394900a6749` | Succeeded | 3 | 53983 | 6144 | 唯一命中 `DSHERP-HITL-ITEM` |
| daily 企业隔离 | `c50dad0eb6fb54a446077a3dd1462257349ab980d4c957181b840c8d7a5a373a` | Succeeded | 5 | 141520 | 10240 | 同一中文名称在 daily 为 0 条 |

合计 **3 个真实运行、14 次模型调用、389835 输入字节、28672 预留输出 token**。预留 token 是运行预算记账，不是实际输出 token 或人民币金额，不能据此宣称精确费用。

alpha 正向页面回答为：物料编号 `DSHERP-HITL-ITEM`、物料名称“`HITL 确认前物料`”、最后修改时间 `2026-09-01 16:27:47.287925`。独立 ERP 数据库回读三项完全相同，持久化 source 的 `record_versions` 也记录同一版本。daily 页面明确返回当前企业无匹配记录；独立 daily 数据库按该中文名称回读同样为不存在，source 的 records 为空，没有带回 alpha 记录。

两个成功回答都包含当前运行的真实 ERP 来源；模型在负例中另行尝试的模糊/文本查询被服务端以 HTTP 417 拒绝，最终只采用已成功的精确查询来源。该低效行为如实保留，不把被拒绝的尝试隐藏成全绿。

最终两站活跃运行均为 0；`DS Operation Proposal`、`DS Execution Record`、`DS Configuration Confirmation`、`DS Configuration Execution` 均为 0；没有业务写入。一次性 daily worker 退出后，alpha 常驻 LaunchAgent 已恢复并存活；无遗留 `dsherp-context-*` 容器或 `context-run-*` 临时目录。

截图：[`alpha 真实记录读取`](evidence/v16/t4.3-alpha-real-item-read.jpg)、[`daily 企业隔离`](evidence/v16/t4.3-daily-enterprise-isolation.jpg)。

## C4 首轮独立审计与整改

Claude Code Opus 以只读审计方检查固定提交 `d64a2f9` 及执行方审计包，实际重跑自动化、读取截图和数据库/运行态。首轮结论为 **C4 BLOCKED**，Critical 0、Important 5；它没有浏览器驱动，因此 T4.2 真实浏览器矩阵也明确标为未独立复验，而不是以截图放行。

审计发现及整改如下：

| 审计发现 | 根因 | Red-Green 整改 |
| --- | --- | --- |
| `transcript-*` 合成用户残留 | `test_context_transcript.py` 的 finally 只删会话链，没删 actor | 新增清理后不存在断言，旧实现稳定红；补精确 User 删除后绿，提交 `3f425b6` |
| 常驻 worker 遇连接重置/HTTP 500 退出，PID 陈旧 | `claim_run` 的 transport/server 异常逸出主循环；PID 由外部手工记录 | 单元红测锁定 transient 继续、417 fastfail 和 PID 生命周期；仅吸收 transport 与 500/502/503/504，PID 原子写入/退出清除，提交 `2a0af9a` |
| operation 并发确认偶发 `QueryDeadlockError` HTTP 500 | MariaDB 等待方在赢家提交后拒绝旧锁快照；operation 未像 configuration 一样观察赢家记录 | 确定性注入 locked-row deadlock，旧实现稳定红；回滚后复核 digest，只读返回唯一 durable Execution、没有重放，提交 `d510e5c`；真实并发用例连续 3 次通过 |
| beta/daily OAuth 码交换缺少自动化守护 | 真实授权码交换回归只覆盖 alpha | 同一原生 OAuth 流程参数化覆盖 alpha/beta/daily，三条授权→callback→业务身份→撤权链均通过，提交 `ccce8a1`；聚焦门从 34 增至 36 项 |
| 审计包遗漏全量门前置且两项判据与持久化事实不符 | 没写 worker 停止/队列清理；Run 无 `model` 字段；417 只保留在答复正文 | 审计包改为先核对活跃运行、bootout worker、用 Frappe 原生命令清理合成队列，结束后恢复；模型改核服务端白名单，417 改核最终答复正文 |

本机忽略提交的 LaunchAgent 同时增加 `KeepAlive=true` 和 10 秒节流；真实 SIGTERM 后由 PID `51322` 自动拉起为 `51406`，随后正式重启 PID `72056` 与 `0600` PID 文件一致。该运行态配置不作为产品代码提交，也不能替代 `2a0af9a` 的进程内 transient 行为测试。

整改代码目标提交 `ccce8a1c3fd32418f1482ffefa2cb24429c3939c` 上，先停止常驻 worker、确认活跃运行 0，并在前后用 Frappe `purge-jobs` 精确清理 alpha 合成测试队列：

- 集成：`165 passed in 698.83s`；结束后清理 379 条可重建测试任务；
- 非集成 Python：`121 passed in 44.53s`；
- 前端：`21 files / 162 tests passed in 12.91s`；
- `npm run build`、`git diff --check` 均退出 0，构建后工作副本无制品差异；
- alpha/daily 分别回读：活跃 Run 0、七类制造单据 0、四类 Proposal/Execution 配置产物 0、`transcript-*` 用户 0、原料仓 `100/100`、在制/委外/成品仓 `0/0`；默认队列空；无 Agent 容器和运行临时目录；
- 四站 ping 成功，正式 worker `launchctl state=running`，PID 与 `.runtime/agent-worker.pid` 一致。

以上是执行方整改证据，**不是 Claude 复审通过**。整改改变了代码和测试，审计对象必须从 `d64a2f9` 更新为 `ccce8a1` 后重新复核；T4.2 真实浏览器矩阵仍需具备浏览器控制能力的独立审计方操作。

## C4 第二轮独立审计与整改

Claude Code Opus 第二轮只读复审确认首轮五项 Important 均已修复，但仍裁决 **C4 BLOCKED**：它在未清理 beta 旧任务的共享 Redis 上重跑全量集成，队列总量超过 Frappe 上限，出现 3 项失败；队列已满时，`finally` 中 Translation 删除又触发动态链接任务，导致一条 Translation 暂时残留。复审同时记录了审计包 daily 客户替换值错误、LaunchAgent 只存在于忽略文件、bootout 后 PID 陈旧、锁等待超时未收敛及浏览器未独立复验等问题。

执行方先按 Site、方法和创建时间只读分类：beta 的 234 条均来自两轮审计窗口（210 条 `delete_dynamic_links`、21 条合成 User `create_contact`、3 条 `frappe.ping`），随后才在 beta 容器用 Frappe 原生命令精确清理。对剩余问题完成第二轮 Red-Green 整改：

| 整改 | 行为证据 | 提交 |
| --- | --- | --- |
| 受版本控制的 LaunchAgent 生成器 | 新测试先因模块/运行时字段缺失而红；生成器使用直接 Python argv、`WorkingDirectory`、固定 PATH、`KeepAlive=true`、10 秒节流和 `0600` 原子 plist；真实 SIGTERM 后 PID `16133` 以退出码 0 自动拉起为 `16198`，PID 文件同步为 0600 | `00f3ff3`、`64a26f5` |
| 四站共享队列隔离 | 新测试先因 helper 缺失而红；session fixture 在前后覆盖 alpha/daily/beta/platform 四站。后续再加“先读取任务归属、未知 Site/方法删除前 fastfail”；并覆盖 Frappe 空队列返回空 stdout 的实际行为 | `982011a`、`3eb72e5` |
| 并发确认 QueryTimeout | 确定性注入 `QueryTimeoutError`，旧实现稳定红；与 deadlock 相同，回滚后复核冻结 digest，只回读唯一 durable Execution，不重放业务动作 | `973879f` |
| 宿主/业务 Runtime 边界 | 首次全量门暴露宿主 LaunchAgent 文件误入隔离 Runtime revision，导致 2 项稳定失败；移出业务哈希清单后两个原生 Runtime 链聚焦用例 `2 passed` | `64a26f5` |
| 审计包事实修正 | daily 客户由错误的 `Agent 合成客户` 改为真实 alpha 值 `DSHERP-TEST-CUSTOMER`；全量门改为四站检查/清理、冷启数据库、受控生成并恢复 LaunchAgent | 本文档提交 |

数据库 OOM 另行按 `systematic-debugging` 取证：失败容器 `.State.OOMKilled=true`，但发生在数据库连续运行约四小时、承受多轮全量门之后。保留原卷冷重启并完成 crash recovery 后，第一次原代码冷单轮为 `165 passed`，峰值 `679788544 / 1073741824` bytes、零 OOM 事件；最终整改目标再次冷启后为：

- 集成：`165 passed in 693.62s`；fixture 结束后四站 RQ 队列均为 0；
- 非集成 Python：`127 passed in 48.14s`；
- 前端：`21 files / 162 tests passed in 14.70s`；
- 构建、`git diff --check` 退出 0，构建制品没有意外差异；
- MariaDB cgroup 最终峰值 `433483776 / 1073741824` bytes，`max/oom/oom_kill` 均为 0；未提高内存上限；
- alpha/daily 活跃 Run、七类制造单据、四类 Proposal/Execution 产物、Translation、`transcript-*` 用户均为 0；alpha/daily 原料仓分别以各自公司缩写回读为 `100/100`，在制/委外/成品仓均 `0/0`；beta Translation 和四类 Agent 产物为 0；
- 未调用 DeepSeek，未修改 v15，scheduler 仍停止。

以上是第二轮后的执行方整改证据；第三、第四轮独立结果见下一节。

## C4 第三、第四轮独立审计与核心放行

Claude Code Opus 第三轮以 `3eb72e5` 为起点独立执行 T4.1、T4.2 真实浏览器矩阵、T4.3 既有运行只读回读和全量自动化。它确认四站登录、React 工作台/侧栏生命周期、企业身份隔离和真实模型来源成立；独立浏览器共保存并复核 8 张截图，其 sha256 如下：

| 页面 | sha256 |
| --- | --- |
| platform 三企业入口 | `955bb9bea2ed907f2a6f20d2b99c756240f0a5c32844d184e8dde0af4c49af65` |
| alpha 记录侧栏 | `29d28986228be6a44f1850a4b07bc9651c6582034c27aaa1def3fb19da33318c` |
| alpha 工作台 / SSO 工作台 | `ef837264afe49cd4a7ab0275038efb399e854244c4e075afb36adf6863e8ca49` |
| beta SSO 工作台 | `d24fb4bc8e08b8c7f961092e8f33bcc1bf40bdfd9ebeb9116e536a538314f1aa` |
| daily SSO 工作台 | `19ef262814483fd4295305a12656c88e300711d92cf671e8eecaa5479a5438e2` |
| 生命周期工作台 | `34ef2eb5eec161f9534f6d8824a23e10aee6a81d23db99f99962fd4a13acc319` |
| 生命周期返回 Item | `18a9d5e6e4c39ec5f7bad8f577a78ff2cea6607e088b93b189a3a87b6a368c5f` |

第三轮没有因页面通过而直接放行，而是发现两个阻断级 Important：operation 在 deadlock/timeout 后的 durable 回读绕过 owner/actor/access 复核；configuration 在 deadlock 后的 durable 回读绕过 owner/target 和 digest 复核。执行方先增加普通外部用户和伪造 digest 的确定性测试，旧实现稳定分别抛出错误结果，再让恢复分支复用公开的 `get_proposal` / `get_confirmation` 身份与摘要校验，提交 `11d22ed`。聚焦测试 11 项通过。

修复目标冷启全量门为：集成 `165 passed in 702.09s`、非集成 `127 passed in 43.51s`、前端 `162 passed`、生产构建退出 0；四站队列和测试产物回到 0，正式 worker 恢复。Claude 第四轮独立比较修复前后代码，并重跑 11 项聚焦测试（`11 passed in 56.98s`），最终裁决 I-1、I-2 均为 FIXED，没有新的 Critical 或阻断级 Important，第三轮 C4 阻断解除。它记录的两个 Minor 均为 fail-closed 或测试覆盖粒度建议，不构成当前放行阻断。

该结论只放行 C4 核心技术、浏览器与既有 provider 证据。随后为真实执行 Frappe 调度任务增加 `scheduler-worker` scheduled profile（提交 `1f1b46c`）；Day 0 又根据实际 OOM 修正 scheduler 资源和两项服务的重启契约（提交 `867048f`）。这些冷静期部署变化仍须在跨日证据完成后连同当前 HEAD 做最终独立审计。

## daily 冷静期启动

冷静期最初采用三个连续完整自然日的保守口径。用户在了解覆盖差异后于 2026-09-02 接受 24 小时加速门：从最后一次受控恢复约 2026-09-01 23:12 起连续运行满 24 小时，跨过午夜与完整日周期，至少取得 Day 0、2026-09-02 早间和满 24 小时后三次分时调度、四件套备份及一次性恢复证据，再对当前 HEAD 做最终独立审计。`v16-daily` 心跳检查改在最早满足时长后的 2026-09-02 23:15 执行；禁止调用 DeepSeek、接触生产数据或修改 v15。该口径减少两个日历日的漂移覆盖，不把 24 小时表述为三日等价证据。

Day 0 于 2026-09-01 22:48 启动 scheduled profile：alpha scheduler 明确 disabled，daily scheduler 明确 enabled，`scheduler-worker` 在线并监听 `short,default,long`。22:52 首个自然调度周期产生 18 条 `Scheduled Job Log`，全部 `Complete`；但 scheduler 随后因原 128 MiB 上限 OOM 并以 137 退出，`restart: no` 使它没有恢复。执行方先以部署测试锁定 256 MiB 和 `unless-stopped` 契约、确认旧配置红，再做最小修改；部署测试 9 项、非集成 Python 128 项通过。22:57 重建后跨过 23:00 下一轮及原故障窗口连续运行 7 分钟，scheduler/worker 均无 OOM、重启计数 0，峰值分别为 `123973632` 和 `162099200` 字节；Day 0 累计 36 条日志全部 `Complete`，队列排空。随后先识别并排除 `docker kill` 会触发 Docker“人工停止不重启”语义的无效验证方法，再从容器内部让应用 PID 1 退出；scheduler 和 worker 分别在约 4 秒、1 秒内由同一容器自动恢复，`restart_count=1`。23:12 恢复链又完成 6 条 `Job OK`，队列再次排空且无错误。

同日备份前缀为 `20260901_224857-dsherp-daily_localhost`：database `1195439`、public files `10240`、private files `10240`、site config `483` 字节。第一次验证因误用系统 Python 缺少 `frappe` 而 fastfail，未创建恢复 Site；改用 control 容器内固定解释器后 `verify_daily_backup.py` 退出 0，源站与一次性恢复站快照一致，恢复 Site 随后删除。完整本地工作证据位于忽略提交的 `work/v16-cooldown/2026-09-01-day0.md`。

## C4 终验完成（2026-09-03）

冷静期起点 2026-09-01 23:12（最后一次受控自恢复），终验于 2026-09-03 00:12 完成，连续运行超过 25 小时。

首次终验（2026-09-02 23:15，执行方）FASTFAIL，退出 1。审计方复核确认根因**不是 v16 回归**：仓库工作树被 `git checkout` 切回 `main`（迁移前状态），而运行栈仍为 v16。`main` 版 compose 的 `daily-provision` 挂载 v15 卷，容器读到 v15 的 `site_config.json`，以 v15 库名 `_c07a82853a23736c` 连接 v16 数据库（v16 库名为 `_58bf110913423627`），必然 1045 Access denied；失败发生在读取源站快照阶段，从未触及恢复逻辑。`codex/v16-migration` 分支上的 `daily-provision` 定义本身正确，T2.1 未遗漏该服务。97 个迁移提交全部完好，切回分支后损伤核查全清；宿主 agent-worker（PID 93381）在整个 `main` 窗口期未重启，从未加载 v15 镜像常量，风险未兑现。

环境修正后第二次失败，暴露真实脆弱点：执行方第三次备份使用了 `bench backup --with-files --compress`，产出 v15 时期的 `.tgz` 形态，而 `verify_daily_backup.py` 按 v16 默认输出期望 `.tar`（`3b21d53` 的有意适配，并有 `-files.tgz` 反向守卫）。已以 TDD 加固：`61bf78f` 让缺件 fastfail 在检出同前缀 `.tgz` 时直接指出原因与处置，守卫与 `.tar` 期望均保持不变；运行时探针以 `--compress` 备份复现，报错为「found …-files.tgz instead. Retake the backup without --compress.」

重取未压缩备份 `20260903_001210`（database 1214050、files.tar 10240、private-files.tar 10240、site_config 483 字节）后，以分支定义（无任何镜像或卷覆盖）执行恢复验证，**退出码 0**；输出重定向到文件后再取退出码，规避早间的输出边界坑。`Traceback|SystemExit|Access denied` 命中 0，一次性恢复站已删除，无遗留 run 容器。

终验其余各步：两容器 `StartedAt` 与 24 小时门基线逐字符一致（`RestartCount=1`、`OOMKilled=false`，连续运行未中断）；冷静期累计 474 条 `Scheduled Job Log` 全部 `Complete`；short/default/long 队列均为 0；内存 92.44 / 96.72 MiB（上限 256 MiB，全程震荡非单调增长）；日志异常扫描两容器均 0 命中。三次分时备份恢复分别为 Day 0 `20260901_224857`、早间 `20260902_071617`、终验 `20260903_001210`。

审计范围：`11d22ed..a5b901c` 冷静期部署变化（审计包 §9，PASS）、`bc475f2`/`c862043` 任务 B/C、`2be961c` 任务 D 均已独立核验通过。

**独立性说明**：第三次恢复验证由审计方自行执行并自行报告，不具备执行/审计两方分离；判定依据为既有脚本退出码而非主观判断，方法完整记录可复现。用户于 2026-09-03 明确接受该结果，不再要求补做分离复核。前两次分时检查保持两方结构。

## C5：全项目切换与 v15 移除（2026-09-03）

`codex/v16-migration` 已快进合并入 `main`（46 个提交），合并后全量非集成回归 129 项通过、部署契约 9 项通过、v15 digest 除测试断言常量外零残留。

v15 卷已按精确 dry-run 清单逐名删除，删除前完成归档前置门：九个 tar 均可完整读取（条目数 4593 / 13 / 27 / 5009 / 26 / 5 / 18 / 1 / 69），另有 123568901 字节全库 SQL dump 含 5 条建库语句，归档位于忽略提交的 `work/v16-rollback-20260901/v15-volumes/`。删除前逐卷确认无容器占用；全程未使用通配符、未执行 `prune`、未执行 `down -v`。

已删除九卷：`dsherp-agent-runtime`、`dsherp-validation_beta-logs`、`dsherp-validation_beta-sites`、`dsherp-validation_db-data`、`dsherp-validation_logs`、`dsherp-validation_platform-logs`、`dsherp-validation_platform-sites`、`dsherp-validation_redis-data`、`dsherp-validation_sites`。删除后仅存 v16 九卷。

**v15 镜像取消删除，且不得强删。** `frappe/erpnext@sha256:cf5905…d341`（`frappe/erpnext:v15.119.3`）被本机既有 **AgenERP 项目**共用，11 个容器引用，其中 `agenerp-agenerp-serve-1`、`agenerp-backend-1`、`agenerp-frontend-1`、`agenerp-scheduler-1` 正在 healthy 运行。按 [AGENTS](../../AGENTS.md)「不修改现有 AgenERP」，该镜像不属于 dsherp 可处置资产，`docker image rm` 的冲突拒绝是正确结果，不使用 `--force`。已核实 AgenERP 容器引用的 dsherp 卷数为 0，本次删除未波及；其 `queue-long`/`queue-short`/`websocket` 的重启循环 RestartCount 已达 1868/1868/2017，属该项目长期既有状态，与本次操作无关，未做任何干预。

删除后四站以正确 Host 头核验：validation 18082、platform 18083、preview 18085、daily 18086 的 `api/method/ping` 均返回 `pong`。

回滚材料（`work/v16-rollback-20260901/`）在此保留：v15 卷已删，tar 归档与 SQL dump 是唯一恢复路径。

## 当前状态与剩余边界

C4 与 C5 均已完成：24 小时冷静期通过、三次分时备份恢复取证、当前 HEAD 全量独立审计通过、`main` 已切换到 v16、v15 九卷按精确清单逐名删除。v15 镜像因与 AgenERP 共用而取消删除（见上节），这是终态决定，不是待办。

**仍然成立的边界**：

1. 全部证据来自本机隔离合成四站，**不是生产环境**，不含生产租户或真实企业数据。因此不得宣称用户可见上线或生产可用。
2. 冷静期只覆盖 24 小时。`Weekly`(2)、`Weekly Long`(3)、`Monthly`(1)、`Monthly Long`(2) 共 8 类调度作业需 7~30 天才触发，**任何版本的冷静期门都未覆盖**（原三日门同样不覆盖）。这不是本次新增豁免，但也不得当作已验证；须在常规运行中另行观察。
3. v15 回滚材料是唯一恢复路径：v15 卷已删除，`work/v16-rollback-20260901/` 下的九个 tar 归档与 123568901 字节 SQL dump 不得清理。
4. `verify_daily_backup.py` 依赖「备份未压缩」这一不变量。`61bf78f` 已让违反时给出可诊断 fastfail，但备份命令本身仍由调用方决定；自动化调度备份走 v16 默认输出，人工补取备份时不要加 `--compress`。
