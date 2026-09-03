# dsherp 生产化总体设计

日期：2026-09-03。状态：方向性架构 spec，七项裁决点已由用户于 2026-09-03 决定（见"已裁决"节），待写计划 1。本文回答"把 dsherp 设计成能扛住生产级校验的系统需要改什么"，定义生产级校验的验收门、目标拓扑、八条工作流的设计与不变量，以及分项实施顺序。每条工作流另行制定实施计划；本文不构成任何一步的实施计划。审计依据见[生产就绪第二轮审计](../../engineering/production-readiness-audit-2026-09-03.md)（编号 S/D/R/T/Q/A 在本文引用）和 2026-09-02 七维审计（已并入本文）。

## 背景与结论

v16 迁移已完成，四站为本机隔离合成环境。两轮审计的总判定：**"不让模型偷偷写数据"的安全底座是生产级的，不动**；不能上生产的原因集中在四个面——(1) 没有可部署的制品与环境（D1–D4），(2) 运行底座在真实并发与真实 provider 下会串行、崩溃、超时（R1–R3、A1），(3) 数据面没有异地备份、迁移路径与审计不可篡改保证（T1–T2、D4），(4) 质量门禁与可观测性为零（Q1–Q2、七维 #7）。这四个面互相独立，可以并行推进，但只有全部达标才算通过生产校验。

## 演示与生产差异的前置约束

2026-09-02 会话"生产性 Agent 与演示 Agent 的差异"给出的判断：demo 上线后完成率不到三成，差别不在模型，而在**输入分布**与**失败处理**。demo 在作者挑过的输入上跑一条快乐路径，生产在长尾输入、脏数据、外部系统抖动下跑十几步。七个具体差别是：输入是挑过的还是真实的；工具是 mock 还是真实系统；有没有中断和重试；"完成"由谁判定；业务规则在 prompt 里还是在人脑里；有没有出口；能不能看见每一步。

本 spec 把这七条从"审计维度"升格为**前置约束**：从现在起，任何新能力在设计阶段就必须逐条回答，答不出的能力不做（与 Agent 体系架构 spec 的"三问"并列）。这是本文最核心的设计决定，其余工作流都是这七条在现有代码上的落地。

| 差别 | 前置约束（新能力设计时必须满足） | 落地工作流 |
|---|---|---|
| 1 输入 | 每个新意图/工具都要有来自真实运行或合成长尾（缺字段、多意图、口语、错别字、中英混杂）的评估用例，不得只用作者写的干净输入；系统提示与工具描述由服务端限制生成，不靠模型试探 | F |
| 2 工具 | 工具错误必须带三分类（validation / permission / transient）与服务端原因透传；模型收到每类错误后的行为有测试；不引入 mock ERP | C、F |
| 3 中断与重试 | 每个写操作带幂等键并先落持久意图；运行有租约与续期，超时后状态可判定；重跑不产生重复单据；取消在 5s 内落地 | C |
| 4 完成判定 | "完成"只由外部可核验事实判定（写后回读、来源与执行记录交叉一致）；模型自述不计入状态；不可核验即 Unknown | C（已有底座保持） |
| 5 业务规则位置 | 流程顺序、前置条件、例外处理进服务端依赖表与前置校验；skill 只承载语义与解释；人脑里的口径在提案时以 NeedsInput 向人索取，不猜 | F |
| 6 出口 | 每个状态机都有"做不了"的终态（NeedsInput、Rejected、Expired、Failed with reason）与升级到人的路径；没有卡死状态；预算超限是明确状态 | C |
| 7 可见性 | 每次模型调用与工具调用留持久事件（输入摘要、返回摘要、决策、耗时、错误分类）；按步骤统计完成率漏斗；日志无凭证 | D |

"从 30% 往上走"的六条做法（确定性骨架加 LLM 节点、完成定义外部可核验、工具错误分类、写操作幂等键、失败案例回流评估集、按步骤看完成率）分别对应上表第 5、4、2、3、1、7 行，全部包含在计划 1、2、6 中。

## 生产级校验的定义（验收门）

