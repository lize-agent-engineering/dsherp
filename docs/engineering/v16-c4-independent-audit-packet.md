# v16 C4 独立审计包

日期：2026-09-01。

本文件由迁移执行方准备，只提供独立审计入口，**不构成 C4 通过结论**。审计方必须自行检查仓库、重跑自动化、操作真实浏览器并回读 ERP/Agent 数据；不能采信执行方在 [`v16-migration-evidence.md`](v16-migration-evidence.md) 中的自报。

## 1. 审计对象与边界

- 原始执行证据提交：`d64a2f98156cdc77e004c734f2a7c32d10f2e8b7`；Claude 首轮和第二轮审计结论均为 C4 BLOCKED。
- 第三轮起点为第二轮整改提交 `3eb72e569c120748054747bb7ebca86b4a09cdf9`。第三轮独立自动化、真实浏览器与既有模型回读通过后，新发现 operation/configuration 并发恢复身份校验缺口；核心修复固定提交为 `11d22ed`，第四轮聚焦复审确认两项均 FIXED，且无新的 Critical 或阻断级 Important。
- 当前冷静期部署代码为 `867048f`：`1f1b46c` 新增 scheduled profile 的队列 worker，Day 0 首轮又暴露 scheduler 128 MiB OOM 和 `restart: no`，随后以红绿测试把 scheduler 调整为 256 MiB，并让 scheduler/worker 使用 `unless-stopped`。这些变化须在三天冷静期完成后连同证据做最终独立审计；本文件当前只记录审计入口，不把 Day 0 外推成整体 C4 通过。
- 授权记录：`390438a03a754463b6cf2e3d4b9163564c054cd0`；OAuth 修复：`d94efcee002216ea7443b95e5ba71d4d7b3454a8`；浏览器证据：`28ea7624b53530d177d6106f05491eff3b4daf4f`；真实模型证据：`d64a2f98156cdc77e004c734f2a7c32d10f2e8b7`。
- 审计环境仅为本机隔离合成四站，不是生产环境，不包含生产租户或真实企业数据。
- 最终复审必须同时检查原始证据、`d64a2f9..ccce8a1` 首轮整改、`ccce8a1..3eb72e5` 第二轮整改、`3eb72e5..11d22ed` 第三轮阻断修复及 `11d22ed..HEAD` 冷静期部署变化，不能只重跑绿灯而跳过根因与修复。
- v15 卷、冷静期回滚材料和本地凭证均不得修改或删除。任何真实模型补跑都需要新的费用授权；本审计默认只读核对既有运行。

先固定对象并确认工作副本：

```bash
git status --short
git show -s --format='%H %P %cI %s' \
  HEAD 867048f 1f1b46c 11d22ed 3eb72e5 64a26f5 973879f 982011a 00f3ff3 ccce8a1 d64a2f9
git diff --check d64a2f9..HEAD
git diff --stat d64a2f9..HEAD
```

审计应在独立 checkout/worktree 执行，不应在执行方当前工作副本上签发结论。

## 2. 运行环境只读预检

```bash
docker compose -p dsherp-validation -f infra/compose.validation.yml ps
for url in \
  http://dsherp-validation.localhost:18082 \
  http://platform.localhost:18083 \
  http://preview.localhost:18085 \
  http://daily.localhost:18086
do
  curl -fsS "$url/api/method/ping"
done
launchctl print "gui/$(id -u)/com.dsherp.agent-worker-v16"
docker ps --format '{{.Names}}' | rg '^dsherp-context-' || true
find /tmp -maxdepth 1 -type d -name 'context-run-*' -print
docker volume ls --format '{{.Name}}' | sort | rg 'dsherp'
```

判据：四站 ping 成功；固定 v16 worker 存活；没有按需 Agent 容器或运行临时目录遗留；v15 与 v16 卷同时存在。`pid` 文件、配置 dump 或容器存在本身都不能替代上述运行态核对。

复审还必须确认 `launchctl` 属性含 `keepalive`，真实进程 PID 与 `0600` `.runtime/agent-worker.pid` 一致；`tests/test_context_worker.py` 中 transport/500 继续、417 fastfail、PID 生命周期行为实际通过。

