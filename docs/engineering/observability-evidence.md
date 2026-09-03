# 可观测与失败回放证据

日期：2026-09-03。当前范围：C0，固定 Runtime 事件形状与隔离合成站历史运行基线。本文件分别记录探针、站点事实和后续检查点证据；本地模型替身结果不等同于真实 provider 证据，也不代表生产部署。

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