"扛住生产级校验"以下列十道门为准。每道门都有可重复执行的验证方式，全部由审计方独立重跑，执行方自报不算通过。

| 门 | 判据 | 验证方式 |
|---|---|---|
| G1 白盒部署 | 一台干净的 Linux x86_64 主机，只给仓库 tag、镜像仓库地址与密钥文件，按文档 60 分钟内拉起 platform + 一个租户站 + worker，全部 healthcheck 绿 | 审计方在新 VM 上按 runbook 执行 |
| G2 升级与回滚 | 对含真实形状数据的租户站执行 `bench migrate` 升级到新 tag，再回滚到旧 tag，业务单据与 DS-* 记录逐字段一致 | 自动化演练脚本，输出比对报告 |
| G3 容灾 | 从异地备份在另一台主机恢复全部站点，RPO ≤ 24h、RTO ≤ 8h；`site_config` 密钥与库转储分离存放 | 季度演练脚本 + 首次由审计方执行 |
| G4 安全边界 | Agent 容器只能到达 provider 与业务站；非 root；控制面文件不在租户容器内；全链路 TLS；guest 端点有速率限制与审计日志；SSO 强制 | 集成测试断言 + 外部安全审查清单 |
| G5 并发与容量 | 两站同时运行不互相阻塞；单站 3 用户同时提问时按序执行、不丢不重、排队状态可见；100 轮历史会话下 `run_status` P95 < 1s；取消 5s 内落地 | 负载脚本 + 混沌用例（杀 worker、断 provider、断 docker） |
| G6 可观测 | 每次运行有结构化日志与事件流落库；失败运行能回放"模型看到了什么"；provider 失败率、队列深度、运行时长有指标与告警 | 注入故障后告警在 5 分钟内触发 |
| G7 审计不可篡改 | 执行记录、提案、运行不可删改（任何角色，含 Administrator）；DocType Policy 变更有版本；管理员可跨用户查审计 | 集成测试断言 + 权限矩阵表 |
| G8 Agent 质量 | 评估集（来自真实失败运行 + 合成注入用例）通过率达标，注入用例零写入提案；每 run 记录模型/prompt/skill 版本 | 评估脚本在 CI 定期运行 |
| G9 质量门禁 | CI 对每次提交跑 lint、类型、非集成测试、前端测试、dist 一致性；集成与 e2e 每日跑；无已知不稳定项 | CI 配置存在且历史 30 天绿 |
| G10 24 小时浸泡 | 生产形态部署连续运行 24h，跨午夜调度、备份、恢复验证正常，无 OOM、无卡 Running、无孤儿容器 | 复用 v16 C4 冷静期方法 |

## 目标拓扑

```text
Internet ──TLS──▶ 反向代理（Caddy，自动证书，*.tenant.example）
                    ├─▶ platform 站（dsherp-frappe 镜像，含 dsherp_platform）
                    └─▶ 每租户 1 个 Site（dsherp-frappe 镜像，含 dsherp_bridge）
                          MariaDB（独立卷，逐站库）  Redis（cache/queue 分离）
                          scheduler + queue worker（restart: unless-stopped, healthcheck）

宿主 systemd ──▶ dsherp-agent-worker（多站协调器，一进程服务 N 站，每站并发上限，试点 1）
                    └─ docker run 一次性 Runtime 容器（非 root，egress 仅 provider 代理 + 本站 API）
                                └─ 出口代理（allowlist：provider 域名）

备份：每站每日四件套 → 对象存储（加密，site_config 与库转储分桶）；恢复演练脚本
观测：JSON 结构化日志 → 日志收集；/metrics（Prometheus 格式）→ 告警；DS Run Event 落库
```

首个生产形态是**单 Linux 主机 + Docker Compose + 自建镜像**；不引入 Kubernetes、多区域或数据库集群。这些留到租户数超过单机容量时另行设计（见"明确不做"）。

## 工作流 A：部署制品与环境（对应 D1–D9）

**目标**：把"一台 Mac 上手工预置的东西"变成可版本化、可在任意 Linux 主机复现、可升级可回滚的制品。