## 3. T4.1 制造闭环复核

### 3.1 固定测试组

以下五个唯一行为测试必须在 v16 alpha 上 fresh 重跑并全绿：

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/integration/test_work_order_operations.py \
  tests/integration/test_purchase_operations.py \
  tests/integration/test_subcontracting_operations.py \
  tests/integration/test_delivery_operations.py \
  -q --tb=short
```

daily 重验不能把 alpha 结果复述为本站结果。审计方应在 `work/` 中创建不提交的机械副本，只替换固定 Site、公司与客户：

| alpha 值 | daily 值 |
| --- | --- |
| `dsherp-validation.localhost` | `dsherp-daily.localhost` |
| `DSHERP 原生验收测试公司` | `DSHERP 日常合成企业` |
| `DSHERP-TEST-CUSTOMER` | `日常 Agent 合成客户` |

除这三项及临时文件路径外发生的任何修改都应中止审计，而不是扩展兜底。临时副本和 `pyc` 在取证后删除，不进入 Git。

### 3.2 finally 后独立回读

审计方需要从两个 Site 的数据库分别确认：

- `DS Model Run.status in (Queued, Running, Waiting)` 为 0；
- Work Order、Stock Entry、Purchase Order、Purchase Receipt、Subcontracting Order、Subcontracting Receipt、Delivery Note 均为 0；
- `DSHERP-MFG-SYN-RM` 在原料仓 `actual_qty/projected_qty=100/100`；
- 原料在制仓、委外仓以及 `DSHERP-MFG-SYN-FG` 成品仓均为 `0/0`；
- 测试创建的临时用户、Conversation、Proposal、Execution 和后台任务均已按精确标识清理。

只接受 ERP 数据库/业务方法回读，不以 pytest 最终绿灯替代清理和库存事实。

## 4. T4.2 真实浏览器复核

审计方必须使用真实浏览器独立重走下列矩阵；静态代码、jsdom、已有截图或执行方录屏均不能代替：

| 检查项 | 独立判据 |
| --- | --- |
| alpha 工作台 | `/desk/dsherp-agent` React 会话栏、对话区和来源入口实际渲染 |
| Item 侧栏 | `Item / DSHERP-HITL-ITEM` 页只有一个“打开 Agent”；侧栏上下文精确为当前记录 |
| 生命周期/互斥 | 工作台页全局侧栏入口为 0、工作台 main 为 1；回到 Item 页入口恢复为 1 |
| platform | 普通成员登录后只见三个 Ready 企业 |
| alpha SSO | 最终身份 `dsherp-reader@example.invalid`，落到 `/desk/dsherp-agent` |
| beta SSO | 最终身份 `beta-reader@example.invalid`，落到 `/desk/dsherp-agent` |
| daily SSO | 最终身份 `daily-operator@example.invalid`，工作台可访问 |

不得点击发送、创建运行或修改 ERP 数据。相关自动化回归必须另行重跑：

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_v16_framework_adaptations.py \
  tests/integration/test_platform_identity.py \
  tests/integration/test_desk_sso.py \
  -q --tb=short
```

执行方证据文件只用于完整性比对，当前固定哈希如下：

```text
a2f3ca149f78dfbef6201bfbfec101d50f0981e427b2ef7894b7bcc0257736ca  t4.2-alpha-record-sidebar.jpg
004ef1d7c8b2ef7af10e2f949ffc7dbcb59cff9f374ee01189d6e7348e50fa17  t4.2-alpha-sso-workbench.jpg
b7687c659713d0e14a7a2810dedb464041e314bbf1510c666ef7a7396231958c  t4.2-alpha-workbench.jpg
8d7cd2dec4615f952a9b6df98e2df481d3549e691643cb642ba67c24f5d13c5b  t4.2-beta-sso-workbench.jpg
b0124270885dffa39cd1b4a1e3afb117cffce39075d481000f7f5b8da6eaa437  t4.2-daily-sso-workbench.jpg
e7e858190476a4cf7f67e46eb09c46d910b06dc4808000487a982090b5b38f2d  t4.2-platform-enterprises.jpg
```

