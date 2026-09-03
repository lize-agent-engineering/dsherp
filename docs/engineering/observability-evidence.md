# 可观测与失败回放证据

日期：2026-09-03。当前范围：C0 至 C2，固定 Runtime 事件形状、隔离合成站历史运行基线、三站迁移事实和本地模型替身事件链。本文件分别记录探针、站点事实和后续检查点证据；本地模型替身结果不等同于真实 provider 证据，也不代表生产部署。

## C0：固定 Runtime 事件形状探针

探针使用 `tests/conftest.py` 的 `model_server` 本地 SSE 替身和项目固定的 DSH SDK/Runtime `0.1.1rc1`，没有读取项目 `.env`，没有调用真实 provider。执行命令：

```sh
.venv/bin/python infra/probe_observability/t0_1_runtime_events.py work/probe-events
```

命令退出码为 0，`finish_reason` 为 `completed`。`RunResult.events` 的事件类型和顶层键如下；表内只记录键名，不记录运行值。

| 事件类型 | 顶层键 |
| --- | --- |
| `agent/inbox/spliced` | `type`, `seq`, `time`, `data` |
| `assistant/chunk` | `type`, `seq`, `time`, `data` |
| `assistant/message` | `type`, `seq`, `time`, `data`, `sourceEventSeqs`, `surfaceOp` |
| `request/context` | `type`, `seq`, `time`, `data` |
| `request/header` | `type`, `seq`, `time`, `data` |
| `session/title` | `type`, `seq`, `time`, `data` |
| `step/end` | `type`, `seq`, `time`, `data` |
| `step/start` | `type`, `seq`, `time`, `data` |
| `tool/call` | `type`, `seq`, `time`, `data` |
| `tool/result` | `type`, `seq`, `time`, `data`, `sourceEventSeqs`, `surfaceOp` |
| `turn/end` | `type`, `seq`, `time`, `data` |
| `turn/start` | `type`, `seq`, `time`, `data` |
| `user/message` | `type`, `seq`, `time`, `data`, `surfaceOp` |

工具事件的内部键名：

- `tool/call.data`：`turn`, `step`, `callId`, `name`, `arguments`。
- `tool/result.data`：`turn`, `step`, `message`, `error`。
- `tool/result.data.message`：`source`, `content`, `role`, `id`。
- `tool/result.data.message.content[]`：`type`, `toolCallId`, `content`, `isError`。

通知方法实测为 `session.event`、`session.status`。

### usage 结论

固定 Runtime 的根会话事件不携带 `usage`：上述 13 类事件的已观测形状都没有 input/output token 计数键。T2.4 不得从 `RunResult.events` 推断 token 用量；它只在 `llm/stream` 边界记录 `finish` 及其 chunk 顶层可观测键名，若该固定 Runtime 的 finish chunk 没有实际 usage，则持久事件中的 `usage` 必须为 `null`，不得估算。

## C0：历史运行基线

2026-09-03 只读盘点前，`dsherp-validation` compose 的既有容器均处于 `Exited (255)`；执行 `docker compose -f infra/compose.validation.yml -p dsherp-validation up -d` 恢复隔离验证栈后，`backend`、`beta-backend`、`db`、`frontend`、`platform-backend`、`platform-frontend`、`redis` 均为 `running`。alpha 与 daily Site 共用 `dsherp-validation-backend-1`，盘点通过容器内 Frappe ORM 读取 `DS Model Run`，没有修改运行记录。

### alpha：`dsherp-validation.localhost`

| 指标 | 数量 |
| --- | ---: |
| 总运行 | 54 |
| `Succeeded` | 42 |
| `Failed` | 12 |

Failed 的 error 前缀分布：

| error 前缀 | 数量 |
| --- | ---: |
| `业务运行失败：TimeoutExpired` | 1 |
| `业务运行失败：RuntimeError` | 9 |
| `运行已过期，未自动重试` | 1 |
| `当前用户已无法读取会话来源` | 1 |

### daily：`dsherp-daily.localhost`

| 指标 | 数量 |
| --- | ---: |
| 总运行 | 1 |
| `Succeeded` | 1 |
| `Failed` | 0 |

daily 没有 Failed error 前缀。C4 的 T4.3 导出基准因此为 alpha 12 条、daily 0 条，合计 12 条 Failed 运行；导出时仍须重新读取站点并报告与本基线之间是否出现新增失败运行。