设计：
- **自建镜像**。`infra/docker/frappe/Dockerfile` 以钉死 digest 的 `frappe/erpnext` 为基底，把 `frappe_app/` 两个 App 安装进镜像（`bench get-app` 本地路径 + `pip install`），前端 dist 随 App 进镜像；`infra/docker/worker/Dockerfile` 打包宿主 worker（Python 3.12 + `requirements.lock`），Runtime 卷改为镜像层。镜像 tag = git tag；compose 只引用 tag，`pull_policy` 改为 `if_not_present`。bind-mount 仅保留在 `compose.dev.yml`。
- **环境分层**。`infra/compose.base.yml` + `compose.dev.yml` / `compose.prod.yml` 覆盖；域名、端口、站点名、网络名、资源限制全部来自 `.env.<env>`，仓库提供 `.env.example`。`runtime_host.py` 的 IMAGE 与网络名从环境读取，指纹机制改为对"渲染后的 compose"计算。
- **TLS 与域名**。Caddy 作为唯一入口，`{tenant}.{base_domain}` 通配证书；nginx 模板改为只做站点路由；`DSHERP_*_ORIGIN` 全部由 base_domain 派生。OAuth redirect_uri、`dsherp_business_sites` 白名单同步派生。
- **进程守护**。worker 提供 `infra/systemd/dsherp-agent-worker.service`（Restart=always、`WatchdogSec`、`ProtectSystem=strict`）；LaunchAgent 降级为 dev 可选；契约测试改为对两种 unit 的渲染器断言。
- **开站 CLI**。`dsherp-admin provision-tenant <slug>` 单入口，内部为幂等步骤链（建站 → 装 App → OAuth Client → 运行服务身份 → membership 绑定 → healthcheck），每步先查现状再执行，可断点续跑；`dsherp-admin retire-tenant` 先归档后删站。`.runtime/control/*` 密码由 CLI 生成并写入 secrets 目录。
- **升级与回滚**。发布 = 新 tag 镜像 + `bench migrate` 逐站执行 + 前后快照比对；回滚 = 旧 tag 镜像 + 从升级前自动备份恢复。`patches.txt` 随 DocType 变更强制新增（CI 检查：DocType JSON 变更必须伴随 patch 或显式 `no-patch` 标注）。
- **供应链**。CI 生成 SBOM（syft）并跑 CVE 扫描（grype）；npm registry 改回官方并锁 `engines`/`.nvmrc`；`.python-version` 钉宿主 3.12；镜像 digest 记录时同时记录架构 manifest。DSH SDK 预发布版本保留，但在 runtime-baseline 明示"预发布"并把升级列为独立评估项。

不变量：任何环境的运行都来自镜像 tag，不来自工作副本；开站不需要人工 `docker exec`；升级前自动备份是硬前置。

验证：G1、G2 在干净 VM 上执行；`tests/test_v16_deployment_contract.py` 改名为部署契约测试并覆盖 prod 渲染结果。

## 工作流 B：安全与租户边界（对应 S1–S9、A2）

**目标**：把"约定"变成代码级边界，通过外部安全审查清单。

