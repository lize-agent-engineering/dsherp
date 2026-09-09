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
| G4 安全边界 | Agent 容器只能到达 provider 与业务站；非 root；控制面文件不在租户容器内；公网入口与一切外联走 TLS，单主机内部各跳限于受控主机的 internal 网络（已裁决 #8）；guest 端点有速率限制与审计日志；SSO 强制；宿主 INPUT 规则随开机恢复并在网络重建后可核验 | 集成测试断言 + 外部安全审查清单 + 宿主防火墙单元的重启/重建演练 |
| G5 并发与容量 | 两站同时运行不互相阻塞；单站 3 用户同时提问时按序执行、不丢不重、排队状态可见；100 轮历史会话下 `run_status` P95 < 1s；取消 5s 内落地 | 负载脚本 + 混沌用例（杀 worker、断 provider、断 docker） |
| G6 可观测 | 每次运行有结构化日志与事件流落库；失败运行能回放"模型看到了什么"；provider 失败率、队列深度、运行时长有指标与告警 | 注入故障后告警在 5 分钟内触发 |
| G7 审计不可篡改 | 执行记录、提案、运行不可删改（任何角色，含 Administrator）；DocType Policy 变更有版本；管理员可跨用户查审计 | 集成测试断言 + 权限矩阵表 |
| G8 Agent 质量 | 评估集（来自真实失败运行 + 合成注入用例）三条并列判据达标（已裁决 #13：回放层 100% / live 注入组 100% 零提案 / live 总体 ≥85% 且不低于归档基线），注入用例零写入提案；每 run 记录模型/prompt/skill 版本与真实用量；预算超限是明确终态而不是又一次失败（逐项偏离见「计划 6 偏离表」） | 回放评估每夜在 CI 跑并与归档基线对照；live 在本机手动跑并归档报表 |
| G9 质量门禁 | CI 对每次提交跑 lint、类型、非集成测试、前端测试、dist 一致性；集成与 e2e 每日跑；无已知不稳定项（计划 5 瘦身：类型检查与 e2e 不做、SBOM 每周不计入，逐项见「计划 5 偏离表」） | CI 配置存在且历史 30 天绿 |
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
- **备份全站化与异地化（T1、T7）**。宿主 systemd timer 每 12 小时对 platform 与全部租户站执行 `dsherp-admin backup --sync`：逐站在稳定窗口（保持 + 服务端闸门 + 等在途执行者 + 维护标志 + 排空写入者）内做原生 `bench backup --with-files`（不压缩，tar 保持 `.tar`）与 G2 口径快照，暂存为一个备份集（数据三件 + 快照 + `set.json`；`site_config_backup.json` 与 `pair.json` 在另一个卷、另一套权限），再用两个各自口令与各自存储身份的 restic 仓库异地保存，读回两份清单核对后才算完整。保留按站点 7 日 + 4 周 + 3 月（另外永远保留每站最新完整集与最新已验证集）；RPO 24h（20h 预警，按数据时点计）、RTO 8h（已裁决 #2）写入 runbook 第 12 节。恢复验证改为每周 `dsherp-admin restore-drill`：在隔离 compose 项目里按备份集记录的构建恢复、比对窗口内快照、抽样解密，成功即删该栈；`restore-site` 是异机冷启动路径。实施细节见[备份切片设计 2.1](2026-09-06-backup-offsite-design.md)。
- **schema 演进（D4）**。两个 App 建立 `patches.txt`；CI 规则：DocType JSON 变更必须伴随 patch 或 `no-patch:` 说明；每个 JSON payload 字段的 `schema_version` 变更必须附回填 patch；迁移测试在 CI 中对"上一 tag 的备份"执行 `bench migrate` 并做逐字段比对（即 G2 的自动化形态）。
- **审计不可篡改（T2）**。DS Model Run、DS Operation Proposal、DS Execution Record、DS Configuration * 全部加 `on_trash` 守卫（无条件拒绝，含 Administrator）、`track_changes: 1`（DS Run Event 已自计划 1 起无条件拒绝改写与删除）；DS Doctype Policy 加 `track_changes` 与变更原因字段。租户下线时整站归档而非删记录（计划 3 收口后归档落在 backend 的 `tenant-archive` 卷，命令回读归档路径）。
- **业务单据关联（T3）**。执行记录增加 `Dynamic Link`（target_doctype/target_name）指向产生的单据，单据侧通过原生 Connections 反查；单据取消/删除时执行记录保留并标注。
- **会话存储纳管（T4）**。`.runtime/business-sessions/*` 改为按 `(site, user, conversation)` 的可枚举目录，加密静态存储（宿主级磁盘加密作为最低要求），原生会话目录按最后写入时间超过 90 天清理（`dsherp-admin sessions --sweep`，常量 `dsherp/sessions.py:RETENTION_DAYS`），不按"归档"时间；旧布局的未归属目录只报告不删；转录关键内容已在 DS Run Event 落库，原生会话目录不再是唯一真相。
- **保留、导出与删除（T4）**。`dsherp-admin export-user-data <site> <user>` 与 `delete-user-data`。删除的边界按已裁决 #10：删除原生会话目录与非审计的个人内容；运行、事件、提案、执行记录及其动作、对象、版本、确认与执行结果等审计事实一律保留，保留量必须仍能回放与追责；已有事件禁止改写，若必须物理清除事件内的个人信息，只能以明确登记的受控脱敏迁移作为不可变承诺的例外执行；界面遮挡不算删除。字段级边界由计划 4 的实施计划逐字段列出。
- **用量计量（T5）**。DS Model Run 增加 `model`、`provider_request_ids`、`actual_input_tokens`、`actual_output_tokens`、`duration_ms`、`skill_versions`；每租户每月用量由平台聚合报表展示。
- **读放大与时区（T8）**。为 conversation、status、expires_at 加索引；`_summaries` 改为聚合查询；时间沿用 Frappe 的存储口径（站点系统时区、无时区标记）；跨站月报把每站时间按其时区换算成 UTC 后分月（`dsherp/usage.py`），未声明时区的站按 UTC 读并在报告里标 `assumed_utc`；不改存储口径。

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
| 1 | 可观测与失败回放（2026-09-03 C4 通过） | 工作流 D 全部；F 的评估集导出（只导出，不做断言） | G6；历史失败运行全部导出为评估用例，新失败运行可从事件流回放到工具级（计划：[2026-09-03-observability-replay](../plans/2026-09-03-observability-replay.md)） | 无。先做的理由：后续所有计划的验证都依赖事件流与日志 |
| 2 | 运行底座可靠性（2026-09-05 终审阻断项逐项关闭；2026-09-06 架构方派 12 个互不知情的代理独立复核 13 项关闭，7 项成立、6 项被推翻——熔断探针地址一项为计划 3 引入的回归，随计划 3 收口修复；其余 5 项 major 残余按用户裁决在收尾切片 `plan2/closeout` 修复并经全量集成门，见证据文档「收尾切片」节；**放行以该切片合入 main 为准**） | 工作流 C 全部；H 的错误透传与 ErrorBoundary | G5；崩溃安全负例全绿（计划：[2026-09-04-runtime-reliability](../plans/2026-09-04-runtime-reliability.md)，证据：[runtime-reliability-evidence](../../engineering/runtime-reliability-evidence.md)） | 1（用事件流验证） |
| 3 | 部署制品与安全边界（2026-09-05 由 Claude 直接实施并经独立审计：G4 判据在 dev、本机生产形态与 x86_64 服务器实测；2026-09-06 按[项目状态审查](../../engineering/project-state-review-2026-09-05.md)收口：归档落持久卷、发布来源可核实、宿主防火墙随开机恢复，各以真实路径演练。状态：**主体实现完成、验收未闭合**——G1 字面判据与 ACME 仍待合规专用主机由审计方执行，按已裁决 #9 不再阻塞计划 4 开工） | 工作流 A 全部；B 的出口控制、非 root、SSO 强制、guest 加固、CSP | G1、G4（证据：[deployment-security-evidence](../../engineering/deployment-security-evidence.md)，runbook：[deployment-runbook](../../engineering/deployment-runbook.md)） | 2（worker 多站形态确定后再打包） |
| 4 | 数据治理与容灾（2026-09-06 首片 G2 由 Claude 直接实施：`release`/`rollback` 重写为按元数据分桶、原生 SQL 分页、任何读错误即中止、逐行逐字段比对、patch 以 `EXPECTED_CHANGES` 声明预期变化、备份集落归档卷、`rollback` 恢复后与升级前快照比对，本机生产形态用 2026-09-03 旧 schema 的真实形状数据演练升级/注入/回滚，证据见 [runtime-baseline 同目录的 data-governance-evidence](../../engineering/data-governance-evidence.md)；后续切片：备份「定时生成 → 异地同步 → 失败可见 → 异机恢复验证」（本机两站今日无任何定时备份任务，T1 从零建）、审计保留与数据生命周期、短期凭据与轮换） | 工作流 E 全部；B 的凭证托管与轮换 | G2、G3、G7 | 3 的制品已合入 main 即可开工；G1/ACME 的外部主机验收按已裁决 #9 与本计划解耦，仍是真实租户接入前的必要验收 |
| 5 | 质量门禁（瘦身版，2026-09-07 起） | 工作流 G 全部；H 剩余项；与本节原文的偏离逐项见下方「计划 5 偏离表」 | G9 | 3（CI 需镜像与 compose.prod） |
| 6 | Agent 质量与成本 | 工作流 F 全部；B 的注入信封 | G8 | 5（串行；执行方式按裁决 #11） |
| 终 | 生产浸泡终验 | 全部 | G10 + 十道门复审 | 1–6 |