## C1：三站事件表迁移

`DS Run Event` 属于 `dsherp_bridge`，因此 alpha、daily、beta 三个 Site 都必须迁移。2026-09-03 的 C1 整改实测如下：

| Site | 容器 | `bench migrate` 退出码 | `table_exists('DS Run Event')` | 表检查退出码 |
| --- | --- | ---: | --- | ---: |
| alpha `dsherp-validation.localhost` | `dsherp-validation-backend-1` | 0 | `True` | 0 |
| daily `dsherp-daily.localhost` | `dsherp-validation-backend-1` | 0 | `True` | 0 |
| beta `dsherp-beta.localhost` | `dsherp-validation-beta-backend-1` | 0 | `True` | 0 |

最终复核从整改后的 HEAD 串行执行 alpha、daily、beta 迁移，三次退出码均为 0；每站迁移后立即由对应 bench 的 long burst worker 完成 `build_index_for_all_routes`。最终共享 Redis 的 default/long 队列均为 `queued=0, failed=0`，没有放宽测试队列卫生规则。后续每次 DocType 或 Report 变更都必须重新迁移并逐站记录退出码。

复核过程中曾错误地并行启动两个 bench 的 long worker；由于两者共享 Redis 且队列同名，shared worker 误取 beta job，beta worker误取 daily job，分别因目标 Site 不在对应容器而失败。只删除了本次产生且逐项核对 Site 与 method 的两个失败 job：`dsherp-beta.localhost||01948341-1af4-49f0-8ca5-937d6d900cd2`、`dsherp-daily.localhost||c9f15813-bcc5-4b73-8db3-1e3c5ee851c9`；随后按“逐站迁移后立即由该站所在 bench 消费”重跑，最终三条索引任务均 `Job OK`。

### 当前不可变边界

`DS Run Event` 的只增不删不改目前是应用层约定：Document 层的 `validate` 拒绝更新、`on_trash` 拒绝删除，但 `frappe.db.delete` 等数据库直写仍可绕过。合成测试仅为按外键顺序清理数据而使用该直写；数据库层约束或触发器留待计划 4 的 G7 收口，C1 不把应用层约定表述为数据库强制保证。

## C2：runner/worker 回写与结构化日志

### 测试门禁

`.venv/bin/python -m pytest tests --ignore=tests/integration -q` 在最终提交候选上退出 0：

```text
........................................................................ [ 50%]
.......................................................................  [100%]
143 passed in 51.57s
```

计划已由提交 `860f593` 把 Node v26 不支持的目录参数修订为测试文件通配。最终执行 `node --test runtime/*.test.cjs` 退出 0：`tests 8, pass 8, fail 0, duration_ms 61.943584`。

完整门禁首次还发现 C1 新增的 `DS Run Event` 使用 `sort_order: ASC`，违反仓库全部自定义 DocType 的 `creation DESC` 契约；先取得 `1 failed, 15 passed`，再改为 `DESC` 后取得 `16 passed`。这是 C1 元数据遗漏，不属于 T2 行为。变更后按全局约束串行迁移 alpha、daily、beta，三次 `bench migrate` 退出码均为 0；三站 `table_exists('DS Run Event')` 均返回 `true`、退出码均为 0，DocType 的 `sort_order` 均为 `DESC`。每站迁移产生的 long queue 搜索索引任务均由对应 bench 串行 burst worker 完成并显示 `Job OK`。

### 本地替身链路与 usage 结论

验证前 `launchctl` 未加载 `com.dsherp.agent-worker-v16`，`.runtime/agent-worker.pid` 不存在，进程表没有 `dsherp.context_worker`。首次链路在新加的 HTTP 读取核验处失败：已挂载的新源码包含 `list_run_events`，但 alpha Web 进程仍加载旧模块，返回 HTTP 417 和 `has no attribute 'list_run_events'`；fixture 正常清理该运行。重启 alpha/beta backend 并核验 alpha HTTP ping 200、beta 容器内 `frappe.ping` 为 `pong` 后，原样执行：

```text
.venv/bin/python -m pytest tests/integration/test_context_worker_chain.py -q -s
1 passed in 94.55s (0:01:34)
```