设计：
- **出口控制（S1）**。Agent 容器改接 `internal: true` 的 `agent` 网络，网络内只有一个出口代理（tinyproxy/squid，allowlist = provider 域名）和业务站 API；`DEEPSEEK_BASE_URL` 指向代理。集成测试断言容器访问任意外网地址失败。
- **非 root 与最小挂载（S7）**。容器 `--user` 改为固定非 root uid，Runtime 卷与 session 目录 chown 对齐；`compose.validation.yml`、`prepare_agent_runtime.sh` 不再挂入容器，指纹改为宿主计算后随 `run.json` 传入。
- **凭证托管（S2、S4、S9）**。平台不再长期保存成员的业务站 api_key/api_secret：改为平台只保存绑定关系，业务站在 SSO 回调时为该用户签发短期 API key 对（Frappe 原生 `generate_keys`），到期由 SSO 续签；`platform_grant` 从 DS Model Run 行移除，只保留在 session。OAuth client secret、worker 服务凭证、provider key 统一由 `dsherp-admin rotate <kind>` 轮换，带版本号与生效时间，轮换不作废原生会话（A6 的 `runtime_revision` 改为不含密钥）。
- **SSO 强制（S3）**。业务站 `validate_session` 对无 grant 的 System User 拒绝（Administrator 与运行服务身份除外，且二者禁止 Desk 登录）；密码登录仅在 dev 环境开启。
- **平台依赖解耦（S8）**。grant 校验结果按 `(user, binding_version, enterprise_version)` 缓存 60s，缓存期内平台不可达仍放行；超过 60s 且平台不可达则拒绝（fail-closed）。平台侧撤销时向业务站推送失效通知（whitelisted 内部端点，HMAC 签名），使撤销延迟 ≤ 60s。
- **guest 端点加固（S5、A5）**。`claim_run`/`run_status`/`run_tool`/`reserve_model_call`/`finish_run` 改为只接受来自 agent 网络的源地址（nginx 层）+ 每 run 每秒速率限制；`receipt_transfer` 的 actor 必须与信封签名者一致且加 nonce；所有 guest 端点写审计日志（见工作流 D）。
- **提示注入的结构化防护（S6、A2）**。工具结果与页面快照统一包在带来源标签的信封中（`{"source":"erp","doctype":..,"untrusted":true,"data":..}`），system prompt 声明信封规则；前端 Markdown 渲染禁用 `img`、`a` 改为纯文本 + 域名白名单，Desk 响应头加 CSP（`img-src 'self'`、`connect-src 'self'`）；评估集含注入用例（见工作流 F）。

不变量：控制面凭证与拓扑不进入租户容器；业务站上的任何 System User 会话都可追溯到一次平台 SSO；模型可见的一切外部数据都带 untrusted 标签。

验证：G4 集成测试矩阵（出网拒绝、非 root、无 grant 拒绝、速率限制、CSP 头）；外部安全审查按本节逐条核。

## 工作流 C：运行底座可靠性（对应 R1–R9、A1、七维 #2 #3 #4 #6）

**目标**：在真实并发与真实 provider 故障下不串行、不崩溃、不丢答案、不卡死。

设计：
- **多站协调器（R3、D2）**。worker 改为一进程服务 N 站：profile 变为站列表，每站独立凭证与 claim 循环，容器并发上限 = 全局槽位（试点默认 3）；`claim_run` 的闸门从"全站单运行"改为"每用户 1 在飞 + 每站并发上限 N（试点默认 1，服务端可配置）"；轮询间隔改为长轮询或事件触发（`send_message` 后 Redis 发布通知，worker 订阅，3s 轮询作兜底）。
- **崩溃安全的主循环（R2、R9）**。`run_once` 的 finish_run 进入 try；主循环捕获一切异常并记录结构化错误后继续；`finish_run` 对 Cancelling 状态的 Succeeded 改为落 `Cancelled` 并保留答案而非 403；`post()` 对非 JSON 响应返回明确错误类型。
- **超时链与租约（R4、R5、R6）**。定义单一预算表：模型单请求 ≤ 90s，run 总时长 ≤ 600s（operation）/300s（query），容器 timeout = run 总时长 + 30s，`expires_at` 由容器每 30s 续租（`renew_lease` 端点），过期即视为 worker 死亡；取消改为向容器发 SIGTERM，容器内以 `cancel()` + 强制 `shutdown(wait=False)` 在 5s 内退出并回写 Cancelled。
- **状态查询廉价化（R1、R8）**。`run_status` 只读 run 行（无 FOR UPDATE、无 `_public`、无 `require_revision`）；权限修订检查移到 `run_tool` 与 `finish_run`（本就存在）；`_public` 的 `authorize_sources` 改为按 run 缓存授权结果并只在 sources 变化时重算。
- **队列与背压（R7）**。Queued 加 `queue_expires_at`（默认 10 分钟），过期自动 Failed 并提示"系统繁忙"；worker 不可用（无 claim 心跳 > 60s）时 `send_message` 返回 503 与明确文案，会话不因 `active_run` 锁死（Queued 可由用户撤回）。
- **错误三分类与透传（七维 #2）**。工具错误分为 `validation`（业务校验，模型可修正）、`permission`（模型不得重试，须告知用户）、`transient`（可重试一次）；`context_mcp.post` 把服务端 `exception` 文本与分类一起返回给模型；服务端错误落 DS Run Event。
- **provider 熔断与探针（A1）**。worker 维护 provider 健康状态：连续 3 次 5xx/超时进入熔断 60s，熔断期不 claim；`dsh_probe` 改为每 5 分钟由 worker 执行的轻量探针，失败即告警；模型名从 `.env` 改为服务端配置（`dsherp_model_policy` conf），`reserve_model_call` 校验来自同一配置。不做模型降级（避免答案质量不可控），只做熔断 + 排队 + 告警。
- **完成判定与出口（七维 #4 #6）**。运行状态加 `NeedsInput`（模型明确需要用户补充时经 `erp_request_input` 工具落地，不再靠 417 拒绝零读取）；提案状态加 `Rejected`（用户拒绝按钮）与 `Expired`（scheduler 每 10 分钟清理）；成功判定除 `finish_reason` 外要求回答引用的 sources 与执行记录交叉一致。

