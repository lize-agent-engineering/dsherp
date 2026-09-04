# 运行底座可靠性实施证据

日期：2026-09-04。分支：`codex/runtime-reliability`。范围：计划 2 的 S0→S7。本文件记录本机隔离合成站与本地模型替身的实施证据，不代表生产租户部署、真实 provider 可用或生产上线。

## S0：预算表与基线

### 起始仓库状态

```text
## codex/runtime-reliability
fe48365 (HEAD -> codex/runtime-reliability) docs: 计划 2 运行底座可靠性实施计划
```

起始工作区无 staged、unstaged 或 untracked 变更。

### 原实现位置

命令：

```sh
nl -ba frappe_app/dsherp_bridge/context_execution.py | sed -n '84,118p'
nl -ba dsherp/context_worker.py | sed -n '108,118p'
nl -ba dsherp/context_worker.py | sed -n '231,241p'
nl -ba dsherp/session_runtime.py | sed -n '14,21p'
```

原始输出：

```text
    84	        frappe.throw('运行配置摘要无效')
    85	    frappe.db.rollback()
    86	    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    87	    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now_datetime()]},pluck='name',order_by='creation asc, name asc'):
    88	        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':'运行已过期，未自动重试','capability_hash':''})
    89	        events.record_safely(name,'expired',{'reason':'lease_expired'})
    90	    if frappe.db.exists('DS Model Run',{'status':['in',['Running','Cancelling']]}):return None
    91	    names=frappe.get_all('DS Model Run',filters={'status':'Queued'},pluck='name',order_by='creation asc',limit_page_length=1)
    92	    if not names:return None
    93	    run=frappe.get_doc('DS Model Run',names[0],for_update=True)
    94	    if run.status!='Queued':return None
    95	    try:
    96	        with _actor(run) as identity:
    97	            conversation=conversations._conversation(run.conversation)
    98	            conversations._public(conversation)
    99	            permission_revision=context_permissions.run_revision(run.owner,run.domain)
   100	    except (frappe.PermissionError,frappe.DoesNotExistError):
   101	        error='当前用户已无法读取会话来源'
   102	        frappe.db.set_value('DS Model Run',run.name,{'status':'Failed','error':error,'capability_hash':''})
   103	        events.record_safely(run.name,'finished',{'status':'Failed','error':error})
   104	        return None
   105	    capability=secrets.token_urlsafe(32)
   106	    domain=run.domain
   107	    if domain not in ('query','operation','configuration'):frappe.throw('未知业务领域')
   108	    combined_revision=hashlib.sha256((permission_revision+runtime_revision+domain).encode()).hexdigest()
   109	    if identity:
   110	        combined_revision=hashlib.sha256((combined_revision+conversations._json(identity)).encode()).hexdigest()
   111	    if conversation.runtime_revision!=combined_revision:
   112	        conversation.runtime_session=uuid.uuid4().hex
   113	        frappe.db.set_value('DS Conversation',conversation.name,{'runtime_session':conversation.runtime_session,'runtime_revision':combined_revision})
   114	    frappe.db.set_value('DS Model Run',run.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
   115	        'expires_at':add_to_date(now_datetime(),seconds=180),'permission_revision':permission_revision,'runtime_revision':runtime_revision})
   116	    events.record_safely(run.name,'claimed',{'domain':domain,'permission_revision':permission_revision,
   117	        'runtime_revision':runtime_revision,'native_session_id':conversation.runtime_session})
   118	    return {'run_id':run.name,'session_id':run.conversation,'native_session_id':conversation.runtime_session,
   108	        secret=Path(temporary)/'run.json'
   109	        fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   110	        with os.fdopen(fd,'w') as file:
   111	            json.dump({**task,**settings},file)
   112	        name='dsherp-context-'+uuid.uuid4().hex
   113	        try:
   114	            result=subprocess.run(docker_command(ROOT,secret,directory,name),capture_output=True,text=True,timeout=140)
   115	            if result.returncode:
   116	                # Only the runner's value-free stack diagnostic, never raw SDK
   117	                # stderr, provider exceptions, request bodies or credentials.
   118	                for line in result.stderr.splitlines():
   231	                    settings=load_settings(args.provider_env)
   232	                    if args.once:
   233	                        run_once(client,settings,state_root,business=business)
   234	                        return 0
   235	                    monitor_ops(client,notifier,ops_state)
   236	                    poll_once(client,settings,state_root,business=business)
   237	                    time.sleep(3)
   238
   239
   240	if __name__=='__main__':raise SystemExit(main())
    14	ROOT=Path(__file__).resolve().parents[1]
    15
    16
    17	def _request_timeout_seconds(domain):
    18	    return 120 if domain=='operation' else 90
    19
    20
    21	class OpenedSession(BaseModel):
```

基线结论：`claim_run` 在第 90 行使用全站单运行闸门；租约在第 115 行固定为 180 秒；`run_container` 在第 114 行固定为 140 秒；模型请求超时在 `session_runtime.py` 第 17–18 行按 operation 120 秒、其他领域 90 秒散落；worker 主循环在第 237 行固定轮询休眠 3 秒。

### alpha 站状态基线

只读命令在 `dsherp-validation-backend-1` 内初始化 `dsherp-validation.localhost`，按 DocType 当前 status options 逐项调用 `frappe.db.count`，并统计 `Pending` 且 `expires_at <= now_datetime()` 的提案。退出码与原始输出：

```text
exit_code=0
{"expired_pending_proposals": 4, "run_status_counts": {"Cancelled": 0, "Cancelling": 0, "Failed": 16, "Queued": 0, "Running": 0, "Succeeded": 43}, "site": "dsherp-validation.localhost"}
```

### Task 0.1 RED

命令：`.venv/bin/python -m pytest tests/integration/test_run_budget.py -q`

退出码：1。原始尾部：

```text
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 5, in <module>
E         ModuleNotFoundError: No module named 'dsherp_bridge.run_budget'
E
E       assert (1 == 0)
E        +  where 1 = CompletedProcess(args=['docker', 'exec', '-i', 'dsherp-validation-backend-1', '/home/frappe/frappe-bench/env/bin/pytho...ll last):\n  File "<stdin>", line 5, in <module>\nModuleNotFoundError: No module named \'dsherp_bridge.run_budget\'\n').returncode

tests/integration/test_run_budget.py:52: AssertionError
=========================== short test summary info ============================
FAILED tests/integration/test_run_budget.py::test_budget_defaults_and_invalid_override_fastfail
1 failed in 27.94s
```

### S0 自检门

预算模块集成测试，命令：`.venv/bin/python -m pytest tests/integration/test_run_budget.py -q`

```text
.                                                                        [100%]
1 passed in 25.60s
```

退出码：0。

非集成完整门，命令：`.venv/bin/python -m pytest tests --ignore=tests/integration -q`

原始输出 chunk 1：

```text
........................................................................ [ 42%]
.......................
```

原始输出 chunk 2：

```text
................................................. [ 85%]
........................                                                 [100%]
168 passed in 51.33s
```

退出码：0。

Node Runtime 完整门，命令：`node --test runtime/*.test.cjs`

```text
✔ dispose waits for native creation and releases exactly the completed handle (1.727167ms)
✔ failed creation remains a request error but cannot break cleanup (1.14075ms)
✔ business catalog rejects unlisted skill directories (4.329459ms)
✔ ordinary and direct compaction requests both require authorization (0.77325ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.200083ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.00725ms)
✔ drift during a response cannot produce a successful terminal chunk (0.800167ms)
✔ finish and errors are reported without affecting the stream (0.318917ms)
ℹ tests 8
ℹ suites 0
ℹ pass 8
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 62.709792
```

退出码：0。

Task 0.1 提交：`bbd1f93692901a2ac5b855fc9df04fb738b62fb0`。实现审查与控制器独立重跑均未发现偏离；`run_budget.py` 只由 Frappe 服务端加载，不是一次性业务 Runtime 容器输入，因此未加入 `config/runtime-files.json`。

## S1：服务端并发、租约、心跳与队列过期

### Task 1.1 RED→GREEN

RED 命令：`.venv/bin/python -m pytest tests/test_v16_framework_adaptations.py::test_ds_model_run_persists_needs_input_status_and_queue_fields -q`

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_ds_model_run_persists_needs_input_status_and_queue_fields ________

>       assert "NeedsInput" in fields["status"]["options"].splitlines()
E       AssertionError: assert 'NeedsInput' in ['Queued', 'Running', 'Cancelling', 'Cancelled', 'Succeeded', 'Failed']

tests/test_v16_framework_adaptations.py:186: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v16_framework_adaptations.py::test_ds_model_run_persists_needs_input_status_and_queue_fields
1 failed in 0.03s
```

退出码：1。

控制器 GREEN 重跑，命令同上：

```text
.                                                                        [100%]
1 passed in 0.01s
```

退出码：0。Task 1.1 提交：`6b7bb2e9aa3e5e3574ad1b97cded6e87f80814af`。

### Task 1.1 三站 migrate

alpha：`docker exec dsherp-validation-backend-1 bench --site dsherp-validation.localhost migrate`

原始尾部：

```text
Updating DocTypes for dsherp_bridge : [========================================] 100%
Syncing jobs...
Syncing fixtures...
Syncing dashboards...
Updating Dashboard for frappe
Updating Dashboard for erpnext
Updating Dashboard for dsherp_bridge
Syncing customizations...
Syncing languages...
Flushing deferred inserts...
Removing orphan doctypes...
Removing orphan Workspaces
Removing orphan Dashboards
Removing orphan Pages
Removing orphan Reports
Removing orphan Notifications
Removing orphan Workspace Sidebars
Removing orphan Desktop Icons
Syncing portal menu...
Updating installed applications...
Executing `after_migrate` hooks...

Queued rebuilding of search index for dsherp-validation.localhost
```

退出码：0。

daily：`docker exec dsherp-validation-backend-1 bench --site dsherp-daily.localhost migrate`

原始尾部：

```text
Updating DocTypes for dsherp_bridge : [==============                          ] 35%
Updating DocTypes for dsherp_bridge : [================                        ] 41%
Updating DocTypes for dsherp_bridge : [==================                      ] 47%
Updating DocTypes for dsherp_bridge : [=====================                   ] 52%
Updating DocTypes for dsherp_bridge : [=======================                 ] 58%
Updating DocTypes for dsherp_bridge : [=========================               ] 64%
Updating DocTypes for dsherp_bridge : [============================            ] 70%
Updating DocTypes for dsherp_bridge : [==============================          ] 76%
Updating DocTypes for dsherp_bridge : [================================        ] 82%
Updating DocTypes for dsherp_bridge : [===================================     ] 88%
Updating DocTypes for dsherp_bridge : [=====================================   ] 94%
Updating DocTypes for dsherp_bridge : [========================================] 100%
Syncing jobs...
Syncing fixtures...
Syncing dashboards...
Updating Dashboard for frappe
Updating Dashboard for erpnext
Updating Dashboard for dsherp_bridge
Syncing customizations...
Syncing languages...
Flushing deferred inserts...
Removing orphan doctypes...
Removing orphan Workspaces
Removing orphan Dashboards
Removing orphan Pages
Removing orphan Reports
Removing orphan Notifications
Removing orphan Workspace Sidebars
Removing orphan Desktop Icons
Syncing portal menu...
Updating installed applications...
Executing `after_migrate` hooks...

Queued rebuilding of search index for dsherp-daily.localhost
```

退出码：0。

beta：`docker exec dsherp-validation-beta-backend-1 bench --site dsherp-beta.localhost migrate`

原始尾部：

```text
Updating DocTypes for dsherp_bridge : [==============                          ] 35%
Updating DocTypes for dsherp_bridge : [================                        ] 41%
Updating DocTypes for dsherp_bridge : [==================                      ] 47%
Updating DocTypes for dsherp_bridge : [=====================                   ] 52%
Updating DocTypes for dsherp_bridge : [=======================                 ] 58%
Updating DocTypes for dsherp_bridge : [=========================               ] 64%
Updating DocTypes for dsherp_bridge : [============================            ] 70%
Updating DocTypes for dsherp_bridge : [==============================          ] 76%
Updating DocTypes for dsherp_bridge : [================================        ] 82%
Updating DocTypes for dsherp_bridge : [===================================     ] 88%
Updating DocTypes for dsherp_bridge : [=====================================   ] 94%
Updating DocTypes for dsherp_bridge : [========================================] 100%
Syncing jobs...
Syncing fixtures...
Syncing dashboards...
Updating Dashboard for frappe
Updating Dashboard for erpnext
Updating Dashboard for dsherp_bridge
Syncing customizations...
Syncing languages...
Flushing deferred inserts...
Removing orphan doctypes...
Removing orphan Workspaces
Removing orphan Dashboards
Removing orphan Pages
Removing orphan Reports
Removing orphan Notifications
Removing orphan Workspace Sidebars
Removing orphan Desktop Icons
Syncing portal menu...
Updating installed applications...
Executing `after_migrate` hooks...

Queued rebuilding of search index for dsherp-beta.localhost
```

退出码：0。

迁移后的 live metadata 只读核验：

```text
alpha exit=0
{"fields": {"answer_flagged": "Check", "needs_input": "Long Text", "provider_failures": "Int", "queue_expires_at": "Datetime"}, "site": "dsherp-validation.localhost", "status_options": ["Queued", "Running", "Cancelling", "Cancelled", "Succeeded", "Failed", "NeedsInput"]}
daily exit=0
{"fields": {"answer_flagged": "Check", "needs_input": "Long Text", "provider_failures": "Int", "queue_expires_at": "Datetime"}, "site": "dsherp-daily.localhost", "status_options": ["Queued", "Running", "Cancelling", "Cancelled", "Succeeded", "Failed", "NeedsInput"]}
beta exit=0
{"fields": {"answer_flagged": "Check", "needs_input": "Long Text", "provider_failures": "Int", "queue_expires_at": "Datetime"}, "site": "dsherp-beta.localhost", "status_options": ["Queued", "Running", "Cancelling", "Cancelled", "Succeeded", "Failed", "NeedsInput"]}
```

迁移与备份恢复影响：四列均为向后兼容的 nullable 或默认 0 字段，status 仅追加选项，不需要历史数据回填；迁移路径是三站 `bench migrate` 的 DocType sync。恢复到变更前备份后再次执行当前版本 migrate 会重建列；回退应用版本时 Frappe 不主动删除新增列，旧代码忽略它们。因不存在业务值转换或回填，未增加空 patch；这一 no-patch 决策避免用无行为的 patch 充当门禁。

### Task 1.2 RED→GREEN

首次运行被三站 migrate 后仍在消费的 scheduler job 阻断，未作为行为 RED 接受：

```text
ERROR tests/integration/test_claim_concurrency.py::test_claim_respects_site_and_owner_limits_and_expires_queued_runs
RuntimeError: Unexpected validation queue jobs
```

等现有 scheduler-worker 消费到队列为空后重跑，目标行为 RED 的原始尾部为：

```text
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 35, in <module>
E         KeyError: 'site'
E
E         During handling of the above exception, another exception occurred:
E
E         frappe.exceptions.QueryDeadlockError: (1020, "Record has changed since last read in table 'tabContact'; try restarting transaction")
E
E       assert 1 == 0