测试在 fixture 清理前通过所有者的 `list_run_events` 实际读取两次运行；两条结果均按 `seq` 返回以下完整 kind 序列：

```text
queued, claimed, runtime_started, model_call_reserved, model_response, tool_call,
model_call_reserved, model_response, runtime_tool_call, tool_result, turn_end,
container_finished, finished
```

两次运行各有两条 `model_response`，每条 payload 都是：

```json
{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"}
```

结论：固定 Runtime 的 finish chunk 只有 `type`、`reason` 顶层键，没有 `usage`；C2 如实记录 `usage: null`，不估算 token。模型地址固定为容器内本地替身 `127.0.0.1:38127/v1`，没有调用真实 provider。事件顺序也表明服务端 `tool_call` 在 runner 汇总 Runtime 通知形成的 `runtime_tool_call` 之前入库；两类事件都存在，未改写实际 `seq` 来迎合展示顺序。

### C2 审计整改

观测回写超时先以三条用例取得红灯：`timeout` 被错误放入业务 JSON；runner 没有受限 flush 入口；worker 的回写继承 client 的 30 秒超时。实现后，`context_mcp.post` 的可选 timeout 只传给 httpx，runner 与 worker 仅在 `record_run_event` 使用 5 秒，其他 RPC 不变。目标三条为 `3 passed in 1.77s`，相关四文件回归为 `32 passed in 8.05s`。MockTransport 抛 `httpx.TimeoutException` 时，flush 的 `error` 为 `TimeoutException`，模型与 worker 的成功结果均保持 Succeeded，总耗时小于 10 秒。

事件映射异常用例先确认 `TypeError` 会逃逸并使成功运行失败；局部捕获后目标为 `1 passed in 1.00s`，相关 runner/runtime/model guard 为 `20 passed in 18.34s`。stderr 只写 `DSHERP_DIAGNOSTIC {"type":"EventMappingFailed","error":"TypeError"}`，记录中没有 `runtime_failed`，最终运行仍为 Succeeded。

`rg` 对 `tests/integration` 的完整清单只有 `test_context_worker_chain.py` 两处 `worker.run_once`，没有其他文件调用 `worker.run_once` 或 `poll_once`。该测试开头现检查 `.runtime/agent-worker.pid` 对应 PID：PID `87290` 存活时，实测 fastfail 为 `常驻 worker 正在运行，会用真实 provider 抢先领取测试运行；先停止它再跑`，未创建测试运行；测试本身不停止 worker。显式 bootout 并复核 PID 文件不存在、进程计数 0 后，`-s` 运行得到上述 1 passed 与 `C2_EVENT_EVIDENCE`。

### worker 状态与 stderr

旧日志可恢复地轮转到 `/Users/lize/Library/Logs/dsherp-agent-worker-v16.log.pre-c2-20260903140303`。替身测试 stderr 写入新日志；恢复前又以数据库只读 SQL 核验 alpha、daily、beta 的 Queued/Running/Cancelling 计数均为 `[[0]]`。整改验收结束后，从 `infra/render_context_worker_launch_agent.py` 重建 plist 并只 bootstrap 一次。最终实态为 launchd `state = running`、`properties = keepalive | runatload`，PID 文件权限 `-rw-------`，PID `202` 与 launchd/进程表一致，`dsherp.context_worker` 精确计数为 1。

日志逐行 JSON 校验命令与输出：

```text
.venv/bin/python - <<'PY'
import json
from pathlib import Path
lines=Path('/Users/lize/Library/Logs/dsherp-agent-worker-v16.log').read_text().splitlines()
for line in lines: json.loads(line)
print(f'json_lines={len(lines)} invalid=0')
PY
json_lines=7 invalid=0
```

不把真实 provider key 展开到命令行或输出，使用进程替换把本地配置值仅送入 grep；服务凭据同样复核：

```text
grep -F -c -f <(.venv/bin/python -c 'from pathlib import Path; from dsherp.runtime_host import load_settings; print(load_settings(Path(".env"))["DEEPSEEK_API_KEY"])') /Users/lize/Library/Logs/dsherp-agent-worker-v16.log
0
provider_grep_exit=1
grep -F -c -f <(jq -r .api_secret .runtime/context-worker.json) /Users/lize/Library/Logs/dsherp-agent-worker-v16.log
0
service_secret_grep_exit=1
```

