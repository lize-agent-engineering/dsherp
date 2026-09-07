# dsherp

基于 DeepSeek Harness SDK 与 ERPNext 的 Agent 驱动制造业 ERP SaaS。

## 当前状态

环境：本机隔离合成四站加两个一次性测试站，ERPNext `16.33.0` / Frappe `16.31.0`，容器 Python `3.14.7`，DSH SDK/Runtime `0.1.1rc1`。全部证据来自合成数据，不代表生产可用；任何环境都未接入真实租户。

| 计划 | 范围 | 状态 | 证据 |
|---|---|---|---|
| 1 可观测与失败回放 | 工作流 D；评估集导出 | 2026-09-03 通过 C4 | [observability-evidence](docs/engineering/observability-evidence.md) |
| 2 运行底座可靠性 | 工作流 C；错误透传与 ErrorBoundary | 收尾切片已合入 main，放行以此为准（12 个独立复核：7 项成立、6 项推翻并修复） | [runtime-reliability-evidence](docs/engineering/runtime-reliability-evidence.md) |
| 3 部署制品与安全边界 | 工作流 A；出口控制、非 root、SSO 强制、CSP | 主体实现完成、验收未闭合：G1 字面判据与 ACME 待合规主机由审计方执行（已裁决 #9） | [deployment-security-evidence](docs/engineering/deployment-security-evidence.md)、[runbook](docs/engineering/deployment-runbook.md) |
| 4 数据治理与容灾 | 工作流 E；凭证托管与轮换 | 全部合入 main（PR #7、#9、#10）；G3 正式验收与真机 `restore-site` 未闭合 | [data-governance-evidence](docs/engineering/data-governance-evidence.md)、runbook 第 12–14 节 |
| 5 质量门禁（瘦身版） | CI 与每夜从零、一条命令开发栈、登记式清理、原生测试与权限矩阵、交接令牌落表 | 本机全部切片实现完成并合入本地 main；push/PR、分支保护、workflow 首跑属用户检查点，G9 从第一个含原生测试的绿色 nightly 起算 | [quality-gates-evidence](docs/engineering/quality-gates-evidence.md) |
| 6 Agent 质量与成本 | 工作流 F；注入信封 | 未开始（串行于计划 5 之后，已裁决 #7） | [生产化总体设计](docs/superpowers/specs/2026-09-03-production-hardening-design.md) |
| 终验 生产浸泡 | 全部 | 未开始 | — |

已推迟、不在计划 5 范围：`dsherp/admin.py` 体量拆分（复盘 Q5）；Playwright 五路径、mypy、存量注入脚本整体改写（理由见证据文档的偏离表）。

### 历史记录（保留，按时间倒序）

2026-08-29 起按 [原生业务页面上下文 Agent 计划](docs/superpowers/plans/2026-08-29-context-agent-sidebar.md) 实施：原生 Desk 全局侧栏负责页面内协作，正式完整入口为 `/desk/dsherp-agent`，包含对话、待确认、执行记录和应用配置。旧 Demo `/desk/dsherp-studio` 独立保留并退出正式导航；所有业务/配置/填表操作继续复用同一 HITL 链路。当前工作台为本地合成环境实现，不代表生产上线。

用户要求收敛旧服务后，旧独立聊天付费 worker 已于 2026-08-29 停止（无在途任务），其提交、领取、工具和完成端点及本地凭证随后退役；旧任务记录只读保留。下文“已启用”的历史描述不再代表当前运行状态。原生 ERP 与身份服务保留。新实现进度见 [上下文 Agent 证据](docs/engineering/context-agent-evidence.md)。

