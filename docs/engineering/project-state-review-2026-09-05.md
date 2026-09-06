# 计划 4 前的项目状态审查

审查日期：2026-09-05（Asia/Shanghai）。代码基线：`main` / `421a917af6e21d2b7a834a566ea2f5d1524036bb`。

结论：**局部纠偏后继续生产化方向；不应按“计划 3 已完整验收”直接执行原样的计划 4，也不需要推倒重来。** 本轮只调查、验证并形成此记录，没有修改业务代码、既有计划、凭据或部署，没有调用付费模型、提交或推送。

## 1. 目标是否偏移

项目核心价值仍是制造企业在自己的 ERPNext Site 内，通过 Agent 读取业务事实、提出操作、由有权限的人确认、执行原生业务动作并核验结果。现有身份隔离、受限工具、HITL、运行事件及非破坏性原生配置预览/发布均服务这个目标。

判断范围须以[现行上下文 Agent 计划](../superpowers/plans/2026-08-29-context-agent-sidebar.md)为准。其第 5–9 行已替代旧的双空间、任意代码生成和破坏性结构修改方向；第 51–58 行限定为原生配置与非破坏性扩展。因此，本次没有把“未实现任意代码/完整代码应用生成”列为缺陷，也不要求计划 4 顺带建设 Profile、分诊或多 Agent 框架。

需要防止的是**用基础设施验收代替产品使用验收**：

- 当前正式平台入口列出现有成员关系和企业（`frontend/src/Portal.jsx`、`frappe_app/dsherp_platform/api.py:15–42`）；生产 runbook 明确成员绑定仍靠平台 Desk 手工操作（`deployment-runbook.md:233`）。
- 制造 skill 明确 BOM 创建等能力暂不支持（`business-skills/erp-operation/SKILL.md:68–70`）。现有制造证据主要来自预置合成数据，不能直接证明空白企业的首次建账体验。
- 后续产品验收应从一个新合成企业开始：成员进入企业、用受支持的 Agent/原生页面补齐资料、走一条制造业务及确认回读链，并看到真实缺口的明确出口。允许复用原生建账/BOM 页面，不据此自动扩充 Agent 写入能力。

这是需要显式安排的用户闭环验收，不是要求另造平台、重做前端或取消生产化工作。

## 2. 本次实际核验

| 层次 | 新鲜证据 | 能支持的结论 |
|---|---|---|
| Git | 开始时工作树干净；`git ls-remote origin refs/heads/main` 与本地 HEAD 同为 `421a917…`；PR #2/#3/#4 的合并在本地历史中 | 计划 2/3 的代码已进入主分支；不等于验收自动通过 |
| Python 非集成 | `.venv/bin/python -m pytest tests --ignore=tests/integration -q` → **370 passed，69.45s** | 当前离线回归通过 |
| 前端 | `npm test` → **22 文件、202 passed，15.91s** | 当前前端自动化通过 |
| Node Runtime | `node --test runtime/*.test.cjs` → **10 passed** | 当前 Runtime 插件测试通过 |
| 前端构建 | 按现有 `build.mjs` 重建到临时目录，6 个 JS/CSS 文件逐字节与入仓 dist 一致，临时目录自动清理 | 当前源码与发布前端产物一致 |
| 真实 ERP 版本 | 在本机 alpha/daily 容器中读得 Frappe **16.31.0**、ERPNext **16.33.0** | 版本与当前基线一致 |
| Worker | LaunchAgent PID 37642；实际 metrics 端口 **9109**；alpha/daily 心跳年龄分别约 **3.5s / 0.4s**；scheduler 未禁用；两站无 Queued/Running/Cancelling；queue、slots、orphan 均为 0 | 基础进程与领取心跳存活；不能证明真实模型业务任务成功 |
| HTTP 入口 | alpha/platform/beta/daily 的 `/login` 均 **200** 且带 CSP | HTTP 页面可达；未作为浏览器登录与交互验收 |
| 普通用户 HTTP | 用现有 `.runtime/erp-users.json` reader 执行 `read_schema(Customer)` → **401 AuthenticationError**；denied/Guest → **403** | 当前 reader 真实读取无法复现。仅在进程内核验：对应 API key 用户存在、enabled=1，但 API secret 不匹配。已定位为本地验收凭据漂移，未展示、轮换或改写凭据，不能据此断言权限代码回归 |
| 本地备份 | alpha 最新数据库备份 `20260903_021630…`，约 **60.72h**；daily 最新 `20260903_003013…`，约 **62.49h**；metrics 亦报告超过 60h | 当前两个本地合成站的备份新鲜度不满足目标 RPO 24h；不是生产数据损失证明 |

本次未运行会清理队列、写入业务夹具的完整集成套件，未重跑真实模型/浏览器 HITL/远端部署。因此不复述历史 `197 passed` 为当前 HEAD 的全量集成结果，也不宣称生产可用。计划 3 证据中的 197 是多轮结果合计，文档自身已有说明（`deployment-security-evidence.md:127–138`）。

## 3. 已确认的工程问题

### A. [P1] 租户下线归档进入容器临时层