七行中有一条 `event_writeback_failed/BusinessRuntimeError`，对应首次链路遇到旧 Web 进程缺少读取/回写端点时的预期结构化诊断；该次运行仍由 fixture 精确清理。其余为三次合成运行的 `claimed`/`container_finished`。日志无明文凭据、无非 JSON 行。

## C3：运维快照、指标与告警

### T3.1 三站迁移与五分钟 scheduler

新增 `DS Ops Snapshot` 后先按 alpha、daily、beta 串行执行完整 migrate，三站退出码均为 0，且三站 `table_exists('DS Ops Snapshot')` 都返回 `true`。迁移生成的三条 `build_index_for_all_routes` 先按站点直接执行成功；确认共享 Redis 只含这三条可重建任务后，再逐站精确移除待处理副本，最终队列为空。全量门禁随后要求快照 DocType 遵守仓库统一的 `creation DESC` 元数据；修正后再次以 `--skip-search-index` 串行迁移，最终三站退出码仍为：

```text
ALPHA_MIGRATE_EXIT=0
DAILY_MIGRATE_EXIT=0
BETA_MIGRATE_EXIT=0
```

`tests/integration/test_ops_snapshot.py` 最终为 `1 passed in 26.67s`。测试覆盖过期 Running、Queued、运行服务身份、System Manager、普通用户拒绝、7 天清理边界和清理时先删事件后删运行；测试开头对活的常驻 worker fastfail。

scheduled profile 于 `2026-09-03T07:49:41Z` 拉起，等待至 `07:54:52Z`，超过完整 5 分钟。daily 的 `DS Ops Snapshot` 从 0 增至 1，最新 `collected_at=2026-09-03 15:52:51.390502`；alpha 的 scheduler 站点配置实测为 disabled，因此没有新增。alpha、daily 的 `Scheduled Job Log` Failed 均为 0；daily 的 `Scheduled Job Type` 方法为 `dsherp_bridge.ops.collect_snapshot`，`last_execution=2026-09-03 15:56:06.276406`。五分钟核验后为安全执行第二轮三站迁移，已精确停止 scheduled 的 scheduler 与 scheduler-worker 两个 profile 服务。

### 全量门禁与 `/metrics`

最终提交候选上执行非集成 Python：

```text
........................................................................ [ 46%]
.......................................................... [ 92%]
...........                                                              [100%]
155 passed in 51.38s
```

同一候选执行 `node --test runtime/*.test.cjs`：`tests 8, pass 8, fail 0, duration_ms 59.865291`。T3.3 目标测试为 `24 passed in 0.81s`。

最终恢复 LaunchAgent 后原样抓取：

```text
curl -s 127.0.0.1:9109/metrics | grep -c '^dsherp_'
17
```

HTTP 状态为 200；`lsof` 显示 PID `18765` 只监听 `127.0.0.1:9109`。`--once` 不启动 HTTP、缺省端口 9109 的行为由 `tests/test_context_worker.py` 覆盖。

### 三项故障注入

注入前明确限定：不发真实业务请求；仅使用 alpha 合成用户、合成消息、本地不可达 provider 地址和临时容器。`.env` 原始 SHA-256 为 `153ad318cd565a004be9d7b4f78cfe15ca2cc509cb391acf03046470849709a6`，权限 `0600`；临时副本只放在 `mktemp` 目录，结束后已精确删除。

1. provider 地址临时指向 `http://127.0.0.1:9`，于 `2026-09-03T08:32:36Z` 连发 3 条 alpha 合成消息。三条运行 `e6470c…226a`、`b7f4dd…f58c`、`786b2f…307` 最终均为 Failed。告警行：

   ```json
   {"ts": "2026-09-03T08:35:19.751+00:00", "event": "alert", "key": "provider_or_runtime_failing", "severity": "critical", "message": "连续运行失败"}
   ```

   从注入时间到告警行时间为 **163.751 秒**，小于 5 分钟；随后立即恢复 `.env` 原始哈希和权限。

2. 活动运行数为 0 时，于 `2026-09-03T08:36:11Z` 创建唯一临时容器 `dsherp-context-orphan-test`。告警行：

   ```json
   {"ts": "2026-09-03T08:36:22.311+00:00", "event": "alert", "key": "orphan_containers", "severity": "warning", "message": "存在孤儿容器"}
   ```

   时间差为 **11.311 秒**。取证后执行精确 `docker rm -f dsherp-context-orphan-test`，容器计数回到 0。

