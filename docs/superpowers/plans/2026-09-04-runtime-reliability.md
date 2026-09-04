# 计划 2：运行底座可靠性 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 本计划在 Codex 中**连续执行**：分支 `codex/runtime-reliability`；阶段 S0–S7 之间不停下等待审计，每个阶段末尾的"自检门"由执行方自己重跑并把输出原样写入证据文档；只有自检门红灯且两次修复后仍红、或需要用户授权的点，才停下报告。全部阶段完成后停下提交终验报告，由审计方一次性独立复核。

**Goal:** 让运行底座在多人同时使用、真实 provider 故障、长会话与用户取消下不串行、不崩溃、不丢答案、不卡死，并给模型和用户明确的"做不了"出口。

**Architecture:** 服务端把"全站单运行"闸门改为"每站并发上限 + 每用户 1 在飞"，用可续期租约替代固定 180s，`run_status` 退化为只读心跳，队列有过期与 worker 心跳背压；宿主 worker 变成一进程服务多站、固定槽位并发、主循环永不因单次运行退出、带 provider 熔断的协调器；容器内 runner 执行统一预算表（模型单请求 ≤ 90s、运行总时长按领域封顶）并在 5 秒内完成取消；工具错误经 MCP 层三分类透传给模型；新增 NeedsInput 运行状态与提案 Rejected/Expired 状态；前端把服务端原因透传给用户并在渲染异常时降级而非白屏。审计底座不变量（白名单、幂等、写后回读、Unknown 不冒充成功）全部保持。

**Tech Stack:** 与计划 1 相同：Frappe v16 / ERPNext v16（容器内 Python 3.14）、宿主 Python 3.12 + httpx + pytest、DSH SDK 0.1.1rc1、FastMCP、Node `node:test`、React 18 + vitest、docker compose `dsherp-validation`。

## Global Constraints

- 全部按 AGENTS.md：中文；TDD（先写失败测试）；fastfail；不 fork Frappe/ERPNext/DSH；按功能分类提交；不推送远端；不提交密钥、租户数据、运行日志。
- 生产化总体设计"前置约束"第 2、3、4、6 条是本计划的验收口径：工具错误三分类、写操作幂等与租约、完成只由外部事实判定、每个状态机都有"做不了"终态。
- 承接计划 1 已固定的规则：每个阶段的自检门都必须附 `.venv/bin/python -m pytest tests --ignore=tests/integration -q` 与 `node --test runtime/*.test.cjs` 完整尾部；DocType/Report/hooks 变更后三站（alpha `dsherp-validation.localhost`、daily `dsherp-daily.localhost`、beta `dsherp-beta.localhost`）都 `bench migrate` 并逐站记录退出码；进程内调用 `worker.run_once`/`poll_once` 的集成测试只能在常驻 worker 停止时运行，且测试开头 fastfail。
- 修改 `config/runtime-files.json` 所列文件会作废在飞运行；新增被容器加载的模块必须加入清单；`tests/test_runtime_revision.py` 必须继续通过。修改 `business-skills/*/SKILL.md` 必须同步更新 `config/business-skills.json` 的 version 与 sha256，`tests/test_business_skills.py` 必须继续通过。
- **不重试模型运行**；熔断只影响是否领取，不影响已领取运行；告警只通知不修复。
- **不改预算数值**：模型调用次数、token 上限沿用现值（已裁决 #6，计划 6 裁定）；本计划只把它们与超时表一起搬进服务端配置。
- 并发默认按已裁决 #2：每站 1，全局槽位 3；全部是服务端 `site_config` 或 worker profile 配置，升档不改代码。
- 观测失败不改变业务结果（计划 1 规则延续）；事件类型新增只能追加到两侧 `RUNNER_KINDS`/`SERVER_KINDS` 并保持一致性测试通过。
- 真实 provider 调用不在本计划范围；provider 探针只用免费的 `GET {DEEPSEEK_BASE_URL}/models`，且只在熔断打开期间执行；自动化全部用本地模型替身。

## 统一预算表（本计划的单一事实源）

| 项 | 值 | 位置 |
|---|---|---|
| 模型单请求超时 | 90s（所有领域，operation 从 120 降到 90） | 服务端 `dsherp_run_budget` conf → claim_run 返回 → runner |
| 运行总时长 | query/configuration 300s，operation 600s | 同上；runner 强制，超时即取消并 Failed |
| 容器 subprocess 超时 | 运行总时长 + 30s | worker 按 claim 返回值计算 |
| 租约 `expires_at` | 180s，剩余 < 90s 时由 `run_status` 续期 | 服务端 |
| 状态轮询 | 2s；取消落地 ≤ 5s | runner |
| 队列过期 `queue_expires_at` | 600s | send_message 写入，claim_run 清扫 |
| worker 心跳陈旧阈值 | 60s（无 claim 调用即视为不可用） | Redis cache 键 `dsherp_worker_heartbeat` |
| 每站并发上限 | `dsherp_site_concurrency`，默认 1 | site_config |
| 每用户在飞上限 | 1（硬编码，不配置） | claim_run |
| 全局容器槽位 | profile `slots`，默认 3 | worker |
| provider 熔断 | 连续 3 次 provider 失败打开 60s；打开期每 60s 探针 `GET /models` | worker |
| 提案过期清扫 | cron `*/10 * * * *` | hooks |

## 文件结构

| 文件 | 职责 | 阶段 |
|---|---|---|
| `frappe_app/dsherp_bridge/context_execution.py` | 并发闸门、租约续期、廉价 `run_status`、队列过期清扫、心跳、`finish_run` 的 `provider_failures` 与完成交叉校验、NeedsInput | S1、S5 |
| `frappe_app/dsherp_bridge/context_api.py` | `send_message` 的 `queue_expires_at` 与 worker 不可用 503；`reject_operation`；`_public` 的 NeedsInput 语义 | S1、S5 |
| `frappe_app/dsherp_bridge/run_budget.py` | 预算表读取：`budget(domain) -> dict`（从 `site_config.dsherp_run_budget` 覆盖默认值） | S0 |
| `frappe_app/dsherp_bridge/ops.py` + `hooks.py` | `expire_proposals` cron；快照增加 `needs_input`、`queue_expired_24h` | S5 |
| DocType JSON：`ds_model_run.json`（status 加 NeedsInput，字段 `queue_expires_at`、`answer_flagged`、`provider_failures`）、`ds_operation_proposal.json`（status 加 Rejected/Expired） | 状态机扩展 | S1、S5 |
| `dsherp/context_worker.py` | 多站 profile、槽位并发、崩溃安全主循环、熔断、容器超时按预算 | S2 |
| `dsherp/provider_circuit.py` | `CircuitBreaker`（纯逻辑）与 `probe_models(base_url, api_key) -> bool` | S2 |
| `dsherp/context_runner.py` | 总时长 deadline、daemon 线程取消、NeedsInput 优雅停止、预算来自 run.json | S3 |
| `dsherp/session_runtime.py` | `request_timeout_seconds` 从 run.json 预算读取 | S3 |
| `dsherp/context_mcp.py`、`dsherp/read_tools.py` | 错误三分类 `ToolFailure`、`erp_request_input` 工具 | S4 |
| `business-skills/*/SKILL.md` + `config/business-skills.json` | 错误分类与 NeedsInput 的模型侧规则 | S4 |
| `frontend/src/context-api.js`、`agent-format.js`、`OperationProposal.jsx`、`ErrorBoundary.jsx`、`ContextSidebar.jsx`、`AgentWorkbench.jsx` | 错误透传、拒绝按钮、新状态标签、ErrorBoundary、轮询退避 | S5、S6 |
| `infra/load_runs.py`、`infra/chaos_drills.md` | G5 负载脚本与混沌演练步骤 | S7 |
| `docs/engineering/runtime-reliability-evidence.md` | 各阶段自检门输出、演练证据 | 全程 |