可用 `shasum -a 256 docs/engineering/evidence/v16/t4.2-*.jpg` 复核；哈希相同只证明文件未变，不证明浏览器行为成立。

## 5. T4.3 真实 DeepSeek 只读证据复核

审计方默认不发起新的付费调用，只核对既有三个不可变运行及其持久化来源：

| Site / 用例 | Run | 预期状态 | 调用/输入字节/预留输出 |
| --- | --- | --- | --- |
| alpha 无匹配校准 | `b857f9613c9921638372a20dcae5c3aac86b4a414cd76de4f148df2ccf9162dc` | Succeeded | `6 / 194332 / 12288` |
| alpha 正向读取 | `54f2c17d9f318fa2d2292faf2f4f1a6d42a3acfff84413d0d6ffc394900a6749` | Succeeded | `3 / 53983 / 6144` |
| daily 企业隔离 | `c50dad0eb6fb54a446077a3dd1462257349ab980d4c957181b840c8d7a5a373a` | Succeeded | `5 / 141520 / 10240` |

必须分别从 alpha 与 daily 数据库核对：

- `DS Model Run` 没有 `model` 字段；模型判据改为核对 `frappe_app/dsherp_bridge/context_execution.py` 对 provider=`deepseek-official`、model=`deepseek-v4-flash` 的服务端硬白名单，并确认三个成功 Run 的 `model_calls>0`、状态与预算计数符合表格；
- alpha 正向 Run 的成功 source 唯一命中 Item `DSHERP-HITL-ITEM`，中文名为“`HITL 确认前物料`”，版本为 `2026-09-01 16:27:47.287925`；与当前 ERP 记录一致；
- daily Run 的成功 sources 中 records 为空；daily 按同一中文名查询也不存在；不得出现 alpha 的记录、版本或来源；
- 两站活跃 Run 为 0；DS Operation Proposal、DS Execution Record、DS Configuration Confirmation、DS Configuration Execution 均为 0；
- 运行中的 HTTP 417 模糊/文本查询拒绝保留在最终答复正文中；结构化 `Run.error` 和 `sources[].error` 为空。最终答复只引用成功的精确来源；不能把正文中的拒绝尝试删掉后宣称零偏差；
- 预留输出 token 只是预算账本，不是实际输出 token 或精确费用。

浏览器截图哈希：

```text
dc3bbb504485a647a30d23c6239573a40ff1602a5259bb0097e5018e1f0d2ec5  t4.3-alpha-real-item-read.jpg
761b3ba3ad00663d2a11411e161933ccedd56849b57b00ec8152bb40c7e1e180  t4.3-daily-enterprise-isolation.jpg
```

可用 `shasum -a 256 docs/engineering/evidence/v16/t4.3-*.jpg` 复核。若审计方认为必须补跑真实 provider，应先停止并取得新的费用授权；不能沿用执行方先前的一次性授权。

## 6. 全量回归与构建

全量集成会在隔离合成站点创建并 finally 删除大量记录，但 Frappe 的动态链接清理任务仍会进入四站共用的 Redis 默认队列。审计方在运行前必须确认三业务站活跃 Run 均为 0，bootout 常驻 worker，并用 `get_jobs` 检查每个 Site 和方法。`infra.v16_integration_queue` 只接受四个固定合成 Site 与 `frappe.ping`、`create_contact`、`delete_dynamic_links` 三类可重建任务；遇到其他 Site/方法会在删除前 fastfail。不得在生产站或队列归属不明时执行：

```bash
launchctl bootout "gui/$(id -u)/com.dsherp.agent-worker-v16"
docker exec dsherp-validation-backend-1 \
  bench --site dsherp-validation.localhost execute \
  frappe.utils.background_jobs.get_jobs --kwargs '{"key":"method"}'
PYTHONPATH=. .venv/bin/python -c \
  'from infra.v16_integration_queue import purge_validation_jobs; print(purge_validation_jobs())'

# 保留数据库卷，只冷重启 MariaDB 进程，避免多轮审计造成的累计内存替代单轮容量事实。
docker compose -p dsherp-validation -f infra/compose.validation.yml restart db
until docker exec dsherp-validation-db-1 mariadb-admin ping --silent; do sleep 1; done
```