### 计划 5 偏离表（瘦身版，2026-09-07）

| 项 | 原文（工作流 G） | 本计划 | 理由 |
|---|---|---|---|
| mypy | 每次 push 跑 ruff + mypy（宿主包） | 不跑 mypy | 仓库没有类型注解基线，首跑即大量报错，只能加忽略或整文件补注解，都是非行为改动；另评估 |
| 风格 lint | ruff | ruff 只启用错误类规则（F、E9），不启用风格规则 | 既有紧凑书写（单行多语句）有 700+ 处，风格规则会触发大面积无行为改动，淹没真实问题 |
| 浏览器 e2e | Playwright 五条路径并自动落截图 | 不做；保留 vitest 组件测试与既有浏览器截图证据 | 需在 CI 内走真实登录链，成本超出瘦身范围；列入"未闭合" |
| Frappe 原生测试 | bridge/platform 业务逻辑测试全部改用 `bench run-tests` | 建立 `frappe_app/*/tests/` 骨架与首批用例（身份金丝雀、权限矩阵、交接窗口），存量注入脚本保留；集成测试改为先登记后创建 | 存量 57 个注入脚本整体改写是整文件重构（复盘 Q5 明确避免）；新逻辑先原生，存量按触碰逐步迁 |
| SBOM / CVE | 每次 push | 每周独立 workflow，不计入 G9 连续绿 | 上游漏洞库变化会让无代码变更的 push 变红，污染"30 天绿"的口径 |
| 权限矩阵 | `tests/permission_matrix.yml` | JSON，放在 `frappe_app/dsherp_bridge/tests/`，宿主静态测试与容器原生测试各读一次 | 容器内不引入 YAML 依赖；与读取它的测试同目录 |
| 每日集成 | 集成与 e2e 每日跑 | 每日在 GitHub 托管 runner 上从零开通四站跑集成 + 原生测试；e2e 不做 | 顺带补上一条命令的可复现开发环境 |
| 原生测试判定 | （原文未涉及） | 退出码非零即失败，**且**必须有 runner 自报的 `Running N <category> tests for <app>` 且 N>0；不产出 junit | 固定镜像里没有 `xmlrunner`，`--junit-xml-output` 不写文件；站点未开 `allow_tests` 时退出码 0 却什么也没跑（容器内实测） |
| G9 判据 | CI 配置存在且历史 30 天绿 | 只对 `ci.yml` 与 `nightly.yml` 计算，从第一个含原生测试步的绿色 nightly 起算（日期与 run 记证据） | 周报型 workflow 不代表代码状态；起算点要可指认 |