## 阶段总览与自检门

| 阶段 | 内容 | 自检门（执行方重跑并入档） |
|---|---|---|
| S0 | 预算表模块与基线 | 预算模块单测；基线数字（当前 claim 闸门、超时常量位置）入档 |
| S1 | 服务端闸门、租约、心跳、队列过期 | 集成：两用户两运行同时 Running（并发 2 配置下）、同用户第二条被拒、租约续期、队列过期、心跳陈旧 503 |
| S2 | worker 多站与崩溃安全、熔断 | 单测：两站轮询、槽位上限、取消瞬间 finish 403 不崩、连续失败打开熔断、探针关闭熔断 |
| S3 | runner 超时链与取消 | 单测（替身）：总时长超限 Failed、取消 ≤5s、NeedsInput 优雅停止 |
| S4 | 错误三分类与 `erp_request_input` | 单测（替身）：三类错误各一条进入下一次模型请求；skill 清单 sha 更新 |
| S5 | 出口与完成判定 | 集成：Rejected/Expired/NeedsInput 状态机；完成自述无执行记录被标记 |
| S6 | 前端健壮性 | vitest：417 原因可见、ErrorBoundary 降级、轮询退避不清空 |
| S7 | G5 负载与混沌、文档收口、三站迁移 | 负载脚本输出、三项混沌结果、全量集成绿、README/spec/证据更新 |

自检门规则：红灯先修；同一红灯修两次仍红，或修复需要改本计划未列的文件超过 3 个，停下报告。需要用户授权的点见文末。

---

## 阶段 S0：预算表模块与基线

### Task 0.1：服务端预算模块 `run_budget.py`

**Files:**
- Create: `frappe_app/dsherp_bridge/run_budget.py`
- Test: `tests/integration/test_run_budget.py`

**Interfaces:**
- Produces: `budget(domain: str) -> dict`，键固定：`model_request_timeout_seconds`（90）、`run_total_seconds`（query/configuration 300，operation 600）、`lease_seconds`（180）、`lease_renew_below_seconds`（90）、`queue_expires_seconds`（600）、`site_concurrency`（`frappe.conf.dsherp_site_concurrency`，默认 1）、`model`（`frappe.conf.dsherp_model_policy.model`，默认 `deepseek-v4-flash`）、`provider`（默认 `deepseek-official`）；`frappe.conf.dsherp_run_budget` 为 dict 时逐键覆盖（只接受 int，非法值 fastfail）。
- 服务端其他模块只从这里读预算，禁止再散落常量。

- [x] **Step 1: 写失败集成测试**（docker exec 模式，同计划 1）：`budget('query')['run_total_seconds']==300`、`budget('operation')['run_total_seconds']==600`、默认 `site_concurrency==1`、`model=='deepseek-v4-flash'`；monkeypatch `frappe.conf.dsherp_run_budget={'run_total_seconds':'x'}` 抛 `frappe.ValidationError`；未知 domain 抛。
- [x] **Step 2: 确认失败** → `ModuleNotFoundError`。
- [x] **Step 3: 实现**

```python
"""Single source of truth for run time/lease/queue budgets. Values come from site_config overrides."""
import frappe

DEFAULTS={'model_request_timeout_seconds':90,'lease_seconds':180,'lease_renew_below_seconds':90,
          'queue_expires_seconds':600,'heartbeat_stale_seconds':60}
TOTAL={'query':300,'configuration':300,'operation':600}


def budget(domain):
    if domain not in TOTAL:frappe.throw('未知业务领域')
    values={**DEFAULTS,'run_total_seconds':TOTAL[domain]}
    override=frappe.conf.get('dsherp_run_budget') or {}
    if not isinstance(override,dict):frappe.throw('dsherp_run_budget 必须是对象')
    for key,value in override.items():
        if key not in values or type(value) is not int or value<=0:frappe.throw(f'dsherp_run_budget.{key} 无效')
        values[key]=value
    concurrency=frappe.conf.get('dsherp_site_concurrency',1)
    if type(concurrency) is not int or concurrency<1:frappe.throw('dsherp_site_concurrency 无效')
    policy=frappe.conf.get('dsherp_model_policy') or {}
    values.update({'site_concurrency':concurrency,'provider':policy.get('provider','deepseek-official'),
                   'model':policy.get('model','deepseek-v4-flash')})
    return values
```

- [x] **Step 4: 运行测试** → PASS。
- [x] **Step 5: 提交** `feat: 服务端统一运行预算表`。

### Task 0.2：基线入档

- [x] 新建 `docs/engineering/runtime-reliability-evidence.md`，"基线"节记录：当前 `claim_run` 单运行闸门位置（`context_execution.py` 中 `frappe.db.exists(... Running/Cancelling)` 行号）、`expires_at` 固定 180s 位置、`context_worker.run_container` 的 140s、`session_runtime._request_timeout_seconds` 的 120/90、worker `time.sleep(3)`；alpha 站当前 `DS Model Run` 各状态计数与 `DS Operation Proposal` Pending 且 `expires_at` 已过的条数（只读查询）。
- [x] 提交 `docs: 运行底座可靠性基线`。

**S0 自检门**：`tests/integration/test_run_budget.py` 全绿；非集成与 Node 门禁尾部入档。

---

## 阶段 S1：服务端并发闸门、租约、心跳与队列过期

### Task 1.1：DocType 字段与状态扩展

**Files:**
- Modify: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_model_run/ds_model_run.json`
- Modify: `tests/test_v16_framework_adaptations.py`（若断言字段计数或 options，需同步）

- [x] `status` options 改为 `Queued\nRunning\nCancelling\nCancelled\nSucceeded\nFailed\nNeedsInput`；新增字段 `queue_expires_at`（Datetime）、`needs_input`（Long Text，模型向用户提出的问题）、`provider_failures`（Int，默认 0）、`answer_flagged`（Check，默认 0）；`field_order` 同步。
- [x] 三站 `bench migrate`，退出码入档。
- [x] 提交 `feat: 运行记录增加 NeedsInput 与队列过期字段`。

### Task 1.2：`claim_run` 并发闸门与队列过期清扫

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`claim_run`）
- Test: `tests/integration/test_claim_concurrency.py`

**Interfaces:**
- 闸门规则：站内 Running/Cancelling 数 ≥ `budget.site_concurrency` → 返回 None；候选 Queued 按 creation 升序取第一条**其 owner 当前没有 Running/Cancelling 运行**的；没有则 None。
- 清扫：Queued 且 `queue_expires_at <= now` → Failed，error `系统繁忙，排队超时，请稍后重试`，事件 `expired {'reason':'queue_expired'}`；Running/Cancelling 且 `expires_at <= now` → 原有逻辑。
- 心跳：每次 `claim_run` 调用（含返回 None）执行 `frappe.cache().set_value('dsherp_worker_heartbeat', now_datetime().isoformat(), expires_in_sec=3600)`。
- 返回值新增 `budget`（`run_budget.budget(domain)` 全量 dict）与 `site`（`frappe.local.site`）。

- [x] **Step 1: 写失败集成测试**：以 Administrator 临时 `update_site_config('dsherp_site_concurrency',2)`（finally 恢复删除）；两个合成用户各 send_message → 两次 claim 都返回运行且状态 Running；第三个用户第三条 claim 返回 None；同一用户第二条消息（第一条仍 Running）claim 返回 None 且第一条不受影响；把一条 Queued 的 `queue_expires_at` 改为过去 → claim 后该运行 Failed 且最后事件 kind=expired、payload.reason=queue_expired；`frappe.cache().get_value('dsherp_worker_heartbeat')` 非空。清理：先删事件再删运行（计划 1 规则）。
- [x] **Step 2: 确认失败**（并发 2 下第二次 claim 仍 None）。
- [x] **Step 3: 实现**：把 `if frappe.db.exists('DS Model Run',{'status':['in',['Running','Cancelling']]}):return None` 替换为：

```python
    from dsherp_bridge.run_budget import budget as run_budget
    now=now_datetime()
    frappe.cache().set_value('dsherp_worker_heartbeat',now.isoformat(),expires_in_sec=3600)
    for name in frappe.get_all('DS Model Run',filters={'status':'Queued','queue_expires_at':['<=',now]},pluck='name',order_by='creation asc, name asc'):
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':'系统繁忙，排队超时，请稍后重试'})
        events.record_safely(name,'expired',{'reason':'queue_expired'})
        events.record_safely(name,'finished',{'status':'Failed','error':'queue_expired'})
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']]},fields=['owner'])
    busy_owners={row.owner for row in active}
    candidates=frappe.get_all('DS Model Run',filters={'status':'Queued'},fields=['name','owner','domain'],order_by='creation asc, name asc',limit_page_length=50)
    names=[row.name for row in candidates if row.owner not in busy_owners]
    if not names:return None
    if len(active)>=run_budget(candidates[0].domain)['site_concurrency']:return None