当前隔离合成环境运行 ERPNext `16.33.0` / Frappe `16.31.0`，容器 Python `3.14.7`；固定 DSH SDK/Runtime 仍为 `0.1.1rc1`。计划 2 的终审阻断项已于 2026-09-05 逐项关闭，四套门在 backend 重载后的最终数字为非集成 `284 passed`、集成 `191 passed`（17 分 18 秒）、前端 22 个文件 `201 passed`、Node Runtime `10 passed`；本轮无 DocType/Report/hooks 变更，因此不需要 migrate。计划 2 于 2026-09-06 由架构方派 12 个互不知情的代理做放行前独立复核：13 项终审关闭中 7 项成立、6 项被推翻（1 项为计划 3 引入的熔断探针地址回归，已随计划 3 收口修复；5 项 major 残余按用户裁决在收尾切片修复并经全量集成门，见[计划 2 证据](docs/engineering/runtime-reliability-evidence.md)「收尾切片」节），放行以该切片合入 main 为准。计划 3（部署制品与安全边界）于 2026-09-05 由 Claude 直接实施：自建两个镜像、生产 compose、`dsherp-admin` 开站/发布/回滚 CLI、systemd 守护、internal 的 agent 网络与唯一出口代理、非 root 容器、业务站 SSO 强制、运行凭据限源限频、CSP 与渲染限制；G4 判据在 dev、本机生产形态与 x86_64 服务器的真实容器上实测（独立审计发现并修复三处缺陷），runbook 在本机 Docker 与 x86_64 服务器各完整执行一次并修掉 22 个断点；2026-09-06 按[项目状态审查](docs/engineering/project-state-review-2026-09-05.md)收口：租户归档落持久卷、发布来源可核实、宿主防火墙随开机恢复并在 x86_64 服务器上经重启/网络重建演练，本机以生产形态跑通「下线 → 重建容器 → 从归档恢复」。状态为「主体实现完成、验收未闭合」：G1 的字面判据（干净专用主机、≤60 分钟）与 ACME 仍待合规主机由审计方执行，按已裁决 #9 不再阻塞计划 4 开工，见[计划 3 证据](docs/engineering/deployment-security-evidence.md)与[部署 runbook](docs/engineering/deployment-runbook.md)。自动化中的工具链使用本地模型替身；仅混沌演练在熔断打开且零活动运行期间按授权调用过一次真实 provider 的免费 `GET /models` 探针，没有调用真实 chat/completions。真实 ERP、浏览器与部署证据分别记录，不能相互替代。

计划 4（数据治理与容灾）已全部合入 main：G2「升级与恢复结果的可靠核验」（PR #7）；备份切片、审计保留与数据生命周期、短期凭据与轮换（PR #9，经三轮独立审查）；以及按[整体复盘](docs/engineering/plan4-retrospective-2026-09-07.md)修正的两条容灾主链路（`backup --sync` 同步失败即非零退出；灾后重发现的备份集从已核验清单回填镜像身份、缺身份即拒绝恢复）、用量完整性与保持过期。证据见[数据治理与容灾证据](docs/engineering/data-governance-evidence.md)与 [runbook 第 12–14 节](docs/engineering/deployment-runbook.md)。它证明工具链与契约，不证明物理异机容灾与正式 RTO；G3 的正式验收、真机 `restore-site` 全流程仍未闭合。计划 5 承接 CI、Frappe 原生测试与登记式夹具清理。

运行事件流、Prometheus 指标与规则化告警已在隔离合成站落地，计划 1 于 2026-09-03 通过 C4；计划 2 的运行底座、G5、混沌、前端截图与终审阻断项的逐项关闭见[运行底座可靠性证据](docs/engineering/runtime-reliability-evidence.md)。失败回放与告警时延见[可观测与失败回放证据](docs/engineering/observability-evidence.md)。这仍不代表生产租户部署或生产可用。

**v15 时期历史证据**：ERPNext `15.119.3` / Frappe `15.118.0` 本地合成环境曾完成原生初始化、普通用户读取/拒绝、alpha 分段与 daily 制造闭环；详见[阶段 2/3 制造闭环与技能升版证据](docs/engineering/stage-2-3-manufacturing-evidence.md)和[原生侧栏 HITL 真实验收](docs/engineering/context-agent-hitl-acceptance.md)。**当前 v16 证据**：四站 fresh provision、制造行为重验、原生浏览器矩阵与经授权真实 DeepSeek 只读矩阵已落档；C4 24 小时冷静期、三次分时备份恢复和当前 HEAD 最终独立审计均已通过，`main` 已切换到 v16，v15 九卷已归档并按精确清单逐名删除（v15 镜像在用户决定废弃 AgenERP 后一并删除）。全部证据来自本机隔离合成四站，不含生产租户数据，因此仍不得宣称可上线或生产可用。详见 [v16 迁移证据](docs/engineering/v16-migration-evidence.md)。