3. 精确 bootout 唯一 worker 后确认 PID 文件不存在、进程计数 0，于 `2026-09-03T08:37:29Z` 创建一条 alpha 合成 Queued 运行 `8a7fe8…2d67`；等待到 `08:39:37Z`，超过 120 秒。重启前主动采集快照，实测 `queued=1`、`queued_oldest_seconds=149`、`last_claim_age_seconds=329`；为避免重启领取后接触真实 provider，再次临时使用 `127.0.0.1:9`。告警行：

   ```json
   {"ts": "2026-09-03T08:40:01.816+00:00", "event": "alert", "key": "worker_not_claiming", "severity": "critical", "message": "有排队但未领取"}
   ```

   从创建 Queued 运行到告警行时间为 **152.816 秒**，小于 5 分钟。该运行随后在本地不可达地址上终止为 Failed，活动运行归零，`.env` 再次恢复原始字节与 `0600`。

三项注入全程没有真实 provider 请求。恢复 `.env` 后，常驻 worker 停止状态下执行 alpha 固定替身用例：

```text
.venv/bin/python -m pytest 'tests/integration/test_context_mcp_chain.py::test_native_context_runtime_reads_actual_erp_through_run_capability[True]' -s -q
.
1 passed in 89.06s (0:01:29)
```

该用例断言运行结果为 `Succeeded/DSHERP_OK`，provider 固定在隔离容器内 `127.0.0.1:38127/v1`，并实际只读 `DSHERP-TEST-ITEM`；fixture 随后清理运行。

### 恢复后的运行态

从受版本控制的生成器重建 plist 并只 bootstrap 一次。最终 `launchctl state=running`，PID 文件、launchd 与进程表一致为 PID `18765`，`dsherp.context_worker` 精确计数 1，metrics 行数 17，孤儿容器 0；alpha、daily、beta 的 Queued/Running/Cancelling 均为 0。`.env` SHA-256 与注入前一致、权限 `0600`，临时备份 marker 与目录均不存在。worker 日志现有 23 行全部可 `json.loads`；真实 provider key 与运行服务 secret 的逐值 grep 均为 0（grep 退出 1）。scheduled profile 的 scheduler 与 scheduler-worker 在迁移后重新拉起，最终均为 running。

最后一次 worker 重启时，`ops_status` 返回了第三项注入期间留下的旧快照，因此在 `08:43:54.217Z` 又输出一条 `worker_not_claiming`；这不是第二次注入，数据库当时活动运行已经为 0。根因不是正常的“五分钟最终一致性窗口”，而是 alpha scheduler 未启用、快照不会自然刷新，同时旧版 `ops_status` 没有返回新鲜度，worker 无法拒绝陈旧数据。该行未删除或改写。

上一轮候选为消除该告警，于 `08:47:52Z` **手工调用 `collect_snapshot()`** 补采恢复态快照；该次放行证据依赖人工采集，不是 scheduler 自然执行，列为明确偏离。本轮整改将 `ops_status` 改为纯读取并返回服务端计算的 `age_seconds`，worker 对空快照或超过 900 秒的快照只发 `ops_snapshot_stale`，不再运行依赖快照的规则或孤儿容器探针；alpha 与 daily 的自然调度连续性另行记录在下方整改证据中。

### C3 审计整改证据

整改提交为 `1fd3020`（陈旧快照）、`b1aeea7`（探针/metrics 启动故障隔离）、`c74c26e`（告警与指标脱敏）、`3eeeb2b`（证据因果修正）和 `632df3f`（空快照测试隔离）。P1.1 单元 RED 为 `6 failed, 18 passed`，GREEN 为 `24 passed`；P1.2 首轮 RED 为 `5 failed, 23 passed`，Cursor 最小实现后主审补获 `ORPHAN_CONTAINERS.set(None)` 的 RED，最终相关测试 `29 passed`；P1.3 RED 为 `2 failed, 9 passed`，GREEN 为 `12 passed`。最终快照集成测试使用模拟空查询、不删真实快照，结果为 `1 passed in 27.15s`。