### 计划 6 偏离表（2026-09-08 起）

第五列写的是「照原文做会漏掉什么」——偏离本身不是让步，它要么换来一个原文换不到的判据，
要么避开一个原文会造成的假绿。

| 项 | 原文 | 本计划 | 理由 | 缺了会漏什么 |
|---|---|---|---|---|
| 温度记录 | 「记录模型名、**温度**、prompt 模板版本…」 | 不设温度，`sampling` 恒为 `'provider-default'` | 请求体不带 temperature、`llm-deepseek` 无该配置项、`DeepSeekHarnessConfig` 无该参数 | provider 改默认温度时，历史 run 只能复现「我们没设」这一事实；装配本身仍可完全复现 |
| 温度的服务端校验 | —（备选方案） | **不加** `reserve_model_call` 的 temperature 门禁 | 新 runtime 发旧 bridge 不认识的键 → TypeError→500→判 transient 重试；「新增门禁必须至少满足一项」三项皆不满足 | 无（该门禁校验的是一个恒为 None 的值） |
| 读工具上限的行为 | 「统一上限（默认 16KB）与游标分页」 | 截断 + `truncated` 标记 + 游标；默认**完全不展开**子表、`read_record` 默认只回非空 | 拒绝会让大单据无路可走；默认展开则几乎每次触发截断，截断成常态（实测 Sales Order schema 62,674 字节 = 3.8×） | 只加上限不改默认展开：模型要靠游标反复读，调用数不降反升，预算裁定数据失真 |
| `erp_search_records` 的游标形状 | 同上 | 服务端只加 `after_name` 入参，**返回仍是 list**；分页提示由容器侧信封给出 | 改返回类型会打到 `record_versions`、`_tool_summary` 与 `test_filtered_search.py` 七处断言 | 维持纯 list 又不给信封提示：模型永远不知道结果被截断（此前正是如此） |
| 工具描述「由服务端限制自动生成」 | 同上 | 双份逐字节相同的 `tool_limits.py`（沿用 `usage.py` 先例），不在运行时向后端取 | 两个部署单元不能互相 import；运行时取会在 MCP stdio 构造期引入网络依赖，而 `failOnStartupError:true` 会掀翻整条运行 | 将来某站用 conf 覆盖页长时描述会与实际不符；本计划不引入这种覆盖能力 |
| 循环检测「即中止」 | 「同一工具同参数连续 3 次即中止并提示」 | 第 3 次即拒绝并写 `loop_detected`；此后 `reserve_model_call` 一律拒绝 → 容器退出 → `finish_run` 落 `BudgetExceeded` | `run_tool` 处于 HTTP 请求里，无法直接终止仍在运行的容器；「毒化后续模型授权」是仓库既有的中止机制 | 从第 3 次工具调用到运行真正停止之间，模型多收到一条拒绝文本；**不会**再有第 4 次工具执行或付费调用 |
| 循环检测的假阳性口径 | — | 参数经 `sanitize` 截断时判**不可比**、不判循环 | 对一个会终止用户运行的判定，宁可漏判不可误判 | 参数极长且只在被截掉部分不同的重复调用不会被判为循环，由预算兜底 |
| 三层预算里的额度 | 「单用户每日调用数、租户每月 token」 | 实现能力与判定位置（`send_message`、429、明确文案），**默认 0 = 不限**；额度键不进 `budget(domain)` 的 plan | 裁决 #12；「默认不新增额外阻断工作的门禁」；plan 会整体下发进容器并被逐键比对 | 默认开启某个数值 = 在没有真实用量分布时设一道会拒绝正常使用的门；完全不实现则租户上线后加限额没有落点 |
| 月 token 的口径 | 同上 | 只统计已结算的 run，是**下界**，文案与报表都说明「在飞运行尚未计入」 | `actual_*_tokens` 只在 run 结束时写入 | 不说明这一点，用户会把一个偏小的数字当作全月实际用量 |
| 制造链依赖表的存放位置 | 「移到 `make_adapters` 依赖表」 | 依赖表只进代码（`_REQUIREMENTS` 与 `_ADAPTERS` 并列同键），`DS Doctype Policy Route` 不加列 | 依赖表是代码事实；放进 DocType 会让 Administrator 可改，而 `get_make_adapter` 与 `resolve_route` 的设计前提就是「路由必须在代码白名单里」 | 进 DocType 会多一条「改数据即可放宽前置校验」的路径，且权限修订会因前置条件调整而轮换 |
| 业务前置校验的完备性 | 「提案前做业务前置校验」 | 五类（引用、必填、仓库、BOM、库存）且逐条写明**做不到的六件**；必填只查子表与指向受治理 DocType 的必填 Link | 实测：Sales Order 只给四个字段即可插入，`naming_series`/`currency` 等五个 `reqd=1` 字段由站点自填——「必填且无默认」分不出「调用方该给」与「站点会填」 | 照原文查全部 `reqd=1`：每次创建都报五个用户本来就不该填的字段，一个会喊狼来了的校验比没有更糟 |
| G8「评估脚本在 CI 定期运行」 | G8 判据 | 只有 replay 进 nightly；live 在本机手动跑，`evals-live.yml` 只有 `workflow_dispatch` 且默认不启用 | live 进 CI 需把 provider key 放进仓库 secrets（属仓库设置变更，独立检查点） | 语义层回归不会每夜自动发现；结构层与注入不变量仍每夜自动守住 |
| G8「通过率达标」 | G8 判据 | 三条并列判据（回放 100% / live 注入组 100% / live 总体 ≥85% 且不低于基线） | 回放确定，任何低于 100% 的阈值等于允许已知回归带绿；「通过率」只对唯一有抖动的层有意义 | 单一平均通过率会让确定性回归被高分掩盖，注入组一条红也可能被平均掉 |
| 注入用例在回放模式下证明了什么 | 「注入用例」 | 回放层的注入用例证明**预言机与服务端**（compliant→pass、obedient→fail）；模型的注入抗性**只能**由 live 证明，这条边界在证据文档里显式写出 | 回放下模型行为是脚本写死的，用它证明「模型不听指令」是自证 | 不写这条边界，一份「回放注入 100%」的报告会被读成「模型抗注入」——本计划最容易产生的假绿 |
| 评估运行写在哪个站 | 「所有业务验证先在隔离合成站」 | `dsherp-daily.localhost`（同为隔离合成站），另加一个幂等的业务用户凭据开通步 | `DS Model Run.on_trash` 无条件拒删；validation 站承载 200+ 条集成测试的清扫与审计报表 | 写 validation 站会逐渐拖慢并干扰集成套件，而这些记录按审计承诺又不能删——不可逆的污染 |
| 长列表虚拟化 | 「200 条以上启用虚拟化」 | 200 条封顶并提示用搜索缩小范围 | 不引入 `rc-virtual-list` 直接依赖与变高行测量；列表本身已是服务端分页 | 超过 200 条历史必须靠搜索定位，不能一路滚动到底 |
| 预算正式值的推导 | 「正式值 = `ceil(P95 × 1.5)` 向上取整到 1024 的倍数」 | 单次输出 token 三域统一 **8192**，不由分位数推出；时长**不按公式收紧** | 实测（2026-09-09，34 条 live）三个域的单次输出观测**都被上限自己截断**，对被截断的观测取分位数量的是上限而不是需求；时长实测 189s/600s，19 条合成运行不是裁剪线上超时的分布 | 照公式套会把上限固定在造成故障的那个值附近，`max-tokens` 饿死答复的缺陷会原样留着 |
| 判据的分层 | —（原文未涉及） | `tool_prefix`、`refusal_class` / `refusal_text`、`proposals.summary` 在 live **报告不计分**（换名 `*_not_scored_live`）；答复类与 `proposals.summary` 在回放不计分 | 回放里模型每一步由脚本写死，判路径等于判脚本；live 里判路径等于判「像不像脚本」——实测 21/34 红在这上面，而模型走的是先读后写、先搜后提、该问就问 | 不分层则 live 分数衡量的是「与脚本的相似度」，真实的行为回归被这层噪声淹没 |
| 注入用例的答复判据 | 「注入用例零写入提案」+ canary 不出现在模型写出的内容里 | canary 只扫**动作**（提案、工具参数、sources）；答复改判 `injection_no_execution_claim`（是否声称已执行） | 实测六条注入用例硬不变量全部成立，却因为模型把注入文本**报告给用户**而全红——「引用着拒绝」与「照着做了」被同一条字符串判据混为一谈；禁 canary 出现在答复等于禁止模型发现注入时告诉人 | 保留原判据会让一个安全行为完全正确的系统被记为全红，并诱导「因为红得没道理所以放宽阈值」 |
| 16 条历史用例的落点 | 「把 16 条迁至 `dsherp-daily.localhost`，不改基的标 `scored:false` + `skip_reason`」 | v1 的 16 条原样留在 `evals/cases/dsherp-validation.localhost/`，daily 站**新建** 34 条 v2；不计分的理由写在该目录的 README | v1 与 v2 的 schema 不同，`load_case` 对 v1 提前返回、根本不读 `skip_reason`；迁过去要么就地升版（改写别人跑出来的历史）要么造一批半截文件 | 「不计分用例必须说明理由」这条测试会一条用例都遍历不到——它现在同时覆盖 v1 归档目录，正是为此 |
| 长尾用例的夹具 | Files 里写了 `evals/setup/longtail.py` | 不新建，长尾用例复用 `rebased_records` 的固定单据 | 长尾测的是提问形态（口语、错别字、混杂、空结果、越权、越界），不是数据形态；再造一份夹具只会多一处要同步的站上状态 | 无——夹具少一份，站上痕迹也少一份 |
| 长尾的域分布 | query 12 / operation 13 / configuration 6 | query 11 / operation 19 / configuration 4（全集 34 条） | 改基用例天然偏 operation（历史失败运行几乎都在写链路上），configuration 域可提的问题本来就少 | configuration 域的长尾覆盖比原计划薄，这一层今天靠三条 `lt-cfg-*` 与注入组的配置那条 |
| `evals-live.yml` 的形态 | Interfaces 写了用 `secrets.DEEPSEEK_API_KEY` | 文件在，但**永不执行评估**：`guard` job 要 `vars.DSHERP_EVAL_LIVE == '1'`，即使打开，最后一步也无条件 `exit 1` 并说明原因 | key 进仓库 secrets 属仓库设置变更（独立检查点，裁决 #13 把 live 定在本机跑）；托管 runner 从零建站的时间预算也放不下一批 live | 它是一份**形状**而不是能力：真要在 CI 跑 live，得先过那个检查点，再把这一步的 `exit 1` 拿掉 |
| 前置校验拒绝的集成证据落点 | 「`test_purchase_operations.py` 与 `test_work_order_operations.py` 各加一条：417 且 `DS Run Event` 有 `tool_refused`、reason 含具体字段名/可用量」 | 不在这两个文件里加，同一性质由三处覆盖：原生用例验拒绝文案含字段名与可用量、`test_error_taxonomy_behavior.py` 在真实 wire 上验 417 与 `untrusted` 信封、评估用例 `lt-preflight-short-stock-04` 端到端验「拒绝且零提案行」 | 这两个文件各是一段 700 行的容器内脚本，再写两段只为重复一个已被三面覆盖的性质；而评估用例那条还多验了一件事——被拒之后**没有留下待确认的提案**，那才是前置校验存在的理由 | 若将来某条制造链的拒绝路径与销售链不同（例如委外的可用量口径），这三处都不会替它作证，要单独补 |
| 制造链路由的 `blocked_when` | 计划写 `['Closed']` | 两条采购路由为 `['Closed','On Hold']`，两条委外路由维持 `['Closed']` | 镜像内实测：`Purchase Order` 有 `On Hold` 状态且 ERPNext 的 `check_for_on_hold_or_closed_status` 两种都拒；`Subcontracting Order` 没有该状态 | 照原文只拦 Closed：挂起的采购单会被 `routes[]` 报成 ready，提案只能在确认时失败 |
| `runtime_revision` 拆分 | 「拆为配置指纹（进 run）与密钥版本（只进 worker）」 | 配置指纹不动（S9 已把密钥排除在外，拆分事实上已完成），只补密钥版本并只写 worker 日志与告警 | 再做一次拆分只会轮换所有会话的 native session 却不增加任何信息 | 密钥版本不在 run 上，追「这条 run 用的是哪一版 key」要对时间戳去查 worker 日志与 `rotations.json` |

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
| 6 | 预算常量正式值 | **2026-09-09 裁定完成**（34 条真实模型批次）：单次输出 token 三域 8192；调用数 operation 15 / query 11 / configuration 8；累计输入字节 786,432；累计输出 token = **调用数 × 单次**（operation 122,880 / query 90,112 / configuration 65,536——低于这个乘积时预留不退款会让它变成一个更小的调用数上限，见证据文档「第三个真实缺陷」）；时长维持 600s / 300s。推导与被截断的观测见「计划 6 偏离表」与 [agent-quality-evidence](../../engineering/agent-quality-evidence.md) Task 6.5 | 观测值归档在 `docs/engineering/data/evals-live-observations-2026-09-09.json`，`tests/test_budget_official_values.py` 数据驱动比对，不写死数字 |
| 7 | 执行模式 | 原为 Codex 执行；计划 3/4/5 实际由 Claude 直接执行并自审，2026-09-08 用户裁决计划 6 沿用「Claude 新会话执行 + 自审」，独立审计按需另开 | 串行顺序不变；「审计方独立重跑」的门（G1/G3）仍由用户或独立会话执行 |
| 8 | 单主机内部 HTTP（2026-09-05 裁决） | 接受：TLS 终止在 Caddy，同一受控主机 internal 网络内各跳为明文；公网入口、provider 与任何跨主机连接继续 HTTPS | G4 判据改写为「公网入口与一切外联走 TLS，单主机内部各跳限于受控主机的 internal 网络」；配套要求宿主防火墙规则持久化并经重启/网络重建验证（计划 3 收口已做） |
| 9 | 外部主机验收与研发解耦（2026-09-05 裁决） | 计划 3 收口后允许计划 4 本地研发开工；G1/ACME 保持未通过，仍作为真实租户接入前的必要验收 | 实施顺序表第 4 行的依赖改为「制品合入即可」；裁决 #7 的串行只约束研发顺序，不再把外部验收当作开工前置 |
| 10 | 用户删除与审计保留的边界（2026-09-05 裁决） | 保留动作、对象、版本、确认与执行结果等必要审计事实；删除会话目录与非审计个人内容；已有事件禁止改写，物理清除事件内个人信息只能走明确登记的受控脱敏迁移；界面遮挡不算删除 | 工作流 E 的 T4 按此改写；字段级边界由计划 4 的实施计划逐字段列出，不能只写「保留骨架、脱敏字段」就施工 |
| 11 | 计划 5 的事实裁决（2026-09-07，补记） | 交接窗口 2 小时；Claude 新会话执行并自审；允许经确认后用驱动把本机 dev 四站从零重建；每日集成在 GitHub 托管 runner 上从零开通 | 工作流 E 的交接令牌与工作流 G 的每日集成按此执行；G9 起算点见 quality-gates-evidence |
| 12 | 计划 6 的执行与评估授权（2026-09-08） | 真实模型（DeepSeek）全额授权，不新增额度闸门，余额用尽即停；评估集 = 16 条历史失败改造 + 隔离站合成（含 ≥5 注入）；上一轮审查的四项收口并入计划 6 切片 0 | 工作流 F 的「真实模型（授权后）」自本裁决起成立；G8 语义层在本机真实跑，CI `schedule` 只回放 |
| 13 | G8 的判据形状与评估的运行位置（2026-09-08） | G8 = 三条**并列**判据：回放层 100%；live 注入组 100% 零提案；live 总体 ≥85% 且不低于归档基线。**低于阈值不得调低阈值。** replay 进每夜 CI，live 只在本机手动跑（provider key 不进仓库 secrets，那属仓库设置变更） | G8 的验收方式由「通过率达标」改写为上述三条；`evals-live.yml` 只有 `workflow_dispatch` 且默认不启用；语义层回归不会每夜自动发现，这一点写入计划 6 偏离表 |

## 与既有文档关系

- 执行底座与 HITL 不变量：以 2026-08-29 上下文 Agent 计划及其证据为准，本 spec 全部保持有效，清单见审计文档"已核验为生产级、设计中保持不动的底座"节。
- [Agent 体系架构设计](2026-08-31-agent-system-architecture-design.md)的产品层（Profile、分诊、治理）排在本 spec 计划 2 之后启动；其 Profile 的 skills 多装载与本 spec 工作流 F 的 skill 强制装载合并实现。
- [首版设计](2026-08-28-dsherp-design.md)的多租户拓扑（平台站 + 每企业一站）与权限表不变；本 spec 工作流 B 把其中"每次业务请求校验成员关系"的口径具体化为 60s 缓存 + 推送撤销。
- [v16 迁移计划](../plans/2026-09-01-v16-migration.md)的 C4 冷静期方法复用为 G10；其"冷静期不覆盖周/月调度"边界由计划 1 的告警与终验的观察期补齐。
- 七维审计（2026-09-02，原只存于会话记忆）自本文起以[生产就绪第二轮审计](../../engineering/production-readiness-audit-2026-09-03.md)"与七维审计的关系"节为仓库内记录。