tests/integration/test_claim_concurrency.py:96: AssertionError
=========================== short test summary info ============================
FAILED tests/integration/test_claim_concurrency.py::test_claim_respects_site_and_owner_limits_and_expires_queued_runs
1 failed in 29.65s
```

目标失败是旧 `claim_run` 响应缺少 `site`（且旧全站闸门尚未允许第二个 owner）；teardown 的 Contact 锁来自测试新建用户触发的异步 Contact job。清理前按唯一前缀核对并用 Frappe 删除 3 个遗留合成用户，输出：

```text
targets=["claim-concurrency-2-0256cd1d0fcd41378d1743ef3021a5f8@example.invalid", "claim-concurrency-1-4c0b03aca3f3442daff608d2a8379c33@example.invalid", "claim-concurrency-0-6a4784cded9046acabcea692898c5cce@example.invalid"]
remaining=0
```

测试随后改为复用站内既有三个合成用户，避免用户/Contact 异步副作用。第一轮实现暴露 Frappe Datetime 空值实际以 zero datetime 参与 `<= now`，导致未设置 `queue_expires_at` 的既有入队记录被错误清扫：

```text
before={"run": "fc95b86be04248808b04a1adca2880663643ae33eb106d0e2bb124136cd62e78", "owner": "dsherp-reader@example.invalid", "status": "Queued", "budget": {"model_request_timeout_seconds": 90, "lease_seconds": 180, "lease_renew_below_seconds": 90, "queue_expires_seconds": 600, "heartbeat_stale_seconds": 60, "run_total_seconds": 300, "site_concurrency": 2, "provider": "deepseek-official", "model": "deepseek-v4-flash"}, "runtime_user": "dsherp-context-runtime@example.invalid"}
claim=null
after={"status": "Failed", "error": "系统繁忙，排队超时，请稍后重试", "events": [{"name": "fc95b86be04248808b04a1adca2880663643ae33eb106d0e2bb124136cd62e78-000001", "seq": 1, "kind": "queued", "source": "server", "error_class": "", "payload": {"domain": "query", "question_chars": 14, "page_type": "unknown"}, "recorded_at": "2026-09-04 01:26:01.087362"}, {"name": "fc95b86be04248808b04a1adca2880663643ae33eb106d0e2bb124136cd62e78-000002", "seq": 2, "kind": "expired", "source": "server", "error_class": "", "payload": {"reason": "queue_expired"}, "recorded_at": "2026-09-04 01:26:01.113352"}, {"name": "fc95b86be04248808b04a1adca2880663643ae33eb106d0e2bb124136cd62e78-000003", "seq": 3, "kind": "finished", "source": "server", "error_class": "", "payload": {"status": "Failed", "error": "queue_expired"}, "recorded_at": "2026-09-04 01:26:01.114946"}]}
```

修复为同时要求 `queue_expires_at is set` 与 `<= now`。业务断言随后通过，但 teardown 再被运行中的 scheduler 注入 `run_scheduled_job` 阻断。没有修改队列卫生夹具；先停 scheduler，等待 scheduler-worker 消费到空，再停 scheduler-worker：

```text
Container dsherp-validation-scheduler-1 Stopped
queue={"dsherp-validation.localhost": ["frappe.model.delete_doc.delete_dynamic_links", "frappe.model.delete_doc.delete_dynamic_links", "frappe.model.delete_doc.delete_dynamic_links"]}
queue=empty
Container dsherp-validation-scheduler-worker-1 Stopped
```

最终 GREEN，命令：`.venv/bin/python -m pytest tests/integration/test_claim_concurrency.py -q`

```text
.                                                                        [100%]
1 passed in 28.04s
```

退出码：0。

相关回归，命令：`.venv/bin/python -m pytest tests/integration/test_claim_concurrency.py tests/integration/test_run_events.py tests/integration/test_context_claim_cancel.py tests/integration/test_context_claim_rejection.py -q`

```text
..........                                                               [100%]
10 passed in 45.56s
```

退出码：0。既有取消竞态测试仅把候选识别从旧字符串行扩展为兼容带 `name` 的字段行，仍在候选选中与加锁之间提交取消并断言运行不被复活。Task 1.2 提交：`a499b8b`。

伴生审查随后提出过期扫描分页、取消竞态、50 行候选窗口和 active 计数分页风险。先补真实集成用例后取得：

```text
FF.                                                                      [100%]
=================================== FAILURES ===================================
_____ test_claim_expires_every_eligible_row_and_never_overwrites_cancelled _____
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 34, in <module>
E         AssertionError
_________________ test_claim_finds_owner_after_fifty_busy_rows _________________
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 29, in <module>
E         AssertionError: None
=========================== short test summary info ============================
FAILED tests/integration/test_claim_concurrency.py::test_claim_expires_every_eligible_row_and_never_overwrites_cancelled
FAILED tests/integration/test_claim_concurrency.py::test_claim_finds_owner_after_fifty_busy_rows
2 failed, 1 passed in 33.01s
```

退出码：1。已复现“扫描后取消被覆盖”和“第 51 条空闲 owner 被 50 行窗口饿死”；21 条 active 的用例在修复前已经通过，说明本版本 Frappe `get_all` 未在该查询上默认截断，但实现仍显式声明 `limit_page_length=0` 以固定接口语义。过期清扫同样显式全量，并对每行 `for_update` 后复核 status/expiry；候选窗口改为全量。

第一次修复后 3 条通过，公平用例到达真正领取路径后因测试自造 `page_context='{}'` 被服务端正确 fastfail；把夹具改为与 `send_message` 相同的合法 unknown 信封后，定向 GREEN：

```text
....                                                                     [100%]
4 passed in 34.94s
```

退出码：0。完整相关回归：

```text
.............                                                            [100%]
13 passed in 50.96s
```

退出码：0。修复提交：`8c26f95`（`fix: 修复领取分页与队列过期竞态`）。

### Task 1.3 RED→GREEN

新增租约行为测试首先确认旧接口缺少续租剩余秒数。命令：`.venv/bin/python -m pytest tests/integration/test_run_lease.py -q`

```text
F                                                                        [100%]
E       KeyError: 'lease_remaining_seconds'
=========================== short test summary info ============================
FAILED tests/integration/test_run_lease.py::test_run_status_renews_only_near_expiry_without_locking_or_revision_reads
1 failed in 26.32s
```

退出码：1。

实现只读 capability 校验与阈值续租后，同一命令独立重跑：

```text
.                                                                        [100%]
1 passed in 24.97s
```

退出码：0。

新契约要求 `run_status` 不再读取权限修订或 SSO 授权，因此三个既有集成断言在修改前先按预期 RED。命令：`.venv/bin/python -m pytest tests/integration/test_context_execution.py::test_capability_reads_as_owner_and_cannot_finish_without_actual_read tests/integration/test_context_permission_revision.py::test_native_permission_change_invalidates_and_rotates_runtime tests/integration/test_desk_sso.py::test_background_run_keeps_sso_revocation_without_browser_session -q`

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
_____ test_capability_reads_as_owner_and_cannot_finish_without_actual_read _____
E         AssertionError
________ test_native_permission_change_invalidates_and_rotates_runtime _________
E         AssertionError: old authorization remained usable
_______ test_background_run_keeps_sso_revocation_without_browser_session _______
E         AssertionError: revoked background grant accepted
=========================== short test summary info ============================
FAILED tests/integration/test_context_execution.py::test_capability_reads_as_owner_and_cannot_finish_without_actual_read
FAILED tests/integration/test_context_permission_revision.py::test_native_permission_change_invalidates_and_rotates_runtime
FAILED tests/integration/test_desk_sso.py::test_background_run_keeps_sso_revocation_without_browser_session
3 failed in 26.56s
```

退出码：1。仅把状态轮询断言改为只读状态/租约契约；`run_tool` 与 `reserve_model_call` 的权限修订和 SSO 撤销拒绝断言继续保留。目标与三处回归共同 GREEN：

```text
....                                                                     [100%]
4 passed in 35.76s
```

退出码：0。

事件常量与服务端事件集成分别独立执行，避免两个同名 `test_run_events.py` 在同一 pytest 进程中产生模块收集冲突：

```text
$ .venv/bin/python -m pytest tests/test_run_events.py -q
....                                                                     [100%]
4 passed in 0.20s

$ .venv/bin/python -m pytest tests/integration/test_run_events.py -q
......                                                                   [100%]
6 passed in 35.02s
```

退出码均为 0。Cursor Grok 4.6 Extra High Fast 对本任务未提交 diff 的只读伴生审查输出：`CLEAN`。

### Task 1.4 RED→目标 GREEN（等待授权）

计划内目标测试实现前 RED：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
______ test_send_message_rejects_unavailable_worker_and_sets_queue_expiry ______
E     AssertionError: unavailable worker accepted
=========================== short test summary info ============================
FAILED tests/integration/test_queue_backpressure.py::test_send_message_rejects_unavailable_worker_and_sets_queue_expiry
1 failed in 25.12s
```

退出码：1。最小实现后控制器独立重跑：

```text
..                                                                       [100%]
2 passed in 31.88s
```

退出码：0。两条测试分别覆盖进程内缺失/陈旧/新鲜心跳与真实 HTTP 状态码。真实 HTTP 回归在新增时先复现 Frappe 将 `ValidationError` 固定映射为 417：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_send_message_http_rejects_unavailable_worker_with_503 __________
E   assert 417 == 503
E    +  where 417 = <Response [417 EXPECTATION FAILED]>.status_code
=========================== short test summary info ============================
FAILED tests/integration/test_queue_backpressure.py::test_send_message_http_rejects_unavailable_worker_with_503
1 failed in 28.53s
```

退出码：1。根因是 Frappe v16 `handle_exception` 读取异常类的 `http_status_code`，而 `frappe.ValidationError` 固定为 417；只写 `frappe.local.response['http_status_code']=503` 无法影响异常响应。实现最小 `WorkerUnavailableError(frappe.ValidationError)` 并固定 503 后，目标测试全绿，仍保持 ValidationError 继承语义。

隔离 backend 重启加载新代码、明确删除心跳后，计划指定的既有会话用例验证了全局夹具需求：

```text
heartbeat= None
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_selected_domain_is_persisted_and_bound_to_request_id ___________
E   AssertionError: {"exception":"dsherp_bridge.context_api.WorkerUnavailableError: 助手服务暂不可用，请稍后再试",...}
E   assert 503 == 200
E    +  where 503 = <Response [503 SERVICE UNAVAILABLE]>.status_code
=========================== short test summary info ============================
FAILED tests/integration/test_context_sessions.py::test_selected_domain_is_persisted_and_bound_to_request_id
1 failed in 27.47s
```

这不是生产实现红灯，而是常驻 worker 按集成约束停止后，既有 `send_message` 测试必须由 session fixture 预置合成心跳。计划末尾授权点 3 已真实触发；当前未修改 `tests/integration/conftest.py`、未提交 Task 1.4。

Cursor Grok 4.6 Extra High Fast 对授权前 S1.4 diff 的只读伴生审查输出：`CLEAN`。

用户授权计划末尾第 3 点后，`validation_queue_hygiene` 在 session 开头写入合成心跳。授权后的目标与既有会话回归首次共同执行：

```text
.............                                                            [100%]
13 passed in 39.16s
```

退出码：0。

最终伴生审查发现 session 单次心跳可能在超过 60 秒的集成长跑中陈旧。为确定性验证该问题，临时两测试探针先把心跳改成 `now-61s`，再从下一测试读取年龄；修复前 RED：

```text
.F                                                                       [100%]
=================================== FAILURES ===================================
_____________ test_02_next_test_receives_fresh_synthetic_heartbeat _____________
E   AssertionError: 61.784304
E   assert 61.784304 < 5
=========================== short test summary info ============================
FAILED tests/integration/test_temporary_heartbeat_fixture_probe.py::test_02_next_test_receives_fresh_synthetic_heartbeat
1 failed, 1 passed in 27.84s
```

退出码：1。新增 function-scoped autouse 合成心跳刷新后，同一探针 GREEN：

```text
..                                                                       [100%]
2 passed in 30.94s
```

退出码：0。临时探针随后删除，不保留为结构性门禁。最终目标与会话回归：

```text
.............                                                            [100%]
13 passed in 55.90s
```

退出码：0。Cursor Grok 4.6 Extra High Fast 复审：`CLEAN`；确认每测试刷新关闭 60 秒陈旧窗口，背压测试仍能在夹具之后自行构造 missing/stale 状态。

## S1 自检门

指定集成门，命令：`.venv/bin/python -m pytest tests/integration/test_claim_concurrency.py tests/integration/test_run_lease.py tests/integration/test_queue_backpressure.py tests/integration/test_run_events.py -q`

```text
.............                                                            [100%]
13 passed in 61.65s (0:01:01)
```

退出码：0。

非集成门首次执行发现一个既有源码 AST 门禁要求所有 `get_all` 都必须带 `order_by`，而 active-owner 集合查询不消费顺序：

```text
................................................. [ 85%]
F........................                                                [100%]
=================================== FAILURES ===================================
_____ test_every_custom_get_all_query_has_explicit_deterministic_ordering ______
E       AssertionError: ['frappe_app/dsherp_bridge/context_execution.py:103']
E       assert not ['frappe_app/dsherp_bridge/context_execution.py:103']
=========================== short test summary info ============================
FAILED tests/test_v16_framework_adaptations.py::test_every_custom_get_all_query_has_explicit_deterministic_ordering
1 failed, 168 passed in 50.95s
```

退出码：1。按仓库“禁止 AST/等价实现门禁”约束删除该全局门禁，没有给集合查询增加无业务价值的排序。重跑同一非集成门：

```text
........................................................................ [ 42%]
........................................................................ [ 85%]
........................                                                 [100%]
168 passed in 50.20s
```

退出码：0。

Node 门，命令：`node --test runtime/*.test.cjs`