```

`site_concurrency` 与领域无关，取任一 domain 的预算即可；`expires_at` 改为 `add_to_date(now,seconds=budget['lease_seconds'])`；返回 dict 增加 `'budget':run_budget(domain),'site':frappe.local.site`。

- [x] **Step 4: 运行测试** → PASS；并跑 `tests/integration/test_run_events.py`、`test_context_claim_cancel.py`、`test_context_claim_rejection.py` 确认既有语义不变。
- [x] **Step 5: 提交** `feat: 站内并发闸门与队列过期清扫`。

### Task 1.3：`run_status` 廉价化与租约续期

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`run_status`、`_run`）
- Test: `tests/integration/test_run_lease.py`

**Interfaces:**
- `run_status(run_id,capability)`：不再 FOR UPDATE、不再调用 `require_revision`/`_public`；用 `frappe.db.get_value('DS Model Run',run_id,['status','capability_hash','expires_at'],as_dict=True)` 校验凭据；若状态 Running/Cancelling 且 `expires_at - now < lease_renew_below_seconds` → `frappe.db.set_value(... 'expires_at': now+lease_seconds)` 并记录事件 `lease_renewed {'expires_at':...}`（用 record_safely）；返回 `{'run_id','status','lease_remaining_seconds'}`。
- 权限修订检查保留在 `run_tool`、`reserve_model_call`、`finish_run`（已有），不在状态轮询里做。
- 新增 `lease_renewed` 到两侧 `SERVER_KINDS`（服务端）并在前端 label 表加"租约续期"。

- [x] **Step 1: 写失败集成测试**：创建 Running 运行，`expires_at=now+60s`；调用 `run_status` → 返回 `lease_remaining_seconds` 约 180 且最后事件 `lease_renewed`；`expires_at=now+150s` 时调用不续期、无新事件；对不存在/错误凭据抛 PermissionError；用 `frappe.db.sql('SHOW ENGINE INNODB STATUS')` 不可行，改为断言函数源码不含 `for_update=True`（`inspect.getsource(run_status)`）。
- [x] **Step 2: 确认失败** → 缺少 `lease_remaining_seconds`。
- [x] **Step 3: 实现**（`_run` 保留给写端点；`run_status` 独立实现）：

```python
@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_status(run_id,capability):
    run=frappe.db.get_value('DS Model Run',run_id,['name','status','capability_hash','expires_at','domain'],as_dict=True)
    if (not run or run.status not in ('Running','Cancelling','NeedsInput') or not run.capability_hash or not isinstance(capability,str)
        or not hmac.compare_digest(run.capability_hash,hashlib.sha256(capability.encode()).hexdigest())):
        raise frappe.PermissionError('运行凭据失效')
    from dsherp_bridge.run_budget import budget
    plan=budget(run.domain);now=now_datetime()
    remaining=int((get_datetime(run.expires_at)-now).total_seconds())
    if remaining<=0:raise frappe.PermissionError('运行凭据失效')
    if run.status in ('Running','Cancelling') and remaining<plan['lease_renew_below_seconds']:
        new_expiry=add_to_date(now,seconds=plan['lease_seconds'])
        frappe.db.set_value('DS Model Run',run.name,'expires_at',new_expiry)
        events.record_safely(run.name,'lease_renewed',{'expires_at':str(new_expiry)})
        remaining=plan['lease_seconds']
    return {'run_id':run.name,'status':run.status,'lease_remaining_seconds':remaining}
```

- [x] **Step 4: 运行测试** → PASS；`tests/test_run_events.py` 常量一致性若因 SERVER_KINDS 新增而红，同步 `dsherp/run_events.py` 注释与 `context_events.SERVER_KINDS`（runner 侧不发该事件，只需服务端加入）。
- [x] **Step 5: 提交** `feat: 状态轮询只读并续期租约`。

### Task 1.4：`send_message` 队列过期与 worker 不可用背压

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_api.py`（`send_message`）
- Test: `tests/integration/test_queue_backpressure.py`

**Interfaces:**
- 新运行写入 `queue_expires_at = now + queue_expires_seconds`。
- 发送前读取 `frappe.cache().get_value('dsherp_worker_heartbeat')`：缺失或超过 `heartbeat_stale_seconds` → `frappe.throw('助手服务暂不可用，请稍后再试', exc=frappe.ValidationError)` 并设置 `frappe.local.response['http_status_code']=503`；不创建运行。测试环境里为了让既有测试通过，`claim_run` 之外允许测试用 `frappe.cache().set_value` 预置心跳（集成 conftest 的 `validation_queue_hygiene` fixture 在 yield 前写一次心跳）。
- `cancel_run` 对 Queued 的撤回保持现状（已有），前端排队态显示"撤回"按钮（S6）。

- [x] **Step 1: 写失败集成测试**：清空心跳键 → send_message 抛 ValidationError 且 `frappe.local.response.http_status_code==503`、无新运行；写入心跳 → 成功且 `queue_expires_at` 约为 now+600s。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现** + 更新 `tests/integration/conftest.py` 的 session fixture 写心跳（通过 docker exec 一行脚本，与 purge 一起）。
- [x] **Step 4: 运行** 本测试 + `tests/integration/test_context_sessions.py` → PASS。
- [x] **Step 5: 提交** `feat: 排队过期与助手不可用背压`。

**S1 自检门**：`tests/integration/test_claim_concurrency.py tests/integration/test_run_lease.py tests/integration/test_queue_backpressure.py tests/integration/test_run_events.py -q` 全绿；三站 migrate 退出码；非集成与 Node 门禁尾部；证据文档记录并发 2 下两运行同时 Running 的 `list_run_events` 序列。

---

## 阶段 S2：worker 多站协调、槽位并发、崩溃安全与 provider 熔断

### Task 2.1：provider 熔断器与免费探针 `dsherp/provider_circuit.py`

**Files:**
- Create: `dsherp/provider_circuit.py`
- Test: `tests/test_provider_circuit.py`