整改代码与证据落盘后重跑全量非集成门禁，尾部为 `164 passed in 51.23s`；同一候选运行 `node --test runtime/*.test.cjs` 为 `tests 8, pass 8, fail 0, duration_ms 57.832875`。定向告警/metrics/worker 日志测试为 `34 passed in 0.87s`。

本轮没有修改 DocType JSON 或 `hooks.py`，因此没有重复执行三站 migrate；上一候选在同一 DocType/hooks 版本上的 alpha、daily、beta migrate 退出码均为 0。本轮只按要求修改站点运行设置：alpha、daily 的 `System Settings.enable_scheduler` 实测启用，beta 的 `is_scheduler_disabled(verbose=False)` 返回 true，作为预览隔离站不参加自然调度。

从 `2026-09-03T09:08:51Z` 到 `09:25:03Z` 连续观察 scheduled profile，scheduler 与 scheduler-worker 均保持 running。排除测试创建并自行清理的快照后，两站自然 `collected_at`（站点时区 UTC+8）为：

```text
alpha: 2026-09-03 17:12:21.996114, 17:16:37.286839, 17:20:21.964856
       interval_seconds: 255.291, 224.678
daily: 2026-09-03 17:12:55.795178, 17:16:01.788206, 17:20:04.359831
       interval_seconds: 185.993, 242.572
```

两站各至少 3 条，`Scheduled Job Type` 均为 `frequency=Cron`、`cron_format=*/5 * * * *`，`last_execution` 分别推进到 `17:20:22.071746` 与 `17:20:04.544243`。实际落库间隔受本环境 Frappe scheduler 轮询时点影响，不承诺精确 300 秒。两站该任务的 `Scheduled Job Log` 都为空，不是通过“Failed=0”推断成功：实测 `Scheduled Job Type.create_log=0`；当前安装的 Frappe 16.31.0 `update_scheduler_log()` 在该值为 false 时只更新 `last_execution` 并提交，不创建 `Scheduled Job Log`。对应 scheduler-worker 日志同时出现任务的 `Successfully completed` 与 `Job OK`。

`dsherp_provider_call_failures_total` 当前仅为预留指标，没有自增点；留待计划 2 的 provider 错误分类落地，不把其存在表述为已采集 provider 失败率。

重启 alpha backend 使其加载新 `ops_status` 后，HTTP 实测返回键 `age_seconds/snapshot`，快照非空且当时 `age_seconds=110`。随后在三站活动运行均为 0、孤儿容器为 0 时，于 `2026-09-03T09:28:53Z` 只 bootstrap 一次 LaunchAgent；观察至 `09:39:57Z`，超过 10 分钟。该窗口 worker 日志新增事件为 `[]`，其中告警数为 0，因此既没有重复 `worker_not_claiming`，也没有其他新告警。最终 PID 文件与进程表均为 `27010`，worker 计数 1；metrics HTTP 仍为 17 条 `dsherp_` 行，孤儿容器 0，alpha/daily/beta 活动运行均为 0。alpha、daily scheduler 均启用且继续生成新快照，beta 保持禁用；scheduled 两容器均为 running。

最终 worker 日志逐行 `json.loads` 的非法行数为 0；对当前 `.env` provider key 与 context-worker service secret 的逐值 grep 均为 0（退出码 1）。`.env` SHA-256 仍为 `153ad318cd565a004be9d7b4f78cfe15ca2cc509cb391acf03046470849709a6`，权限 `0600`。整改过程没有创建业务运行、没有改 provider 地址，也没有调用真实 provider。

## C4：管理员审计、前端事件流与失败回放

### 报表、三站迁移与权限边界

`DS Agent Audit` 的集成测试先以“Report 不存在”取得红灯，实现后为 `1 passed in 29.78s`。真实浏览器首次打开又发现报表没有日期筛选器，只显示“审计报表需要 from_date 与 to_date”；新增资产测试先以 `FileNotFoundError: ds_agent_audit.js` 失败，再增加标准 Query Report 的 `from_date`、`to_date` 必填筛选器，目标测试为 `2 passed in 0.01s`。Administrator 随后在 alpha 真实浏览器打开报表成功，页面展示 6 条当日运行，至少包含 `dsherp-denied@example.invalid` 与 `dsherp-reader@example.invalid` 两个 owner。