不变量：任何时刻一个 run 只有一个执行者且有活租约；worker 进程不会因单次运行的任何结果退出；用户可见状态与 ERP 事实不矛盾。

验证：G5 负载与混沌用例；`tests/test_context_worker.py` 扩展崩溃安全负例；集成测试覆盖续租、过期、取消 5s。

## 工作流 D：可观测性与运维（对应七维 #7、S5、T6）

**目标**：从"零 logging"到能回答"谁在何时用哪条凭据做了什么、失败时模型看到了什么"。

设计：
- **结构化日志**。宿主 worker 与容器 runner 统一 JSON 行日志（`run_id`、`site`、`user`、`event`、`duration_ms`、`error_class`），密钥字段永不进日志（现有"无值诊断帧"机制保留）；Frappe 侧用 `frappe.logger('dsherp')` 输出到 `logs/dsherp.log` 并轮转。
- **事件流落库**。新 DocType `DS Run Event`（run、seq、kind、payload、created）：记录 claim、每次模型调用（含 provider request id、实际 token）、每次工具调用（参数摘要 + 结果摘要 + 错误分类）、续租、取消、finish；`RunResult.events` 不再丢弃。转录页从事件流渲染，失败可回放。
- **指标与告警**。worker 暴露 `/metrics`（队列深度、claim 延迟、运行时长分布、provider 失败率、熔断状态、容器槽位占用）；Frappe 侧 scheduler 每 5 分钟汇总卡 Running、过期提案、备份成功率。告警规则首批：provider 熔断、队列深度 > N、备份失败、worker 心跳丢失、孤儿容器 > 0。
- **管理员审计视图（T6）**。System Manager 可按用户/时间/DocType 查执行记录与提案（原生报表，只读），不受会话可见性漂移影响。
- **guest 端点审计（S5）**。每次 capability 使用记录到 DS Run Event，含源地址与结果。

不变量：每次模型调用与工具调用都有一条持久事件；日志中不出现任何凭证值。

验证：G6 注入故障后 5 分钟内告警；随机抽一条失败运行能从事件流复盘到工具级。

## 工作流 E：数据治理与容灾（对应 T1–T8、D4、T2）

**目标**：数据不会因单机损毁而丢失，schema 可演进，审计不可篡改，租户数据可导出可删除。