位置：`dsherp/admin.py:495–513`；`infra/compose.prod.yml:82–84`。

`retire_tenant` 先备份，再调用 `bench drop-site --no-backup`。本次读取了已安装 Frappe 16.31.0 的 `frappe/commands/site.py:1009–1077`：该命令会把整个站目录移动到 `/home/frappe/frappe-bench/archived/sites/<site>`，不是当场删除备份。

但 prod backend 只持久化 `sites` 和 `logs`，没有持久化 `archived`；固定基底镜像的 `Config.Volumes` 同样只有这两处。因此归档从持久卷移入容器可写层，backend 因升级重建后会丢失。**尚未执行破坏性下线复现，结论依据是已安装命令实现和实际镜像/compose 持久化边界。**

建议：在现有下线流程中把整站归档明确存入持久位置，回读并返回真实归档位置，再完成下线；用一次合成站“下线→重建容器→恢复”证明存活。不要新建通用归档框架。

### B. [P1] 发布来源声明没有绑定实际构建内容

位置：`infra/release_images.py:27–34,83–95`。

`--git-commit` 仅检查十六进制格式；Docker build context 使用当前工作目录。没有验证工作树内容是否来自声明的提交、发布 tag 是否对应该提交。脏工作树或与实际源码不符的 `--git-commit` 都可被记录为发布来源。远端 `git ls-remote --tags origin` 本次返回空列表，不能按现有 runbook 的 tag 克隆路径复现已有发布。

建议：让现有构建入口从可核实的提交/tag 输入产生制品；有 Git 和使用 `git archive` 的路径都要能解释来源。无需新增哈希层或供应链平台。

### C. [P1] Agent 宿主隔离没有形成重启后的交付闭环

位置：`dsherp/admin.py:516–532`；`docs/engineering/deployment-runbook.md:174–184`。

命令只打印绑定当前 Docker bridge 名的 INPUT 规则；runbook 明确规则不随重启保留，把持久化留给操作者。仓库未提供对应恢复制品或验证证据。按文档只下发临时规则后重启，或重建 agent 网络使 bridge 名改变，原有宿主隔离会失效；worker 的自动恢复不保证规则先恢复。

建议：复用现有 systemd/runbook 补足规则恢复与启动顺序，再真实验证一次重启/网络重建后的宿主拒绝。此项为已有安全边界的持久性缺口，非假设性新增门禁。

### D. [P1，计划 4 首片] 升级一致性校验同时可能漏报与误报

位置：`dsherp/admin.py:571–580,608–655,713–725`。

当前 `SNAPSHOT`：

- 明确排除 `istable` 与 `issingle`，并只用 SQL 列读取父记录。订单明细、库存明细等子表未进入校验；本次真实站点 metadata 确认 `Sales Order Item` 是子表，父表 SQL 列不含 `items`。
- 对读取异常 `except Exception: continue`，无法读取的数据可被静默漏掉。
- 反而包含 Patch Log 等迁移元数据，合法 patch 产生的新记录会被判为数据不一致，令 `release` 返回失败。
- 报告只有每个 DocType 的数量/摘要，不是文案所称的逐业务字段差异；`rollback` 只输出恢复后的快照，没有与升级前快照对比。

本次用**现有 SNAPSHOT 代码**及内存合成元数据/行做无站点写入的最小复现：仅子表数量由 1 改为 99 → `compare_snapshots` 无差异；仅正常新增 Patch Log → 报差异。该复现不是一次真实 migrate/恢复演练。

建议：作为 G2 的首个行为切片修正，明确业务数据及相关子表/Single 范围、合法迁移的预期变化、读取失败的 fastfail、恢复后逐字段核验。避免全库无差别快照和禁止所有合法变化的校验。

## 4. 计划与验收口径需要修订的地方

| 项 | 当前事实 | 建议 |
|---|---|---|
| 计划 2 | PR #2 已合并，当前离线回归通过；README 与总体设计仍写“待审计放行” | 核对并统一最终放行记录，不从合并动作推断验收，也不机械重跑全部历史审计 |
| 计划 3 | 主体制品已合入；自身证据第 5 行明确 G1 不记通过，ACME 未验；另有上述 A–C 收口项 | 表述为“主体实现完成、验收未闭合” |
| G4 | 已有真实容器边界证据；但证据第 237 行承认 Caddy 后各跳为 HTTP，与 spec 的“全链路 TLS”字面不符 | 明确是否接受单主机内部 HTTP 的架构取舍，并同步验收口径；不能同时承认例外又宣布原判据全部通过 |
| 计划 4 | 目前只有总体设计工作流 E 与实施顺序表，没有分项实施计划 | 先形成短而可执行的任务切片，不照搬整段架构散文直接施工 |
| RTO | 工作流 E 第 135 行写 4h；G3 与用户已裁决 #2 写 8h | 采用已裁决的 **8h**，属于文档修正，无需重新选择 |
| 审计与删除 | 第 137/144 行要求审计记录只增不删，第 140 行又要求删除运行和事件 | 写明业务运行可变阶段、终结后不可变内容、可删除会话/个人信息与保留审计事实的边界。不得凭实现者自行选择删除已承诺保留的审计数据 |

