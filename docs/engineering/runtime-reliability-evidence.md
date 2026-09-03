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