```text
✔ dispose waits for native creation and releases exactly the completed handle (1.685833ms)
✔ failed creation remains a request error but cannot break cleanup (0.949667ms)
✔ business catalog rejects unlisted skill directories (4.112125ms)
✔ ordinary and direct compaction requests both require authorization (0.631875ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.181083ms)
✔ runtime drift rejects subsequent streams even if the file is restored (0.945834ms)
✔ drift during a response cannot produce a successful terminal chunk (0.664125ms)
✔ finish and errors are reported without affecting the stream (0.275583ms)
ℹ tests 8
ℹ suites 0
ℹ pass 8
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 57.305333
```

退出码：0。

并发 2 现场取证按真实独立请求事务在每次 `claim_run` 后提交；两条运行同时为 Running，事件序列均为 queued→claimed：

```text
{"runs": [{"events": ["queued", "claimed"], "run_id": "8555e5520e053d0d1c5cf0980c6153db684131b6b4c1e7dce8fd7bc1aebb27ea", "status": "Running"}, {"events": ["queued", "claimed"], "run_id": "01b91e39e748a3eadfb0dbd85466e34c7de5a2775ba6762cee17b28cd998aa92", "status": "Running"}], "site_concurrency": 2}
```

取证数据已在输出后删除。第一次取证把两个 `claim_run` 错误地放在同一事务，第二次入口的设计性 rollback 撤销了第一次未提交领取；已按真实 worker 请求边界纠正，不作为产品红灯。

本阶段 DocType 变更后的三站 migrate 退出码沿用 Task 1.1 当次执行：alpha=0、daily=0、beta=0，迁移原始尾部与 live metadata 已逐站记录在上文。

## S2

### Task 2.1 RED→GREEN

命令：`.venv/bin/python -m pytest tests/test_provider_circuit.py -q`

实现前 RED：

```text
==================================== ERRORS ====================================
_______________ ERROR collecting tests/test_provider_circuit.py ________________
tests/test_provider_circuit.py:3: in <module>
    from dsherp.provider_circuit import CircuitBreaker,probe_models
E   ModuleNotFoundError: No module named 'dsherp.provider_circuit'
=========================== short test summary info ============================
ERROR tests/test_provider_circuit.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.10s
```

退出码：2。最小实现后：

```text
.....                                                                    [100%]
5 passed in 0.03s
```

退出码：0。所有探针测试均使用 `httpx.MockTransport`，没有真实网络/provider 请求，合成 key 未打印。

伴生审查发现 half-open 试探若返回 `other` 会永久耗尽唯一 allowance。先补行为测试，修复前 RED：

```text
.F....                                                                   [100%]
=================================== FAILURES ===================================
_________________ test_breaker_reopens_from_half_open_on_other _________________
E   AssertionError: assert ('half_open' == 'open'
E     - open
E     + half_open)
=========================== short test summary info ============================
FAILED tests/test_provider_circuit.py::test_breaker_reopens_from_half_open_on_other
1 failed, 5 passed in 0.06s
```

退出码：1。修复为 half-open 的 `other` 重新打开 60 秒窗口，closed/open 的 `other` 仍 no-op；控制器独立 GREEN：

```text
......                                                                   [100%]
6 passed in 0.03s
```

退出码：0。Cursor Grok 4.6 Extra High Fast 复审：`CLEAN`。

### Task 2.2 RED→GREEN

首批行为测试要求多站轮转/槽位、异常不逃逸与 profile 兼容；服务端测试要求 `finish_run` 返回并持久化 `model_error` 事件计数。实现前分别 RED：

