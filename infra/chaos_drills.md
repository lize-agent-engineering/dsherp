# 运行底座混沌演练

范围仅限本机隔离的 alpha `dsherp-validation.localhost` 与 daily `dsherp-daily.localhost`。开始前必须确认 `.runtime/agent-worker.pid` 不存在、进程表中 `python -m dsherp.context_worker` 为 0、两站 `Queued/Running/Cancelling` 为 0、`dsherp-context-*` 为 0。演练只使用合成用户和合成问题；每次创建的 run、conversation、session 目录及一次性容器必须按确切 ID 清理。

## 1. worker 被 SIGKILL 后租约清扫

1. 使用 `infra/load_runs.py` 的一次性本地 SSE 容器和专用临时 provider-env；不得读取或改写 `.env`。
2. 用 `.runtime/context-worker-sites.json` 启动一次性双站 worker，取得其 `.runtime/agent-worker.pid`。
3. 以 `.runtime/erp-users.json` 的 reader 在 alpha 新建唯一 request_id 的只读 query 运行。
4. 轮询该 run 的 `list_run_events`；必须在状态为 Running 且已经出现 `runtime_started` 后，对第 2 步的确切 PID 执行 `kill -9 PID`。不要停止 backend、Redis 或本地 SSE。
5. 核验 worker 退出码为 SIGKILL，run 仍为 Running；读取该 run 的服务端 `expires_at`，不修改租约或站点预算，等待站点时间越过它。
6. 使用同一专用 provider-env 启动第二个一次性 worker。它第一次调用 `claim_run` 时应把过期 Running run 置为 Failed，并新增唯一事件 `expired`，payload 必须精确为 `{"reason":"lease_expired"}`。
7. 正常停止第二个 worker。以原会话 owner 再向同一 conversation 发送唯一 request_id 的消息；确认新 run 成功入队，然后用 owner 的 `cancel_run` 撤回为 Cancelled。这证明旧租约失败没有锁死会话。
8. 记录 `runtime_started`、SIGKILL、原租约到期、清扫失败、继续消息五个时刻；删除本轮两个 run 的事件与 run、一个 conversation、首轮 scope 对应的业务 session 目录，并核验 worker/pid/活动运行/上下文容器/模型容器均为 0。

2026-09-04 实测通过。第一次在“继续会话”步骤误用运行服务 client，服务端正确返回 HTTP 403；该次记录已清理。修正为 conversation owner client 后完整演练通过，原始时间线见 `docs/engineering/runtime-reliability-evidence.md`。

## 2. provider 三连失败与熔断恢复

1. 使用 `infra/load_runs.py` 的一次性本地 SSE 容器和专用临时 provider-env；不得读取或改写 `.env`。失败注入阶段把该 provider-env 的地址指向 `127.0.0.1:9`，文件模式必须为 0600。
2. 确认 `.runtime/agent-worker.pid` 不存在、进程表中 `python -m dsherp.context_worker` 为 0、两站 `Queued/Running/Cancelling` 为 0、metrics `127.0.0.1:9109` 未被占用。用 `.runtime/context-worker-sites.json` 启动一次性双站 worker。
3. 以既有合成用户在 alpha 与 daily 入队恰好三条只读运行。等待三条均为 Failed，且每条 `list_run_events` 含 `model_error`。
4. 从该 worker 的 `/metrics` 核验 `dsherp_provider_circuit_open 1` 与 `dsherp_provider_call_failures_total 3`；从结构化日志核验恰好一条 critical `provider_circuit_open` 告警。
5. 真实 key 只允许写入熔断仍打开且两站活动运行精确为 0/0 时的那一个一次性 0600 provider-env 窗口。该窗口最多一次获授权 GET /models，不得发送 chat/completions；真实窗口零 claim。窗口关闭后真实 key 不得再留在 provider-env。
6. 熔断 gauge 回到 0 后，必须先把同一 provider-env 切回本地 SSE，再创建恢复运行。不得在真实 key 仍在窗口内入队或领取恢复运行。
7. 确认恢复运行 Succeeded。停止一次性 worker。按确切 ID 删除本轮 run、conversation、session 目录与一次性容器；核验 worker/pid/活动运行/上下文容器/模型容器均为 0，且 `.env` 未变。

2026-09-04 实测通过。修复前的两次 live RED 已按确切 ID 清理，且从未到达真实 provider。原始时间线见 `docs/engineering/runtime-reliability-evidence.md`。

## 3. alpha backend 短停与 worker 自恢复

1. 确认常驻 worker 已停、两站无活动运行、`dsherp-validation-backend-1` 正在运行。使用一次性本地 SSE 与专用临时 provider-env；不得改 `.env`。
2. 用 `.runtime/context-worker-sites.json` 启动一次性双站 worker，取得 `.runtime/agent-worker.pid` 后立即对该 PID 发送 SIGSTOP。必须先暂停再入队，避免领取竞态。
3. worker 仍暂停时，以 `.runtime/erp-users.json` 的 reader 在 alpha 新建唯一 request_id 的只读 query 运行，并确认状态仍为 Queued。
4. 执行 `docker stop dsherp-validation-backend-1`。确认该容器已停止后，再对同一 PID 发送 SIGCONT。
5. 停机约 10 秒。期间核验同一 PID 仍存活、两站不可用、结构化日志出现 `worker_error`。不要停止 Redis 或本地 SSE。
6. 只执行 `docker start dsherp-validation-backend-1`，恢复同一个 backend。任何异常路径的 finally 必须优先恢复这一同一 backend，再停止 worker 或清理记录。
7. 恢复后要求 worker PID 不变，并由同一 PID 领取第 3 步的合成运行至 Succeeded。
8. 正常停止 worker。删除本轮 run、conversation 与 session 目录；核验 alpha/daily HTTP、worker/pid、活动运行及孤儿容器，且同一 backend 仍在运行。

2026-09-04 实测通过。原始时间线见 `docs/engineering/runtime-reliability-evidence.md`。