阶段 1–3 的原型与两次 DeepSeek 页面端到端验收属于 v15 时期历史证据，见[原生 Desk 证据与剩余事项](docs/engineering/native-desk-evidence.md)。当前 v16 本地入口：[alpha 原生登录页](http://127.0.0.1:18082/login)、[正式 Agent 工作台](http://127.0.0.1:18082/desk/dsherp-agent)、[应用构建原型](http://127.0.0.1:18082/desk/dsherp-studio)。页面与数据均为隔离合成环境，不代表真实生成、发布或生产迁移。

[真实 Agent 工作台实现与验收边界](docs/engineering/real-agent-workbench-evidence.md)。当前 v16 平台入口为[企业空间](http://platform.localhost:18083/desk/dsherp-home)，进入企业后使用对应 Site 的 `/desk/dsherp-agent`；已落档的真实模型查询只读取隔离合成业务数据，尚未接入生产企业。

真实平台入口：[企业空间](http://platform.localhost:18083/desk/dsherp-home)，边界与验证见 [平台身份证据](docs/engineering/platform-identity-evidence.md)。

前端构建、原生入口同步和浏览器验收见 [阶段 2 原型证据](docs/engineering/frontend-prototype-evidence.md)。

## 产品方向

- 多家企业注册使用；每个客户企业对应独立 ERPNext Site。
- 服务从零建立 ERP 数据的制造企业，支持自制、委外和混合生产。
- Agent 生成包含数据、页面、规则、权限和测试的业务应用，经隔离预览与管理员确认发布。
- Agent 引导建立基础资料，并推进销售、采购、生产、委外、入库和交付。
- 查询按权限执行；基础资料和草稿按授权创建；正式提交、取消和影响库存或账务的操作需要有权限的人确认。
- 首版不做外部 ERP 接入、历史迁移、自动排产优化和银行支付接入。

## 文档

- [首版设计](docs/superpowers/specs/2026-08-28-dsherp-design.md)
- [Agent 体系架构设计](docs/superpowers/specs/2026-08-31-agent-system-architecture-design.md)
- [ERPNext 中文术语纠正包设计](docs/superpowers/specs/2026-08-31-terminology-correction-design.md)
- [生产化总体设计](docs/superpowers/specs/2026-09-03-production-hardening-design.md)
- [首次技术验证计划](docs/superpowers/plans/2026-08-28-foundation-validation.md)
- [项目开发约定](AGENTS.md)
- [准确版本与运行契约](docs/engineering/runtime-baseline.md)
- [DSH 分层验证证据](docs/engineering/dsh-validation-evidence.md)
- [ERPNext 真实验证证据](docs/engineering/erpnext-integration-evidence.md)
- [阶段 1 策略迁移工程证据](docs/engineering/stage-1-policy-migration-evidence.md)
- [阶段 2/3 制造闭环与技能升版证据](docs/engineering/stage-2-3-manufacturing-evidence.md)
- [ERPNext 中文术语纠正包实施与验收证据](docs/engineering/terminology-pack-evidence.md)
- [当前前端优先与应用生成计划](docs/superpowers/plans/2026-08-28-frontend-agent-app-generation.md)
- [已确认的平台身份实施计划](docs/superpowers/plans/2026-08-28-platform-identity.md)
- [阶段 3 身份绑定参考](docs/superpowers/plans/2026-08-28-identity-bound-read-execution.md)
- [制造业务闭环验证实施计划](docs/superpowers/plans/2026-08-31-manufacturing-loop-validation.md)
- [ERPNext/Frappe v16 完整迁移计划](docs/superpowers/plans/2026-09-01-v16-migration.md)
- [生产就绪第二轮审计](docs/engineering/production-readiness-audit-2026-09-03.md)
- [计划 1：可观测与失败回放](docs/superpowers/plans/2026-09-03-observability-replay.md)
- [计划 2：运行底座可靠性](docs/superpowers/plans/2026-09-04-runtime-reliability.md)
- [计划 3：部署制品与安全边界 证据](docs/engineering/deployment-security-evidence.md)
- [部署 runbook（单 Linux 主机 + Compose + 自建镜像）](docs/engineering/deployment-runbook.md)
- [计划 4：数据治理与容灾 证据](docs/engineering/data-governance-evidence.md)
- [计划 5：质量门禁 证据](docs/engineering/quality-gates-evidence.md)
- [计划 5：质量门禁（瘦身版）实施计划](docs/superpowers/plans/2026-09-07-quality-gates.md)
- [外部参考：PenguinHarness 对 dsherp 的可借鉴之处](docs/engineering/penguin-harness-reference-2026-09-07.md)

## 最小验证

已在 macOS arm64、Python 3.12.11 验证以下命令；未声明其他平台兼容：

```sh
uv venv --python 3.12.11 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
```

`pytest -q` 是非集成套件（`pytest.ini` 让它默认不收集 `tests/integration`，那套需要四站与 `.runtime` 夹具）。

真实模型调用需事先授权费用，并通过进程环境提供 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`，随后运行 `.venv/bin/python -m dsherp.dsh_probe`。脚本不自动加载 `.env`，不读取其他项目凭证；只执行固定合成提示，不访问 ERP。会话临时目录执行后清理，只输出脱敏摘要。

## 隔离 ERP 验证

当前 v16 隔离环境端口为：alpha API `127.0.0.1:18081`、alpha Desk `localhost:18082`、platform `platform.localhost:18083`、beta 隔离预览 `preview.localhost:18085`、daily `daily.localhost:18086`。四个 Site 分别为 `dsherp-validation.localhost`、`dsherp-platform.localhost`、`dsherp-beta.localhost`、`dsherp-daily.localhost`；beta 的 `18084` 不监听。

```sh
docker compose -f infra/compose.validation.yml up -d
.venv/bin/python -m pytest tests/integration -m integration -q
```

从零拉起并开通四站（空 Docker 也可以）：`.venv/bin/python infra/dev_stack.py up --provision`；拆掉：`down --volumes`。

生产形态不用这份 compose：镜像由 `infra/release_images.py` 从 tag 构建，栈由 `infra/compose.prod.yml` 加 `infra/env/prod.env` 拉起，开站与发布走 `bin/dsherp-admin`，逐步命令见[部署 runbook](docs/engineering/deployment-runbook.md)。

集成测试需本地 `.runtime/` 普通测试用户配置；缺失会明确失败，不自动跳过。此命令不自动建站或生成资料，首次开通记录和资源边界见 ERP 证据。

开发参考 skills 位于 `.agents/skills/dsh-sdk-development/` 和 `.agents/skills/erpnext-integration/`；不作为业务运行时 skills。

### 原生测试（Frappe 内）

两个专用测试站只给 `frappe_app/*/tests` 下的测试用，nginx 不暴露它们（对两站都回 421）：
`dsherp-test.localhost`（backend，frappe/erpnext/dsherp_bridge，合成公司 DNT）与
`dsherp-platform-test.localhost`（platform-backend，frappe/dsherp_platform）。
`dev_stack.py up --provision` 的最后一步会建它们。

```sh
.venv/bin/python infra/dev_stack.py native-tests --out work/junit-native
docker compose -f infra/compose.validation.yml exec -T backend \
  bench --site dsherp-test.localhost run-tests --app dsherp_bridge --module dsherp_bridge.tests.test_harness
```

改了 `frappe_app/` 之后先 `docker restart dsherp-validation-backend-1 dsherp-validation-platform-backend-1`
（gunicorn 缓存模块；`bench run-tests` 是另一个进程，本身不缓存）。

成败不由 `bench` 的退出码单独判定：站点没开 `allow_tests` 时它打印
「Testing is disabled for the site!」并返回 0，什么也没跑。判据是退出码非零即失败，
**且**必须出现 runner 自己的 `Running N <category> tests for <app>` 且 N>0
（`dsherp/native_tests.py`）。镜像里没有 `xmlrunner`，`--junit-xml-output` 不写文件，
所以产出是每个 App 一份 `.log` 和一份解析后的 `.json`。


## 技术方向

原生 Frappe Desk 承载业务页面与基础管理；自定义 App 通过扩展入口加载 React + Ant Design 的 Agent、构建及发布页面；Python 应用服务通过 DSH SDK 驱动独立 Runtime；Frappe 自定义 App 封装业务操作；ERPNext 承载业务规则和单据。版本需经过首次验证后固定，不把上游 master 当作稳定依赖。