**Interfaces:**
- `CircuitBreaker(threshold=3, open_seconds=60)`：`record(outcome: str, now: float)`（outcome ∈ `ok|provider_failure|other`）；`allow(now) -> bool`（closed → True；open 且未到期 → False；open 到期 → half_open → True 一次）；`state` ∈ `closed|open|half_open`；`consecutive_failures` 属性。
- `probe_models(base_url: str, api_key: str, *, timeout=5, client=None) -> bool`：`GET {base_url}/models`，`Authorization: Bearer`，`trust_env=False`，200 → True，其余/异常 → False；永不抛出；不打印 key。
- 熔断打开期间 worker 每 60s 调 `probe_models`，True → `breaker.reset()`（closed）；half_open 的下一次真实运行 ok → closed，provider_failure → 重新 open。

- [x] **Step 1: 写失败测试**

```python
import httpx
from dsherp.provider_circuit import CircuitBreaker,probe_models

def test_breaker_opens_after_three_provider_failures_and_recovers():
    b=CircuitBreaker(threshold=3,open_seconds=60)
    for _ in range(2):b.record('provider_failure',now=0)
    assert b.allow(1) and b.state=='closed'
    b.record('provider_failure',now=2)
    assert b.state=='open' and not b.allow(30)
    assert b.allow(63) and b.state=='half_open'
    b.record('provider_failure',now=64);assert b.state=='open' and not b.allow(65)
    assert b.allow(125);b.record('ok',now=126);assert b.state=='closed' and b.consecutive_failures==0
    b.record('other',now=127);assert b.state=='closed'

def test_probe_models_is_free_quiet_and_never_raises():
    seen=[]
    def handler(request):
        seen.append((request.method,str(request.url),request.headers.get('authorization')))
        return httpx.Response(200,json={'data':[]}) if len(seen)==1 else httpx.Response(503)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=client) is True
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=client) is False
    assert seen[0][0]=='GET' and seen[0][1]=='http://provider.invalid/v1/models' and seen[0][2]=='Bearer sk-synthetic-key'
    broken=httpx.Client(transport=httpx.MockTransport(lambda r:(_ for _ in ()).throw(httpx.ConnectError('down'))))
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=broken) is False
```

- [x] **Step 2: 确认失败** → `ModuleNotFoundError`。
- [x] **Step 3: 实现**（纯逻辑，`time` 不在内部读取，全部由调用方传 `now`；`probe_models` 内部 `client or httpx.Client(trust_env=False)`，`timeout=timeout`，任何异常返回 False）。
- [x] **Step 4: 运行** → PASS。
- [x] **Step 5: 提交** `feat: provider 熔断器与免费模型探针`。

### Task 2.2：多站 profile 与槽位并发的协调器主循环

**Files:**
- Modify: `dsherp/context_worker.py`（`main`、新增 `Coordinator`）
- Create: `infra/merge_context_worker_profiles.py`
- Modify: `infra/render_context_worker_launch_agent.py`（参数不变，只改注释）
- Test: `tests/test_context_worker.py`、`tests/test_v16_deployment_contract.py`

**Interfaces:**
- profile 新格式：`{'slots':3,'metrics_port':9109,'alert_webhook':null,'sites':[{'site','base_url','business_url','api_key','api_secret'}...]}`；旧格式（顶层 `site/base_url/...`）自动视为单站列表（`normalize_profile(profile) -> dict`）。
- `Coordinator(sites, settings_loader, slots, execute, breaker, probe)`：
  - `tick(now) -> int`：按轮转顺序对每站各尝试一次 claim（跳过：熔断 `allow(now)` 为 False、空闲槽位为 0）；每个领取到的运行提交到 `ThreadPoolExecutor(max_workers=slots)` 执行 `run_claimed(site_client, task, settings)`；返回本 tick 领取数。
  - `run_claimed`：现有 `run_once` 的执行段（scope 校验 → execute → writeback → finish_run），全部包在 try/except Exception 内；`finish_run` 也在 try 内；任何异常记 `worker_error` 日志与指标，**不得逃逸线程**；执行成功/失败后 `breaker.record(...)`：`finish_run` 响应里 `provider_failures>0` → `provider_failure`，Succeeded/Cancelled/NeedsInput → `ok`，其他 → `other`。
  - 容器超时：`execute` 的 `timeout` 从 `task['budget']['run_total_seconds']+30` 取（`run_container(task,settings,directory,timeout)` 增加参数，默认 170 兼容旧调用）。
  - 熔断打开时 `tick` 每 60s 调 `probe`，True → reset。
- `main`：flock 与 pid 不变（仍单进程）；一个 httpx client per site；`monitor_ops` 只对 `sites[0]`（alpha）执行（快照按站，其余站在 S7 的 Deferred 说明）；主循环 `while True: try: coordinator.tick(); poll 3s except Exception: log worker_error` —— **主循环永不退出**（SIGTERM 除外）。
- 指标：新增 `dsherp_slots_busy`（gauge）、`dsherp_provider_circuit_open`（gauge 0/1）、`dsherp_claims_total{site}` 改带 site 标签；`dsherp_provider_call_failures_total` 在 `provider_failure` 时自增（计划 1 预留）。

- [x] **Step 1: 写失败测试（tests/test_context_worker.py 追加）**

```python
def test_coordinator_round_robins_sites_and_respects_slots(tmp_path):
    from dsherp.context_worker import Coordinator
    claims={'a':['r1','r2'],'b':['r3']};finished=[]
    def make_client(site):
        def handler(request):
            method=request.url.path.rsplit('.',1)[-1];body=json.loads(request.content)
            if method=='claim_run':
                queue=claims[site]
                return httpx.Response(200,json={'message':{'run_id':queue.pop(0),'scope_id':site*64,'capability':'c','domain':'query',
                    'budget':{'run_total_seconds':300}} if queue else {}})
            if method=='finish_run':finished.append((site,body['run_id']));return httpx.Response(200,json={'message':{'status':'Succeeded','provider_failures':0}})
            return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
        return httpx.Client(base_url='http://'+site,transport=httpx.MockTransport(handler))
    sites=[{'site':s,'client':make_client(s),'business':{'business_url':'http://x','site':s}} for s in ('a','b')]
    executed=[]
    def execute(task,settings,directory,timeout):
        executed.append((task['site'],task['run_id'],timeout));return {'status':'Succeeded','answer':'ok'}
    c=Coordinator(sites,lambda:SETTINGS,slots=2,execute=execute,breaker=None,probe=lambda:True,state_root=tmp_path)
    assert c.tick(now=0)==2            # 一轮各站一次：a→r1，b→r3
    c.wait_idle()
    assert c.tick(now=1)==1 and c.wait_idle() is None   # a→r2
    assert sorted(x[1] for x in executed)==['r1','r2','r3'] and all(x[2]==330 for x in executed)
    assert sorted(finished)==[('a','r1'),('a','r2'),('b','r3')]


def test_run_claimed_never_escapes_and_records_provider_failure(tmp_path):
    from dsherp.context_worker import Coordinator
    from dsherp.provider_circuit import CircuitBreaker
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1]
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'a'*64,'capability':'c','domain':'query','budget':{'run_total_seconds':300}}})
        if method=='finish_run':return httpx.Response(403,json={'exc_type':'PermissionError'})   # 取消瞬间的 403
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':1}})
    client=httpx.Client(base_url='http://a',transport=httpx.MockTransport(handler))
    breaker=CircuitBreaker()
    c=Coordinator([{'site':'a','client':client,'business':{'business_url':'http://x','site':'a'}}],lambda:SETTINGS,slots=1,
                  execute=lambda *a,**k:{'status':'Succeeded','answer':'ok'},breaker=breaker,probe=lambda:False,state_root=tmp_path)
    assert c.tick(now=0)==1;c.wait_idle()      # 不抛出
    assert breaker.state=='closed'             # 403 不是 provider 失败
    def failing(*a,**k):raise RuntimeError('boom')
    c.execute=failing
    for i in range(3):
        c.tick(now=10+i);c.wait_idle()
    assert breaker.state=='open' and c.tick(now=20)==0   # 打开后不领取
```

