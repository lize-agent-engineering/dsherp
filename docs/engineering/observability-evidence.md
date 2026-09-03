# 可观测与失败回放证据

日期：2026-09-03。当前范围：C0 与 C1 整改，固定 Runtime 事件形状、隔离合成站历史运行基线和三站迁移事实。本文件分别记录探针、站点事实和后续检查点证据；本地模型替身结果不等同于真实 provider 证据，也不代表生产部署。

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
