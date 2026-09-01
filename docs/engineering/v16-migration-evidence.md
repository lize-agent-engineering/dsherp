# ERPNext/Frappe v16 迁移证据

日期：2026-09-01

分支：`codex/v16-migration`

## 结论边界

本记录只证明本机隔离合成环境中的版本切换、四站 fresh provision、自动化回归、固定 Runtime 本地模型替身链、制造行为重验和本轮应用内真实浏览器 UI 验收。它不证明生产部署、生产租户可用或真实 DeepSeek 页面效果。

截至本记录，C0–C3 已由执行方完成，T4.1 已执行并回读清理结果，T4.2 浏览器矩阵和 T4.3 真实 DeepSeek 只读矩阵均已完成并落档；若干天冷静期、C5 文档全量更新与 v15 移除仍未完成。C4 放行仍要求独立审计，不能以本文件的执行方自报代替。

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
- fresh 重建前的 `.runtime` 完整备份位于忽略提交的 `work/v16-rollback-20260901/runtime`，旧现场运行状态位于 `work/v16-rollback-20260901/retired-live-runtime`。它们在冷静期和 v15 删除授权前不得清理。

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

## 当前待完成门槛

1. **C4 独立审计与冷静期**：T4.1–T4.3 均仍待独立审计；执行方已准备 [`v16-c4-independent-audit-packet.md`](v16-c4-independent-audit-packet.md)，该入口不构成审计结论。审计放行后，daily 还需若干天调度与备份正常证据。
2. **C5**：README、runtime baseline、开发 skills 和最终数字尚未更新；v15 归档、dry-run、用户确认和逐名删除尚未执行。

因此当前不得宣称 v16 迁移整体完成、用户可见上线或可删除 v15。