首次第二轮复跑前，数据库容器已连续运行约四小时并承受多套全量门，最终因 1 GiB cgroup 上限被 OOM kill；卷内数据库经 Aria/InnoDB crash recovery 完整恢复。冷重启后同一全量集成单轮通过，最终整改目标的 cgroup 峰值为 `433483776 / 1073741824` bytes，`max=0`、`oom=0`、`oom_kill=0`。因此不提高容量，而把冷启动作为独立审计可复现前置；数据库状态或卷不得删除。

随后至少执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/integration -q --tb=short
PYTHONPATH=. .venv/bin/python -m pytest tests --ignore=tests/integration -q --tb=short
(cd frontend && npm test)
(cd frontend && npm run build)
git diff --check
```

集成 session fixture 无论测试成功或正常失败都会先分类、再精确清理四站任务；硬中断后仍须人工重跑同一检查/清理命令。恢复 worker 必须从受版本控制的生成器重建 LaunchAgent，不能依赖执行方手写的忽略文件：

```bash
.venv/bin/python infra/render_context_worker_launch_agent.py
launchctl bootstrap "gui/$(id -u)" .runtime/com.dsherp.agent-worker-v16.plist
```

随后核对 `state=running`、`keepalive`、真实 PID 与 `0600` PID 文件。收集数量以固定提交实际结果为准；最终整改执行方结果为 integration 165、非集成 127、前端 162，不能直接采信。构建后应确认 `frappe_app/dsherp_bridge/public` 制品来自当前 `frontend/src` 且工作副本没有意外差异；不能只看命令退出码。

## 7. 审计裁决表

审计方应在独立结论中逐项填写实际命令、时间、输出摘要和证据路径：

| 项目 | PASS/FAIL | 独立证据 |
| --- | --- | --- |
| 固定提交与工作副本 |  |  |
| 四站运行态、worker、容器和卷 |  |  |
| T4.1 alpha 五用例与 finally 回读 |  |  |
| T4.1 daily 五用例与 finally 回读 |  |  |
| T4.2 真实浏览器矩阵 |  |  |
| T4.2 自动化回归 |  |  |
| T4.3 三个 Run、sources、ERP 隔离与零写入 |  |  |
| 全量 Python、前端与构建 |  |  |
| Critical / Important 缺陷 |  |  |
| C4 独立审计总裁决 |  |  |

任何一项 FAIL、未执行或证据只能来自执行方自报时，C4 均不得放行。

## 8. 冷静期与 C5 边界

C4 核心技术和浏览器审计通过后仍不能立即进入 C5。daily 必须跨若干自然日留下 scheduler 正常运行和每日四件套备份可恢复证据；每一天至少记录备份前缀、四件大小、`verify_daily_backup.py` 退出 0、一次性恢复 Site 已删除及当天异常。

冷静期已于 2026-09-01 启动，但该日只计 Day 0，不计完整自然日：alpha scheduler disabled、daily enabled；首轮任务全部 Complete 后 scheduler 曾因 128 MiB OOM 退出且没有自动恢复，执行方以 TDD 修复为 256 MiB 与 `unless-stopped`，重建后跨过下一轮和原故障窗口无 OOM。Day 0 最终累计 36 条 Scheduled Job Log 全部 Complete、队列排空；备份 `20260901_224857-dsherp-daily_localhost` 四件套恢复验证退出 0，一次性恢复 Site 已删除。完整工作记录位于 `work/v16-cooldown/2026-09-01-day0.md`。通过门仍是 2026-09-02、2026-09-03、2026-09-04 三个连续完整自然日，且结束后须审计当前 HEAD。

冷静期完成前不得更新“迁移完成”状态，不得归档或删除 v15。即使冷静期完成，v15 移除仍需单独用户授权、逐卷 dry-run 和最终确认。