计划 4 的凭证工作仍然必要：平台 `_business()` 目前读取长期成员 api_secret 调用业务站（`frappe_app/dsherp_platform/api.py:45–63`），与已决定的业务站签发短期凭据尚有差距。需要一起定义并发登录、轮换、过期、成员撤销和旧请求在途行为。

## 5. 建议的下一步

1. **一次有限收口**：修 A–C，恢复本地验收凭据的一致性并核对备份调度事实；修复应沿用最小行为 RED→实现→真实路径验证，不扩建门禁体系。凭据处理另按本项目既有安全方式执行，本次未改。
2. **修订计划 4**：先统一 RTO、审计保留/删除边界；按“升级与恢复可核验（D）→全站异地备份和异机恢复→审计保留及数据生命周期→短期凭据和轮换”形成可交付切片。用量及索引复用已有事件和框架，按实际必要性安排。
3. **保留 G1/ACME 未通过状态**：补合规专用主机的独立部署验证。若不希望等待该主机阻塞本地开发，应先明确调整原来的串行依赖，把外部部署验收与本地计划 4 研发拆开；不能通过改写完成状态偷渡。
4. **保留面向用户的终验**：用新合成企业跑成员进入、原生建账、Agent 查询/提案、确认及 ERP 回读。不要只用预置夹具与健康探针替代；也不要借此把已废弃功能重新放回范围。
5. **计划 5/6 保持价值约束**：计划 5 优先自动运行已有有效行为验证；计划 6 聚焦真实失败输入、实际模型行为与成本。替身通过率不能代表实际 Agent 完成率，CI 条数不能代表业务完成。

本次建议属于审查结论，尚未修改原计划、放行门或执行后续实施。

## 复核与处置（Claude，2026-09-05 → 2026-09-06）

审查的四条工程问题逐条在仓库代码与容器内的真实 Frappe 16.31.0 上核对，全部成立；A 与 D 比审查写的更重：

- **A**：下线命令自己做的 `bench backup` 写在站目录内的 `private/backups`，随后 `drop-site` 把整个站目录连同这份备份一起搬进 `archived/`——归档步骤被下线步骤直接抵消；返回值里也没有归档路径。基底镜像 `Config.Volumes` 确为 `sites` 与 `logs` 两处。
- **B**：远端与本地都没有任何 tag；现有 manifest 记录的提交 `7482304` 在 main 历史里，问题在于流程允许造假。
- **C**：unit 渲染器没有前置规则单元，worker 的 `prepare_host` 不检查规则。
- **D**：误报不只 Patch Log——DocType、Custom Field、Property Setter、Module Def 等所有随 migrate 变动的元数据表都会让 `release` 以失败退出；`SNAPSHOT` 脚本没有被任何测试执行过（`SnapshotBench` 替掉了 `python()`），仓库里也没有一次真实 release 报告。G2 目前只是骨架。

补充与更正：

- 第 2 节的「denied/Guest → 403」与我此前的「denied 200」不矛盾：denied 能认证（`get_logged_user` 200），受限 `read_schema(Customer)` 是 403，是「能登录、无业务读权限」的正常行为。reader 两个端点均 401 属实。
- 凭据漂移的根因已定位：`tests/integration/test_sso_machine_auth.py`（计划 3 提交 `f9bd051`，2026-09-05 13:14）对共享的合成 reader 调用 `generate_keys`，每跑一次就作废 `.runtime/erp-users.json` 里的密钥（站上 api_key 未变、`modified` 为当日 13:10、Version 无字段差异，与 `generate_keys` 只改密码字段一致）。测试改为自建并删除临时探针用户；验证站 provisioner 增加 `--reissue <actor>`，只重签该演员的密钥并原地改写档案（原 provisioner 遇到已有档案会拒绝覆盖，「重跑 provisioner 即可」的说法不准确）。修复后 reader `get_logged_user`/`read_schema(Customer)` 均 200，denied 403。
- 备份调度事实：Frappe 16 核心 hooks 没有每日站点备份任务，两站 `Scheduled Job Type` 里只有 `delete_downloadable_backups`；本机两站最近的备份是 2026-09-03 的人工/脚本备份。所以「60 小时」不是失败，是尚无调度——计划 4 的备份切片必须覆盖「定时生成 → 异地同步 → 失败可见 → 异机恢复验证」。
- 第 4 节的 RTO、审计/删除边界、G4 措辞、计划 2/3 状态与计划 4 依赖，已按四项裁决（spec 已裁决 #8/#9/#10 与实施顺序表）改写。

处置：A/B/C 与计划 3 的一处回归（宿主熔断探针地址）在分支 `plan3/closeout` 修复并各以真实路径演练，证据见 [deployment-security-evidence.md](deployment-security-evidence.md)「计划 3 收口」节；D 留作计划 4 首片；计划 2 的放行记录改为「放行前独立复核未通过」，见 [runtime-reliability-evidence.md](runtime-reliability-evidence.md) 末节。