设计：
- **备份全站化与异地化（T1、T7）**。scheduler 每日对 platform 与全部租户站执行四件套备份（原生 `scheduled_backup`，强制未压缩以维持验证不变量），`dsherp-admin backup-sync` 把备份加密后推到对象存储（restic 或 rclone + age），`site_config_backup.json` 单独进密钥桶，与库转储永不同目录同权限；保留策略 7 日 + 4 周 + 3 月；RPO 24h、RTO 4h 写入 runbook。恢复演练脚本泛化 `verify_daily_backup.py`：接受任意站名与从对象存储拉取，不再硬编码合成夹具。
- **schema 演进（D4）**。两个 App 建立 `patches.txt`；CI 规则：DocType JSON 变更必须伴随 patch 或 `no-patch:` 说明；每个 JSON payload 字段的 `schema_version` 变更必须附回填 patch；迁移测试在 CI 中对"上一 tag 的备份"执行 `bench migrate` 并做逐字段比对（即 G2 的自动化形态）。
- **审计不可篡改（T2）**。DS Model Run、DS Operation Proposal、DS Execution Record、DS Configuration * 全部加 `on_trash` 守卫（无条件拒绝，含 Administrator）、`track_changes: 1`；DS Doctype Policy 加 `track_changes` 与变更原因字段。租户下线时整站归档而非删记录。
- **业务单据关联（T3）**。执行记录增加 `Dynamic Link`（target_doctype/target_name）指向产生的单据，单据侧通过原生 Connections 反查；单据取消/删除时执行记录保留并标注。
- **会话存储纳管（T4）**。`.runtime/business-sessions/*` 改为按 `(site, user, conversation)` 的可枚举目录，加密静态存储（宿主级磁盘加密作为最低要求），会话归档 90 天后清理；转录关键内容已在 DS Run Event 落库，原生会话目录不再是唯一真相。
- **保留、导出与删除（T4）**。`dsherp-admin export-user-data <site> <user>` 与 `delete-user-data`（删除会话、运行、事件与原生会话目录，保留执行记录与提案的脱敏审计副本）。
- **用量计量（T5）**。DS Model Run 增加 `model`、`provider_request_ids`、`actual_input_tokens`、`actual_output_tokens`、`duration_ms`、`skill_versions`；每租户每月用量由平台聚合报表展示。
- **读放大与时区（T8）**。为 conversation、status、expires_at 加索引；`_summaries` 改为聚合查询；时间统一 UTC 存储。

不变量：任一站点在任一时刻都有一份 24h 内的异地备份可恢复；审计类记录只增不删；DocType 变更必有迁移路径。

验证：G3 演练；G7 权限矩阵断言删除被拒；G2 迁移比对。

## 工作流 F：Agent 质量与成本（对应七维 #1 #5、A3–A7、S6）

**目标**：Agent 的失败可度量、可回归，成本可控可归因。

设计：
- **评估集（七维 #1）**。从已导入的 v15 历史与后续真实失败运行导出 `evals/cases/*.json`（问题、页面快照、期望工具序列或期望拒绝、期望提案摘要），首批 ≥ 30 条，含注入用例 ≥ 5 条（ERP 字段中藏指令，期望零提案）；`evals/run.py` 用本地模型替身回放工具链做结构断言，用真实模型（授权后）做语义断言；通过率写入 CI 报表，回归即红。
- **skill 强制装载（A3）**。系统提示直接内嵌当前 Profile 的 skill 摘要（沿用 sha256 校验），`skill` 工具仅用于取全文；装载失败 fastfail。
- **工具输出治理（A4、A7）**。所有读工具返回统一上限（默认 16KB）与游标分页；`erp_read_record` 默认只返回 permitted 且非空字段，子表按需展开；工具描述由服务端限制自动生成，与实现同源；`_forbid_extra_tool_arguments` 覆盖全部工具。
- **预算与额度（A5、七维预算常量）**。三层预算：单 run（现有常量转为服务端配置并裁定正式值）、单用户每日调用数、租户每月 token；超额返回明确文案；循环检测：同一工具同参数连续 3 次即中止并提示。
- **业务前置校验（七维 #4）**。提案生成时服务端校验 Link 存在、BOM 有效、仓库合法、库存可用，失败按 `validation` 分类回给模型修正，不进入待确认。
- **流程骨架进代码（七维 #5）**。制造链顺序与依赖从 SKILL.md 移到 `make_adapters` 依赖表（前置单据状态、必需字段），服务端在提案时校验前置，skill 只描述业务语义。
- **可复现性（A6）**。每 run 记录模型名、温度、prompt 模板版本、skill 版本清单、runtime 文件指纹（不含密钥）；`runtime_revision` 拆为"配置指纹"（进 run）与"密钥版本"（只进 worker）。