```text
==================================== ERRORS ====================================
________________ ERROR collecting tests/test_context_worker.py _________________
ImportError: cannot import name 'Coordinator' from 'dsherp.context_worker'
ImportError: cannot import name 'normalize_profile' from 'dsherp.context_worker'
=========================== short test summary info ============================
ERROR tests/test_context_worker.py
3 errors in 0.43s
```

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_finish_run_reports_and_persists_provider_failure_count ____________
E       AssertionError: {'status': 'Failed'}
=========================== short test summary info ============================
FAILED tests/integration/test_run_events.py::test_finish_run_reports_and_persists_provider_failure_count
1 failed in 24.01s
```

首版实现的完整单元/部署契约回归暴露 7 条旧接口与异常收口差异，修正后为 34 passed；完整 `run_events` 首次因既有精确返回值缺少新字段而 RED，更新契约后为 7 passed。伴生审查随后指出 403 误计 provider、熔断窗口从领取时刻而非完成时刻起算、half-open 同 tick 可领多条、未初始化无 claim 站点指标。四条回归测试修复前原始输出：

```text
FFFF                                                                     [100%]
=================================== FAILURES ===================================
___________ test_run_claimed_never_escapes_and_records_provider_failure ___________
E       assert 1 == 0
E        +  where 1 = <dsherp.provider_circuit.CircuitBreaker object>.consecutive_failures
____________ test_circuit_open_period_starts_when_slow_run_finishes ____________
E       TypeError: Coordinator.__init__() got an unexpected keyword argument 'clock'
_____________ test_half_open_tick_claims_only_one_trial_across_sites _____________
E       TypeError: Coordinator.__init__() got an unexpected keyword argument 'clock'
___________ test_coordinator_initializes_claim_metric_for_every_site ___________
E       AssertionError: assert [] == [(0, {'site': 'alpha'}), (0, {'site': 'daily'})]
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_run_claimed_never_escapes_and_records_provider_failure
FAILED tests/test_context_worker.py::test_circuit_open_period_starts_when_slow_run_finishes
FAILED tests/test_context_worker.py::test_half_open_tick_claims_only_one_trial_across_sites
FAILED tests/test_context_worker.py::test_coordinator_initializes_claim_metric_for_every_site
4 failed in 0.59s
```

修复后同四条：

```text
....                                                                     [100%]
4 passed in 0.38s
```

第一次计划指定全套又准确暴露 `run_once` 指标初始化次序不一致：

```text
..............F........................                                  [100%]
=================================== FAILURES ===================================
___________ test_run_once_updates_claim_result_and_duration_metrics ____________
E       AssertionError: assert [(1, {'site':...': 'legacy'})] == [(1, {'site': 'legacy'})]
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_run_once_updates_claim_result_and_duration_metrics
1 failed, 38 passed in 0.44s
```

把序列初始化放到首次 claim 计数之前后，计划指定命令 `.venv/bin/python -m pytest tests/test_context_worker.py tests/test_v16_deployment_contract.py -q`：

```text
.......................................                                  [100%]
39 passed in 0.32s
```

退出码：0。`run_events` 集成回归：

```text
.......                                                                  [100%]
7 passed in 45.17s
```

退出码：0。常驻 `com.dsherp.agent-worker-v16` 此前现场核验为未加载，没有与集成进程争抢任务。

第一轮 Cursor Grok 4.6 Extra High Fast 复审发现两条可复现问题：half-open 在空队列时永久耗尽试探；熔断/满槽期间不 claim 导致站点心跳饿死。先补三条协调器回归，修复前：

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
____________ test_half_open_empty_poll_releases_trial_for_next_tick ____________
E           assert 0 == 1
____________ test_tick_refreshes_site_heartbeats_while_circuit_open ____________
E       AssertionError: assert [] == ['worker_heartbeat']
___________ test_tick_refreshes_site_heartbeats_while_slots_are_full ___________
E               AssertionError: assert 0 == 2
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_half_open_empty_poll_releases_trial_for_next_tick
FAILED tests/test_context_worker.py::test_tick_refreshes_site_heartbeats_while_circuit_open
FAILED tests/test_context_worker.py::test_tick_refreshes_site_heartbeats_while_slots_are_full
3 failed in 0.37s
```

受运行身份约束的真实站点 heartbeat 接口实现前：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____ test_worker_heartbeat_requires_runtime_identity_and_refreshes_cache ______
E       AssertionError: Traceback (most recent call last):
E         ImportError: cannot import name 'worker_heartbeat' from 'dsherp_bridge.context_execution' (/opt/dsherp-frappe/dsherp_bridge/context_execution.py)
=========================== short test summary info ============================
FAILED tests/integration/test_claim_concurrency.py::test_worker_heartbeat_requires_runtime_identity_and_refreshes_cache
1 failed in 28.13s
```

修复后，协调器三条与真实站点接口分别为：

```text
...                                                                      [100%]
3 passed in 0.23s
```

```text
.                                                                        [100%]
1 passed in 23.39s
```

控制器最终核对发现 `run_claimed` 的 claim 字段解包仍在总 try 外。畸形 claim 回归先 RED：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_______________ test_run_claimed_contains_malformed_claim_errors _______________
E       KeyError: 'capability'
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_run_claimed_contains_malformed_claim_errors
1 failed in 0.40s
```

把解包纳入同一异常收口后：

```text
.                                                                        [100%]
1 passed in 0.23s
```

最终受影响单元/部署/熔断器回归：

```text
.................................................                        [100%]
49 passed in 0.32s
```

最终受影响真实站点集成回归，命令：`.venv/bin/python -m pytest tests/integration/test_run_events.py tests/integration/test_claim_concurrency.py -q`

```text
............                                                             [100%]
12 passed in 59.44s
```

退出码：0。没有调用真实 provider；熔断探针仍仅由 MockTransport 覆盖。Cursor Grok 4.6 Extra High Fast 最终只读复审：`CLEAN`。

### Task 2.3 RED→GREEN 与本机运行态

Cursor Grok 4.6 Extra High Fast 先写合成 profile 行为测试。实现前：

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
_____ test_context_worker_launch_agent_is_reproducible_and_self_restarting _____
E       AssertionError: assert ['/Users/lize...der-env', ...] == ['/Users/lize...der-env', ...]
E         At index 4 diff: '/Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker.json' != '/Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json'
_ test_merge_context_worker_profiles_writes_merged_sites_without_changing_inputs _
E       ModuleNotFoundError: No module named 'infra.merge_context_worker_profiles'
__ test_merge_context_worker_profiles_fast_fails_on_missing_or_invalid_input ___
E       ModuleNotFoundError: No module named 'infra.merge_context_worker_profiles'
=========================== short test summary info ============================
FAILED tests/test_v16_deployment_contract.py::test_context_worker_launch_agent_is_reproducible_and_self_restarting
FAILED tests/test_v16_deployment_contract.py::test_merge_context_worker_profiles_writes_merged_sites_without_changing_inputs
FAILED tests/test_v16_deployment_contract.py::test_merge_context_worker_profiles_fast_fails_on_missing_or_invalid_input
3 failed in 0.05s
```

控制器审查实现后独立重跑：

```text
...........                                                              [100%]
11 passed in 0.02s
```

退出码：0；`git diff --check` 退出 0、无输出。真实合并只输出非敏感字段：

```text
target=/Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json
mode=0o600
slots=3
sites=dsherp-validation.localhost,dsherp-daily.localhost
input_bytes_unchanged=true
```

旧服务本来未加载，停止/渲染/首次 bootstrap：

```text
bootout_exit=3
render_exit=0
bootstrap_exit=0
Boot-out failed: 3: No such process
/Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json
plist_mode=600
```

首次启动现场：

```text
state = running
pid = 84602
last exit code = (never exited)
pidfile=84602
pidfile_mode=600
84602     1 /Users/lize/Documents/ChatGPT/dsherp/.venv/bin/python -m dsherp.context_worker --profile /Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json --provider-env /Users/lize/Documents/ChatGPT/dsherp/.env
matching_worker_count=1
python3.1 84602 lize 4u IPv4 TCP 127.0.0.1:9109 (LISTEN)
dsherp_claims_total{site="dsherp-validation.localhost"} 0
dsherp_claims_total{site="dsherp-daily.localhost"} 0
dsherp_slots_busy 0
dsherp_provider_circuit_open 0
```

仓库提交：`dd7d96f feat: 本机 worker 服务 alpha 与 daily 两站`。

## S2 自检门

非集成完整门，命令：`.venv/bin/python -m pytest tests --ignore=tests/integration -q`

```text
........................................................................ [ 38%]
........................................................................ [ 76%]
............................................                             [100%]
188 passed in 51.30s
```

退出码：0。Node 门，命令：`node --test runtime/*.test.cjs`

```text
✔ dispose waits for native creation and releases exactly the completed handle (2.231334ms)
✔ failed creation remains a request error but cannot break cleanup (1.016208ms)
✔ business catalog rejects unlisted skill directories (4.258709ms)
✔ ordinary and direct compaction requests both require authorization (0.667541ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.187584ms)
✔ runtime drift rejects subsequent streams even if the file is restored (0.967ms)
✔ drift during a response cannot produce a successful terminal chunk (0.674875ms)
✔ finish and errors are reported without affecting the stream (0.376209ms)
ℹ tests 8
ℹ suites 0
ℹ pass 8
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 61.7005
```

退出码：0。运行中指标复核：

```text
state = running
pid = 84602
last exit code = (never exited)
dsherp_claims_total{site="dsherp-validation.localhost"} 0
dsherp_claims_total{site="dsherp-daily.localhost"} 0
dsherp_slots_busy 0
dsherp_provider_circuit_open 0
```

停常驻 worker：

```text
bootout_exit=0
Bad request.
Could not find service "com.dsherp.agent-worker-v16" in domain for user gui: 501
pidfile_present=false
old_pid_alive=false
```

其中 `Bad request` 是随后用于确认“已经不存在”的 `launchctl print` 输出；bootout 本身退出 0。`tests/integration/test_context_worker_chain.py` 的首个测试语句执行 `_require_stopped_agent_worker()` fastfail；本次未触发失败，使用本地合成模型替身，不调用 provider：

```text
{"ts": "2026-09-04T02:33:59.805+00:00", "event": "claimed", "site": "dsherp-validation.localhost", "run_id": "9ae67611b6aed015073f04d793689e17867039520f2c694cac9529ca997a07e8"}
{"ts": "2026-09-04T02:34:43.046+00:00", "event": "container_finished", "run_id": "9ae67611b6aed015073f04d793689e17867039520f2c694cac9529ca997a07e8", "status": "Succeeded", "duration_ms": 43239}
{"ts": "2026-09-04T02:34:43.399+00:00", "event": "claimed", "site": "dsherp-validation.localhost", "run_id": "396d6e72fc3bad20958f13394529215b71cfd1bb5c0b8d817af5aec6f4580844"}
{"ts": "2026-09-04T02:35:31.330+00:00", "event": "container_finished", "run_id": "396d6e72fc3bad20958f13394529215b71cfd1bb5c0b8d817af5aec6f4580844", "status": "Succeeded", "duration_ms": 47929}
C2_EVENT_EVIDENCE=[{"run_id":"9ae67611b6aed015073f04d793689e17867039520f2c694cac9529ca997a07e8","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"model_responses":[{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"},{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"}]},{"run_id":"396d6e72fc3bad20958f13394529215b71cfd1bb5c0b8d817af5aec6f4580844","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"model_responses":[{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"},{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"}]}]
.
1 passed in 117.89s (0:01:57)
```

退出码：0。恢复 bootstrap 退出 0，新 PID `85392`、唯一进程、pidfile 0600。首次 3 秒检查时 metrics 为 502；进程采样显示仍在首次构造 SSL context/加载 CA，约 31 秒后完成启动：

```text
85392 00:31 /Users/lize/Documents/ChatGPT/dsherp/.venv/bin/python -m dsherp.context_worker --profile /Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json --provider-env /Users/lize/Documents/ChatGPT/dsherp/.env
python3.1 85392 lize 4u IPv4 TCP 127.0.0.1:9109 (LISTEN)
dsherp_claims_total{site="dsherp-validation.localhost"} 0
dsherp_claims_total{site="dsherp-daily.localhost"} 0
dsherp_slots_busy 0
dsherp_provider_circuit_open 0
```

真实 HTTP 路由最初两站均 417，服务端消息确认常驻 Frappe web 进程缓存了旧 `context_execution` 模块。对隔离验证 backend 做正常代码重载（不是 S7 故障注入）后：

```text
Container dsherp-validation-backend-1 Restarting
Container dsherp-validation-backend-1 Started
backend_restart_exit=0
sites_ready_attempt=4
dsherp-validation.localhost heartbeat_status=200 message_keys=heartbeat
dsherp-daily.localhost heartbeat_status=200 message_keys=heartbeat
```

worker 在 `2026-09-04T02:37:01.825Z` 后不再产生 `BusinessRuntimeError`，二次等待至 `02:37:29Z` 仍无新错误；PID `85392`、回环 9109 与四条指标保持正常。S2 没有新增 DocType/Report/hooks，无需 migrate。没有调用真实 provider。

## S3

### Task 3.1 RED→GREEN

Cursor Grok 4.6 Extra High Fast 先把业务 runtime 超时测试改为消费服务端预算。实现前：

```text
FF.FFFFFF                                                                [100%]
FAILED tests/test_session_runtime.py::test_run_config_without_budget_fails_before_harness
FAILED tests/test_session_runtime.py::test_claimed_budget_timeout_reaches_harness
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[True]
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[False]
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[90]
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[90.0]
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[0]
FAILED tests/test_session_runtime.py::test_invalid_budget_timeout_is_missing_run_budget[-1]
8 failed, 1 passed in 0.15s
```

控制器审查后独立运行 `.venv/bin/python -m pytest tests/test_session_runtime.py tests/test_runtime_revision.py -q`：

```text
.................                                                        [100%]
17 passed in 2.91s
```

退出码：0。业务 `request_timeout_seconds` 只接受 `budget.model_request_timeout_seconds` 的正整数；缺失/非法值在 Harness 构造前 fastfail；无 `run_config` 的纯上下文仍为 90 秒。没有 provider 请求。提交：`2141c3f`。

### Task 3.2 RED→GREEN

新增 cancel grace、总 deadline、NeedsInput 三条行为测试，生产实现前：

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
_____________ test_cancel_returns_within_grace_even_if_model_hangs _____________
E       TypeError: monitored_run() got an unexpected keyword argument 'grace'
__________________ test_run_total_deadline_cancels_and_fails ___________________
E       TypeError: monitored_run() got an unexpected keyword argument 'deadline'
___________________ test_needs_input_status_stops_gracefully ___________________
E       TypeError: monitored_run() got an unexpected keyword argument 'grace'
=========================== short test summary info ============================
FAILED tests/test_context_runner.py::test_cancel_returns_within_grace_even_if_model_hangs
FAILED tests/test_context_runner.py::test_run_total_deadline_cancels_and_fails
FAILED tests/test_context_runner.py::test_needs_input_status_stops_gracefully
3 failed in 1.82s
```

实现 daemon 模型线程、Queue 结果通道、grace join、deadline 事件后，三条目标：

```text
...                                                                      [100%]
3 passed in 3.57s
```

完整相关门首次暴露 3.1 后两个既有测试文件仍手工构造无 budget 的 `run.json`：

```text
..................FFFFFFFFF                                              [100%]
9 failed, 18 passed in 22.83s
```

只给这两个直接消费者补合成预算。第一次机械编辑引入两处缩进错误，收集结果为 `2 errors in 0.62s`；立即纠正后同一计划门：

```text
...........................                                              [100%]
27 passed in 30.43s
```

退出码：0。Cursor Grok 4.6 Extra High Fast 只读复审：`CLEAN`。提交：`3a3c103`。

### Task 3.3 RED→GREEN

Cursor 先写 worker 行为测试；最初测试 scope 使用非十六进制字符，被现有校验正确拒绝，纠正测试数据后接受的 RED：

```text
F.F                                                                      [100%]
=================================== FAILURES ===================================
________________ test_run_container_accepts_needs_input_result _________________
E   RuntimeError: Invalid business runtime result
______ test_run_once_finishes_needs_input_and_clears_consecutive_failures ______
E   assert [] == [0]
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_run_container_accepts_needs_input_result
FAILED tests/test_context_worker.py::test_run_once_finishes_needs_input_and_clears_consecutive_failures
2 failed, 1 passed in 0.31s
```

实现后控制器独立重跑：

```text
.....................................                                    [100%]
37 passed in 0.30s
```

退出码：0。`NeedsInput` 容器结果原样回写、清零连续失败；服务端 finish 只写 `needs_input`，不写普通 `answer`，并清空能力凭据。`erp_request_input` 与完整集成链未提前实现。提交：`9f06d65`。

## S3 自检门

非集成完整门：

```text
........................................................................ [ 35%]
........................................................................ [ 71%]
..........................................................               [100%]
202 passed in 54.67s
```

退出码：0。Node 门：

```text
✔ dispose waits for native creation and releases exactly the completed handle (1.685042ms)
✔ failed creation remains a request error but cannot break cleanup (1.065709ms)
✔ business catalog rejects unlisted skill directories (3.842041ms)
✔ ordinary and direct compaction requests both require authorization (0.591417ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.163791ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.185625ms)
✔ drift during a response cannot produce a successful terminal chunk (0.2605ms)
✔ finish and errors are reported without affecting the stream (0.314542ms)
ℹ tests 8
ℹ suites 0
ℹ pass 8
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 58.209667
```

退出码：0。进程内 worker 链前停止常驻进程：

```text
pre_stop_pid=85392
bootout_exit=0
pidfile_present=false
old_pid_alive=false
```

测试文件开头的 `_require_stopped_agent_worker()` 未触发，说明 fastfail 前提满足。本地模型替身集成链原始输出：

```text
{"ts": "2026-09-04T03:02:01.952+00:00", "event": "claimed", "site": "dsherp-validation.localhost", "run_id": "642763d8e892fc79755383951a68dd80fd5766ea9df6085cb84158821f3261fa"}
{"ts": "2026-09-04T03:02:36.799+00:00", "event": "container_finished", "run_id": "642763d8e892fc79755383951a68dd80fd5766ea9df6085cb84158821f3261fa", "status": "Succeeded", "duration_ms": 34846}
{"ts": "2026-09-04T03:02:37.149+00:00", "event": "claimed", "site": "dsherp-validation.localhost", "run_id": "b36ef25428800dc74bc8ac67aacb3d287f7dba3e9ae7d001b04187ff66224fc4"}
{"ts": "2026-09-04T03:03:12.318+00:00", "event": "container_finished", "run_id": "b36ef25428800dc74bc8ac67aacb3d287f7dba3e9ae7d001b04187ff66224fc4", "status": "Succeeded", "duration_ms": 35168}
C2_EVENT_EVIDENCE=[{"run_id":"642763d8e892fc79755383951a68dd80fd5766ea9df6085cb84158821f3261fa","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"model_responses":[{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"},{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"}]},{"run_id":"b36ef25428800dc74bc8ac67aacb3d287f7dba3e9ae7d001b04187ff66224fc4","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"model_responses":[{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"},{"usage":null,"chunk_keys":["type","reason"],"purpose":"conversation","model":"deepseek-v4-flash"}]}]
.
1 passed in 96.03s (0:01:36)
```

退出码：0。两次运行各约 35 秒，均高于 90 秒续租阈值之外，因此事件序列没有 `lease_renewed`，符合计划允许说明。

worker 停止期间正常重载验证 backend，`backend_restart_exit=0`，两站第 4 次半秒轮询恢复；`restore_bootstrap_exit=0`。这次冷启动较慢：35 秒时进程仍在模块导入且无 pidfile/9109，约 75 秒建立 pidfile，约 119 秒完成 TLS 初始化与指标监听；launchd 始终 `runs=1`、`last exit code=(never exited)`，不是崩溃循环。最终现场：

```text
99647 01:59 S /Users/lize/Documents/ChatGPT/dsherp/.venv/bin/python -m dsherp.context_worker --profile /Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json --provider-env /Users/lize/Documents/ChatGPT/dsherp/.env
python3.1 99647 lize 4u IPv4 TCP 127.0.0.1:9109 (LISTEN)
dsherp_claims_total{site="dsherp-validation.localhost"} 0
dsherp_claims_total{site="dsherp-daily.localhost"} 0
dsherp_slots_busy 0
dsherp_provider_circuit_open 0
```

S3 没有新增 DocType/Report/hooks，无需 migrate。没有调用真实 provider。

## S4

### Task 4.1 RED→GREEN

先加入三类业务 HTTP 失败的分类与序列化测试。实现前，缺失 `classify_failure`/`ToolFailure` 的首个目标为 1 failed in 0.34s；展开三类契约后为 5 failed, 5 passed in 0.36s。实现纯分类器、Frappe 安全消息提取、紧凑 JSON `ToolFailure` 与单次非 200 处理后，审查发现曾通过改写 `ToolFailure.__name__` 隐藏真实诊断类型。先把 worker 行为断言改为真实类型，得到 1 failed in 0.36s；删除别名后目标 1 passed in 0.34s。控制器最终运行 MCP + worker：

```text
...............................................                          [100%]
47 passed in 0.39s
```

`ToolFailure` 保持真实类名且是 `BusinessRuntimeError` 子类；响应正文只保留 `error_class/message/retryable`，HTTP 状态只留在对象诊断字段。没有 provider 请求。提交：`c6c9f12`。

### Task 4.2 RED→GREEN

集成测试首语句确认常驻 worker 已停止：停止前 PID `99647`，bootout 退出 0，随后 pidfile 不存在且旧 PID 已死亡。工具面测试实现前：

```text
FFFF.......                                                              [100%]
4 failed, 7 passed in 0.36s
```

真实站点在实现前拒绝新工具，目标为 1 failed in 29.47s。实现三个领域的严格 `erp_request_input`、Running→NeedsInput 状态、问题落库、事件、状态读取、清空 capability 的结束路径和公开消息回退后，控制器最终单元组：

```text
...............                                                          [100%]
15 passed in 0.27s
```

最终真实站点 NeedsInput + run-events + context-execution：

```text
.........                                                                [100%]
9 passed in 60.96s
```

退出码均为 0。Cursor Grok 4.6 Extra High Fast 只读复审：`CLEAN`。没有 provider 请求；worker 为后续 S4 集成保持停止。提交：`0c93aff`。

### Task 4.3 RED→GREEN

三份 skill 的版本与错误出口断言先落地，首次计划命令原始尾部：

```text
.F...FF.F..FFF...                                                        [100%]
=========================== short test summary info ============================
FAILED tests/test_business_skills.py::test_configuration_skill_is_pinned_and_in_runtime_identity
FAILED tests/test_business_skills.py::test_query_skill_plans_bom_then_batches_warehouse_scoped_bins
FAILED tests/test_business_skills.py::test_operation_skill_discovers_dynamic_capabilities_and_manufacturing_routes
FAILED tests/test_business_skills.py::test_business_skills_share_tool_error_and_impossible_exit_rules
FAILED tests/test_model_guard.py::test_business_denial_prevents_actual_provider_request[allow]
FAILED tests/test_model_guard.py::test_business_denial_prevents_actual_provider_request[skill]
FAILED tests/test_model_guard.py::test_business_denial_prevents_actual_provider_request[operation]
7 failed, 10 passed in 8.52s
```

其中后三条是 Task 4.2 已增加工具、旧工具目录期望未同步；同步 `erp_request_input` 后仍以版本和 skill 正文缺节保持 6 failed, 11 passed。三份 skill 加入完全相同的“工具错误与做不了的出口”，版本更新为 1.4.0/2.2.0/1.1.0，并按原始文件字节更新既有 SHA-256 清单。控制器逐份复算摘要后独立运行：

```text
.................                                                        [100%]
17 passed in 8.25s
```

退出码：0。模型流量只到本地 fixture，没有 provider 请求。提交：`cff7b41`。

### Task 4.4 行为覆盖

此任务按计划只新增测试；单一 validation 探测落地时，Task 4.1 已实现该行为，因此首次即绿，没有制造伪红灯：

```text
.                                                                        [100%]
1 passed in 1.59s
```

补齐 validation/permission/transient 三类后，控制器独立运行真实 pinned Runtime + 本地 `model_server` + `create_server`/`httpx.MockTransport`：

```text
...                                                                      [100%]
3 passed in 3.66s
```

退出码：0。第二次模型请求最后一条 tool 结果包含服务端 message 与精确 `error_class`。临时 mode-0600 spec/cordis 均由测试清理，没有 provider 请求。提交：`c20b62a`。

## S4 自检门

自检前现场：`launchctl print` 返回 113，`.runtime/agent-worker.pid` 不存在，进程列表与回环 9109 均没有常驻 worker；`tests/integration/test_needs_input.py` 的首语句 fastfail 未触发。非集成完整门原始尾部：

```text
........................................................................ [ 34%]
........................................................................ [ 68%]
..................................................................       [100%]
210 passed in 57.92s
```

退出码：0。Node 门原始输出：

```text
✔ dispose waits for native creation and releases exactly the completed handle (1.812625ms)
✔ failed creation remains a request error but cannot break cleanup (1.3245ms)
✔ business catalog rejects unlisted skill directories (5.961833ms)
✔ ordinary and direct compaction requests both require authorization (0.939458ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.216875ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.081416ms)
✔ drift during a response cannot produce a successful terminal chunk (0.746459ms)
✔ finish and errors are reported without affecting the stream (0.355ms)
ℹ tests 8
ℹ suites 0
ℹ pass 8
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 67.081459
```

退出码：0。NeedsInput 集成原始输出：

```text
.
1 passed in 27.86s
```

退出码：0。随后在 alpha 站直接运行并清理的真实（替身）NeedsInput 状态链原始输出：

```text
{"run_id":"f4c98b325358c9021c43d01061edbc07b005085d414d44dcc300d02cf3beeadf","tool_result":{"status":"NeedsInput"},"finish_result":{"run_id":"f4c98b325358c9021c43d01061edbc07b005085d414d44dcc300d02cf3beeadf","status":"NeedsInput","provider_failures":0},"active_run":null,"message":{"id":"f4c98b325358c9021c43d01061edbc07b005085d414d44dcc300d02cf3beeadf","question":"S4 替身：需要用户指定仓库","answer":"请指定仓库","error":"","status":"NeedsInput","context":{"page_type":"unknown","route":[],"schema_version":1},"domain":"query","sources":[]},"events":[{"seq":1,"kind":"queued","payload":{"domain":"query","question_chars":14,"page_type":"unknown"}},{"seq":2,"kind":"claimed","payload":{"domain":"query","permission_revision":"c15895236ae33232509e84fdd863345395c2e50cfcf46b97d8cd54c056c20532","runtime_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","native_session_id":"7feec14554a44cf7b0cc2ca39c4d0f25"}},{"seq":3,"kind":"needs_input","payload":{"question_chars":5}},{"seq":4,"kind":"tool_call","payload":{"tool":"erp_request_input","arguments":{"question":"请指定仓库"},"duration_ms":4,"result":{"keys":["status"]}}},{"seq":5,"kind":"finished","payload":{"status":"NeedsInput","answer_chars":5,"error":"","model_calls":0,"provider_failures":0}}]}
```

测试行已清理。S4 没有 DocType/Report/hooks 变更，因此无需 migrate。正常 backend 代码重载与两站 HTTP 核验：

```text
dsherp-validation-backend-1
backend_restart_exit=0
sites_ready_attempt=1
dsherp-validation.localhost heartbeat_status=200 message_keys=heartbeat
dsherp-daily.localhost heartbeat_status=200 message_keys=heartbeat
```

首次恢复虽 bootstrap 退出 0，但 240 秒内没有达到双站指标门。只读核验发现磁盘 plist 自 S2 后未重新渲染，实际参数仍指向旧 `context-worker.json`；launchd 一直 `running`、`runs=1`、`last exit code=(never exited)`，不是崩溃。第 1 次修复循环只刷新本地运行态：停止、用已提交渲染器重建 plist、复核参数后重新 bootstrap。关键输出摘录：

```text
/Users/lize/Documents/ChatGPT/dsherp/.runtime/com.dsherp.agent-worker-v16.plist
repair1_bootout_exit=0
repair1_render_exit=0
    3 => "--profile"
    4 => "/Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json"
    5 => "--provider-env"
repair1_bootstrap_exit=0
repair1_worker_ready_attempt=1
state=running
pidfile=37215
pidfile_mode=600
37215     1 S    00:08 /Users/lize/Documents/ChatGPT/dsherp/.venv/bin/python -m dsherp.context_worker --profile /Users/lize/Documents/ChatGPT/dsherp/.runtime/context-worker-sites.json --provider-env /Users/lize/Documents/ChatGPT/dsherp/.env
python3.1 37215 lize 4u IPv4 TCP 127.0.0.1:9109 (LISTEN)
dsherp_claims_total{site="dsherp-validation.localhost"} 0
dsherp_claims_total{site="dsherp-daily.localhost"} 0
dsherp_slots_busy 0
dsherp_provider_circuit_open 0
```

最终 worker PID `37215` 唯一、pidfile 0600、仅回环监听、加载双站 profile。没有调用真实 provider。

## S5

### Task 5.1 RED→GREEN

Cursor Grok 4.6 Extra High Fast 先写拒绝/过期的真实站点集成测试。生产接口不存在时原始尾部：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_reject_and_expire_are_terminal_without_execution _____________
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 8, in <module>
E         ImportError: cannot import name 'expire_proposals' from 'dsherp_bridge.operations' (/opt/dsherp-frappe/dsherp_bridge/operations.py)
=========================== short test summary info ============================
FAILED tests/integration/test_proposal_exits.py::test_reject_and_expire_are_terminal_without_execution
1 failed in 24.88s
```

实现完成但站点尚未 migrate 时，同一测试继续以站点元数据红灯：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_reject_and_expire_are_terminal_without_execution _____________
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 31, in <module>
E         frappe.exceptions.ValidationError:  status cannot be "Rejected". It should be one of "Pending", "Authorized", "Succeeded", "Failed", "Unknown"
=========================== short test summary info ============================
FAILED tests/integration/test_proposal_exits.py::test_reject_and_expire_are_terminal_without_execution
1 failed in 23.24s
```

DocType/hook 变更后三站迁移逐站退出码：

```text
ALPHA_MIGRATE_EXIT=0
DAILY_MIGRATE_EXIT=0
BETA_MIGRATE_EXIT=0
```

迁移后实际元数据：

```text
{"label":"alpha","site":"dsherp-validation.localhost","status_options":["Pending","Authorized","Succeeded","Failed","Unknown","Rejected","Expired"],"fields":["rejected_by","rejected_request_id"]}
{"label":"daily","site":"dsherp-daily.localhost","status_options":["Pending","Authorized","Succeeded","Failed","Unknown","Rejected","Expired"],"fields":["rejected_by","rejected_request_id"]}
{"label":"beta","site":"dsherp-beta.localhost","status_options":["Pending","Authorized","Succeeded","Failed","Unknown","Rejected","Expired"],"fields":["rejected_by","rejected_request_id"]}
```

三站 migrate 各产生一条 `build_index_for_all_routes`。未放宽队列卫生规则：逐站直接执行搜索索引函数均退出 0；确认共享队列恰好只有这三条可重建副本后，各精确清除 1 条，最终按现有“空串代表空队列”的解析语义得到 `{}`。第一次诊断误把原始空串断言为必须字面输出 `{}`，该脚本断言失败；纠正诊断后未改代码。

```text
ALPHA_SEARCH_INDEX_EXIT=0
DAILY_SEARCH_INDEX_EXIT=0
BETA_SEARCH_INDEX_EXIT=0
Purged 1 jobs
ALPHA_SEARCH_JOB_PURGE_EXIT=0
Purged 1 jobs
DAILY_SEARCH_JOB_PURGE_EXIT=0
Purged 1 jobs
BETA_SEARCH_JOB_PURGE_EXIT=0
POST_PURGE_RAW=''
POST_PURGE_JOBS={}
```

目标测试与既有提案回归：

```text
........                                                                 [100%]
8 passed in 56.00s
```

退出码：0。实现包含 owner/digest/request-id 绑定的拒绝、同 request_id 重放幂等、逐行锁定的 Pending 过期、精确 confirm 出口、两类事件与十分钟 cron。测试暂存后恢复 C0 基线，没有业务写入残留。没有 provider 请求。提交：`4569baa`。

### Task 5.2 RED→GREEN

真实读取一次 ERP 记录后，以完成自述结束；实现前状态仍为 Succeeded，但标记未写入：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____ test_finish_run_flags_unverified_completion_without_changing_status ______
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 35, in <module>
E         AssertionError: {'status': 'Succeeded', 'answer_flagged': 0}
=========================== short test summary info ============================
FAILED tests/integration/test_completion_crosscheck.py::test_finish_run_flags_unverified_completion_without_changing_status
1 failed in 27.75s
```

计划文件表漏列 `context_events.py`，但 `record` 会拒绝未知的计划事件 `unverified_completion_claim`；只在该文件增加这一事件常量，共 1 个计划外文件，未达到用户规定的“超过 3 个才停”阈值。控制器独立运行目标与 run-events：

```text
........                                                                 [100%]
8 passed in 47.20s
```

退出码：0。所有 finished 事件包含本 run 的 proposals、Succeeded executions、sources 计数；只有 Succeeded 完成自述且没有成功执行记录时写 `answer_flagged=1` 与后续警示事件，不改变成功状态；公开消息带布尔标记。没有 provider 请求。提交：`2fe5d34`。

### Task 5.3 RED→GREEN

运行 ops 集成前正常停止常驻 worker，且测试函数首语句 fastfail 保留。停止输出：

```text
pre_stop_pid=37215
bootout_exit=0
stopped_attempt=2
pidfile_present=false
old_pid_alive=false
metrics_listener_present=false
```

快照与告警断言实现前原始尾部：

```text
FF.......                                                                [100%]
=================================== FAILURES ===================================
_________________ test_collect_snapshot_and_authorized_status __________________
E         AssertionError: {'queued': 1, 'queued_oldest_seconds': 0, 'running': 1, 'running_stuck': 1, 'pending_proposals_expired': 4, 'last_claim_age_seconds': 70345, 'runs_24h': {'Failed': 5, 'NeedsInput': 1, 'Queued': 1, 'Running': 1}, 'backup_age_hours': 33.93, 'site': 'dsherp-validation.localhost', 'collected_at': '2026-09-04 12:12:27.711743'}
___________________ test_rules_fire_only_on_their_condition ____________________
E       AssertionError: assert [] == ['queue_expiring']
=========================== short test summary info ============================
FAILED tests/integration/test_ops_snapshot.py::test_collect_snapshot_and_authorized_status
FAILED tests/test_alerts.py::test_rules_fire_only_on_their_condition
2 failed, 7 passed in 27.98s
```

实现后控制器独立运行：

```text
.........                                                                [100%]
9 passed in 27.06s
```

退出码：0。快照计数当前 NeedsInput、24 小时内精确 `reason=queue_expired` 的事件（不含 lease_expired）、24 小时内修改且当前 Expired 的提案；`queue_expired_24h>10` 发 warning。没有 DocType/Report/hooks 变更，无需 migrate。没有 provider 请求。提交：`20d5cdf`。

## S5 自检门

常驻 worker 保持停止，四个计划集成测试开头的 fastfail 均通过。原始输出：

```text
....                                                                     [100%]
4 passed in 35.77s
```

退出码：0。三站 migrate 退出码与实际元数据见 Task 5.1。启动 scheduled profile：

```text
Container dsherp-validation-scheduler-1 Started
Container dsherp-validation-scheduler-worker-1 Started
SCHEDULED_UP_EXIT=0
```

启动前 alpha 的 C0 基线为 4 条过期 Pending，`expire_proposals` 已是 `frequency=Cron`、`cron_format=*/10 * * * *`、`last_execution=null`。有界轮询原始状态：

```text
2026-09-04T12:16:09 attempt=1 {"statuses":{"5he61l424p":"Pending","d3u0kkn4kt":"Pending","ffvbmiq9s5":"Pending","fi3af9hog4":"Pending"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":null}],"failed_logs":0}
2026-09-04T12:16:25 attempt=2 {"statuses":{"5he61l424p":"Pending","d3u0kkn4kt":"Pending","ffvbmiq9s5":"Pending","fi3af9hog4":"Pending"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":null}],"failed_logs":0}
2026-09-04T12:16:42 attempt=3 {"statuses":{"5he61l424p":"Pending","d3u0kkn4kt":"Pending","ffvbmiq9s5":"Pending","fi3af9hog4":"Pending"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":null}],"failed_logs":0}
2026-09-04T12:16:58 attempt=4 {"statuses":{"5he61l424p":"Pending","d3u0kkn4kt":"Pending","ffvbmiq9s5":"Pending","fi3af9hog4":"Pending"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":null}],"failed_logs":0}
2026-09-04T12:17:15 attempt=5 {"statuses":{"5he61l424p":"Pending","d3u0kkn4kt":"Pending","ffvbmiq9s5":"Pending","fi3af9hog4":"Pending"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":null}],"failed_logs":0}
2026-09-04T12:17:31 attempt=6 {"statuses":{"5he61l424p":"Expired","d3u0kkn4kt":"Expired","ffvbmiq9s5":"Expired","fi3af9hog4":"Expired"},"jobs":[{"method":"dsherp_bridge.operations.expire_proposals","frequency":"Cron","cron_format":"*/10 * * * *","last_execution":"2026-09-04 12:17:21.527808"}],"failed_logs":0}
S5_SCHEDULED_ACCEPTED attempt=6
```

约 82 秒内 4/4 C0 基线成为 Expired，失败日志为 0。停止前发现 alpha/daily 已入队正常 `run_scheduled_job`；未删除任务或放宽夹具。保持 scheduler 停止、只让 scheduler-worker 自然消费，原始尾部：

```text
DRAIN attempt=10 queued=9 sites=['dsherp-daily.localhost', 'dsherp-validation.localhost']
DRAIN attempt=11 queued=6 sites=['dsherp-daily.localhost', 'dsherp-validation.localhost']
DRAIN attempt=12 queued=3 sites=['dsherp-daily.localhost']
DRAIN attempt=13 queued=3 sites=['dsherp-daily.localhost']
DRAIN attempt=14 queued=0 sites=[]
SCHEDULER_WORKER_DRAIN_STOP_EXIT=0
S5_FINAL_QUEUE={}
```

最终 scheduler 与 scheduler-worker 均停止，调度记录、Job Type 与 C0 的 Expired 状态保留。常驻 agent worker 仍停止，为后续集成隔离。没有调用真实 provider。

## S6

### Task 6.1 RED→GREEN

服务端业务原因透传、错误类型和可重试描述实现前，定向测试原始摘要：

```text
Test Files  1 failed (1)
Tests  6 failed | 14 passed (20)
```

首次实现后，控制器审查发现通用 `*Error` exception 会把 `RuntimeError: PRIVATE SECRET` 暴露到 UI。先补回归测试的原始摘要：

```text
Test Files  1 failed (1)
Tests  1 failed | 20 passed (21)
```

exception 回退收窄为明确列举的 ValidationError 类型后，控制器定向重跑：

```text
Test Files  1 passed (1)
Tests  21 passed (21)
```

退出码：0。提交：`9af5c70`。

### Task 6.2 RED→GREEN

ErrorBoundary、两个 Desk 根与 PreviewTransfer 根均先由缺少组件/包裹产生 RED；最初三个套件原始摘要：

```text
Test Files  3 failed (3)
Tests  no tests
```

控制器补查 PreviewTransfer 根时的行为红灯：

```text
FAIL  src/desk-context.test.jsx > PreviewTransfer 根包在 ErrorBoundary 中，现有挂载句柄语义不变
AssertionError: expected [Function PreviewTransfer] to be [Function ErrorBoundary]
- Expected: [Function ErrorBoundary]
+ Received: [Function PreviewTransfer]
Tests  1 failed | 3 passed (4)
```

错误界面改为复用 Ant Design Result/Button 前，组件约束红灯：

```text
FAIL  src/ErrorBoundary.test.jsx > 捕获子树渲染错误后显示固定文案和重新加载按钮，不泄露原始错误或堆栈
AssertionError: expected null to be truthy
Tests  1 failed | 2 passed (3)
```

三个根节点最终定向 GREEN：

```text
Test Files  3 passed (3)
Tests  11 passed (11)
```

拒绝端点、按钮与上层调用先分别因缺少 `reject_operation`/拒绝按钮产生 RED：

```text
FAIL  src/context-api.test.js > 操作拒绝仅向同源拒绝端点 POST 提案绑定和 CSRF，不接受正文、身份或 URL
Error: 不支持的会话操作
Tests  1 failed | 21 passed (22)
```

```text
FAIL  src/OperationProposal.test.jsx > Pending 未过期提案显示拒绝，点击只提交绑定且不重复
FAIL  src/OperationProposal.test.jsx > 拒绝成功后不可再确认，确认与拒绝互斥
FAIL  src/OperationProposal.test.jsx > 拒绝失败不伪造已拒绝，展示错误并保持确认禁用且不重试写入
TestingLibraryElementError: Unable to find an accessible element with the role "button" and name "拒绝"
Tests  3 failed | 13 passed (16)
```

```text
FAIL  src/ContextSidebar.test.jsx > 会话提案拒绝实际调用 reject_operation，不走确认
TestingLibraryElementError: Unable to find an accessible element with the role "button" and name "拒绝"
Tests  1 failed | 24 passed (25)
```

控制器审查发现非 Rejected 响应会被伪造成已拒绝，回归测试先红：

```text
FAIL  src/OperationProposal.test.jsx > 拒绝响应不完整或意外时不伪造已拒绝，要求刷新核实且不重试写入
TestingLibraryElementError: Unable to find an element with the text: 拒绝结果尚未核实，请刷新记录核实
提案已拒绝
Tests  1 failed | 16 passed (17)
```

修复后该组件 17/17，通过后拒绝链路组合测试 63/63。

状态映射、事件标签及 NeedsInput/完成自述警告的 RED 摘要：

```text
FAIL  src/agent-format.test.js > NeedsInput、Rejected、Expired 使用准确文案与色调
AssertionError: expected 'NeedsInput' to be '需要你补充信息'
Tests  1 failed | 9 passed (10)
```

```text
FAIL  src/agent-transcript.test.js > 新增运行事件按精确标签翻译，未核实完成自述用 danger
-   "租约续期", "请求用户补充", "提案已拒绝", "提案已过期", "完成自述未经核实"
+   "lease_renewed", "needs_input", "proposal_rejected", "proposal_expired", "unverified_completion_claim"
Tests  1 failed | 11 passed (12)
```

```text
FAIL  AgentWorkbench > NeedsInput 把回答显示为待补充问题并聚焦本页业务问题输入框
FAIL  AgentWorkbench > answer_flagged 在回答旁给出准确警告且不泄漏其他字段
FAIL  ContextSidebar > NeedsInput 把回答显示为待补充问题并聚焦侧栏业务问题输入框
Test Files  2 failed (2)
Tests  3 failed | 53 passed (56)
```

实现后这四个套件组合输出：

```text
Test Files  4 passed (4)
Tests  78 passed (78)
Duration  9.96s
```

轮询保留/退避/可重试发送与排队撤回的行为测试先红。原始摘要：

```text
Tests  3 failed | 1 passed | 26 skipped (30)
```

```text
Tests  2 failed | 30 skipped (32)
```

实现后两套件组合输出：

```text
Test Files  2 passed (2)
Tests  62 passed (62)
Duration  10.69s
```

源码与 dist 首次一起提交：`a2dd73d`。真实浏览器断网随后复现 transport reject 原样暴露且不可重试；补测试时原始 RED：

```text
FAIL  src/context-api.test.js > fetch 网络中断只抛安全可重试错误，不泄漏原异常且不重试
AssertionError: expected TypeError: PRIVATE Failed to fetch to match object { message: '网络连接中断，请检查连接后重试', …(2) }
Test Files  1 failed (1)
Tests  1 failed | 23 passed (24)
```

只把非 Abort transport reject 转成单次、安全的 transient 错误；修复后：

```text
Test Files  1 passed (1)
Tests  24 passed (24)
Duration  125ms
```

修复提交：`a3163a1`。

alpha backend 正常重启并从新进程提供 Desk：

```text
Container dsherp-validation-backend-1 Restarting
Container dsherp-validation-backend-1 Started
alpha_login_http=200
```

浏览器只使用 alpha 隔离站点和合成普通用户；提案与 NeedsInput 直接按真实服务端状态机建档，没有启动 agent worker、没有调用 provider。点击“拒绝”、显示 NeedsInput、真实断网超过 5 秒并保留侧栏之后，清理前对 alpha 实际状态的原始核验：

```text
{"proposal_status": "Rejected", "proposal_rejected_by": "dsherp-writer@example.invalid", "proposal_request_bound": true, "needs_input_status": "NeedsInput", "needs_input_question": "请指定要查询的仓库", "offline_conversation_title": "S6-BROWSER-OFFLINE-8c51136e"}
```

断网修复后的页面同时显示“网络连接中断，请检查连接后重试”、原历史“侧栏保留验证历史”、可编辑内容“断网时保留输入”，发送按钮可用但未自动发送。三张截图：

- `docs/engineering/evidence/runtime-reliability/s6-proposal-rejected.png`
- `docs/engineering/evidence/runtime-reliability/s6-needs-input-focused.png`
- `docs/engineering/evidence/runtime-reliability/s6-sidebar-offline-retains-session.png`

截图文件原始识别：

```text
docs/engineering/evidence/runtime-reliability/s6-proposal-rejected.png: PNG image data, 1200 x 1279, 8-bit/color RGB, non-interlaced
docs/engineering/evidence/runtime-reliability/s6-needs-input-focused.png: PNG image data, 1200 x 1279, 8-bit/color RGB, non-interlaced
docs/engineering/evidence/runtime-reliability/s6-sidebar-offline-retains-session.png: PNG image data, 1200 x 1279, 8-bit/color RGB, non-interlaced
```

验收夹具与专用用户删除后再查为空；清理输出：

```text
S6_BROWSER_CLEANUP conversations=3 proposals=1 runs=3 users=1
```

既有 writer 的会话列表包含一条早前合成会话，其来源指向已删除的销售订单，`search_sessions` 按 fastfail 返回 not found；本轮没有删除或改写该范围外数据。拒绝与 NeedsInput 通过显式 session URL 验证；侧栏断网改用本轮创建且已清理的专用普通用户验证。

## S6 自检门

`cd frontend && npm test` 原始输出：

```text

> dsherp-desk@0.1.0 test
> NODE_OPTIONS=--no-experimental-webstorage vitest run


 RUN  v4.1.11 /Users/lize/Documents/ChatGPT/dsherp/frontend


 Test Files  22 passed (22)
      Tests  200 passed (200)
   Start at  13:06:11
   Duration  13.55s (transform 1.68s, setup 550ms, import 27.30s, tests 24.40s, environment 7.97s)

```

退出码：0。

`cd frontend && node build.mjs` 原始输出为空，退出码 0。

`git diff --exit-code frappe_app/dsherp_bridge/public/dist` 原始输出为空，退出码 0。

S6 没有 DocType/Report/hooks 变更，无需 migrate。发出阶段状态前再次核验容器与 alpha 站点清理结果：

```text
dsherp-validation-backend-1 Up 9 minutes
dsherp-agent-worker=STOPPED
dsherp-scheduler=STOPPED
dsherp-scheduler-worker=STOPPED
0
0
```

最后两行依次为 alpha 上 `title LIKE "S6-%"` 的会话数量、临时用户 `s6-browser-8c51136e@example.invalid` 的数量；查询退出码 0。常驻 agent worker、scheduler、scheduler-worker 均保持停止；没有调用真实 provider。

## S7 / Task 7.1：G5 负载

最终用户/系统结果：一个可重复的 `infra/load_runs.py` 会在常驻 worker 已停、alpha/daily 无活动运行时 fastfail 预检；随后以专用临时 provider-env 启动一次性双站 worker，并把 provider 仅指向同一 Docker 网络里的本地 SSE 替身。脚本对 alpha 三名既有合成用户和 daily 合成用户各发一条只读问题，轮询终态、计算运行时间与事件序列，并发调用 `run_status` 20 次后计算 P95；最后精确删除本轮站点记录、四个业务 session 目录、临时 provider-env 和模型容器。

当前实际凭据边界与计划文字有一处明确偏离：`.runtime/erp-users.json` 由既有 provisioner 生成 `reader/denied` 两个 token profile，第三名既有 alpha 用户 `writer` 在独立的 `.runtime/context-writer.json`；daily 身份由站点映射持有。脚本不旋转或复制凭据，而是显式读取这两个 alpha 文件、核验恰好三个唯一用户；reader/denied 走真实 HTTP，writer/daily 在各自隔离 Site 内以普通用户身份提交。`denied` 没有 Item read 权限，本地替身调用现有 `erp_request_input`，因此按真实状态机以 NeedsInput 结束而不是伪造只读成功。

### RED→GREEN

纯行为测试在实现文件不存在时的原始 RED：

```text
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_load_runs.py ___________________
ImportError while importing test module '/Users/lize/Documents/ChatGPT/dsherp/tests/test_load_runs.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
../../../.local/share/uv/python/cpython-3.12-macos-aarch64-none/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_load_runs.py:5: in <module>
    from infra.load_runs import load_alpha_actors, summarize_run, validate_load
E   ModuleNotFoundError: No module named 'infra.load_runs'
=========================== short test summary info ============================
ERROR tests/test_load_runs.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.07s
```

第一次直接执行真实脚本在导入路径处 fastfail，尚未启动 worker 或创建运行：

```text
Traceback (most recent call last):
  File "/Users/lize/Documents/ChatGPT/dsherp/infra/load_runs.py", line 22, in <module>
    from dsherp.runtime_host import IMAGE, load_settings
ModuleNotFoundError: No module named 'dsherp'
```

加入显式仓库根路径后，脚本到达业务链，但 Runtime 容器没有命中宿主 SSE。相同镜像/网络的最小网络探针原始结论：

```text
{"exit": 1, "last_stderr": "urllib.error.URLError: <urlopen error [Errno -2] Name or service not known>", "seen": []}
{"rows": [{"host": "172.27.0.1", "exit": 1, "stdout": "", "error": "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>"}, {"host": "host.lima.internal", "exit": 1, "stdout": "", "error": "urllib.error.URLError: <urlopen error [Errno -2] Name or service not known>"}, {"host": "host.containers.internal", "exit": 1, "stdout": "", "error": "urllib.error.URLError: <urlopen error [Errno -2] Name or service not known>"}], "seen": []}
```

因此本地替身改为一次性受限容器，并在与业务 Runtime 相同的 `dsherp-validation_api` 网络内提供 SSE；不修改生产容器命令、不加入主机别名。模型容器单独健康验证原始输出：

```text
G5_MODEL_CONTAINER_READY=dsherp-g5-model-d7265f2ec677
G5_MODEL_INITIAL_REQUESTS=0
G5_MODEL_CONTAINER_STOPPED
G5_MODEL_CONTAINER_REMNANTS=0
```

真实负载随即全部到达本地替身。控制器曾额外要求前一条 `finished` 事件写回后才能领取下一条；实测 denied 在 NeedsInput 已成为终态后，下一条可在前一容器最后约 0.9 秒收尾时领取。该限制不是计划验收、也不保护真实故障，按仓库约定删除；仍严格验证 alpha 三条 claimed 顺序、无 Failed，以及 daily claimed 早于 alpha 第二条。

纯行为测试最终 GREEN：

```text
...................                                                      [100%]
19 passed in 0.04s
```

### G5 结果表

| Site / owner | run_id | queued→claimed 秒 | 总时长秒 | 状态 | kind 序列 |
| --- | --- | ---: | ---: | --- | --- |
| alpha / reader | `12fb0fc7448aec8c745ba26b895e906b72d7d1f7d16c15ffd6dea69a8c94a42a` | 3.808631 | 24.924969 | Succeeded | queued, claimed, runtime_started, model_call_reserved, model_response, tool_call, model_call_reserved, model_response, runtime_tool_call, tool_result, turn_end, container_finished, finished |
| alpha / denied | `23efc88752fb3f668da3f8738512d6524cafee36bf503607d005ce1147571ada` | 25.685703 | 48.879349 | NeedsInput | queued, claimed, runtime_started, model_call_reserved, model_response, needs_input, tool_call, runtime_tool_call, tool_result, turn_end, container_finished, finished |
| alpha / writer | `58b7c220eb9e5cafdf83849817b54948f1fe43d397f5c17de13bb298d50d3413` | 46.862508 | 72.145355 | Succeeded | queued, claimed, runtime_started, model_call_reserved, model_response, tool_call, model_call_reserved, model_response, runtime_tool_call, tool_result, turn_end, container_finished, finished |
| daily / daily-operator | `0cefad20ad01a5417dcda5edfb1113f7d801c11e97a6451e2fcbd521d2d3be3c` | 0.647646 | 22.268828 | Succeeded | queued, claimed, runtime_started, model_call_reserved, model_response, tool_call, model_call_reserved, model_response, runtime_tool_call, tool_result, turn_end, container_finished, finished |

daily 的 claimed 时间为 `13:38:38.062012`，早于 alpha 第二条的 `13:39:00.476861`。alpha 的 claimed 顺序与提交顺序一致；四条无 Failed。20 次并发 `run_status` 的 P95 为 `0.145412s`，小于 1 秒。

真实 GREEN 命令 `.venv/bin/python infra/load_runs.py` 的原始输出：

```text
G5_LOAD_RESULT={"runs":[{"run_id":"12fb0fc7448aec8c745ba26b895e906b72d7d1f7d16c15ffd6dea69a8c94a42a","site":"dsherp-validation.localhost","owner":"dsherp-reader@example.invalid","status":"Succeeded","queued_to_claimed_seconds":3.808631,"total_seconds":24.924969,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"queued_at":"2026-09-04 13:38:34.759429","claimed_at":"2026-09-04 13:38:38.568060","finished_at":"2026-09-04 13:38:59.684398"},{"run_id":"23efc88752fb3f668da3f8738512d6524cafee36bf503607d005ce1147571ada","site":"dsherp-validation.localhost","owner":"dsherp-denied@example.invalid","status":"NeedsInput","queued_to_claimed_seconds":25.685703,"total_seconds":48.879349,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","needs_input","tool_call","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"queued_at":"2026-09-04 13:38:34.791158","claimed_at":"2026-09-04 13:39:00.476861","finished_at":"2026-09-04 13:39:23.670507"},{"run_id":"58b7c220eb9e5cafdf83849817b54948f1fe43d397f5c17de13bb298d50d3413","site":"dsherp-validation.localhost","owner":"dsherp-writer@example.invalid","status":"Succeeded","queued_to_claimed_seconds":46.862508,"total_seconds":72.145355,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"queued_at":"2026-09-04 13:38:35.920057","claimed_at":"2026-09-04 13:39:22.782565","finished_at":"2026-09-04 13:39:48.065412"},{"run_id":"0cefad20ad01a5417dcda5edfb1113f7d801c11e97a6451e2fcbd521d2d3be3c","site":"dsherp-daily.localhost","owner":"daily-operator@example.invalid","status":"Succeeded","queued_to_claimed_seconds":0.647646,"total_seconds":22.268828,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"queued_at":"2026-09-04 13:38:37.414366","claimed_at":"2026-09-04 13:38:38.062012","finished_at":"2026-09-04 13:38:59.683194"}],"run_status_p95_seconds":0.145412,"run_status_samples":20,"provider_requests":7,"worker":{"pid":82939,"returncode":0,"events":[{"ts":"2026-09-04T05:38:34.677+00:00","event":"alert","key":"ops_snapshot_stale","severity":"warning","message":"运维快照陈旧"},{"ts":"2026-09-04T05:38:38.068+00:00","event":"claimed","site":"dsherp-daily.localhost","run_id":"0cefad20ad01a5417dcda5edfb1113f7d801c11e97a6451e2fcbd521d2d3be3c"},{"ts":"2026-09-04T05:38:38.574+00:00","event":"claimed","site":"dsherp-validation.localhost","run_id":"12fb0fc7448aec8c745ba26b895e906b72d7d1f7d16c15ffd6dea69a8c94a42a"},{"ts":"2026-09-04T05:38:58.888+00:00","event":"container_finished","run_id":"0cefad20ad01a5417dcda5edfb1113f7d801c11e97a6451e2fcbd521d2d3be3c","status":"Succeeded","duration_ms":20820},{"ts":"2026-09-04T05:38:58.918+00:00","event":"container_finished","run_id":"12fb0fc7448aec8c745ba26b895e906b72d7d1f7d16c15ffd6dea69a8c94a42a","status":"Succeeded","duration_ms":20343},{"ts":"2026-09-04T05:39:00.483+00:00","event":"claimed","site":"dsherp-validation.localhost","run_id":"23efc88752fb3f668da3f8738512d6524cafee36bf503607d005ce1147571ada"},{"ts":"2026-09-04T05:39:22.789+00:00","event":"claimed","site":"dsherp-validation.localhost","run_id":"58b7c220eb9e5cafdf83849817b54948f1fe43d397f5c17de13bb298d50d3413"},{"ts":"2026-09-04T05:39:23.597+00:00","event":"container_finished","run_id":"23efc88752fb3f668da3f8738512d6524cafee36bf503607d005ce1147571ada","status":"NeedsInput","duration_ms":23110},{"ts":"2026-09-04T05:39:47.605+00:00","event":"container_finished","run_id":"58b7c220eb9e5cafdf83849817b54948f1fe43d397f5c17de13bb298d50d3413","status":"Succeeded","duration_ms":24816}]},"cleanup":{"dsherp-validation.localhost":{"runs":0,"conversations":0},"dsherp-daily.localhost":{"runs":0,"conversations":0}},"business_session_paths_removed":4,"final_active":{"dsherp-validation.localhost":0,"dsherp-daily.localhost":0}}
```

发出 Task 7.1 状态前再次核验；原始输出：

```text
PIDFILE_ABSENT
worker_process_count=0
context_container_count=0
model_container_count=0
0
0
0
0
```

后四个 0 依次是 alpha 本轮问题/探针残留、alpha 活动运行、daily 本轮问题残留、daily 活动运行。Task 7.1 没有 DocType/Report/hooks 变更，无需 migrate；整个负载只调用本地 SSE 替身，没有读取 `.env`、没有调用真实 provider。

## S7 / Task 7.2：混沌演练

### 项 1：worker SIGKILL 与租约清扫

演练前 fastfail 的原始输出：

```text
CHAOS1_FASTFAIL_OK resident_worker=0 active_alpha_daily=0
CHAOS1_CONTAINERS_CLEAR
```

第一次演练已经取得 SIGKILL→租约到期→下一 worker 清扫的核心路径，但继续会话时误用了运行服务 client，服务端按权限边界返回 `HTTP 403`。该轮 finally 精确清理后核验 pid/worker/本轮记录与活动运行均为 0；没有把 403 当成产品失败。改为 conversation owner 的 reader client 后，从零状态完整重演。

成功演练的原始输出：

```text
CHAOS1_RUNTIME_STARTED=2026-09-04T05:48:51.097+00:00
CHAOS1_WORKER_KILLED=2026-09-04T05:48:51.100+00:00 pid=86460
CHAOS1_POST_KILL_STATUS=Running at=2026-09-04T05:48:53.195+00:00
CHAOS1_WAITING_LEASE status=Running expires_at=2026-09-04 13:51:32.844162
CHAOS1_WAITING_LEASE status=Running expires_at=2026-09-04 13:51:32.844162
CHAOS1_WAITING_LEASE status=Running expires_at=2026-09-04 13:51:32.844162
CHAOS1_WAITING_LEASE status=Running expires_at=2026-09-04 13:51:32.844162
CHAOS1_WAITING_LEASE status=Running expires_at=2026-09-04 13:51:32.844162
CHAOS1_LEASE_READY=2026-09-04T05:51:39.531+00:00 expires_at=2026-09-04 13:51:32.844162
CHAOS1_SWEEP_FAILED=2026-09-04T05:51:41.534+00:00 sweeper_pid=91433
CHAOS1_CONTINUED=Cancelled at=2026-09-04T05:51:41.648+00:00
CHAOS1_RESULT={"runtime_started_at":"2026-09-04T05:48:51.097+00:00","worker_killed_at":"2026-09-04T05:48:51.100+00:00","worker_pid":86460,"post_kill_status":"Running","lease_expires_at":"2026-09-04 13:51:32.844162","lease_ready_at":"2026-09-04T05:51:39.531+00:00","swept_failed_at":"2026-09-04T05:51:41.534+00:00","sweeper_pid":91433,"expired_event":{"kind":"expired","payload":{"reason":"lease_expired"},"recorded_at":"2026-09-04 13:51:40.097471"},"first_kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","expired"],"continued_run_id":"424c0d1dcd713b14a49d5ac0bdd82a100f43c64ebe692a9b5261239cbce147ce","continued_status":"Cancelled","continued_at":"2026-09-04T05:51:41.648+00:00","local_provider_requests":2,"orphan_context_containers":0,"cleanup":{"runs":0,"conversations":0},"final_active":{"dsherp-validation.localhost":0,"dsherp-daily.localhost":0},"pidfile_exists":false,"worker_process_count":0,"model_container_count":0}
```

演练结论：`runtime_started` 后 3ms 对一次性 worker 发 SIGKILL；运行仍为 Running，没有假完成。原 180 秒租约到期后，第二 worker 在约 2 秒内清扫为 Failed，事件序列以唯一 `expired {"reason":"lease_expired"}` 结束。同一会话立即接受下一条消息并可撤回为 Cancelled；没有自动重试旧运行。模型请求仅到本地 SSE。

落档前再次核验原始状态：

```text
PIDFILE_ABSENT
worker_process_count=0
context_container_count=0
model_container_count=0
dsherp-validation-backend-1 Up 53 minutes
0
0
0
```

三个 0 依次为 alpha 本轮问题记录、alpha 活动运行、daily 活动运行。项 1 没有 DocType/Report/hooks 变更，无需 migrate。项 2 的真实-key `GET /models` 探针及项 3 的 alpha backend 约 10 秒短停尚未执行，等待计划末尾要求的用户授权。

### 项 2：provider 三连失败与熔断恢复

最终用户/系统结果：一次性双站 worker 在专用 provider-env 指向 `127.0.0.1:9` 时连续完成恰好三条 Failed 运行并打开熔断；真实 key 只进入熔断已打开且两站活动运行 0/0 的一次性 0600 provider-env 窗口，最多一次获授权 GET /models、真实窗口零 claim，且不发生 chat/completions；切回本地 SSE 后才创建恢复运行并 Succeeded；`.env` 未变，本轮夹具全部清除。两次分类修复前的 live RED 已清理且未到达真实 provider。

Alert TDD RED 原始输出：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____ test_three_provider_failure_runs_emit_one_provider_circuit_open_alert _____
E       assert 0 == 1
E        +  where 0 = len([])
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_three_provider_failure_runs_emit_one_provider_circuit_open_alert
1 failed in 0.27s
```

Alert TDD GREEN 原始输出：

```text
.                                                                        [100%]
1 passed in 0.26s
```

```text
....................................................                     [100%]
52 passed in 0.42s
```

第一次 live chaos RED（分类修复前）原始输出：

```text
Traceback (most recent call last):
  File "/Users/lize/Documents/ChatGPT/dsherp/work/runtime-reliability-chaos2.py", line 343, in <module>
    main()
  File "/Users/lize/Documents/ChatGPT/dsherp/work/runtime-reliability-chaos2.py", line 187, in main
    raise RuntimeError("failed provider run lacks model_error")
RuntimeError: failed provider run lacks model_error
```

诊断重跑暴露的 value-safe 事件序列（第二条 live RED）：

```text
RuntimeError: failed provider run lacks model_error: {"site":"dsherp-validation.localhost","run_id":"45162690e1d538833d5e946cc8b3b03ed1e496f225c2f32a18ed7e00ce7ffc80","status":"Failed","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","model_call_reserved","model_response","model_call_reserved","model_response","model_call_reserved","model_response","model_call_reserved","model_response","model_call_reserved","model_response","turn_end","runtime_failed","runtime_failed","finished"]}
```

两次诊断运行均按确切记录、会话、worker 与容器清理，且从未到达真实 provider 探针。

固定 DSH 0.1.1rc1 源码确认 provider 失败可带内到达为 `finish.reason.kind=error`；错误码映射见 https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/llm/llm-pi-ai/src/stream.ts#L76-L114 ，in-stream error 到 finish 的转换见 https://github.com/deepseek-ai/deepseek-harness/blob/528c682e061696f5a160f363f236ecbf53cbd006/packages/llm/llm-pi-ai/src/stream.ts#L199-L204 。

In-stream provider 分类 TDD RED 原始输出：

```text
✔ business catalog rejects unlisted skill directories (4.767792ms)
✔ ordinary and direct compaction requests both require authorization (0.704792ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.19ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.419042ms)
✔ drift during a response cannot produce a successful terminal chunk (0.659375ms)
✔ finish and errors are reported without affecting the stream (0.302125ms)
✖ an in-stream provider error finish is reported as model_error (0.734708ms)
ℹ tests 7
ℹ suites 0
ℹ pass 6
ℹ fail 1
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 61.331958

✖ failing tests:

test at runtime/model-guard.test.cjs:74:1
✖ an in-stream provider error finish is reported as model_error (0.734708ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
  + actual - expected

  + 'model_response'
  - 'model_error'
           ^
```

In-stream provider 分类 GREEN 原始输出：

```text
✔ dispose waits for native creation and releases exactly the completed handle (3.298417ms)
✔ failed creation remains a request error but cannot break cleanup (1.383541ms)
✔ business catalog rejects unlisted skill directories (5.526334ms)
✔ ordinary and direct compaction requests both require authorization (0.685792ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.188334ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.117792ms)
✔ drift during a response cannot produce a successful terminal chunk (0.777292ms)
✔ finish and errors are reported without affecting the stream (0.35ms)
✔ an in-stream provider error finish is reported as model_error (0.187041ms)
ℹ tests 9
ℹ suites 0
ℹ pass 9
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 68.166917
```

```text
.........................................................                [100%]
57 passed in 8.47s
```

CHAOS2 最终原始输出：

```text
CHAOS2_RESULT={"queued_at":"2026-09-04T06:20:28.101+00:00","failures":[{"site":"dsherp-validation.localhost","run_id":"aded9bfa009115709fa5feae0a25c23e2a23ff3035bd9fd6a389fa26986fcb79","status":"Failed","model_error_count":6,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","turn_end","runtime_failed","runtime_failed","finished"]},{"site":"dsherp-validation.localhost","run_id":"26b36126c867339f2c4d4c30db22f07c4f17600f9d24979f7ed4aa1cfb522148","status":"Failed","model_error_count":6,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","turn_end","runtime_failed","runtime_failed","finished"]},{"site":"dsherp-daily.localhost","run_id":"9270fefafa00e74f724bbb8652e8339427d9096a00f3562e3bcb11bfa102241c","status":"Failed","model_error_count":6,"kinds":["queued","claimed","runtime_started","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","model_call_reserved","model_error","turn_end","runtime_failed","runtime_failed","finished"]}],"circuit_opened_at":"2026-09-04T06:21:46.927+00:00","open_metric_line":"dsherp_provider_circuit_open 1","provider_failures_metric_line":"dsherp_provider_call_failures_total 3","real_probe_window_started_at":"2026-09-04T06:21:59.698+00:00","real_probe_window_ended_at":"2026-09-04T06:22:48.004+00:00","probe_closed_seconds_after_restore":48.306,"closed_metric_line":"dsherp_provider_circuit_open 0","real_probe_contract":{"method":"GET","path":"/models","maximum_calls":1},"active_during_real_probe":{"dsherp-validation.localhost":0,"dsherp-daily.localhost":0},"local_provider_restored_at":"2026-09-04T06:22:50.900+00:00","recovery_run":{"run_id":"5e7c2cb34957dc8dcccf519b584f509eac16e6045737d0142031295ff1366221","status":"Succeeded","kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"]},"local_provider_requests":2,"alert_line":{"ts":"2026-09-04T06:21:44.879+00:00","event":"alert","key":"provider_circuit_open","severity":"critical","message":"模型服务熔断已打开"},"worker_pid":26829,"worker_returncode":0,"claims_during_real_probe":0,"env_file_unchanged":true,"cleanup":{"dsherp-validation.localhost":{"runs":0,"conversations":0},"dsherp-daily.localhost":{"runs":0,"conversations":0}},"session_paths_removed":4,"final_active":{"dsherp-validation.localhost":0,"dsherp-daily.localhost":0},"pidfile_exists":false,"worker_process_count":0,"context_container_count":0,"model_container_count":0}
```

演练结论：两次 TDD 修复后本项 PASS。恰好三条 provider-failure 运行，每条 6 个 `model_error`；worker 级指标为 `dsherp_provider_call_failures_total 3`，熔断 gauge 为 `dsherp_provider_circuit_open 1`。恰好一条 critical `provider_circuit_open` 告警，时间为 `2026-09-04T06:21:44.879+00:00`。真实-key 窗口仅在熔断已打开且两站活动运行精确 0/0 之后开始（`2026-09-04T06:21:59.698+00:00` 至 `2026-09-04T06:22:48.004+00:00`）；探针契约为最多一次获授权 GET /models，真实窗口零 claim。gauge 在恢复后 48.306 秒关闭为 `dsherp_provider_circuit_open 0`。`local_provider_restored_at` 为 `2026-09-04T06:22:50.900+00:00`，之后才创建恢复运行 `5e7c2cb34957dc8dcccf519b584f509eac16e6045737d0142031295ff1366221` 并 Succeeded。恢复阶段本地 SSE `local_provider_requests` 为 2。真实 provider 只发生这一次探针，不发生 chat/completions。`.env` 未变（`env_file_unchanged` true）。cleanup 为两站 `runs` 0、`conversations` 0，`session_paths_removed` 4，`final_active` 0/0，`pidfile_exists` false，`worker_process_count` 0，`context_container_count` 0，`model_container_count` 0。两次 live RED 不计入成功演练，已完整清理，从未到达真实 provider。Task 7.2 无 DocType/Report/hooks 变更，因而不 migrate。

### 项 3：alpha backend 短停与 worker 自恢复

最终用户/系统结果：一次性 worker 先 SIGSTOP，再排入合成运行并保持 Queued，然后 stop 同一 alpha backend，再 SIGCONT，避免领取竞态；同一 PID 在停机窗口存活并写出 `worker_error`，两站不可用；约 10.005 秒后 finally 优先恢复同一 backend，两站 readiness 200；同一 PID 领取该运行并经本地 SSE Succeeded；backend 最终 running，夹具全部清除。

CHAOS3 preflight 原始输出：

```text
CHAOS3_FASTFAIL_OK resident_worker=0 active_alpha_daily=0
backend_running=true status=running
context_container_count=0
model_container_count=0
```

CHAOS3 最终原始输出：

```text
CHAOS3_RESULT={"worker_pid_before":27734,"worker_paused_at":"2026-09-04T06:23:56.118+00:00","queued_run_id":"b2424adeef2a0813c94f88fa10d30cf48daf9999aef194ceb1f17736982dc007","queued_status_before_stop":"Queued","docker_stop_invoked_at":"2026-09-04T06:23:57.516+00:00","backend_stopped_at":"2026-09-04T06:23:58.452+00:00","docker_stop_exit":0,"docker_stop_stdout":"dsherp-validation-backend-1","worker_continued_at":"2026-09-04T06:23:58.472+00:00","worker_alive_mid_outage_at":"2026-09-04T06:23:58.474+00:00","outage_observations":{"dsherp-validation.localhost":"ConnectError","dsherp-daily.localhost":"ConnectError"},"docker_start_invoked_at":"2026-09-04T06:24:08.458+00:00","backend_downtime_seconds":10.005,"backend_started_at":"2026-09-04T06:24:08.639+00:00","docker_start_exit":0,"docker_start_stdout":"dsherp-validation-backend-1","backend_ready_at":"2026-09-04T06:24:10.251+00:00","readiness":{"dsherp-validation.localhost":"200","dsherp-daily.localhost":"200"},"recovery_status":"Succeeded","recovery_kinds":["queued","claimed","runtime_started","model_call_reserved","model_response","tool_call","model_call_reserved","model_response","runtime_tool_call","tool_result","turn_end","container_finished","finished"],"worker_pid_after":27734,"local_provider_requests":2,"worker_error_count":16,"first_worker_error":{"ts":"2026-09-04T06:23:58.546+00:00","event":"worker_error","site":"dsherp-validation.localhost","error_class":"ConnectError"},"claimed_line":{"ts":"2026-09-04T06:24:11.417+00:00","event":"claimed","site":"dsherp-validation.localhost","run_id":"b2424adeef2a0813c94f88fa10d30cf48daf9999aef194ceb1f17736982dc007"},"worker_returncode":0,"cleanup":{"dsherp-validation.localhost":{"runs":0,"conversations":0},"dsherp-daily.localhost":{"runs":0,"conversations":0}},"session_paths_removed":1,"backend_running_final":true,"final_active":{"dsherp-validation.localhost":0,"dsherp-daily.localhost":0},"pidfile_exists":false,"worker_process_count":0,"context_container_count":0,"model_container_count":0}
```

演练结论：本项 PASS。同一 worker pid 27734 在停机前后保持不变。两站停机观察均为 `ConnectError`。`backend_downtime_seconds` 为 10.005。首条 `worker_error` 于 `2026-09-04T06:23:58.546+00:00`，`worker_error_count` 为 16。同一 pid 于 `2026-09-04T06:24:11.417+00:00` claimed 运行 `b2424adeef2a0813c94f88fa10d30cf48daf9999aef194ceb1f17736982dc007`，经本地 SSE Succeeded；`local_provider_requests` 为 2。`docker_stop_stdout` 与 `docker_start_stdout` 均为 `dsherp-validation-backend-1`，退出码均为 0。恢复后两站 readiness 均为 `200`。`backend_running_final` true。cleanup 为两站 `runs` 0、`conversations` 0，`session_paths_removed` 1，`final_active` 0/0，`pidfile_exists` false，`worker_process_count` 0，`context_container_count` 0，`model_container_count` 0。Task 7.2 无 DocType/Report/hooks 变更，因而不 migrate。

### Task 7.2 提交前复核

Node 完整 runtime 门原始输出：

```text
✔ dispose waits for native creation and releases exactly the completed handle (2.654333ms)
✔ failed creation remains a request error but cannot break cleanup (1.378416ms)
✔ business catalog rejects unlisted skill directories (5.040666ms)
✔ ordinary and direct compaction requests both require authorization (0.779084ms)
✔ a swallowed compaction denial still poisons all subsequent model calls (0.208917ms)
✔ runtime drift rejects subsequent streams even if the file is restored (1.598708ms)
✔ drift during a response cannot produce a successful terminal chunk (0.769209ms)
✔ finish and errors are reported without affecting the stream (0.302375ms)
✔ an in-stream provider error finish is reported as model_error (0.152334ms)
ℹ tests 9
ℹ suites 0
ℹ pass 9
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 64.667084
```

相关 Python 回归原始输出：

```text
.........................................................                [100%]
57 passed in 8.53s
```

`git diff --check` 退出码为 0、stdout 为空。发出 Task 7.2 状态前，对仓库与站点实际状态再次核验；原始输出：

```text
branch=codex/runtime-reliability
backend=running
pidfile_exists=false
worker_process_count=0
context_container_count=0
model_container_count=0
metrics_9109_reachable=false
active_query_exit=0
{"active_runs": 0, "site": "dsherp-validation.localhost"}
{"active_runs": 0, "site": "dsherp-daily.localhost"}
```

Task 7.2 的修改不包含 DocType、Report 或 hooks，阶段末无需 migrate。获授权的真实 provider 行为仍只有熔断打开期的一次 `GET /models`；提交前复核没有调用 provider。

### Task 7.2 独立审查修复

独立审查发现，固定 DSH 的 error finish 还包含上下文溢出、空响应、无效请求等非 provider 可用性故障；若把全部 `model_error` 计入 `provider_failures`，三次用户输入错误会误开全 worker 熔断。最小集成行为测试先同时记录 4 个候选 provider 错误类（其中包含旧的通用 `ProviderError`）与 7 个明确非可用性错误类；修复前 RED 原始输出：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_finish_run_reports_and_persists_provider_failure_count __________

>       assert result.returncode == 0, result.stdout + result.stderr
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 23, in <module>
E         AssertionError: {'run_id': '4m2eb226vi', 'status': 'Failed', 'provider_failures': 11}
E
E       assert 1 == 0

tests/integration/test_run_events.py:415: AssertionError
=========================== short test summary info ============================
FAILED tests/integration/test_run_events.py::test_finish_run_reports_and_persists_provider_failure_count
1 failed in 23.76s
```

第一轮先把 `ProviderError`、`TRANSPORT`、`TIMEOUT`、`SERVER` 从所有 error finish 中分离；其他 error finish 仍保留为 `model_error`，但不阻断其他用户。目标 GREEN 原始输出：

```text
.                                                                        [100%]
1 passed in 26.54s
```

随后按“只有明确可归因于 provider 可用性”收紧：通用 `ProviderError` 没有稳定原因，不能凭名称推定为 5xx/超时/传输故障。把期望收紧为只统计 `TRANSPORT`、`TIMEOUT`、`SERVER` 后再次先 RED：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_finish_run_reports_and_persists_provider_failure_count __________
>       assert result.returncode == 0, result.stdout + result.stderr
E       AssertionError: Traceback (most recent call last):
E           File "<stdin>", line 23, in <module>
E         AssertionError: {'run_id': '8rtivlea3l', 'status': 'Failed', 'provider_failures': 4}
E
E       assert 1 == 0
tests/integration/test_run_events.py:415: AssertionError
=========================== short test summary info ============================
FAILED tests/integration/test_run_events.py::test_finish_run_reports_and_persists_provider_failure_count
1 failed in 25.70s
```

严格分类 GREEN 原始输出：

```text
.                                                                        [100%]
1 passed in 24.25s
```

此前只读审查还指出：到 60 秒的 `/models` 探针若返回 False，旧实现随后调用 `allow()` 会误转 half-open 并领取一条业务运行。最小失败路径测试先把第二条运行留在 claim 队列；修复前 RED 原始输出：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_coordinator_failed_probe_keeps_open_circuit_from_claiming ________

>           assert coordinator.tick(now=60)==0 and probes==[False]
E           assert (1 == 0)
E            +  where 1 = tick(now=60)
E            +    where tick = <dsherp.context_worker.Coordinator object at 0x10bdabd40>.tick

tests/test_context_worker.py:499: AssertionError
----------------------------- Captured stderr call -----------------------------
{"ts": "2026-09-04T06:45:27.017+00:00", "event": "claimed", "site": "a", "run_id": "first"}
{"ts": "2026-09-04T06:45:27.017+00:00", "event": "runtime_failed", "run_id": "first", "error_class": "RuntimeError", "duration_ms": 0}
{"ts": "2026-09-04T06:45:27.019+00:00", "event": "claimed", "site": "a", "run_id": "second"}
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_coordinator_failed_probe_keeps_open_circuit_from_claiming
1 failed in 0.38s
```

失败探针现保持 circuit open、gauge 1，并返回零 claim。目标 GREEN 与相关纯 Python 回归原始输出：

```text
.                                                                        [100%]
1 passed in 0.23s
```

```text
..........................................................               [100%]
58 passed in 8.43s
```

服务端事件文件级回归原始输出：

```text
.......                                                                  [100%]
7 passed in 44.09s
```

本轮修复仍未调用 provider，也未改 DocType、Report 或 hooks。

复审提交前的仓库/站点清理状态原始输出：

```text
backend=running
pidfile_exists=false
worker_process_count=0
context_container_count=0
model_container_count=0
active_query_exit=0
{"active_runs": 0, "site": "dsherp-validation.localhost"}
{"active_runs": 0, "site": "dsherp-daily.localhost"}
```

`git diff --check` 退出码 0、stdout 为空。

第一次复审进一步把失败探针路径推进到下一轮 tick：`t=60` 虽然被第一轮修复拒绝，`t=63` 仍依据 breaker 原始 `_opened_at` 转为 half-open 并领取。扩展同一个失败路径测试后，第二轮 RED 原始输出：

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_coordinator_failed_probe_keeps_open_circuit_from_claiming ________
>           assert coordinator.tick(now=63)==0 and probes==[False]
E           assert (1 == 0)
E            +  where 1 = tick(now=63)
E            +    where tick = <dsherp.context_worker.Coordinator object at 0x107b93b90>.tick

tests/test_context_worker.py:503: AssertionError
----------------------------- Captured stderr call -----------------------------
{"ts": "2026-09-04T06:53:16.400+00:00", "event": "claimed", "site": "a", "run_id": "first"}
{"ts": "2026-09-04T06:53:16.400+00:00", "event": "runtime_failed", "run_id": "first", "error_class": "RuntimeError", "duration_ms": 0}
{"ts": "2026-09-04T06:53:16.402+00:00", "event": "claimed", "site": "a", "run_id": "second"}
{"ts": "2026-09-04T06:53:16.402+00:00", "event": "runtime_failed", "run_id": "second", "error_class": "RuntimeError", "duration_ms": 0}
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_coordinator_failed_probe_keeps_open_circuit_from_claiming
1 failed in 0.37s
```

第二轮实现让 Coordinator 在 breaker 仍为 open 且尚未到下一探针时直接拒绝领取；只有 `probe()` 返回 True 才 reset。扩展目标测试 GREEN：

```text
.                                                                        [100%]
1 passed in 0.23s
```

相关回归最初暴露两条旧 Coordinator 测试仍要求“探针 False 后执行 half-open 业务试跑”：

```text
.............................F.F..........................               [100%]
=================================== FAILURES ===================================
____________ test_half_open_tick_claims_only_one_trial_across_sites ____________
>           assert coordinator.tick(now=60)==1 and len(claimed)==1
E           assert (0 == 1)
____________ test_half_open_empty_poll_releases_trial_for_next_tick ____________
>           assert coordinator.tick(now=60)==0 and breaker.state=='half_open'
E           AssertionError: assert (0 == 0 and 'open' == 'half_open'
=========================== short test summary info ============================
FAILED tests/test_context_worker.py::test_half_open_tick_claims_only_one_trial_across_sites
FAILED tests/test_context_worker.py::test_half_open_empty_poll_releases_trial_for_next_tick
2 failed, 56 passed in 9.41s
```

这两条旧断言与计划指定的“open 期只探针，True 才 reset”冲突，已删除；`CircuitBreaker` 自身的 half-open 纯逻辑测试保留。最终相关门原始输出：

```text
........................................................                 [100%]
56 passed in 8.48s
```

同一失败路径在第二次修复后转绿，未触发“两次修复仍红”的停止条件。