报表文件最终变更后重新串行迁移。alpha、daily 使用 `dsherp-validation-backend-1`，beta 使用独立的 `dsherp-validation-beta-backend-1`，最终实测退出码分别为：

```text
alpha_exit=0
daily_exit=0
beta_exit=0
```

定位 beta 容器前曾把 beta Site 错投到 alpha/daily bench，得到明确的 `404 Not Found: dsherp-beta.localhost does not exist` 和退出码 2；没有把该错误当作成功，查明当前容器拓扑后在正确 beta 容器重跑为 0。迁移产生的正常 scheduler job 已由既有 scheduler-worker 消费，未放宽集成队列卫生规则。

另以真实合成 System Manager（非 Administrator）检查 `DS Model Run` 权限，结果为 `{'read': False, 'report': False}`。因此若要让该角色从原生入口打开报表，确实必须新增 `DS Model Run` 只读权限；按 C4 补充要求，本轮没有修改或提交该权限，等待用户明确确认。Administrator 报表验收不依赖这项未授权变更。

### 失败用例导出

C0 基准为 alpha 12 条、daily 0 条 Failed。导出前重新只读盘点得到 alpha 16、daily 0，因此 alpha 相对基准新增 4 条，均为 C3 合成故障注入留下的失败运行；没有删除历史失败来迎合旧基准。第一次导出结果为：

```text
alpha: {'exported': 16, 'skipped': 0}
daily: {'exported': 0, 'skipped': 0}
alpha 幂等复跑: {'exported': 0, 'skipped': 16}
```

首轮导出因一条历史 submit 提案指向已删除的合成 Sales Order 而失败。新增纯函数回归测试先确认 `KeyError: id` 红灯，再改为只读冻结在 `DS Operation Proposal` 上的 `name/status/payload` 生成摘要，避免导出历史审计资料时重新访问已经消失的业务目标；最终 `tests/test_eval_cases.py` 为 `2 passed`。当前 `evals/cases` 共 16 个 JSON，全部位于 alpha 目录；逐文件执行区分大小写的 `grep -RIlE 'capability|api_secret|DEEPSEEK' evals/cases | wc -l` 输出 `0`。C4 结束前清理一次临时回放运行后，站点 Failed 计数仍为 alpha 16、daily 0，与已导出文件一致。

### 无 Item 权限失败回放

常驻 worker 已停止且进程计数为 0。使用 `.runtime/erp-users.json` 中已有的 `dsherp-denied@example.invalid` 合成身份，实际确认 Item read 权限为 false；本地模型替身固定在隔离地址，不读取 `.env` provider 地址，也没有调用真实 provider。替身请求 `erp_read_record(Item, DSHERP-TEST-ITEM)`，得到 HTTP 403 工具错误。运行 `768a71…ba336` 最终为 Failed，所有者端点返回 23 条事件：

| seq | kind | error_class | payload 键名 |
| ---: | --- | --- | --- |
| 1 | `queued` | — | `domain,page_type,question_chars` |
| 2 | `claimed` | — | `domain,native_session_id,permission_revision,runtime_revision` |
| 3 | `runtime_started` | — | `session_id` |
| 4,6,8,10,12,14,16 | `model_call_reserved` | — | `call_index,input_bytes,max_output_tokens,model,purpose` |
| 5,7,9,11,13,15,17 | `model_response` | — | `chunk_keys,model,purpose,usage` |
| 18 | `runtime_tool_call` | — | `arguments,call_id,name,seq` |
| 19 | `tool_error` | `ToolError` | `call_id,code,seq,text` |
| 20 | `turn_end` | — | `reason,seq` |
| 21 | `runtime_failed` | `RuntimeError` | `frames,type` |
| 22 | `runtime_failed` | `RuntimeError` | `duration_ms` |
| 23 | `finished` | — | `answer_chars,error,model_calls,status` |

第 19 条的脱敏文本为 `Error: Error executing tool erp_read_record: 业务运行请求未完成（HTTP 403）`，所以工具权限失败可在不暴露凭据的情况下回放；`runtime_failed` 明确先于最终 `finished`。本次路径没有触发 `compaction`，不推断未观测到的类型名。截图和事件读取完成后，精确删除这条临时运行及其 23 条事件与独占会话；删除前提案数为 0，删除后运行/会话均不存在，alpha Failed 从临时的 17 回到 16，活动运行数为 0。

