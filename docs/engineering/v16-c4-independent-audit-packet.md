# v16 C4 独立审计包

日期：2026-09-01。

本文件由迁移执行方准备，只提供独立审计入口，**不构成 C4 通过结论**。审计方必须自行检查仓库、重跑自动化、操作真实浏览器并回读 ERP/Agent 数据；不能采信执行方在 [`v16-migration-evidence.md`](v16-migration-evidence.md) 中的自报。

## 1. 审计对象与边界

- 原始执行证据提交：`d64a2f98156cdc77e004c734f2a7c32d10f2e8b7`；首轮 Claude 审计结论为 C4 BLOCKED。
- 首轮整改代码/测试固定提交：`ccce8a1c3fd32418f1482ffefa2cb24429c3939c`。后续文档提交只记录审计与整改事实；若产品代码、测试或部署契约再变化，必须再次更新固定对象。
- 授权记录：`390438a03a754463b6cf2e3d4b9163564c054cd0`；OAuth 修复：`d94efcee002216ea7443b95e5ba71d4d7b3454a8`；浏览器证据：`28ea7624b53530d177d6106f05491eff3b4daf4f`；真实模型证据：`d64a2f98156cdc77e004c734f2a7c32d10f2e8b7`。
- 审计环境仅为本机隔离合成四站，不是生产环境，不包含生产租户或真实企业数据。
- 复审必须同时检查原始证据和 `d64a2f9..ccce8a1` 整改增量，不能只重跑绿灯而跳过首轮五项 Important 的根因与修复。
- v15 卷、冷静期回滚材料和本地凭证均不得修改或删除。任何真实模型补跑都需要新的费用授权；本审计默认只读核对既有运行。

先固定对象并确认工作副本：

```bash
git status --short
git show -s --format='%H %P %cI %s' ccce8a1 3f425b6 2a0af9a d510e5c d64a2f9
git diff --check d64a2f9..ccce8a1
git diff --stat d64a2f9..ccce8a1
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
| `Agent 合成客户` | `日常 Agent 合成客户` |

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

全量集成会在隔离合成站点创建并 finally 删除大量记录，但 Frappe 的动态链接清理任务仍会进入默认队列。审计方在运行前必须确认三站活跃 Run 均为 0，bootout 常驻 worker，并确认队列任务只属于本轮合成测试后使用 Frappe 原生命令清理；不得在生产站或队列归属不明时执行：

```bash
launchctl bootout "gui/$(id -u)/com.dsherp.agent-worker-v16"
docker exec dsherp-validation-backend-1 \
  bench purge-jobs --site dsherp-validation.localhost --queue default
```

随后至少执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/integration -q --tb=short
PYTHONPATH=. .venv/bin/python -m pytest tests --ignore=tests/integration -q --tb=short
(cd frontend && npm test)
(cd frontend && npm run build)
git diff --check
```

无论测试成功或失败，都应再次用同一 Frappe 命令清理本轮合成任务，再从 `.runtime/com.dsherp.agent-worker-v16.plist` 恢复 LaunchAgent，并核对 `state=running`、keepalive、真实 PID 与 PID 文件。收集数量以固定提交实际结果为准；整改执行方结果为 integration 165、非集成 121、前端 162，不能直接采信。构建后应确认 `frappe_app/dsherp_bridge/public` 制品来自当前 `frontend/src` 且工作副本没有意外差异；不能只看命令退出码。

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

C4 技术审计通过后仍不能立即进入 C5。daily 必须跨若干自然日留下 scheduler 正常运行和每日四件套备份可恢复证据；每一天至少记录备份前缀、四件大小、`verify_daily_backup.py` 退出 0、一次性恢复 Site 已删除及当天异常。当前只有 2026-09-01 基线，scheduler 已停止且没有跨日自动任务，**冷静期尚未开始，也未满足若干天要求**。

冷静期完成前不得更新“迁移完成”状态，不得归档或删除 v15。即使冷静期完成，v15 移除仍需单独用户授权、逐卷 dry-run 和最终确认。