第二个用例要求 `finish_run` 响应 `provider_failures` 缺失且执行异常时把 outcome 记为 `provider_failure`——**约定**：runner 异常（容器非零退出）在服务端事件里若有 `model_error`，`finish_run` 会回传 `provider_failures>=1`；执行方在 S1 未做该统计，因此本任务同时在 `finish_run` 返回值加 `provider_failures`（`frappe.db.count('DS Run Event',{'run':run.name,'kind':'model_error'})`）。测试里 `finish_run` 返回 403 的路径以 `RuntimeError` 兜底判为 `provider_failure`，与实现约定一致：**执行异常且无法拿到服务端计数 → 按 provider_failure 计**（保守，宁可早熔断）。

- [x] **Step 2: 确认失败** → `ImportError: Coordinator`。
- [x] **Step 3: 实现**：`normalize_profile`、`Coordinator`（`ThreadPoolExecutor`、`wait_idle()` 等待全部 future、每站 `last_claim` 与轮转索引、`busy` 计数）；`run_once`/`poll_once` 保留为单站兼容包装（`--once` 与既有集成测试用），内部改调 `Coordinator` 的 `run_claimed`。`main` 使用 `normalize_profile`，为每站建 client；`worker_log.configure` 加入全部站的 `api_secret`。
- [x] **Step 4: 运行** `.venv/bin/python -m pytest tests/test_context_worker.py tests/test_v16_deployment_contract.py -q` → PASS（LaunchAgent 契约测试不需要改参数）。
- [x] **Step 5: 提交** `feat: worker 多站槽位协调与崩溃安全主循环`。

### Task 2.3：合并本机两份 profile 并重启 worker

- [x] 写 `infra/merge_context_worker_profiles.py`：读取 `.runtime/context-worker.json` 与 `.runtime/context-worker-daily.json`，输出 `.runtime/context-worker-sites.json`（0600，`sites` 两项，`slots:3`）；不改旧文件。
- [x] `infra/render_context_worker_launch_agent.py` 的 `--profile` 指向新文件；按计划 1 的做法停止旧 LaunchAgent、重建 plist、bootstrap 一次；核对 pid 唯一、`/metrics` 含 `dsherp_slots_busy`。
- [x] 提交 `feat: 本机 worker 服务 alpha 与 daily 两站`。

**S2 自检门**：非集成全绿；`curl -s 127.0.0.1:9109/metrics | grep -E '^dsherp_(slots_busy|provider_circuit_open|claims_total)'` 三行入档；停常驻 worker 后跑 `tests/integration/test_context_worker_chain.py`（单站兼容路径）绿；恢复 worker 并记录 pid。

---

## 阶段 S3：runner 超时链、取消落地与 NeedsInput 优雅停止

### Task 3.1：预算随 run.json 进入容器

**Files:**
- Modify: `dsherp/context_worker.py`（`run_claimed` 把 `task['budget']` 原样写入 run.json，已随 `{**task,**settings}` 进入）
- Modify: `dsherp/session_runtime.py`（`open_runtime` 的 `request_timeout_seconds` 改读 `run_config` 里的 `budget.model_request_timeout_seconds`，缺失时 fastfail）
- Modify: `config/runtime-files.json` 无需变（文件已在清单）
- Test: `tests/test_session_runtime.py`

- [x] **Step 1: 写失败测试**：`open_runtime(settings,dir,'s',resume=False,run_config=path)` 中 run.json 无 `budget` → `ValueError('Missing run budget')`；有 `budget.model_request_timeout_seconds=90` → `DeepSeekHarness` 收到 `request_timeout_seconds=90`（monkeypatch `dsherp.session_runtime.DeepSeekHarness` 捕获 kwargs）。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**：`_request_timeout_seconds(domain)` 删除，改 `_budget(run_config)`；无 `run_config`（纯上下文模式）保持 90。
- [x] **Step 4: 运行** `tests/test_session_runtime.py tests/test_runtime_revision.py -q` → PASS。
- [x] **Step 5: 提交** `feat: 模型请求超时由服务端预算下发`。

### Task 3.2：总时长 deadline、daemon 线程与 5 秒取消

**Files:**
- Modify: `dsherp/context_runner.py`（`monitored_run`、`run_business`）
- Test: `tests/test_context_runner.py`

**Interfaces:**
- `monitored_run(runtime,question,session_id,status,*,poll_interval=2,record=None,deadline=None,grace=5)`：
  - 模型调用在 `threading.Thread(daemon=True)` 中执行（不再用 `ThreadPoolExecutor`，避免退出时 `shutdown(wait=True)` 阻塞）；结果经 `queue.Queue` 回传。
  - 轮询：每 `poll_interval` 查 `status()`；`Cancelling` → `cancel()`，最多等 `grace` 秒线程结束，无论是否结束都返回 `{'status':'Cancelled','answer':''}`；`NeedsInput` → `cancel()` 后返回 `{'status':'NeedsInput','answer':status_dict.get('needs_input','')}`（S4 之前 `run_status` 尚无该键，取空串）；超过 `deadline`（单调时钟）→ `cancel()`，记事件 `runtime_failed {'reason':'run_total_exceeded'}`，抛 `RuntimeError('Run time budget exceeded')`。
  - `status()` 回调改为返回完整 dict（`run_status` 响应），`check()` 取 `['status']`。
- `run_business`：`deadline=time.monotonic()+config['budget']['run_total_seconds']`；容器 `main()` 在返回前若有 daemon 线程未结束，直接 `os._exit(code)`（在 `print` 与 `flush` 之后），避免解释器等待。

- [x] **Step 1: 写失败测试**

```python
def test_cancel_returns_within_grace_even_if_model_hangs(model_server,tmp_path):
    import time
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return {'status':'Cancelling' if state['received'].is_set() else 'Running'}
    started=time.monotonic()
    try:
        with open_runtime(settings,tmp_path,'hang-cancel',resume=False) as runtime:
            result=monitored_run(runtime,'wait','hang-cancel',status,poll_interval=.05,grace=1)
        assert result=={'status':'Cancelled','answer':''}
        assert time.monotonic()-started<8
    finally:state['release'].set()


def test_run_total_deadline_cancels_and_fails(model_server,tmp_path):
    import time
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    recorded=[]
    try:
        with open_runtime(settings,tmp_path,'deadline-run',resume=False) as runtime:
            with pytest.raises(RuntimeError,match='budget'):
                monitored_run(runtime,'wait','deadline-run',lambda:{'status':'Running'},poll_interval=.05,deadline=time.monotonic()+1,record=recorded.append,grace=1)
        items=[i for b in recorded for i in b]
        assert items[-1]['kind']=='runtime_failed' and items[-1]['payload'].get('reason')=='run_total_exceeded'
    finally:state['release'].set()


def test_needs_input_status_stops_gracefully(model_server,tmp_path):
    settings,requests,state=model_server
    state.update(received=threading.Event(),release=threading.Event())
    def status():return {'status':'NeedsInput','needs_input':'请告诉我仓库'} if state['received'].is_set() else {'status':'Running'}
    try:
        with open_runtime(settings,tmp_path,'needs-input',resume=False) as runtime:
            result=monitored_run(runtime,'wait','needs-input',status,poll_interval=.05,grace=1)
        assert result=={'status':'NeedsInput','answer':'请告诉我仓库'}
    finally:state['release'].set()
```

