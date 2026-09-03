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