### 告警时延与真实浏览器证据

C4 按要求复用 C3 已独立放行的三项注入，不重复制造故障：provider 不可达 `163.751` 秒、孤儿容器 `11.311` 秒、worker 未领取 `152.816` 秒，三项均小于 5 分钟；对应 JSON 告警行与注入时刻保留在上方“C3：三项故障注入”。

真实浏览器截图：

- `evidence/observability/c4-agent-audit-report.png`：Administrator 打开的 `DS Agent Audit`，同屏可见两个 owner。
- `evidence/observability/c4-workbench-event-stream.png`：`dsherp-reader@example.invalid` 在 Agent 工作台展开运行 `8a7fe8…2d67` 的事件流；展开前由所有者 API 独立读取为 19 条，页面显示“共 19 条”，行数一致。

浏览器验证前已执行 `node build.mjs` 并提交 `frappe_app/dsherp_bridge/public/dist`，随后重启 alpha backend；登录端点 HTTP 200，容器保持 running。两张 PNG 已重新读回并目视核验，不以文件存在代替 UI 事实。

### C4 收口遗留

`list_run_events` 对不存在 run id 原先返回 `DoesNotExistError`，与无权运行的 `PermissionError` 形成可枚举差异；集成测试先取得该 404 形状红灯，最小改为两者均返回“运行不属于当前用户”，目标用例为 `1 passed in 29.45s`。`verify_daily_backup.snapshot()` 的 `audit_counts` 已加入 `DS Run Event`，源/恢复站会同时比较事件数；源码测试先红后绿为 `17 passed`。

仍未在本计划扩项的边界：授权/预算拒绝补 `model_error`、事件写回的租约与行锁、`worker_log` 非容器值脱敏，以及 sanitize 的 AST 级实现一致性，继续归计划 2；`notifications` 目前收集但不参与映射，C4 没有伪称其已使用。`DS Run Event` 的数据库层不可变约束仍属于计划 4（G7），本计划只保证应用层约定。

### C4 最终门禁与恢复态

门禁前确认 `.runtime/agent-worker.pid` 不存在、常驻 agent worker 进程为 0。为隔离长集成测试，临时停止 scheduler 两个 profile；停止时发现它们已合法入队 alpha/daily 定时任务，没有删除任务或放宽夹具，只启动 scheduler-worker 自然消费至队列 `{}`，随后再次停止。四套命令最终输出：

```text
.venv/bin/python -m pytest tests --ignore=tests/integration -q
168 passed in 52.91s

cd frontend && npm test
Test Files  21 passed (21)
Tests       167 passed (167)
Duration    13.53s

node --test runtime/*.test.cjs
tests 8, pass 8, fail 0, duration_ms 60.635667

.venv/bin/python -m pytest tests/integration -q
174 passed in 766.45s (0:12:46)
```

伴生审查在第一次门禁后发现前端把真实 `runtime_tool_call.payload.name` 错读为测试伪造的 `payload.tool`。回归测试先明确得到“调用工具 undefined”红灯，再修正实现和两份 dist，提交 `e96994a`；上面四套输出均为该修复后的最终 HEAD 重跑结果。审查还发现浏览器工具返回 JPEG 字节却使用 `.png` 扩展名；两份证据已转码为真实 PNG，`file` 分别确认为 `PNG image data, 746 x 863` 与 `596 x 863`。

门禁后重新启动 scheduler 与 scheduler-worker；从受版本控制的生成器重建 plist，并只执行一次 `launchctl bootstrap`。最终 PID 文件、`launchctl` 与 `kill -0` 一致为 `46688`，`dsherp.context_worker` 进程计数 1，`/metrics` 的 `dsherp_` 行数 17，孤儿容器 0；alpha、daily、beta 的 Queued/Running/Cancelling 均为 0。此 PID 是本次恢复态快照，不是稳定标识。

当前事件面板按计划接口只请求 `page=1`，若单次运行超过 200 条则会显示前 200 条而未消费 `has_more`；本次真实运行只有 19 条，放行实例不受影响。该长运行分页属于伴生审查 P2，保留为后续实现，不把本次 19 条一致性扩大表述为任意长度完整回放。