- [x] **Step 2: 确认失败** → `TypeError: unexpected keyword 'deadline'`/`grace`。
- [x] **Step 3: 实现**；既有用例里 `status` 回调返回字符串的，改为 dict 形式（同一提交内更新测试）。
- [x] **Step 4: 运行** `tests/test_context_runner.py tests/test_context_runtime.py tests/test_model_guard.py tests/test_context_compaction.py -q` → PASS。
- [x] **Step 5: 提交** `feat: runner 总时长预算与五秒取消`。

### Task 3.3：worker 侧容器超时与 NeedsInput 结果

**Files:**
- Modify: `dsherp/context_worker.py`（`run_container` 的 `timeout` 参数；结果校验允许 `NeedsInput`）
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`finish_run` 接受 `NeedsInput`：要求 `run.status=='NeedsInput'`，把 `answer` 写入 `needs_input` 字段并保持 status；`_run` 允许 NeedsInput 状态通过凭据校验以便 finish）
- Test: `tests/test_context_worker.py`、`tests/integration/test_needs_input.py`（S5 完整链路，此处只做单测）

- [x] **Step 1: 写失败测试**：`run_container` 对 `{'status':'NeedsInput','answer':'问'}` 不再抛 `Invalid business runtime result`；`run_claimed` 把 NeedsInput 结果原样 `finish_run` 且 `_note_run('NeedsInput',...)` 清零连续失败。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**。
- [x] **Step 4: 运行** → PASS。
- [x] **Step 5: 提交** `feat: worker 接受 NeedsInput 结果并按预算设容器超时`。

**S3 自检门**：非集成全绿；停常驻 worker 后跑 `tests/integration/test_context_worker_chain.py` 绿并记录事件序列里的 `lease_renewed`（若运行短于 90s 不会出现，注明）；恢复 worker。

---

## 阶段 S4：工具错误三分类透传与 `erp_request_input`

### Task 4.1：MCP 层错误分类

**Files:**
- Modify: `dsherp/context_mcp.py`（`post`、`BusinessRuntimeError`、新增 `ToolFailure`、`classify_failure`）
- Modify: `dsherp/read_tools.py`（无改动，错误由 `invoke` 抛出）
- Test: `tests/test_context_mcp.py`

**Interfaces:**
- `classify_failure(status_code: int, body: dict|None, transport_error: Exception|None) -> dict`：返回 `{'error_class': 'validation'|'permission'|'transient', 'message': str, 'retryable': bool, 'http_status': int|None}`。规则：transport 异常/超时/5xx/502-504 → transient（retryable True）；401/403 或 `exc_type=='PermissionError'` → permission（False）；417/400 及其他 4xx → validation（False）。`message` 取 Frappe 响应的 `_server_messages` 首条 `message` 字段，其次 `exception` 冒号后的文本，截断 500 字，不含栈。
- `post(...)` 在非 200 时抛 `ToolFailure(classification)`，其 `str()` 为一行 JSON：`{"error_class":..,"message":..,"retryable":..}`——FastMCP 会把异常文本作为 `isError=true` 的工具结果返回给模型；worker/runner 侧对 `claim_run`/`finish_run` 的既有 `BusinessRuntimeError` 语义保留（`ToolFailure` 继承 `BusinessRuntimeError` 并保留 `status_code`）。

- [x] **Step 1: 写失败测试**

```python
def test_tool_failures_are_classified_and_serialized():
    from dsherp.context_mcp import classify_failure,ToolFailure,post
    import httpx,json
    assert classify_failure(417,{'exc_type':'ValidationError','_server_messages':json.dumps([json.dumps({'message':'库存不足'})])},None)=={'error_class':'validation','message':'库存不足','retryable':False,'http_status':417}
    assert classify_failure(403,{'exception':'frappe.exceptions.PermissionError: 无权读取'},None)['error_class']=='permission'
    assert classify_failure(503,None,None)['error_class']=='transient' and classify_failure(None,None,httpx.ReadTimeout('t'))['retryable'] is True
    def handler(request):return httpx.Response(417,json={'exc_type':'ValidationError','_server_messages':json.dumps([json.dumps({'message':'仓库不存在'})])})
    with httpx.Client(base_url='http://x',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as caught:post(client,'run_tool',run_id='r',capability='c',tool='erp_read_record',arguments={})
    payload=json.loads(str(caught.value))
    assert payload=={'error_class':'validation','message':'仓库不存在','retryable':False} and caught.value.status_code==417
```

- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**（`_server_messages` 是 JSON 字符串数组，每项又是 JSON 字符串；解析失败回退到 `exception`）。
- [x] **Step 4: 运行** `tests/test_context_mcp.py tests/test_context_worker.py -q` → PASS。
- [x] **Step 5: 提交** `feat: 工具错误三分类透传给模型`。

### Task 4.2：`erp_request_input` 工具与服务端 NeedsInput 落地

**Files:**
- Modify: `dsherp/context_mcp.py`（三个领域都注册 `erp_request_input(question: str) -> dict`）
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`_run_tool` 分支：`tool=='erp_request_input'` → 校验 1–2000 字，`frappe.db.set_value(run,{'status':'NeedsInput','needs_input':question})`，事件 `needs_input {'question_chars':n}`，返回 `{'status':'NeedsInput'}`；两侧 `SERVER_KINDS` 加 `needs_input`）
- Modify: `config/dsh-context.yml` 无需改（工具由 MCP server 暴露）
- Test: `tests/test_context_mcp.py`（工具面契约）、`tests/integration/test_needs_input.py`

- [x] **Step 1: 写失败测试**：MCP 服务器三个领域都列出 `erp_request_input` 且 `additionalProperties:false`（沿用 `_forbid_extra_tool_arguments`）；集成：Running 运行调用 `run_tool(...,'erp_request_input',{'question':'请指定仓库'})` 后状态 NeedsInput、`needs_input` 字段落库、`run_status` 返回 `{'status':'NeedsInput','needs_input':'请指定仓库',...}`；随后 `finish_run(status='NeedsInput',answer='请指定仓库')` 成功且 `capability_hash` 清空；`_public` 的 `active_run` 为 None（用户可继续发消息）。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**（`run_status` 响应增加 `needs_input` 文本；`_public` 的消息里 `answer` 为空时若 `needs_input` 非空则 `answer=needs_input` 并附 `status='NeedsInput'`）。
- [x] **Step 4: 运行** → PASS；三站 migrate 若 S1 已加字段则无需再迁移。
- [x] **Step 5: 提交** `feat: 模型可用 erp_request_input 向用户索取信息`。

### Task 4.3：模型侧规则进 skill 并更新清单

**Files:**
- Modify: `business-skills/erp-query/SKILL.md`（version 1.3.0 → 1.4.0）、`business-skills/erp-operation/SKILL.md`（2.1.0 → 2.2.0）、`business-skills/erp-configuration/SKILL.md`（1.0.0 → 1.1.0）
- Modify: `config/business-skills.json`（三条 version 与 sha256）
- Test: `tests/test_business_skills.py`、`tests/test_model_guard.py`

- [x] 在三份 SKILL.md 增加同一节"工具错误与做不了的出口"：`error_class=validation` → 修正参数最多重试一次，仍失败则用 `erp_request_input` 向用户说明；`permission` → 不得重试，直接告知用户无权并结束；`transient` → 原样重试一次，再失败则结束并说明；不能自行猜测缺失信息，用 `erp_request_input` 索取。
- [x] 重新计算 sha256 写入清单；`tests/test_business_skills.py` 中断言 version 字符串的用例同步更新（`1.4.0`、`2.2.0`、`1.1.0`）。
- [x] 运行 `tests/test_business_skills.py tests/test_model_guard.py tests/test_runtime_revision.py -q` → PASS。
- [x] 提交 `feat: skill 增加错误分类与索取信息规则`。