不变量：注入用例永远零提案；每次运行可由记录的版本组合复现同一装配；预算超限是明确状态而不是失败。

验证：G8 评估通过率；`tests/test_model_guard.py` 扩展 skill 强制装载与循环检测。

## 工作流 G：质量门禁（对应 Q2–Q4、Q6、D8）

设计：
- **CI**。GitHub Actions：每次 push 跑 ruff + mypy（宿主包）+ 非集成 pytest + vitest + dist 一致性（重建后 `git diff --exit-code`）+ SBOM/CVE；每日跑集成与 e2e（在 CI 内用 compose 拉起 v16 站）；`pytest.ini` 定义 `integration`/`e2e` marker 与全局超时，`pytest tests` 默认排除 integration。
- **Frappe 侧离线测试（Q2）**。bridge 与 platform 的业务逻辑测试改用 Frappe 原生 `bench run-tests --app dsherp_bridge` 在容器内执行（`FrappeTestCase`，事务回滚），替代 `docker exec -i python -` 注入脚本；纯函数部分（白名单、diff、摘要）抽到可宿主导入的模块并直接单测。
- **集成清理与稳定性（Q3）**。所有集成夹具改为"先登记后创建"，清理由 session 级 finalizer 按登记表执行，不依赖被超时杀掉的脚本；`test_policy_seed` 超时根因排查后加 marker 或拆分，禁止"未复现即结案"。
- **浏览器 e2e（Q4）**。Playwright 覆盖登录链、侧栏挂载、发送/取消、提案确认、417 文案可见、ErrorBoundary 降级五条路径；截图作为证据自动落档。
- **权限矩阵**。`tests/permission_matrix.yml` 列出角色 × DocType × 动作期望，测试由矩阵生成。

## 工作流 H：前端健壮性（对应 Q1、Q5、Q6、A2）

设计：
- 统一 `apiFetch` 错误解析：所有非 2xx 若为 JSON 则提取 `exception`/`_server_messages` 并展示服务端原因，417 归为"业务校验"而非网络错误；错误对象携带分类供 UI 决定是否允许重试。
- 侧栏与工作台各加 ErrorBoundary，渲染异常显示"助手暂不可用 + 重载"而非卸载；轮询错误进入退避重试（5s→30s），不清空已加载会话。
- `crypto.randomUUID` 加回退；Markdown 组件禁用 img、链接纯文本化（见工作流 B）；长列表虚拟化在 200 条以上启用。

## 实施顺序与分项计划

八条工作流拆成六个实施计划，每个计划有自己的检查点与放行标准（沿用 Claude 架构+审计、Codex 执行的模式）。顺序按"先让失败可见、再让底座不倒、再让环境可复现、最后收口数据与 Agent 质量"排列；P1 与 P2 项默认跟随所属计划，不单独排期。

| 序 | 计划 | 覆盖 | 关键放行门 | 依赖 |
|---|---|---|---|---|
| 1 | 可观测与失败回放 | 工作流 D 全部；F 的评估集导出（只导出，不做断言） | G6；历史失败运行全部导出为评估用例，新失败运行可从事件流回放到工具级（计划：[2026-09-03-observability-replay](../plans/2026-09-03-observability-replay.md)） | 无。先做的理由：后续所有计划的验证都依赖事件流与日志 |
| 2 | 运行底座可靠性 | 工作流 C 全部；H 的错误透传与 ErrorBoundary | G5；崩溃安全负例全绿 | 1（用事件流验证） |
| 3 | 部署制品与安全边界 | 工作流 A 全部；B 的出口控制、非 root、SSO 强制、guest 加固、CSP | G1、G4 | 2（worker 多站形态确定后再打包） |
| 4 | 数据治理与容灾 | 工作流 E 全部；B 的凭证托管与轮换 | G2、G3、G7 | 3（对象存储与 CLI 属部署制品） |
| 5 | 质量门禁 | 工作流 G 全部；H 剩余项 | G9 | 3（CI 需镜像与 compose.prod） |
| 6 | Agent 质量与成本 | 工作流 F 全部；B 的注入信封 | G8 | 5（串行执行，见已裁决 #7） |
| 终 | 生产浸泡终验 | 全部 | G10 + 十道门复审 | 1–6 |

