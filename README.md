# dsherp

基于 DeepSeek Harness SDK 与 ERPNext 的 Agent 驱动制造业 ERP SaaS。

## 当前状态

2026-08-29 起按 [原生业务页面上下文 Agent 计划](docs/superpowers/plans/2026-08-29-context-agent-sidebar.md) 实施：原生 Desk 全局侧栏负责页面内协作，正式完整入口为 `/desk/dsherp-agent`，包含对话、待确认、执行记录和应用配置。旧 Demo `/desk/dsherp-studio` 独立保留并退出正式导航；所有业务/配置/填表操作继续复用同一 HITL 链路。当前工作台为本地合成环境实现，不代表生产上线。

用户要求收敛旧服务后，旧独立聊天付费 worker 已于 2026-08-29 停止（无在途任务），其提交、领取、工具和完成端点及本地凭证随后退役；旧任务记录只读保留。下文“已启用”的历史描述不再代表当前运行状态。原生 ERP 与身份服务保留。新实现进度见 [上下文 Agent 证据](docs/engineering/context-agent-evidence.md)。

当前隔离合成环境运行 ERPNext `16.33.0` / Frappe `16.31.0`，容器 Python `3.14.7`；固定 DSH SDK/Runtime 仍为 `0.1.1rc1`。截至计划 1 C4 的最终门禁分别为非集成 `168 passed`、集成 `174 passed`、前端 21 个文件 `167 passed`、Node Runtime `8 passed`。自动化中的工具链使用本地模型替身，真实模型、真实 ERP 与浏览器证据另行记录，不能互相替代。

运行事件流、Prometheus 指标与规则化告警已在隔离合成站落地，计划 1 于 2026-09-03 通过 C4；失败回放、告警时延和真实浏览器截图见[可观测与失败回放证据](docs/engineering/observability-evidence.md)。这仍不代表生产租户部署或生产可用。

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

## 最小验证

已在 macOS arm64、Python 3.12.11 验证以下命令；未声明其他平台兼容：

```sh
uv venv --python 3.12.11 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
.venv/bin/python -m pytest tests/test_dsh_probe.py tests/test_erp_mcp_config.py -q
```

真实模型调用需事先授权费用，并通过进程环境提供 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`，随后运行 `.venv/bin/python -m dsherp.dsh_probe`。脚本不自动加载 `.env`，不读取其他项目凭证；只执行固定合成提示，不访问 ERP。会话临时目录执行后清理，只输出脱敏摘要。

## 隔离 ERP 验证

当前 v16 隔离环境端口为：alpha API `127.0.0.1:18081`、alpha Desk `localhost:18082`、platform `platform.localhost:18083`、beta 隔离预览 `preview.localhost:18085`、daily `daily.localhost:18086`。四个 Site 分别为 `dsherp-validation.localhost`、`dsherp-platform.localhost`、`dsherp-beta.localhost`、`dsherp-daily.localhost`；beta 的 `18084` 不监听。

```sh
docker compose -f infra/compose.validation.yml up -d
.venv/bin/python -m pytest tests/integration -q
```

集成测试需本地 `.runtime/` 普通测试用户配置；缺失会明确失败，不自动跳过。此命令不自动建站或生成资料，首次开通记录和资源边界见 ERP 证据。

开发参考 skills 位于 `.agents/skills/dsh-sdk-development/` 和 `.agents/skills/erpnext-integration/`；不作为业务运行时 skills。

## 技术方向

原生 Frappe Desk 承载业务页面与基础管理；自定义 App 通过扩展入口加载 React + Ant Design 的 Agent、构建及发布页面；Python 应用服务通过 DSH SDK 驱动独立 Runtime；Frappe 自定义 App 封装业务操作；ERPNext 承载业务规则和单据。版本需经过首次验证后固定，不把上游 master 当作稳定依赖。