### Task 4.4：模型收到每类错误后的行为测试（替身）

**Files:**
- Test: `tests/test_error_taxonomy_behavior.py`

- [x] 用 `model_server` 与容器外的 `create_server` + `httpx.MockTransport`（同 `tests/test_context_mcp.py` 的方式）构造三类工具失败；断言模型收到的下一次请求（`requests[1]['messages']` 中最后一条 tool 结果）文本包含 `"error_class":"validation"`/`permission`/`transient` 与服务端 `message`；每类一条用例。此处不断言模型如何反应（那是评估集，计划 6）。
- [x] 提交 `test: 三类工具错误进入模型上下文`。

**S4 自检门**：非集成全绿；`node --test runtime/*.test.cjs` 绿；`tests/integration/test_needs_input.py` 绿；证据文档记录一条真实（替身）NeedsInput 运行的事件序列。

---

## 阶段 S5：出口状态机与完成判定交叉校验

### Task 5.1：提案 Rejected / Expired 与拒绝端点

**Files:**
- Modify: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_operation_proposal/ds_operation_proposal.json`（status options 加 `Rejected\nExpired`）
- Modify: `frappe_app/dsherp_bridge/operations.py`（新增 `reject(proposal_id, digest, request_id)`；`expire_proposals()`；`confirm` 对 Rejected/Expired 的报错文案）
- Modify: `frappe_app/dsherp_bridge/hooks.py`（cron `*/10 * * * *` → `dsherp_bridge.operations.expire_proposals`）
- Modify: `frappe_app/dsherp_bridge/context_events.py`（`SERVER_KINDS` 加 `proposal_rejected`、`proposal_expired`）
- Test: `tests/integration/test_proposal_exits.py`

**Interfaces:**
- `reject(proposal_id, digest, request_id)`：whitelisted POST；仅提案所属会话的 owner；`digest` 必须匹配；仅 Pending 可拒；写 `status='Rejected'`、`rejected_by`（新增 Data 字段）、`rejected_request_id`；幂等：同 request_id 重复调用返回当前状态；事件挂在提案的 `model_run` 上：`proposal_rejected {'proposal':id}`。
- `expire_proposals()`：Pending 且 `expires_at<=now` → `Expired`，每条记事件 `proposal_expired`；scheduler 每 10 分钟；返回处理数。
- `confirm`：`status in ('Rejected','Expired')` → `frappe.throw('提案已被拒绝/已过期，请重新提出操作')`（分别）。
- `get_proposal` 输出 `status` 原样；前端标签 S6 补。

- [x] **Step 1: 写失败集成测试**：合成用户创建 Pending 提案（沿用 `test_operation_proposals.py` 的构造方式）→ `reject` 成功且 status Rejected、事件 `proposal_rejected`；再次 `confirm` 抛 `已被拒绝`；另一用户 `reject` 抛 PermissionError；构造 `expires_at` 过去的 Pending 提案 → `expire_proposals()` 返回 1 且状态 Expired、事件 `proposal_expired`；`confirm` 抛 `已过期`。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**；三站 migrate（DocType 变更）退出码入档。
- [x] **Step 4: 运行** 本测试 + `tests/integration/test_operation_proposals.py -q` → PASS。
- [x] **Step 5: 提交** `feat: 提案可拒绝并定时过期`。

### Task 5.2：完成判定交叉校验

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`finish_run` Succeeded 分支）
- Modify: `frappe_app/dsherp_bridge/context_api.py`（`_public` 消息增加 `answer_flagged`）
- Test: `tests/integration/test_completion_crosscheck.py`

**Interfaces:**
- `finish_run(status='Succeeded')` 计算：`proposals = count(DS Operation Proposal where model_run=run)`、`executions = count(DS Execution Record Succeeded whose proposal.model_run=run)`、`sources = len(run.sources)`；若 `answer` 匹配完成自述正则 `(已|成功)(创建|提交|保存|完成|生成|录入|执行)` 且 `executions==0` → `answer_flagged=1`，事件 `unverified_completion_claim {'proposals':..,'executions':..}`；不改变 status（不把回答判为失败，只标记）。`finished` 事件 payload 加 `proposals/executions/sources`。
- `_public` 每条消息带 `answer_flagged`；前端 S6 显示提示"回答声称已完成，但没有对应的执行记录；请以待确认/执行记录为准"。

- [x] **Step 1: 写失败集成测试**：Running 运行读一次记录后 `finish_run(Succeeded, answer='已创建销售订单 SO-001')` → `answer_flagged==1`、最后事件 `unverified_completion_claim`；同样运行 `answer='共找到 3 条物料'` → `answer_flagged==0`；`get_session` 消息含 `answer_flagged`。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**。
- [x] **Step 4: 运行** → PASS；`tests/integration/test_run_events.py` 仍绿。
- [x] **Step 5: 提交** `feat: 完成自述与执行记录交叉校验`。

### Task 5.3：运维快照与告警补充

**Files:**
- Modify: `frappe_app/dsherp_bridge/ops.py`（快照增加 `needs_input`、`queue_expired_24h`、`proposals_expired_24h`）
- Modify: `dsherp/alerts.py`（规则：`queue_expired_24h>10` → `queue_expiring`（warning））
- Test: `tests/integration/test_ops_snapshot.py`、`tests/test_alerts.py`

- [x] 测试先红后绿；提交 `feat: 快照与告警覆盖排队过期与待补充输入`。

**S5 自检门**：`tests/integration/test_proposal_exits.py tests/integration/test_completion_crosscheck.py tests/integration/test_needs_input.py tests/integration/test_ops_snapshot.py -q` 全绿；三站 migrate 退出码；scheduled profile 下 10 分钟内 `Scheduled Job Type` 出现 `expire_proposals` 且 alpha 上 C0 基线里的过期 Pending 提案变为 Expired（数量入档）。

---

## 阶段 S6：前端健壮性

### Task 6.1：服务端原因透传

**Files:**
- Modify: `frontend/src/context-api.js`（`contextApi` 非 2xx 分支）
- Test: `frontend/src/context-api.test.js`

- [x] **Step 1: 写失败测试**：mock `fetch` 返回 417 JSON `{exc_type:'ValidationError',_server_messages:JSON.stringify([JSON.stringify({message:'库存不足'})])}` → 抛出 `Error('库存不足')` 且 `error.kind==='validation'`；503 且 `_server_messages` 含"助手服务暂不可用" → `error.kind==='unavailable'`；无 JSON 的 502 → 保留现文案且 `kind==='transient'`；401/403 行为不变（`kind==='permission'`）。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**：解析 `_server_messages`/`exception`，构造 `Error` 并附 `kind`、`httpStatus`；导出 `describeError(error)` 供组件决定是否允许重试（`transient`/`unavailable` 可重试，其余不可）。
- [x] **Step 4: 运行** `cd frontend && npm test -- context-api` → PASS。
- [x] **Step 5: 提交** `feat: 前端展示服务端业务原因`。

### Task 6.2：ErrorBoundary、轮询退避、新状态标签与拒绝按钮

**Files:**
- Create: `frontend/src/ErrorBoundary.jsx`（class 组件，`componentDidCatch` 记录到 console 并显示"助手暂不可用，请重新加载"+ 重载按钮）
- Modify: `frontend/src/desk-context.jsx`、`frontend/src/agent-workbench-entry.js`（根节点包 ErrorBoundary）
- Modify: `frontend/src/ContextSidebar.jsx`（轮询错误不 `setSessions([])`；退避 5s→10s→20s→30s 上限；错误可重试时保留发送框）
- Modify: `frontend/src/agent-format.js`（`NeedsInput:['需要你补充信息','warning']`、`Rejected:['已拒绝','default']`、`Expired:['已过期','default']`）；`frontend/src/agent-transcript.js` 事件 label 表新增 `lease_renewed`→"租约续期"、`needs_input`→"请求用户补充"、`proposal_rejected`→"提案已拒绝"、`proposal_expired`→"提案已过期"、`unverified_completion_claim`→"完成自述未经核实"（tone danger）
- Modify: `frontend/src/OperationProposal.jsx`（Pending 时增加"拒绝"按钮 → `contextApi('reject_operation',{proposal_id,digest,request_id})`；`context-api.js` 的 methods 加 `reject_operation:['proposal_id','digest','request_id']` 与 URL 映射到 `dsherp_bridge.operations.reject`）
- Modify: `frontend/src/agent-turn.jsx`（`answer_flagged` 提示条；`NeedsInput` 消息以问题形式显示并聚焦输入框）
- Modify: `frontend/src/AgentWorkbench.jsx`（排队态"撤回"按钮调用既有 `cancel_run`）
- Test: `ErrorBoundary.test.jsx`、`ContextSidebar.test.jsx`、`OperationProposal.test.jsx`、`agent-format.test.js`、`AgentWorkbench.test.jsx`

- [x] 每项先写失败测试再实现；`node build.mjs` 重建 dist 并与源码一起提交；重启 alpha backend 后真实浏览器验证：拒绝一条提案、NeedsInput 问题显示、断网 5 秒后侧栏不清空会话，截图落档 `docs/engineering/evidence/runtime-reliability/`。
- [x] 提交 `feat: 前端错误降级、拒绝提案与新状态`。

**S6 自检门**：`cd frontend && npm test` 全绿；dist 重建后 `git diff --exit-code frappe_app/dsherp_bridge/public/dist` 为 0；三张截图路径入档。

---

## 阶段 S7：G5 负载与混沌、文档收口

### Task 7.1：负载脚本

**Files:**
- Create: `infra/load_runs.py`
- Create: `docs/engineering/runtime-reliability-evidence.md`（"G5 负载"节）

- [x] 脚本：读取 `.runtime/erp-users.json` 里的 3 个合成用户（`reader`/`writer` 等既有），对 alpha 各发 1 条只读问题（`request_id` 唯一）；对 daily 用 daily 的合成用户发 1 条；替身模型（把 worker 的 provider env 临时指向 `tests/conftest` 同款本地 SSE 替身，做法沿用计划 1 C3 演练：以专用 provider-env 文件启动一次性 worker，不动 `.env`）；轮询直到全部终态；输出每条运行的 `queued→claimed` 等待、总时长、状态，以及 `list_run_events` 的 kind 序列；断言：alpha 三条按序执行且无 Failed，daily 一条与 alpha 并行（其 `claimed` 时间早于 alpha 第二条），`run_status` 在 100 轮历史会话上的延迟与历史无关且满足预算——**2026-09-04 审计修订**：并发度取 worker 槽位数（`slots`，试点为 3）而不是 20，采样 20 次，nearest-rank P95 < 1s；同时在一个 0 轮历史的新会话上以同样方法采样，两者 P95 之比 ≤ 1.5。修订依据：验证栈 backend 为 0.5 CPU、1 worker × 2 threads，空操作 `ping` 在 20 并发下 P95 已达 0.40s，20 并发使请求在 CPU 上串行化，尾延迟 ≈ 20 × 单请求 55–60ms ≈ 1.0–1.2s，与 `run_status` 实现无关；20 并发是网页层容量测试，属计划 3 的部署规格。脚本仍须把 20 并发的 P95 作为"容量基线"打印并入档，但不作为门。失败分支必须先打印全部样本与 P95 再抛错。
- [x] 结果表入档；提交 `test: G5 负载脚本与证据`。

### Task 7.2：混沌演练

- [x] 三项各做一次并记录时间线：
  1. 运行中 `kill -9` 一次性 worker → 运行租约到期后被下一个 worker 清扫为 Failed（`expired lease_expired`），会话可继续发消息；
  2. provider env 指向 `127.0.0.1:9` 连发 3 条 → 熔断打开（`dsherp_provider_circuit_open 1`、告警 `provider_circuit_open`），恢复地址后 60s 内探针关闭熔断并成功处理 1 条；
  3. `docker stop dsherp-validation-backend-1` 10 秒后恢复 → worker 主循环不退出（pid 不变），日志出现 `worker_error` 后恢复领取。
- [x] 写入 `infra/chaos_drills.md`（可重复步骤）与证据文档；提交 `docs: 运行底座混沌演练证据`。

### Task 7.3：文档收口与终验

- [x] `docs/engineering/runtime-baseline.md`：预算表与配置键（`dsherp_run_budget`、`dsherp_site_concurrency`、`dsherp_model_policy`、worker profile `sites/slots`）。
- [ ] README 当前状态与测试数字；spec 实施顺序表计划 2 行标注"实施完成，待审计"；生产就绪审计文档把 R1–R9、A1 标为"计划 2 已处理"。（2026-09-05 最终独立审查未通过，状态已恢复为补修中。）
- [ ] 全量门：非集成、前端、Node、`tests/integration -q` 全量（常驻 worker 停止状态下），三站 migrate 退出码，恢复 worker 并记录 pid。（`c6b3e8e` 后需重跑。）
- [ ] 本计划复选框逐项核验后勾选；提交 `docs: 收口运行底座可靠性计划`。（终审阻断项关闭后再勾选。）

**S7 自检门 / 终验报告模板**（停下并原样提交给审计方）：

```text
提交列表（按阶段分组）
门禁：非集成 N passed；前端 N passed；Node 8/8；集成 N passed（耗时）
三站 migrate 最后一次退出码
预算表实际生效值（alpha site_config 摘要，不含密钥）
G5 负载表：run_id、queued→claimed 秒、总时长、状态、kind 序列
run_status P95
混沌三项时间线与结论
熔断指标与告警行
worker pid / 进程数 / 槽位 / 孤儿容器
偏离与 Deferred 清单
```

## 验证方式汇总

- 非集成：`.venv/bin/python -m pytest tests --ignore=tests/integration -q`
- runtime 插件：`node --test runtime/*.test.cjs`
- 前端：`cd frontend && npm test && node build.mjs`
- 集成：`.venv/bin/python -m pytest tests/integration -q`（常驻 worker 停止状态下）
- 运行态：`/metrics`、LaunchAgent 日志、`DS Ops Snapshot`、`Scheduled Job Type`

## 回退方案

- 任何阶段：revert 对应提交；DocType 新增字段与 status option 为向后兼容，可留。
- worker：LaunchAgent 指回旧 profile 文件即可回到单站模式（`normalize_profile` 兼容旧格式）。

## 需要用户授权/确认的点

1. 本计划不调用真实 provider；熔断探针 `GET /models` 为免费接口，但会携带真实 key 访问 provider，仅在熔断打开期执行——执行前在报告里说明一次。
2. 混沌演练第 3 项会短暂停止 alpha backend 容器（约 10 秒），期间四站中 alpha/daily 不可用；执行前告知。
3. 若 S1 的心跳背压导致既有集成测试大面积需要预置心跳，允许在 `tests/integration/conftest.py` 统一处理，不逐文件改。