计划 1 与 2 完成即解除本轮审计的 R1–R3、A1、Q1 与七维审计的全部五项；计划 3 与 4 解除 D1–D4、S1–S2、T1–T2。终验通过前，任何环境都不接入真实租户。

## 明确不做（本轮）

- Kubernetes、多主机编排、多区域、MariaDB 集群或读写分离；单机容量用尽时另立 spec。
- 模型降级或多 provider 路由；只做熔断、排队与告警。
- 完整计费平台；只做用量计量与额度。
- 替换 DSH SDK 预发布版本；只标注风险并把升级列为独立评估。
- 通用工作流引擎、连接器市场（沿用首版设计"首版不做"）。

## 已裁决（2026-09-03，用户决定）

| # | 裁决点 | 决定 | 对本文的影响 |
|---|---|---|---|
| 1 | 生产主机形态 | 单台 Linux x86_64 + Docker Compose + 自建镜像 | 工作流 A 按现文执行；Kubernetes 与多机不在本轮 |
| 2 | 首批规模与 SLO | ≤3 个租户站试点，RPO 24h / RTO 8h，单站并发 1 | G3 RTO 改为 8h；G5 改为"单站按序不丢不重 + 两站互不阻塞"；工作流 C 试点默认每站 1、全局 3 槽位，均为服务端配置，升档不改代码 |
| 3 | 审计不可篡改 | 对外承诺：任何角色（含 Administrator）不可删改 | T2 定为 P0；工作流 E 的 on_trash 无条件拒绝与 track_changes 按现文执行；G7 按此验收 |
| 4 | 凭证托管 | 业务站签发短期 API key，平台不长期保存 | 工作流 B 凭证部分按现文执行；不引入外部密钥库 |
| 5 | 注入防护深度 | 信封标签 + 渲染限制 + 评估用例 | 工作流 B/F 按现文执行；不做独立输出复检调用，写入仍由 HITL 兜底 |
| 6 | 预算常量正式值 | 由计划 6 用评估集数据裁定；现值（10 次 / 30720 token / 120s）作为临时默认并转为服务端配置 | 工作流 F 预算条按现文执行；计划 2 只做常量配置化，不改数值 |
| 7 | 执行模式 | Claude 架构+审计、Codex 执行，六个计划串行 | 实施顺序表按 1→2→3→4→5→6→终验串行；计划 6 不与 4、5 并行 |

## 与既有文档关系

- 执行底座与 HITL 不变量：以 2026-08-29 上下文 Agent 计划及其证据为准，本 spec 全部保持有效，清单见审计文档"已核验为生产级、设计中保持不动的底座"节。
- [Agent 体系架构设计](2026-08-31-agent-system-architecture-design.md)的产品层（Profile、分诊、治理）排在本 spec 计划 2 之后启动；其 Profile 的 skills 多装载与本 spec 工作流 F 的 skill 强制装载合并实现。
- [首版设计](2026-08-28-dsherp-design.md)的多租户拓扑（平台站 + 每企业一站）与权限表不变；本 spec 工作流 B 把其中"每次业务请求校验成员关系"的口径具体化为 60s 缓存 + 推送撤销。
- [v16 迁移计划](../plans/2026-09-01-v16-migration.md)的 C4 冷静期方法复用为 G10；其"冷静期不覆盖周/月调度"边界由计划 1 的告警与终验的观察期补齐。
- 七维审计（2026-09-02，原只存于会话记忆）自本文起以[生产就绪第二轮审计](../../engineering/production-readiness-audit-2026-09-03.md)"与七维审计的关系"节为仓库内记录。
