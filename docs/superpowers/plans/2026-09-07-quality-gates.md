# 计划 5：质量门禁（瘦身版）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 学过的失败不能悄悄回来（每个 PR 与每次合入 main 自动跑 ruff、非集成 pytest、vitest、dist 一致性、Node runtime 测试、DocType 迁移守卫）；每次冲破一天内变成用例（每夜在 GitHub Actions 里从零拉起四站，跑全部集成测试与 Frappe 原生测试，红了当天修或删）；审查方不用再自己重跑（junit、compose 日志、台账作为工件上传）。顺带还清三笔债：集成夹具登记式清理、G7 权限矩阵、配置交接的平台令牌落表。

**Architecture:** 三个工作流——`ci.yml`（PR + push main：python / frontend / runtime 三个 job）、`nightly.yml`（02:00 Asia/Shanghai：`infra/dev_stack.py up --provision` → 集成 pytest → 原生测试 → 工件 → `down --volumes`）、`supply-chain.yml`（每周 SBOM/CVE，允许红，**不计入 G9**）。宿主侧新驱动 `infra/dev_stack.py` 把 31 步 fail-closed 的开通脚本串成可续跑的一条命令（步骤表 + 台账 `.runtime/dev-stack.json` + 每步廉价探针，矛盾即停）。集成测试新增 `tests/integration/site_exec.py`（唯一的容器脚本入口）与 `residue.py`（先登记后创建、函数级清理、会话起点清上次残留）。Frappe 原生测试放 `frappe_app/<app>/tests/`，在两个专用 `allow_tests` 测试站上跑；权限矩阵是 JSON 单一来源，宿主静态测试与容器原生测试各读一次。交接令牌复用 R7：行加 `expires_at`，grant 进缓存、寿命 = 窗口，patch 回填并第一次真实使用 `EXPECTED_CHANGES`。

**Tech Stack:** Python 3.12.11（`.venv`，uv 0.9.27，`requirements.lock` 带哈希）、pytest 8.4.2 + pytest-timeout 2.4.0、ruff 0.14.14、Node v26.7.0（vitest 4.1.11 / esbuild 0.28.2 / `node --test`）、Docker Compose v2、Frappe 16.31.0 / ERPNext 16.33.0（固定 digest 镜像）、GitHub Actions（全部 action 以提交 SHA 固定）。

## Global Constraints

- 已裁决、不再讨论：不做 mypy、不做 ruff 风格规则、不做 Playwright、不整体重写注入脚本测试；`supply-chain.yml` 在 G9 之外；交接窗口 2 小时；Claude 新会话执行，每个切片末停检查点；本机从零重建需用户在检查点确认。
- TDD（AGENTS.md）：行为先写失败测试再实现；测试按行为，不锁源码文本、服务数量、文本排版。fastfail：配置缺失、探针与台账矛盾、主机名不解析、镜像不存在 → 明确报错并说明查什么，从不静默重跑或降级。
- **不配置任何重试**（pytest、工作流、驱动）；唯一例外是 Docker Hub 拉镜像的 shell 退避（那是取件不是测试）。超时不盲目上调：先按 junit `time`/`--durations` 拿数据再改。
- 所有 GitHub Actions 以**提交 SHA** 固定，后跟版本注释；本计划里 `<sha of …>` 是**唯一允许的占位符**，实施时用 `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>^{}'` 取 40 位 SHA 写死。第三方二进制（syft、grype、镜像）按校验和/digest 固定，版本号写具体值，不写 `latest`。
- 容器命令只经 `admin.Bench.run/python/script`（stdin 传脚本；`secrets=` 遮 argv）；口令永远不上 argv。宿主侧密钥文件 0600、目录 0700；台账、junit、日志上传前 `scan-artifacts` 核对不含任何密钥值。
- 改了 `frappe_app/` → `docker restart dsherp-validation-backend-1 dsherp-validation-beta-backend-1 dsherp-validation-platform-backend-1` 再跑集成；改了 DocType JSON / patch → 四个开发站 + 两个测试站 `bench migrate`（命令见切片 E Task 4）；跑进程内 `worker.run_once` 的集成测试前停常驻 worker `launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16`，跑完 `bootstrap` 回去；`scheduled` profile 不能在跑。
- 分支每切片一条（`plan5/hygiene`、`plan5/ci`、`plan5/dev-stack`、`plan5/cleanup`、`plan5/native-tests`、`plan5/transfer-window`、`plan5/docs`），切片末合入 main；提交信息中文 `feat:/fix:/test:/docs:`；**每次 push、开 PR、改仓库设置、本机重建都是用户检查点**。不提交 `.runtime/`、密钥、`work/`。
- 计划中的代码块是**候选实现**：允许等价实现；与已核实事实冲突时以事实为准。抖动规则：一条用例无可复现原因失败，一天内修或删（记提交号与 run 链接），不加 `flaky`/`rerun`。
- 上位设计偏离逐项写入切片 F 的偏离表（mypy、风格 lint、Playwright、原生测试范围、SBOM 周报、矩阵 JSON 位置、G9 起算口径）。

## 背景（为什么做、做成什么样）

- 总体设计（`docs/superpowers/specs/2026-09-03-production-hardening-design.md`）实施顺序表第 5 行：计划 5 = 工作流 G 全部 + H 残余，放行门 G9。
- 用户 2026-09-07 对"质量门禁跟不上速度、总会被冲破"的质疑，双方达成的口径：门禁不防生产失败，它保证**学过的失败不能悄悄回来；每次冲破一天内变成用例；审查方不用再自己重跑**。用户接受瘦身版。
- **砍或缓**：mypy、ruff 风格规则、Playwright 五路径、59 个注入脚本测试整体改写。**保留**：PR 门自动跑现有四套门 + 迁移守卫（基线改 PR base）；每日从零拉栈跑集成（顺带一条命令开发环境）；登记式清理；权限矩阵；交接令牌落表收口。
- 用户裁决（本会话）：**Claude 在新会话全部执行**（同计划 3/4，阶段末停检查点）；**交接窗口 2 小时**；**允许经确认后用驱动把本机 dev 四站从零重建一次**；每日集成在 **GitHub 托管 runner 上从零开通**。
- 工作树里 `AGENTS.md` 有一处未提交修改（"使用 TDD…"改为"测试顺序按风险分级…"），用户确认：**保留，是 Codex 在整理相关内容**。切片 F 的 AGENTS.md 任务在它之后追加抖动规则并一并提交，不 stash、不还原。

## 已核实的事实（计划建立在这些之上）

- 仓库公开、Actions 已启用、`main` 无分支保护/ruleset；无 `.github/`、`pytest.ini`、根 `pyproject.toml`、`ruff.toml`、根 `conftest.py`。`requirements.lock` 的 runtime wheel 有三个 hash（含 Linux）；`.python-version` 3.12.11；`.nvmrc` v26.7.0；`frontend/package-lock.json` v3。
- 非集成基线 `648 passed in 120.79s`（2026-09-07 本机）；3 处条件跳过；`tests/` 与 `tests/integration/` 有 **3 对同名文件**（`test_audit_immutability.py`、`test_run_events.py`、`test_run_grants.py`）且两目录无 `__init__.py` → 同一会话收集两者会 `import file mismatch`。
- ruff `--select F,E9` 111 条：82 F811（pytest 夹具形参，误报）、24 F401、3 F841、2 F821（`infra/seed_identity.py:14` 的 `seed_input` 由宿主注入；`tests/test_run_events.py:201` 缺 `import pytest`）；默认 E,F 892 条（E701/E702 757）→ 风格规则必须关。
- 迁移守卫 `tests/test_doctype_patch_guard.py:58-72` 用 `merge-base(origin/main, HEAD)`，在 main 上是空区间。仓库无 tag。
- 集成套件：67 个模块，57 个注入脚本；10 个只 rollback；约 40 个 commit 后在脚本 finally 删（外层 `subprocess.run` 超时 SIGKILL 会跳过 finally）；无共享 exec 助手（14 种签名 + 约 38 处内联）；容器名硬编码 81/18/2 处；高风险残留清单见切片 C。conftest 的 `_key_answers_as` 把 HTTPError/OSError/ValueError 一律当"密钥死了"→ `reissue()`。
- dev 栈：`infra/compose.validation.yml` 项目名写死 `dsherp-validation`；常驻 8 服务 3040 MiB / 1.65 CPU；三个镜像 `pull_policy: never`；无 healthcheck；密钥文件 `.runtime/control/*` 由 compose bind mount 提供（Linux 上归属沿用宿主，容器内 frappe uid 1000 读不到 0600 文件）；`provision_identity.py` 读 `/run/dsherp-control/*`，没有任何 compose 服务提供；`platform_admin_password`/`beta_admin_password` 无生成器；从零开通共 31 步，全部脚本 fail-closed 不可续跑；`preview.localhost`/`daily.localhost` 需要解析到回环。
- Frappe 16.31：`bench run-tests` 需 `allow_tests` 或 `CI` 环境变量；`before_tests` 在多 App 站点直接返回，ERPNext 16 无该钩子；`from frappe.tests import IntegrationTestCase` 可用（类级回滚）；发现器按 `frappe.get_app_path(app)` 走目录，PYTHONPATH 挂载的 App 解析为 `/opt/dsherp-frappe/dsherp_bridge`（已探）；App 从不 `bench get-app`（dev bind mount / prod `COPY` + `ENV PYTHONPATH`）；`frappe_app/` 下无任何测试；`.dockerignore` 只排根 `tests`。
- 交接令牌：grant 含平台 OAuth access token；`validate_grant` 每次调平台；唯一读者 `export_transfer`（被 `accept_transfer` 与每次 `check_origin` 触发）；交接行/配置包无过期；控制器把 `platform_grant` 列入冻结字段；R7 先例 `grants.py` + `patches/v1/drop_run_grant.py`；全仓尚无 patch 声明 `EXPECTED_CHANGES`（`dsherp/release_compare.py` 的 `_validate` 接受 `patch/doctype/fields/rows/columns_removed`，并再导出 `row_hash`）；`user_data.py:38` 的"平台令牌不在行里"对交接表不成立。
- 权限事实：DS-* DocType 的 permissions 大多为空；`DS Agent Task`（platform）给 System Manager rwd 且无 on_trash，与 README"旧任务只读保留"矛盾；`tests/test_audit_immutability.py:14` 的平台路径写错（`dsherp_platform/dsherp_platform/doctype`，真实为 `dsherp_platform/platform/doctype`）；无 `frappe.only_for`；guest 白名单 7 个。
- `dsherp/admin.py`：`CONTROL_SECRETS_BY_ENV['dev']` 五项、`ensure_secrets(resolved, root, names)` 返回 `{'created','kept','directory'}`、`runtime_dir/secrets_dir` 读 `DSHERP_RUNTIME_DIR/SECRETS_DIR`、`Bench(resolved, kind, root, runner, service=…)` 的 `run/python/site_exists`、`BENCH_PYTHON`、`SITES`；`deploy_env.BASE_IMAGE`、`env_file`、`settings`。

---

## 切片顺序、门禁与检查点

| 序 | 切片 | 分支 | 交付 | 切片末门禁 | 用户检查点 |
|---|---|---|---|---|---|
| 0 | 仓库卫生 | `plan5/hygiene` | `pytest.ini` + integration 隔离 + marker + timeout；`ruff.toml` + 29 处清理；守卫基线 `DSHERP_GUARD_BASE` | 非集成全绿、ruff 0、vitest、node、集成 `--collect-only` 无 error、本机集成一次（记最慢 20 条） | PR 合入 |
| A | PR 门 | `plan5/ci` | `ci.yml`（python/frontend/runtime）；分支保护 | 三绿 → 故意一红（dist 不一致）→ 三绿 | push/PR；分支保护是仓库设置 |
| B | 从零开通驱动 + 每夜 | `plan5/dev-stack` | compose 两个 control 服务；`infra/dev_stack.py`（secrets/up/provision/status/down/scan-artifacts/native-tests 桩）；`nightly.yml`（此时不含原生测试步）；`supply-chain.yml`；本机从零重建 | 单测约 21 条；nightly `workflow_dispatch` 首跑绿（记时长/内存/磁盘）；本机重建后集成约 210 passed、worker 心跳恢复 | push；`workflow_dispatch`；**本机重建须确认** |
| C | 集成清理 | `plan5/cleanup` | `site_exec.py`、`residue.py` + 登记表 + 会话起点清残留；重签三态；9 个高风险测试迁移；policy_seed 超时决策 | 宿主单测（sweep 生成器、分类器）；本机集成全绿；故意 kill 一次看下次会话清残留 | PR 合入 |
| D | 原生测试站与权限矩阵 | `plan5/native-tests` | `provision_test_site.py` + 两个 control 服务；`run_native_tests`；`frappe_app/*/tests/` 骨架；`permission_matrix.json` + 宿主静态测试 + 容器原生测试；修 `DS Agent Task` delete、`test_audit_immutability.py:14`；nightly 加原生测试步 | 原生测试全绿（两站）；矩阵改错一处即红；四站 + 测试站 migrate | PR 合入；nightly 首个含原生步的绿夜 = **G9 起算** |
| E | 交接令牌窗口 | `plan5/transfer-window` | DocType/控制器/patch（首个 `EXPECTED_CHANGES`）/缓存租约/导出拒绝/对端中继/`get_bundle` 过滤/前端一行提示/`user_data` 边界 | 宿主单测；原生 5 条；集成回滚式 + alpha 真令牌 + 双站 HTTP；六站 migrate 核对 | PR 合入 |
| F | 文档与规则 | `plan5/docs` | README 状态表、AGENTS.md 抖动规则、spec 偏离表与 E 口径、证据文档、runbook §15 | 文档核对命令 | PR 合入 |

每个切片末除上表外都跑：`.venv/bin/ruff check .`、`.venv/bin/python -m pytest -q`、`(cd frontend && npm test)`、`node --test runtime/*.test.cjs`。证据全部写 `docs/engineering/quality-gates-evidence.md`（切片 F 建骨架；此前各切片先在 PR 描述里给数字，F 时搬入）。

## 文件结构

| 文件 | 切片 | 职责 |
|---|---|---|
| `pytest.ini`、`tests/conftest.py`、`tests/integration/conftest.py`、`tests/test_collection_layout.py` | 0 | integration 按路径隔离 + marker + timeout |
| `ruff.toml`、`requirements.in`/`.lock`、29 处源码 | 0 | 只开 F/E9；ruff 与 pytest-timeout 进 lock |
| `infra/check_doctype_patches.py`、`tests/test_doctype_patch_guard.py` | 0 | `base_revision()` |
| `.github/workflows/ci.yml` | A | PR 门三 job |
| `infra/compose.validation.yml` | B、D | `platform-provision`、`beta-provision`、`test-provision`、`platform-test-provision` 与 secrets |
| `infra/dev_stack.py`、`tests/test_dev_stack.py` | B、D | 驱动；`run_native_tests` 在 D 接线 |
| `.github/workflows/nightly.yml`、`.github/workflows/supply-chain.yml` | B（D 加原生步） | 每夜 / 每周 |
| `tests/integration/site_exec.py`、`tests/integration/residue.py`、`tests/test_integration_residue.py`、`tests/integration/credentials_check.py`、`tests/test_credentials_check.py` | C | 容器脚本入口、登记表与清扫、重签三态 |
| 9 个高风险集成测试 | C | 迁移到 `residue` |
| `infra/provision_test_site.py`、`frappe_app/dsherp_bridge/tests/`、`frappe_app/dsherp_platform/tests/`、`.dockerignore` | D | 测试站与原生测试骨架 |
| `frappe_app/dsherp_bridge/tests/permission_matrix.json`、`tests/test_permission_matrix.py`、`frappe_app/*/tests/test_permission_matrix.py`、`ds_agent_task.json`、`tests/test_audit_immutability.py` | D | G7 矩阵 |
| `ds_configuration_transfer.json/.py`、`patches/v1/expire_transfer_grant.py`、`patches.txt`、`grants.py`、`configuration_transport.py`、`configuration_transfer.py`、`configuration.py`、`configuration_execution.py`、`frappe_app/dsherp_bridge/tests/test_transfer_grant.py`、`tests/test_transfer_grant.py`、`tests/integration/test_transfer_grants.py`、`dsherp/user_data.py`、`frontend/src/ConfigurationBundle.jsx` + dist | E | 交接窗口 |
| `README.md`、`AGENTS.md`、spec、`docs/engineering/quality-gates-evidence.md`、`docs/engineering/deployment-runbook.md` | F | 文档 |

---

## 切片 0：仓库卫生（本机即可闭环）

### Task 0.1: `pytest.ini`、integration 目录隔离与 marker、pytest-timeout

**Files:**
- Create: `pytest.ini`、`tests/test_collection_layout.py`
- Modify: `tests/conftest.py`、`tests/integration/conftest.py`、`requirements.in`、`requirements.lock`、`README.md`

**Interfaces:**
- Produces 约定命令：非集成 `.venv/bin/python -m pytest -q`（默认不碰 `tests/integration`）；集成 `.venv/bin/python -m pytest tests/integration -m integration -q`。
- 方案：根 conftest 的 `pytest_ignore_collect` 按路径忽略（等价于原来手敲的 `--ignore=tests/integration`，成为默认），再加 `integration` marker 用于点名/报表。不能只靠 `addopts = -m "not integration"`：那要求一个会话收集两目录，三对同名文件会让收集直接报错；加 `__init__.py` 又会破坏 `tests/integration/` 里 `from test_platform_identity import platform_client` 这类同目录裸导入。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_collection_layout.py
"""The unit suite and the integration suite are two suites: the default run never touches
tests/integration (it needs the four-Site stack and reuses unit-test file names), and naming
the directory brings all of it in under the `integration` marker."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _collect(*arguments):
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
                            cwd=ROOT, text=True, capture_output=True, timeout=300)
    assert result.returncode == 0, result.stderr[-800:] + result.stdout[-800:]
    return result.stdout


def test_the_default_run_leaves_the_integration_suite_out_and_naming_it_brings_it_in():
    default = _collect()
    assert "tests/integration/" not in default
    assert "test_collection_layout.py" in default
    named = _collect("tests/integration")
    listed = [line for line in named.splitlines() if "::" in line]
    assert listed and all(line.startswith("tests/integration/") for line in listed)
    assert "error" not in named.lower()


def test_every_integration_item_carries_the_marker_and_nothing_else_does():
    total = len(re.findall(r"^tests/integration/.*::", _collect("tests/integration"), re.MULTILINE))
    marked = len(re.findall(r"^tests/integration/.*::", _collect("tests/integration", "-m", "integration"), re.MULTILINE))
    unmarked = re.findall(r"^tests/integration/.*::", _collect("tests/integration", "-m", "not integration"), re.MULTILINE)
    assert total > 0 and marked == total and unmarked == []
    assert "integration" not in _collect("tests/test_collection_layout.py", "-m", "integration")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_collection_layout.py -q`
Expected: 两条失败（默认收集会因同名文件报 `import file mismatch`，或 `tests/integration/` 出现在列表里；marker 未注册）。

- [ ] **Step 3: 实现**

```ini
# pytest.ini
[pytest]
testpaths = tests
# infra/ 与 tests/ 都不是包；这样 `pytest` 与 `python -m pytest` 都能 import infra.*、tests.*
pythonpath = .
markers =
    integration: 需要本机隔离 ERP 四站（docker compose）与 .runtime 夹具的真实链路测试；只在命令行点名 tests/integration 时收集
addopts = --strict-markers
# pytest-timeout：单个用例的安全网（不是性能门）。每夜集成用 --timeout 600 覆盖。
timeout = 300
timeout_method = signal
norecursedirs = .venv node_modules work evals frontend .runtime
```

`tests/conftest.py` 顶部追加：

```python
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parent / "integration"


def pytest_ignore_collect(collection_path, config):
    """tests/integration is its own suite: it needs the four-Site stack, and it reuses unit-test
    file names (test_run_events.py, ...), so the two can never share one session. It is collected
    only when named on the command line: `python -m pytest tests/integration -m integration`."""
    if Path(collection_path).resolve() != INTEGRATION:
        return None
    base = Path(config.invocation_params.dir)
    requested = []
    for argument in config.args:
        path = (base / argument.split("::", 1)[0]).resolve()
        requested.append(path == INTEGRATION or INTEGRATION in path.parents)
    return not any(requested)
```

`tests/integration/conftest.py` 在 import 之后追加：

```python
INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Every item under this directory is an integration test; the marker is what
    `-m integration` selects and what the junit report groups by."""
    for item in items:
        if INTEGRATION_DIR in Path(item.path).resolve().parents:
            item.add_marker(pytest.mark.integration)
```

`requirements.in`（完整）：

```
deepseek-harness-sdk==0.1.1rc1
pytest==8.4.2
pytest-timeout==2.4.0
mcp==1.26.0
httpx==0.28.1
# MCP v1.26.0 upstream lock baseline; newer 2.15 emits unresolved lifespan warnings.
pydantic-settings==2.10.1
# 门禁工具；与运行时共用一份 lock（已知取舍：会一并进入 agent 运行时 venv，惰性无害）。
ruff==0.14.14
```

```sh
uv pip compile requirements.in --generate-hashes -o requirements.lock
git diff --stat requirements.lock        # 只应新增 pytest-timeout、ruff 两个块；其它包版本一个不变，否则停下检查
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
.venv/bin/python -m pytest --version && .venv/bin/ruff --version
```

README「最小验证」改为 `uv venv … && uv pip sync … && .venv/bin/ruff check . && .venv/bin/python -m pytest -q`；「隔离 ERP 验证」的运行命令改为 `.venv/bin/python -m pytest tests/integration -m integration -q`，并加一句"从零拉起见 `python infra/dev_stack.py up --provision`"。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_collection_layout.py -q` → `2 passed`
Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3` → 与基线一致（648 + 2 passed），`tests/integration` 未出现
Run: `.venv/bin/python -m pytest tests/integration -m integration --collect-only -q | tail -1` → `N tests collected`（约 210），无 error

- [ ] **Step 5: 提交**

```bash
git checkout -b plan5/hygiene
git add pytest.ini tests/conftest.py tests/integration/conftest.py tests/test_collection_layout.py requirements.in requirements.lock README.md
git commit -m "test: pytest.ini 与 integration 套件按路径隔离、打 marker，接入 pytest-timeout"
```

### Task 0.2: `ruff.toml` 与 pyflakes 清理

**Files:**
- Create: `ruff.toml`
- Modify: 下表 29 处 + 2 处 noqa

- [ ] **Step 1: 写失败测试**（lint 本身）

```toml
# ruff.toml
# 门禁只用 pyflakes 的 F 系列与 E9（语法/读取错误）。风格规则（E701/E702 等）在本仓库有
# 700+ 处一行多语句，明确不作为门禁；不要在这里加 E/W 风格规则或 mypy。
target-version = "py312"
extend-exclude = [".venv", "node_modules", "work", "evals/runs", "frappe_app/dsherp_bridge/public/dist", ".runtime"]

[lint]
select = ["F", "E9"]

[lint.per-file-ignores]
# pytest 夹具以形参名注入：与同名导入/定义"重复"是框架约定，不是错误。
"tests/**" = ["F811"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/ruff check . --statistics` → `24 F401 / 3 F841 / 2 F821`，`Found 29 errors.`

- [ ] **Step 3: 实现（逐条）**

先自动修安全项（**不要**对 `tests/integration` 的夹具导入用 `--fix`），再逐块看 diff：

```sh
.venv/bin/ruff check --fix --select F401 \
  dsherp infra frappe_app tests/test_admin_cli.py tests/test_business_credentials.py tests/test_business_skills.py \
  tests/test_document_links.py tests/test_session_paging.py tests/test_session_runtime.py tests/test_validation_provisioner.py \
  tests/integration/test_audit_immutability.py tests/integration/test_needs_input.py tests/integration/test_sso_machine_auth.py
git diff
```

手工处理：

| 位置 | 处理 |
|---|---|
| `tests/integration/test_context_mcp_chain.py:12` `from test_context_sessions import created` | 行尾 `# noqa: F401  # pytest 夹具，以形参名注入` |
| `tests/integration/test_context_worker_chain.py:11` `from test_context_sessions import clients,created,API` | 同上 |
| `dsherp/release_compare.py:22` `row_hash` | 保留并标 `# noqa: F401  # 再导出：tests/test_admin_cli.py 以 release_compare.row_hash 使用` |
| `tests/test_restore_drill.py:8` | 去掉 `_tenant_row` |
| `tests/test_provider_circuit.py:56` | 删除函数内未用的 `import dsherp.provider_circuit as circuit` |
| `dsherp/admin.py:1488-1489` | 删除两行函数内未用导入 |
| `dsherp/context_worker.py:20` | 去掉 `IMAGE` |
| `dsherp/restore_drill.py:19` | 去掉 `release_snapshot` |
| `dsherp/user_data.py:17` | 去掉 `import json` |
| `frappe_app/dsherp_bridge/context_execution.py:25` | 去掉 `PROVIDER_FAILURE_ERROR_CLASSES` |
| `infra/supply_chain.py:13` | 去掉 `import sys` |
| `dsherp/backup.py:205` F841 | 删除 `admin_log = …` 行 |
| `infra/initialize_daily_synthetic.py:24` F841 | `state = json.loads(...)` 改 `json.loads(...)`（保留解析＝保留校验） |
| `tests/test_backup_cli.py:684` F841 | `report = _backup(bench)` 改 `_backup(bench)` |
| `infra/seed_identity.py:14` F821 | `kind = seed_input['kind']  # noqa: F821  # seed_input 由 run_identity_seed.py 写在 stdin 脚本首行注入` |
| `tests/test_run_events.py` F821 | 顶部加 `import pytest` |

（以上行号以 2026-09-07 的 main 为准；实施时以 ruff 输出为准逐条核对，每处删除的名字在该文件里确实无其它引用。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/ruff check .` → `All checks passed!`
Run: `.venv/bin/python -m pytest -q 2>&1 | tail -2` → 通过数不变；`test_run_events.py::test_the_vocabulary_is_the_one_the_shipped_runtime_emits` 为 passed
Run: `.venv/bin/python -m pytest tests/integration/test_context_worker_chain.py tests/integration/test_context_mcp_chain.py --collect-only -q | tail -1` → 收集无 error

- [ ] **Step 5: 提交**

```bash
git add ruff.toml dsherp infra frappe_app tests
git commit -m "fix: 接入 ruff（只开 F/E9），清理 24 处未用导入、3 处未用变量、2 处未定义名"
```

### Task 0.3: 迁移守卫接受 CI 给的比较基线

**Files:**
- Modify: `infra/check_doctype_patches.py`、`tests/test_doctype_patch_guard.py`

**Interfaces:**
- Produces: `check_doctype_patches.base_revision(environ=None, runner=subprocess.run, root=ROOT) -> str | None`：`DSHERP_GUARD_BASE` 非空即返回；否则 `git merge-base origin/main HEAD`；都没有返回 `None`。CLI `base` 改为可选，缺省走 `base_revision()`，为 `None` 时 `SystemExit` 提示设置 `DSHERP_GUARD_BASE` 或 fetch `origin/main`。
- Consumes（CI）：PR → `github.event.pull_request.base.sha`；push main → `github.event.before`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_doctype_patch_guard.py` 追加：

```python
def test_the_guard_base_comes_from_the_environment_before_the_merge_base():
    import subprocess
    from infra.check_doctype_patches import base_revision
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "abc123\n", "")

    assert base_revision({"DSHERP_GUARD_BASE": "deadbeef"}, runner=git) == "deadbeef" and calls == []
    assert base_revision({"DSHERP_GUARD_BASE": "  "}, runner=git) == "abc123" and calls[0][:2] == ["git", "merge-base"]
    no_origin = lambda command, **kwargs: subprocess.CompletedProcess(command, 128, "", "fatal: Not a valid object name origin/main")
    assert base_revision({}, runner=no_origin) is None
```

并把现有 `test_the_guard_runs_over_this_branch_and_finds_it_shippable` 改为用 `base_revision()` 取基线，为 `None` 时 `pytest.skip("no DSHERP_GUARD_BASE and no origin/main to compare against")`，其余不变。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_doctype_patch_guard.py -q` → `ImportError: cannot import name 'base_revision'`

- [ ] **Step 3: 实现**

`infra/check_doctype_patches.py`（`_git` 之前加，`main` 改）：

```python
import os


def base_revision(environ=None, runner=subprocess.run, root=ROOT):
    """The revision a change is judged against. CI names it (DSHERP_GUARD_BASE: the PR's base
    or the commit that was pushed over); a developer's checkout falls back to the merge-base
    with origin/main; None when there is nothing to compare with."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get('DSHERP_GUARD_BASE') or '').strip()
    if explicit:
        return explicit
    found = runner(['git', 'merge-base', 'origin/main', 'HEAD'], cwd=root, text=True, capture_output=True, timeout=60)
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


def main(argv=None):
    parser = argparse.ArgumentParser(description='DocType 变更必须带迁移路径')
    parser.add_argument('base', nargs='?', default=None, help='缺省：$DSHERP_GUARD_BASE，否则 merge-base origin/main HEAD')
    parser.add_argument('head', nargs='?', default='HEAD')
    arguments = parser.parse_args(argv)
    base = arguments.base or base_revision()
    if not base:
        raise SystemExit('没有可比较的基线：设置 DSHERP_GUARD_BASE，或先 git fetch origin main')
    span = f'{base}..{arguments.head}'
    ...  # 其余不变
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_doctype_patch_guard.py -q` → `11 passed`
Run: `DSHERP_GUARD_BASE=$(git rev-parse origin/main) .venv/bin/python -m infra.check_doctype_patches; echo $?` → `0`

- [ ] **Step 5: 提交**

```bash
git add infra/check_doctype_patches.py tests/test_doctype_patch_guard.py
git commit -m "feat: 迁移守卫的比较基线可由 DSHERP_GUARD_BASE 指定，CI 传 PR base 或 push 前提交"
```

### 切片 0 结束门

```sh
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/integration -m integration --collect-only -q | tail -1
(cd frontend && npm test)                    # 22 文件 201 passed
node --test runtime/*.test.cjs               # 10 passed
```

真实路径：本机开发栈仍在运行，停常驻 worker 后跑一次 `.venv/bin/python -m pytest tests/integration -m integration -q --timeout 600 --junitxml work/junit-integration-local.xml`，确认 marker/timeout 改动没有改变集成结果（约 210 passed），把 junit 里 `time` 最长的 20 条记下来作为超时决策的第一份数据。**检查点：PR 合入 main。**

---

## 切片 A：PR 门

### Task A.1: `.github/workflows/ci.yml`

**Files:** Create `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: 切片 0 的命令；`frontend/package.json` 的 `test`/`build`；仓库根 `.nvmrc`。
- Produces: 三个检查名 `python`、`frontend`、`runtime`；工件 `junit-python`。

- [ ] **Step 1–2:** 没有本机单测；"失败测试"是 Step 4 里故意让它红一次。

- [ ] **Step 3: 实现**

```yaml
# .github/workflows/ci.yml
# PR 门禁：学过的失败不能悄悄回来。三个 job 互不依赖，任何一个红都挡合入（分支保护）。
name: ci

on:
  pull_request:
  push:
    branches: [main]

# PR 上新推送取消旧运行；main 上不取消——迁移守卫的基线是"上一次 push 的 HEAD"，
# 取消 main 上的运行会留下没检查过的区间。
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@<sha of actions/checkout v4>  # v4.2.2
        with:
          fetch-depth: 0   # 迁移守卫要能 diff 到 base

      - uses: astral-sh/setup-uv@<sha of astral-sh/setup-uv v6>  # v6.x
        with:
          version: "0.9.27"
          enable-cache: true
          cache-dependency-glob: requirements.lock

      - name: 与 README 完全相同的环境
        run: |
          uv python install 3.12.11
          uv venv --python 3.12.11 .venv
          uv pip sync --python .venv/bin/python --require-hashes requirements.lock

      - name: ruff（只有 F/E9，见 ruff.toml）
        run: .venv/bin/ruff check .

      - name: 迁移守卫的比较基线
        run: |
          set -eu
          if [ "${{ github.event_name }}" = pull_request ]; then
            base='${{ github.event.pull_request.base.sha }}'
          else
            base='${{ github.event.before }}'
          fi
          if ! git cat-file -e "${base}^{commit}" 2>/dev/null; then
            echo "基线 ${base} 不在历史里（首个提交或强推），改用 HEAD~1"
            base=$(git rev-parse HEAD~1)
          fi
          echo "DSHERP_GUARD_BASE=${base}" >> "$GITHUB_ENV"

      - name: 非集成 pytest（含迁移守卫的真实调用）
        run: |
          mkdir -p work
          .venv/bin/python -m pytest -q --junitxml work/junit-unit.xml --durations=25

      - uses: actions/upload-artifact@<sha of actions/upload-artifact v4>  # v4.x
        if: always()
        with:
          name: junit-python
          path: work/junit-unit.xml
          retention-days: 30

  frontend:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@<sha of actions/checkout v4>  # v4.2.2
      - uses: actions/setup-node@<sha of actions/setup-node v4>  # v4.x
        with:
          node-version-file: .nvmrc
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm test
      - run: npm run build
      - name: 提交的 dist 必须与源码一致（构建是确定性的，2026-09-05 已逐字节核实）
        working-directory: ${{ github.workspace }}
        run: git diff --exit-code --stat -- frappe_app/dsherp_bridge/public/dist

  runtime:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<sha of actions/checkout v4>  # v4.2.2
      - uses: actions/setup-node@<sha of actions/setup-node v4>  # v4.x
        with:
          node-version-file: .nvmrc
      - run: node --test runtime/*.test.cjs
```

注意：venv 必须建在 `.venv`（`tests/test_doctype_patch_guard.py` 与 `bin/dsherp-admin` 硬编码）；四个会拉起真实 DSH runtime 子进程的单测依赖 lock 里的 Linux wheel；若 setup-node 清单没有 26.7.0，改 `.nvmrc` 与 `engines` 到存在的 26.x 并本机重验 dist 确定性，不在工作流里另写版本。

- [ ] **Step 4: 运行确认通过（用户检查点：push 分支、开 PR）**

1. `git push -u origin plan5/ci` 并开 PR；观察三个检查绿。
2. **让它红一次**：再推一个临时提交，改 `frontend/src/` 任一可见字符串但不重建 dist → `frontend` 必须红在 `git diff --exit-code`；`git revert` 后再推，三绿。两次 run URL 记入证据。
3. 下载 `junit-python`，确认 skipped 只剩 `test_run_events.py` 那条（Linux 有 runtime wheel 则为 0）。

- [ ] **Step 5: 提交**

```bash
git checkout -b plan5/ci
git add .github/workflows/ci.yml
git commit -m "feat: PR 门禁 ci.yml——ruff、非集成 pytest 与迁移守卫、vitest 与 dist 一致性、Node runtime 测试"
```

### Task A.2: `main` 分支保护（用户检查点：仓库设置）

前置：Task A.1 的 PR 已合入且 main 上的 `ci` 为绿。用户明确同意后执行：

```sh
gh api -X PUT repos/lize-agent-engineering/dsherp/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["python", "frontend", "runtime"]},
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
gh api repos/lize-agent-engineering/dsherp/branches/main/protection --jq .required_status_checks.contexts
```

Expected: `["python","frontend","runtime"]`；再开一个空改动 PR，合并按钮在检查完成前禁用。记录到证据文档。

---

## 切片 B：从零开通驱动、每夜与每周工作流、本机重建

### Task B.1: compose 增加 `platform-provision` 与 `beta-provision`

**Files:** Modify `infra/compose.validation.yml`

**Interfaces:**
- Produces: `docker compose -f infra/compose.validation.yml --profile control run --rm --no-deps platform-provision|beta-provision`。
- Consumes: `infra/provision_identity.py` 读 `/run/dsherp-control/db_root_password` 与 `/run/dsherp-control/<kind>_admin_password`——用 compose 密钥的绝对 `target:` 恰好放这两个文件，不整目录挂载。

- [ ] **Step 1: 现状** `docker compose -f infra/compose.validation.yml --profile control config --services | sort` → 无这两个服务。

- [ ] **Step 2: 实现**

在 `validation-provision` 之后插入：

```yaml
  # provision_identity.py 只读 /run/dsherp-control/ 下的两个文件；用密钥的绝对 target 精确给这两个，
  # 不整目录挂载（那会把 backup/daily/admin 五个口令一并交给一次性容器）。
  platform-provision:
    profiles: [control]
    image: frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
    pull_policy: never
    mem_limit: 640m
    memswap_limit: 640m
    cpus: 0.5
    restart: "no"
    entrypoint: []
    command: ["/home/frappe/frappe-bench/env/bin/python", "/opt/provision_identity.py", "platform"]
    environment: {PYTHONPATH: /opt/dsherp-frappe}
    secrets:
      - source: db_root_password
        target: /run/dsherp-control/db_root_password
      - source: platform_admin_password
        target: /run/dsherp-control/platform_admin_password
    volumes:
      - v16-platform-sites:/home/frappe/frappe-bench/sites
      - v16-platform-logs:/home/frappe/frappe-bench/logs
      - ../frappe_app:/opt/dsherp-frappe:ro
      - ./provision_identity.py:/opt/provision_identity.py:ro
    depends_on: [db, redis]
    networks: [validation]
    logging: *logging

  beta-provision:
    profiles: [control]
    image: frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
    pull_policy: never
    mem_limit: 640m
    memswap_limit: 640m
    cpus: 0.5
    restart: "no"
    entrypoint: []
    command: ["/home/frappe/frappe-bench/env/bin/python", "/opt/provision_identity.py", "beta"]
    environment: {PYTHONPATH: /opt/dsherp-frappe}
    secrets:
      - source: db_root_password
        target: /run/dsherp-control/db_root_password
      - source: beta_admin_password
        target: /run/dsherp-control/beta_admin_password
    volumes:
      - v16-beta-sites:/home/frappe/frappe-bench/sites
      - v16-beta-logs:/home/frappe/frappe-bench/logs
      - ../frappe_app:/opt/dsherp-frappe:ro
      - ./provision_identity.py:/opt/provision_identity.py:ro
    depends_on: [db, redis]
    networks: [validation]
    logging: *logging
```

文件末尾 `secrets:` 追加：

```yaml
  platform_admin_password:
    file: ../.runtime/control/platform_admin_password
  beta_admin_password:
    file: ../.runtime/control/beta_admin_password
```

- [ ] **Step 3: 运行确认通过**

Run: `docker compose -f infra/compose.validation.yml --profile control config --services | sort | tr '\n' ' '` → 含 `beta-provision platform-provision`，无警告
Run: `docker compose -f infra/compose.validation.yml --profile control config | grep -A3 'target: /run/dsherp-control'` → 两处 target 各带 source（若本机 compose 拒绝绝对 target，退回 `../.runtime/control:/run/dsherp-control:ro` 整目录只读挂载并在证据记原因）
Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py -q` → 通过

- [ ] **Step 4: 提交**

```bash
git checkout -b plan5/dev-stack
git add infra/compose.validation.yml
git commit -m "feat: compose 增加 platform-provision/beta-provision 控制面服务，只挂它们各自需要的两个密钥"
```

### Task B.2: `infra/dev_stack.py` 骨架——步骤表、台账、探针一致性

**Files:** Create `infra/dev_stack.py`、`tests/test_dev_stack.py`

**Interfaces:**
- Produces:
  - `Stack(resolved, root=ROOT, runner=subprocess.run, *, resolve=socket.getaddrinfo, http=None, sleep=time.sleep, clock=time.monotonic)`，属性 `runtime`、`ledger_path`；方法 `compose(*args)`、`run(command, *, timeout, stdin)`、`succeeds(command)`、`host_script(name, *args)`、`compose_run(service)`、`bench(kind)`、`site_present(kind)`、`db_probe(kind, body)`、`volume_exists(name)`、`wait_for(what, check, *, timeout, interval)`、`load_ledger()`、`record(step)`
  - `Step(name, run, probe=None, produces=(), rerun_safe=False, inspect='')`；`STEPS`（27 步，对应 31 步映射的 3–29）
  - `provision(stack, *, skip=()) -> {'ran': [...], 'skipped': [...]}`；台账 `.runtime/dev-stack.json` = `{"format": 1, "steps": {name: {"finished_at": iso, "produces": [...]}}}`
  - 探针返回 `list[bool]`：全真＝结果在，全假＝结果不在，混合＝半成品 → `Fault`
- Consumes: `admin.Bench(resolved, 'tenant', root, runner, service=…)` 的 `run/python/site_exists`、`admin.Fault`、`admin.runtime_dir`、`admin.ensure_secrets`、`admin.CONTROL_SECRETS_BY_ENV`、`admin.BENCH_PYTHON`、`deploy_env.settings/env_file/BASE_IMAGE`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_dev_stack.py
"""One command builds the four-Site development stack from nothing and one takes it down.
The provisioning scripts are fail-closed; the driver above them must know what is done,
must check the stack agrees, and must stop - not re-run - when it does not."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dsherp import deploy_env
from infra import dev_stack


BENCH_PYTHON = "/home/frappe/frappe-bench/env/bin/python"
REPO = Path(__file__).resolve().parents[1]


def _done(command, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def _private(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


class FakeHost:
    """Stands in for subprocess.run. Sites, users, settings and profile files appear only when
    the step that makes them has run, so ordering and skipping are observable."""

    def __init__(self, runtime, *, images=True, secrets_readable=True, db_up=True):
        self.runtime = Path(runtime)
        self.calls = []
        self.images = images
        self.secrets_readable = secrets_readable
        self.db_up = db_up
        self.sites = set()
        self.facts = set()
        self.volumes = set()
        self.backup_present = False

    def _effect(self, script, argv):
        r = self.runtime
        if script == "run_validation_provision.py":
            self.sites.add("dsherp-validation.localhost")
            users = {"reader": {"user": "dsherp-reader@example.invalid", "api_key": "rk", "api_secret": "READER-SECRET-VALUE"},
                     "denied": {"user": "dsherp-denied@example.invalid", "api_key": "dk", "api_secret": "DENIED-SECRET-VALUE"}}
            _private(r / "erp-users.json", users); _private(r / "erp-reader.json", users["reader"]); _private(r / "erp-denied.json", users["denied"])
        elif script == "provision_validation_company.py":
            self.facts.add(f"{'dsherp-validation.localhost' if argv[-1] == 'alpha' else 'dsherp-beta.localhost'}:setup")
        elif script == "run_identity_seed.py":
            kind = argv[-1]
            user = "member@example.invalid" if kind == "platform" else "beta-reader@example.invalid"
            self.facts.add(f"dsherp-{kind}.localhost:User:{user}")
            _private(r / f"{kind}-users.json", {"operator": {"password": f"{kind.upper()}-PASSWORD-VALUE"}})
        elif script == "setup_platform.py":
            self.facts.add("dsherp-platform.localhost:setup")
        elif script == "bind_identity.py":
            self.facts.update({"dsherp-platform.localhost:DS Enterprise:alpha", "dsherp-platform.localhost:DS Enterprise:beta"})
        elif script == "provision_desk_oauth.py":
            self.facts.add("dsherp-platform.localhost:OAuth Client:DSHERP alpha Desk")
        elif script == "provision_daily_agent.py":
            self.facts.add("dsherp-platform.localhost:DS Enterprise:daily")
            _private(r / "context-worker-daily.json", {"api_secret": "DAILY-RUNTIME-SECRET"})
        elif script == "initialize_daily_synthetic.py":
            self.facts.add("dsherp-daily.localhost:User:daily-operator@example.invalid")
        elif script == "provision_context_worker.py":
            self.facts.add("dsherp-validation.localhost:conf:dsherp_runtime_user")
            _private(r / "context-worker.json", {"api_secret": "ALPHA-RUNTIME-SECRET"})
        elif script == "provision_context_writer.py":
            self.facts.add("dsherp-validation.localhost:User:dsherp-writer@example.invalid")
            _private(r / "context-writer.json", {"password": "WRITER-PASSWORD-VALUE"})
        elif script == "merge_context_worker_profiles.py":
            _private(r / "context-worker-sites.json", {"slots": 3, "sites": []})
        elif script == "provision_preview_operator.py":
            self.facts.add("dsherp-beta.localhost:User:dsherp-preview@example.invalid")
            _private(r / "preview-operator.json", {"password": "PREVIEW-PASSWORD-VALUE"})
        elif script == "provision_configuration_preview.py":
            self.facts.add("dsherp-validation.localhost:conf:dsherp_configuration_preview")
            _private(r / "configuration-preview.json", {"secret": "PAIR-SECRET-VALUE"})
        elif script in ("provision_alpha_doctype_policies.py", "provision_manufacturing_fixture.py"):
            pass
        else:
            raise AssertionError("unexpected host script " + script)

    def _probe(self, site, body):
        if "is_setup_complete" in body:
            return f"{site}:setup" in self.facts
        for doctype in ("User", "DS Enterprise"):
            match = re.search(r"exists\('%s', '([^']+)'" % doctype, body)
            if match:
                return f"{site}:{doctype}:{match.group(1)}" in self.facts
        match = re.search(r"'OAuth Client', \{'app_name': '([^']+)'", body)
        if match:
            return f"{site}:OAuth Client:{match.group(1)}" in self.facts
        match = re.search(r"frappe\.conf\.get\('([^']+)'\)", body)
        if match:
            return f"{site}:conf:{match.group(1)}" in self.facts
        raise AssertionError("unexpected probe:\n" + body)

    def __call__(self, command, **kwargs):
        argv = list(command)
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return _done(argv, 0 if self.images else 1)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return _done(argv, 0 if argv[3] in self.volumes else 1)
        if argv[:3] == ["docker", "volume", "rm"]:
            self.volumes.discard(argv[3]); return _done(argv)
        if argv[0] == "sh" and argv[1].endswith("prepare_agent_runtime.sh"):
            self.volumes.add(dev_stack.AGENT_RUNTIME_VOLUME); return _done(argv)
        if argv[0] == "sh":
            return _done(argv)
        if argv[0] == sys.executable and "/infra/" in argv[1]:
            self._effect(Path(argv[1]).name, argv); return _done(argv, stdout="ok\n")
        assert argv[:2] == ["docker", "compose"], argv
        if "run" in argv:
            tail = argv[argv.index("run") + 1:]
            if "--entrypoint" in tail:
                return _done(argv, 0 if self.secrets_readable else 1, "readable\n" if self.secrets_readable else "", "Permission denied")
            self.sites.add({"platform-provision": "dsherp-platform.localhost", "beta-provision": "dsherp-beta.localhost",
                            "daily-provision": "dsherp-daily.localhost"}[tail[-1]])
            return _done(argv)
        if "down" in argv:
            self.sites.clear(); self.facts.clear(); return _done(argv)
        if "up" in argv:
            return _done(argv)
        if "ps" in argv:
            return _done(argv, stdout="backend\ndb\nredis\n")
        index = argv.index("exec")
        service, rest = argv[index + 2], argv[index + 3:]
        joined = " ".join(rest)
        if "mariadb-admin" in joined:
            return _done(argv, 0 if self.db_up else 1)
        if "redis-cli" in joined:
            return _done(argv, stdout="PONG\n")
        if "socket.create_connection" in joined:
            return _done(argv)
        if "site_config.json" in joined and rest[:2] == ["sh", "-c"]:
            site = re.search(r"sites/([^/]+)/site_config.json", joined).group(1)
            return _done(argv, stdout="present\n" if site in self.sites else "absent\n")
        if "private/backups" in joined:
            return _done(argv, stdout="yes\n" if self.backup_present else "no\n")
        if rest[:2] == ["bench", "--site"] and "backup" in rest:
            self.backup_present = True; return _done(argv)
        if rest[:2] == ["bench", "--site"] and "clear-cache" in rest:
            return _done(argv)
        if rest == [BENCH_PYTHON, "-"]:
            body = kwargs["input"]
            site = re.search(r"frappe\.init\(site='([^']+)'", body).group(1)
            return _done(argv, stdout=json.dumps(self._probe(site, body)) + "\n")
        raise AssertionError("unexpected command: " + " ".join(argv))


class _Response:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *exc): return False


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.delenv("DSHERP_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DSHERP_SECRETS_DIR", raising=False)
    (tmp_path / "infra" / "env").mkdir(parents=True)
    shutil.copy(REPO / "infra" / "env" / "dev.env", tmp_path / "infra" / "env" / "dev.env")
    resolved = deploy_env.settings({"DSHERP_ENV": "dev", "DSHERP_AGENT_UID": "1000", "DSHERP_AGENT_GID": "1000"}, root=tmp_path)
    host = FakeHost(tmp_path / ".runtime")
    clock = {"now": 0.0}
    built = dev_stack.Stack(resolved, root=tmp_path, runner=host,
                            resolve=lambda name, port: [(None, None, None, None, ("127.0.0.1", 0))],
                            http=lambda request, timeout=10: _Response(200),
                            sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
                            clock=lambda: clock["now"])
    built.host = host
    return built


def _mutations(host):
    """The calls that change the stack: host scripts and control containers, in order."""
    out = []
    for argv in host.calls:
        if argv[0] == sys.executable:
            out.append(Path(argv[1]).name + "".join(" " + a for a in argv[2:]))
        elif argv[0] == "sh":
            out.append(Path(argv[1]).name)
        elif "run" in argv and "--entrypoint" not in argv:
            out.append("compose run " + argv[-1])
        elif "exec" in argv and ("backup" in argv or "clear-cache" in argv):
            out.append(" ".join(argv[argv.index("exec") + 3:]))
    return out


EXPECTED_ORDER = [
    "prepare_agent_runtime.sh", "prepare_dev_backup_volumes.sh",
    "run_validation_provision.py", "provision_validation_company.py --target alpha",
    "compose run platform-provision", "run_identity_seed.py platform", "setup_platform.py",
    "compose run beta-provision", "provision_validation_company.py --target beta", "run_identity_seed.py beta",
    "bind_identity.py", "provision_desk_oauth.py",
    "compose run daily-provision", "provision_daily_agent.py", "initialize_daily_synthetic.py",
    "provision_context_worker.py", "provision_context_writer.py", "merge_context_worker_profiles.py",
    "provision_preview_operator.py", "provision_configuration_preview.py",
    "provision_alpha_doctype_policies.py --site dsherp-validation.localhost --policy-set manufacturing",
    "provision_alpha_doctype_policies.py --site dsherp-daily.localhost --policy-set manufacturing",
    "provision_alpha_doctype_policies.py --site dsherp-beta.localhost",
    "provision_manufacturing_fixture.py --site dsherp-validation.localhost",
    "provision_manufacturing_fixture.py --site dsherp-daily.localhost",
    "bench --site dsherp-validation.localhost clear-cache", "bench --site dsherp-daily.localhost clear-cache",
    "bench --site dsherp-beta.localhost clear-cache",
    "bench --site dsherp-daily.localhost backup --with-files",
]


def test_a_fresh_host_runs_every_step_in_the_mapped_order_and_records_each(stack):
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == EXPECTED_ORDER
    assert report["skipped"] == [] and report["ran"] == [step.name for step in dev_stack.STEPS]
    ledger = json.loads(stack.ledger_path.read_text())
    assert ledger["format"] == 1 and set(ledger["steps"]) == {step.name for step in dev_stack.STEPS}
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", row["finished_at"]) for row in ledger["steps"].values())
    assert ledger["steps"]["validation-site"]["produces"] == ["erp-users.json", "erp-reader.json", "erp-denied.json"]


def test_a_second_run_changes_nothing_and_skips_every_step(stack):
    dev_stack.provision(stack)
    stack.host.calls.clear()
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == [] and report["ran"] == [] and len(report["skipped"]) == len(dev_stack.STEPS)


def test_a_recorded_step_whose_result_vanished_stops_and_names_the_step(stack):
    dev_stack.provision(stack)
    (stack.runtime / "erp-users.json").unlink()   # the profile is gone but the Site is still there
    stack.host.calls.clear()
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert "validation-site" in str(error.value) and "down --volumes" in str(error.value)
    assert "run_validation_provision.py" not in " ".join(_mutations(stack.host))


def test_a_result_that_exists_without_a_record_is_never_reprovisioned(stack):
    stack.host.sites.add("dsherp-validation.localhost")            # a Site nobody recorded
    for name in ("erp-users.json", "erp-reader.json", "erp-denied.json"):
        _private(stack.runtime / name, {})
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert "validation-site" in str(error.value)
    assert not any(m.startswith("run_validation_provision") for m in _mutations(stack.host))


def test_rerun_safe_steps_are_simply_rerun_when_ledger_and_stack_disagree(stack):
    dev_stack.provision(stack)
    (stack.runtime / "context-worker-sites.json").unlink()
    stack.host.calls.clear()
    report = dev_stack.provision(stack)
    assert _mutations(stack.host) == ["merge_context_worker_profiles.py"] and report["ran"] == ["worker-profile"]


def test_skipping_the_runtime_volume_leaves_it_out_of_the_run_and_the_ledger(stack):
    dev_stack.provision(stack, skip=("agent-runtime-volume",))
    assert "prepare_agent_runtime.sh" not in _mutations(stack.host)
    assert "agent-runtime-volume" not in json.loads(stack.ledger_path.read_text())["steps"]


def test_a_corrupt_ledger_is_reported_not_rebuilt(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True)
    stack.ledger_path.write_text("{not json")
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.provision(stack)
    assert str(stack.ledger_path) in str(error.value) and not stack.host.calls
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q` → `ModuleNotFoundError: No module named 'infra.dev_stack'`

- [ ] **Step 3: 实现**

```python
#!/usr/bin/env python3
# infra/dev_stack.py
"""从零拉起四站开发栈的唯一入口；也是拆掉它的唯一入口。

infra/ 下的开通脚本各自 fail-closed 且不可续跑：发现站点、用户或凭据文件已存在就停下，不猜。
这个驱动是它们上面的"可续跑层"：把做完的步骤记进台账（.runtime/dev-stack.json），跳过一步之前
先向栈本身核实该步的结果确实在（探针）。台账与探针必须一致；不一致就停下，说出是哪一步、去查
什么——盲目重跑一个 fail-closed 脚本，正是这些脚本被写成拒绝的事。

台账里没有任何密钥值：只有步骤名、时间戳和该步产出的凭据文件名。
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from dsherp import admin, deploy_env


ROOT = deploy_env.ROOT
COMPOSE_FILE = 'infra/compose.validation.yml'
# 与 compose 里三处 image: 完全一致。这里只核对存在，从不拉取（pull_policy: never）。
IMAGES = (
    deploy_env.BASE_IMAGE,
    'mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3',
    'redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015',
)
AGENT_RUNTIME_VOLUME = 'dsherp-v16-agent-runtime'
HOSTS = ('preview.localhost', 'daily.localhost', 'platform.localhost', 'dsherp-validation.localhost')
HOSTS_LINE = '127.0.0.1 ' + ' '.join(HOSTS)
# compose 的 secrets: 段引用的五个，加上 provision_identity.py 从 /run/dsherp-control 读的两个。
CONTROL_SECRETS = admin.CONTROL_SECRETS_BY_ENV['dev'] + ('platform_admin_password', 'beta_admin_password')
LEDGER_NAME = 'dev-stack.json'
LEDGER_FORMAT = 1
WORKER_PID = 'agent-worker.pid'
WORKER_LABEL = 'com.dsherp.agent-worker-v16'
BENCH_PYTHON = admin.BENCH_PYTHON
SITES = {'alpha': ('backend', 'dsherp-validation.localhost'),
         'daily': ('backend', 'dsherp-daily.localhost'),
         'platform': ('platform-backend', 'dsherp-platform.localhost'),
         'beta': ('beta-backend', 'dsherp-beta.localhost')}
BACKENDS = ('backend', 'platform-backend', 'beta-backend')
PINGS = (('alpha', 'http://127.0.0.1:18082/api/method/ping', 'localhost'),
         ('platform', 'http://127.0.0.1:18083/api/method/ping', 'platform.localhost'),
         ('beta', 'http://127.0.0.1:18085/api/method/ping', 'preview.localhost'),
         ('daily', 'http://127.0.0.1:18086/api/method/ping', 'daily.localhost'))
# 口令永远不上 argv：mariadb-admin 从环境变量读，值来自容器里的密钥文件。
DB_PING = 'MYSQL_PWD=$(cat /run/secrets/db_root_password) mariadb-admin ping -h127.0.0.1 -uroot'
REDIS_PING = 'redis-cli ping | grep -q PONG'
PORT_OPEN = "import socket;socket.create_connection(('127.0.0.1',8000),timeout=2).close()"
SECRETS_READABLE = 'cat /run/secrets/db_root_password /run/secrets/validation_admin_password >/dev/null && echo readable'
BACKUP_PRESENT = ('cd /home/frappe/frappe-bench/sites/dsherp-daily.localhost/private/backups 2>/dev/null && '
                  'ls *-database.sql.gz *-site_config_backup.json *-files.tar *-private-files.tar >/dev/null 2>&1 '
                  '&& echo yes || echo no')
SECRET_KEYS = ('api_secret', 'password', 'client_secret', 'secret')
Fault = admin.Fault


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


class Stack:
    """Where the repository is, how to run a process, and the benches of the four Sites."""

    def __init__(self, resolved, root=ROOT, runner=subprocess.run, *, resolve=socket.getaddrinfo,
                 http=None, sleep=time.sleep, clock=time.monotonic):
        if resolved['env'] != 'dev':
            raise Fault('dev_stack 只操作 DSHERP_ENV=dev 的隔离栈')
        self.resolved = resolved
        self.root = Path(root)
        self.runner = runner
        self.resolve = resolve
        self.http = http or build_opener(ProxyHandler({})).open
        self.sleep = sleep
        self.clock = clock
        self.runtime = admin.runtime_dir(resolved, self.root)

    # ---- processes -------------------------------------------------------------------
    def compose(self, *arguments):
        command = ['docker', 'compose', '-p', self.resolved['project']]
        env_file = deploy_env.env_file('dev', self.root)
        if env_file.exists():
            command += ['--env-file', str(env_file)]
        return command + ['-f', str(self.root / COMPOSE_FILE), *arguments]

    def run(self, command, *, timeout=600, stdin=None):
        io = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
        result = self.runner(command, cwd=str(self.root), text=True, capture_output=True, timeout=timeout, **io)
        if result.returncode:
            tail = '\n'.join(((result.stderr or '') + '\n' + (result.stdout or '')).strip().splitlines()[-8:])
            raise Fault(f'命令失败：{" ".join(command[:5])} …\n{tail}')
        return result.stdout or ''

    def succeeds(self, command, *, timeout=120):
        result = self.runner(command, cwd=str(self.root), text=True, capture_output=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
        return result.returncode == 0

    def host_script(self, name, *arguments):
        # 以文件路径而不是 -m 运行：provision_context_writer.py 靠 sys.path[0] == infra/ 导入同目录模块。
        return self.run([sys.executable, str(self.root / 'infra' / name), *arguments], timeout=1800)

    def compose_run(self, service):
        return self.run(self.compose('--profile', 'control', 'run', '--rm', '--no-deps', service), timeout=1800)

    # ---- the four Sites ----------------------------------------------------------------
    def bench(self, kind):
        service, _site = SITES[kind]
        return admin.Bench(self.resolved, 'tenant', self.root, self.runner, service=service)

    def site_present(self, kind):
        return self.bench(kind).site_exists(SITES[kind][1])

    def db_probe(self, kind, body):
        out = self.bench(kind).python(SITES[kind][1], body, timeout=120)
        return json.loads(out.strip().splitlines()[-1])

    def volume_exists(self, name):
        return self.succeeds(['docker', 'volume', 'inspect', name])

    def wait_for(self, what, check, *, timeout, interval=5):
        started = self.clock()
        while True:
            if check():
                return
            if self.clock() - started > timeout:
                raise Fault(f'等待 {what} 超过 {timeout} 秒；看 docker compose -f {COMPOSE_FILE} logs')
            self.sleep(interval)

    # ---- ledger ------------------------------------------------------------------------
    @property
    def ledger_path(self):
        return self.runtime / LEDGER_NAME

    def load_ledger(self):
        if not self.ledger_path.exists():
            return {'format': LEDGER_FORMAT, 'steps': {}}
        try:
            data = json.loads(self.ledger_path.read_text())
        except ValueError as error:
            raise Fault(f'台账损坏，不要重建，先看 {self.ledger_path}：{error}')
        if data.get('format') != LEDGER_FORMAT or not isinstance(data.get('steps'), dict):
            raise Fault(f'台账格式不是 {LEDGER_FORMAT}，先看 {self.ledger_path}')
        return data

    def record(self, step):
        ledger = self.load_ledger()
        ledger['steps'][step.name] = {'finished_at': now_iso(), 'produces': list(step.produces)}
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.ledger_path.with_name(f'.{LEDGER_NAME}.{os.getpid()}')
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
        os.replace(temporary, self.ledger_path)


class Step:
    def __init__(self, name, run, probe=None, produces=(), rerun_safe=False, inspect=''):
        self.name, self.run, self.probe = name, run, probe
        self.produces, self.rerun_safe, self.inspect = tuple(produces), rerun_safe, inspect


# ---- probes: each returns a list of booleans (one per piece of evidence) -----------------
def _file(name):
    return lambda stack: [(stack.runtime / name).is_file()]


def _site(kind):
    return lambda stack: [stack.site_present(kind)]


def _db(kind, body):
    # A question to a Site that does not exist is not "no": the Site itself is the first evidence.
    def probe(stack):
        if not stack.site_present(kind):
            return [False]
        return [stack.db_probe(kind, body)]
    return probe


def _both(*probes):
    return lambda stack: [flag for probe in probes for flag in probe(stack)]


def _volume(name):
    return lambda stack: [stack.volume_exists(name)]


def _backup(stack):
    return ['yes' in stack.bench('daily').run('sh', '-c', BACKUP_PRESENT, timeout=60)]


SETUP_COMPLETE = "print(json.dumps(bool(frappe.is_setup_complete())))"


def USER(email):
    return f"print(json.dumps(bool(frappe.db.exists('User', '{email}'))))"


def ENTERPRISE(name):
    return f"print(json.dumps(bool(frappe.db.exists('DS Enterprise', '{name}'))))"


def OAUTH(app_name):
    return f"print(json.dumps(bool(frappe.db.exists('OAuth Client', {{'app_name': '{app_name}'}}))))"


def CONF(key):
    return f"print(json.dumps(bool(frappe.conf.get('{key}'))))"


def _clear_caches(stack):
    for kind in ('alpha', 'daily', 'beta'):
        stack.bench(kind).run('bench', '--site', SITES[kind][1], 'clear-cache', timeout=120)


STEPS = [
    Step('agent-runtime-volume', lambda s: s.run(['sh', str(s.root / 'infra/prepare_agent_runtime.sh')], timeout=1800),
         probe=_volume(AGENT_RUNTIME_VOLUME), rerun_safe=True, inspect=f'docker volume inspect {AGENT_RUNTIME_VOLUME}'),
    Step('backup-volumes', lambda s: s.run(['sh', str(s.root / 'infra/prepare_dev_backup_volumes.sh')], timeout=120),
         rerun_safe=True),
    Step('validation-site', lambda s: s.host_script('run_validation_provision.py'),
         probe=_both(_file('erp-users.json'), _site('alpha')), produces=('erp-users.json', 'erp-reader.json', 'erp-denied.json'),
         inspect='docker compose exec backend ls /home/frappe/frappe-bench/sites 与 .runtime/erp-*.json'),
    Step('alpha-company', lambda s: s.host_script('provision_validation_company.py', '--target', 'alpha'),
         probe=_db('alpha', SETUP_COMPLETE), inspect='alpha 站 frappe.is_setup_complete()'),
    Step('platform-site', lambda s: s.compose_run('platform-provision'), probe=_site('platform'),
         inspect='docker compose exec platform-backend ls /home/frappe/frappe-bench/sites'),
    Step('platform-users', lambda s: s.host_script('run_identity_seed.py', 'platform'),
         probe=_both(_file('platform-users.json'), _db('platform', USER('member@example.invalid'))),
         produces=('platform-users.json',), inspect='平台站 User member@example.invalid 与 .runtime/platform-users.json'),
    Step('platform-setup', lambda s: s.host_script('setup_platform.py'), probe=_db('platform', SETUP_COMPLETE),
         inspect='平台站 frappe.is_setup_complete()'),
    Step('beta-site', lambda s: s.compose_run('beta-provision'), probe=_site('beta'),
         inspect='docker compose exec beta-backend ls /home/frappe/frappe-bench/sites'),
    Step('beta-company', lambda s: s.host_script('provision_validation_company.py', '--target', 'beta'),
         probe=_db('beta', SETUP_COMPLETE), inspect='beta 站 frappe.is_setup_complete()'),
    Step('beta-users', lambda s: s.host_script('run_identity_seed.py', 'beta'),
         probe=_both(_file('beta-users.json'), _db('beta', USER('beta-reader@example.invalid'))),
         produces=('beta-users.json',), inspect='beta 站 User beta-reader@example.invalid 与 .runtime/beta-users.json'),
    Step('bind-identity', lambda s: s.host_script('bind_identity.py'), probe=_db('platform', ENTERPRISE('alpha')),
         inspect='平台站 DS Enterprise alpha/beta'),
    Step('desk-oauth', lambda s: s.host_script('provision_desk_oauth.py'), probe=_db('platform', OAUTH('DSHERP alpha Desk')),
         inspect='平台站 OAuth Client「DSHERP alpha Desk」与 alpha/beta 的 Social Login Key'),
    Step('daily-site', lambda s: s.compose_run('daily-provision'), probe=_site('daily'),
         inspect='docker compose exec backend ls /home/frappe/frappe-bench/sites'),
    Step('daily-agent', lambda s: s.host_script('provision_daily_agent.py'),
         probe=_both(_file('context-worker-daily.json'), _db('platform', ENTERPRISE('daily'))),
         produces=('context-worker-daily.json',), inspect='平台站 DS Enterprise daily 与 .runtime/context-worker-daily.json'),
    Step('daily-synthetic', lambda s: s.host_script('initialize_daily_synthetic.py'),
         probe=_db('daily', USER('daily-operator@example.invalid')), inspect='daily 站 User daily-operator@example.invalid'),
    Step('context-worker', lambda s: s.host_script('provision_context_worker.py'),
         probe=_both(_file('context-worker.json'), _db('alpha', CONF('dsherp_runtime_user'))),
         produces=('context-worker.json',), inspect='alpha site_config 的 dsherp_runtime_user 与 .runtime/context-worker.json'),
    Step('context-writer', lambda s: s.host_script('provision_context_writer.py'),
         probe=_both(_file('context-writer.json'), _db('alpha', USER('dsherp-writer@example.invalid'))),
         produces=('context-writer.json',), inspect='alpha 站 User dsherp-writer@example.invalid 与 .runtime/context-writer.json'),
    Step('worker-profile', lambda s: s.host_script('merge_context_worker_profiles.py'),
         probe=_file('context-worker-sites.json'), produces=('context-worker-sites.json',), rerun_safe=True),
    Step('preview-operator', lambda s: s.host_script('provision_preview_operator.py'),
         probe=_both(_file('preview-operator.json'), _db('beta', USER('dsherp-preview@example.invalid'))),
         produces=('preview-operator.json',), inspect='beta 站 User dsherp-preview@example.invalid 与 .runtime/preview-operator.json'),
    Step('configuration-preview', lambda s: s.host_script('provision_configuration_preview.py'),
         probe=_both(_file('configuration-preview.json'), _db('alpha', CONF('dsherp_configuration_preview'))),
         produces=('configuration-preview.json',), rerun_safe=True),
    Step('policies-alpha', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site', 'dsherp-validation.localhost', '--policy-set', 'manufacturing'), rerun_safe=True),
    Step('policies-daily', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site', 'dsherp-daily.localhost', '--policy-set', 'manufacturing'), rerun_safe=True),
    Step('policies-beta', lambda s: s.host_script('provision_alpha_doctype_policies.py', '--site', 'dsherp-beta.localhost'), rerun_safe=True),
    Step('manufacturing-alpha', lambda s: s.host_script('provision_manufacturing_fixture.py', '--site', 'dsherp-validation.localhost'), rerun_safe=True),
    Step('manufacturing-daily', lambda s: s.host_script('provision_manufacturing_fixture.py', '--site', 'dsherp-daily.localhost'), rerun_safe=True),
    Step('clear-cache', _clear_caches, rerun_safe=True),
    Step('daily-backup', lambda s: s.bench('daily').run('bench', '--site', 'dsherp-daily.localhost', 'backup', '--with-files', timeout=900),
         probe=_backup, inspect='backend 里 sites/dsherp-daily.localhost/private/backups'),
]


def produced_files():
    return [name for step in STEPS for name in step.produces]


def provision(stack, *, skip=()):
    ledger = stack.load_ledger()
    ran, skipped = [], []
    for step in STEPS:
        if step.name in skip:
            continue
        recorded = step.name in ledger['steps']
        found = None if step.probe is None else list(step.probe(stack))
        if found and any(found) and not all(found):
            raise Fault(f'步骤 {step.name} 的结果只存在一部分（探针 {found}）：栈里的站点/记录与 .runtime 里的凭据文件不配套。'
                        f'不重跑 fail-closed 脚本；查 {step.inspect}；确认后用 `dev_stack.py down --volumes` 重建。')
        present = None if found is None else all(found)
        if recorded and present in (True, None):
            skipped.append(step.name)
            continue
        if step.rerun_safe or (not recorded and present in (False, None)):
            step.run(stack)
            stack.record(step)
            ran.append(step.name)
            continue
        if recorded:
            raise Fault(f'台账记录 {step.name} 已完成，但栈里没有它的结果（卷被重建而 .runtime 没清？）。'
                        f'不重跑 fail-closed 脚本；查 {step.inspect}；确认要重建就先 `dev_stack.py down --volumes`。')
        raise Fault(f'台账没有 {step.name}，但栈里已有它的结果（手工开通过或台账丢失？）。'
                    f'不重跑 fail-closed 脚本；查 {step.inspect}；要以这个栈为准就先 `dev_stack.py down --volumes` 重建。')
    return {'ran': ran, 'skipped': skipped}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q` → `7 passed`；`.venv/bin/ruff check infra/dev_stack.py tests/test_dev_stack.py` → 通过

- [ ] **Step 5: 提交**

```bash
git add infra/dev_stack.py tests/test_dev_stack.py
git commit -m "feat: dev_stack 驱动骨架——27 步步骤表、台账、探针一致性，矛盾即停不重跑"
```

### Task B.3: `up`（主机名、镜像、密钥、compose up、就绪等待、密钥可读探针）、`status`

**Files:** Modify `infra/dev_stack.py`、`tests/test_dev_stack.py`

**Interfaces:**
- Produces: `check_runtime_dir`、`check_hosts`、`check_images`、`wait_ready`、`check_secrets_readable`；`up(stack, *, provision_too=False, skip_runtime_volume=False) -> {'secrets': {...}, ['provision': {...}]}`（只有名字）；`status(stack) -> {'ledger', 'running_services', 'pings', 'hosts'}`。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_up_refuses_when_a_host_name_does_not_resolve_to_loopback_and_says_which_line_to_add(stack):
    def resolve(name, port):
        if name == "preview.localhost":
            raise OSError("Name or service not known")
        return [(None, None, None, None, ("127.0.0.1", 0))]
    stack.resolve = resolve
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    assert "preview.localhost" in str(error.value) and dev_stack.HOSTS_LINE in str(error.value)
    assert not any(argv[:2] == ["docker", "compose"] for argv in stack.host.calls)


def test_up_refuses_when_a_pinned_image_is_missing_and_names_the_pull_command(stack):
    stack.host.images = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    for image in dev_stack.IMAGES:
        assert f"docker pull {image}" in str(error.value)
    assert not any("up" in argv for argv in stack.host.calls if argv[:2] == ["docker", "compose"])


def test_up_creates_the_seven_control_secrets_privately_and_reports_only_their_names(stack):
    report = dev_stack.up(stack)
    directory = stack.runtime / "control"
    from dsherp import admin
    assert set(report["secrets"]["created"]) == set(dev_stack.CONTROL_SECRETS)
    assert len(dev_stack.CONTROL_SECRETS) == len(admin.CONTROL_SECRETS_BY_ENV["dev"]) + 2   # 切片 D 会给 dev 再加 test_admin_password
    for name in dev_stack.CONTROL_SECRETS:
        path = directory / name
        assert path.stat().st_mode & 0o777 == 0o600 and len(path.read_text().strip()) >= 32
    values = [(directory / name).read_text().strip() for name in dev_stack.CONTROL_SECRETS]
    assert not any(value in json.dumps(report) for value in values)
    assert dev_stack.up(stack)["secrets"]["created"] == []          # never regenerated


def test_up_waits_for_db_redis_and_every_backend_before_declaring_ready(stack):
    dev_stack.up(stack)
    compose = [argv for argv in stack.host.calls if argv[:2] == ["docker", "compose"]]
    started = next(i for i, argv in enumerate(compose) if "up" in argv)
    after = [" ".join(argv) for argv in compose[started + 1:]]
    assert any("mariadb-admin ping" in line and "MYSQL_PWD=$(cat /run/secrets/db_root_password)" in line for line in after)
    assert not any("MYSQL_PWD=" in line and "$(cat" not in line for line in after)   # never a password literal
    assert any("redis-cli ping" in line for line in after)
    for service in dev_stack.BACKENDS:
        assert any(f"exec -T {service}" in line and "socket.create_connection" in line for line in after)


def test_up_gives_up_on_a_database_that_never_answers(stack):
    stack.host.db_up = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack)
    assert "db" in str(error.value) and "logs" in str(error.value)


def test_up_stops_when_the_frappe_user_cannot_read_the_secrets_and_gives_the_linux_remedy(stack):
    stack.host.secrets_readable = False
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(stack, provision_too=True)
    assert "chown 1000" in str(error.value)
    assert not any(argv[0] == sys.executable for argv in stack.host.calls)   # nothing was provisioned


def test_up_with_provision_runs_the_steps_after_the_stack_is_ready(stack):
    report = dev_stack.up(stack, provision_too=True, skip_runtime_volume=True)
    assert report["provision"]["ran"][0] == "backup-volumes" and "agent-runtime-volume" not in report["provision"]["ran"]


def test_the_runtime_directory_must_be_the_repository_one(stack, monkeypatch, tmp_path):
    monkeypatch.setenv("DSHERP_RUNTIME_DIR", str(tmp_path / "elsewhere"))
    other = dev_stack.Stack(stack.resolved, root=stack.root, runner=stack.host)
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.up(other)
    assert "DSHERP_RUNTIME_DIR" in str(error.value)


def test_status_reports_ledger_services_and_pings_without_any_secret(stack):
    dev_stack.up(stack, provision_too=True, skip_runtime_volume=True)
    report = dev_stack.status(stack)
    assert set(report) == {"ledger", "running_services", "pings", "hosts"}
    assert report["pings"] == {"alpha": 200, "platform": 200, "beta": 200, "daily": 200}
    assert "backend" in report["running_services"] and "validation-site" in report["ledger"]
    assert "SECRET-VALUE" not in json.dumps(report) and "PASSWORD-VALUE" not in json.dumps(report)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q` → `AttributeError: module 'infra.dev_stack' has no attribute 'up'`

- [ ] **Step 3: 实现**（`provision` 之后追加）

```python
def check_runtime_dir(stack):
    expected = (stack.root / '.runtime').resolve()
    if stack.runtime.resolve() != expected:
        raise Fault(f'开发栈的开通脚本只认 {expected}；不要设置 DSHERP_RUNTIME_DIR / DSHERP_SECRETS_DIR（现在是 {stack.runtime}）')


def check_hosts(stack):
    missing = []
    for name in HOSTS:
        try:
            addresses = {row[4][0] for row in stack.resolve(name, None)}
        except OSError:
            addresses = set()
        if not addresses & {'127.0.0.1', '::1'}:
            missing.append(name)
    if missing:
        raise Fault('这些主机名没有解析到回环地址：' + '、'.join(missing) + '\n在 /etc/hosts 加这一行后重试：\n' + HOSTS_LINE)


def check_images(stack):
    missing = [image for image in IMAGES if not stack.succeeds(['docker', 'image', 'inspect', image])]
    if missing:
        raise Fault('compose 以 pull_policy: never 运行，这些镜像本机没有，先按 digest 拉取：\n'
                    + '\n'.join(f'docker pull {image}' for image in missing))


def wait_ready(stack):
    stack.wait_for('db 应答 ping', lambda: stack.succeeds(stack.compose('exec', '-T', 'db', 'sh', '-c', DB_PING)), timeout=180)
    stack.wait_for('redis 应答 PONG', lambda: stack.succeeds(stack.compose('exec', '-T', 'redis', 'sh', '-c', REDIS_PING)), timeout=60)
    for service in BACKENDS:
        stack.wait_for(f'{service} 监听 8000',
                       lambda service=service: stack.succeeds(stack.compose('exec', '-T', service, BENCH_PYTHON, '-c', PORT_OPEN)),
                       timeout=300)


def check_secrets_readable(stack):
    """Compose bind-mounts file secrets with the host file's ownership. The provisioning containers
    run as the image's frappe user (uid 1000); on a Linux host whose user is not 1000 they cannot
    read a 0600 file. Say so before the first fail-closed script hits it."""
    command = stack.compose('--profile', 'control', 'run', '--rm', '--no-deps', '--entrypoint', 'sh',
                            'validation-provision', '-c', SECRETS_READABLE)
    result = stack.runner(command, cwd=str(stack.root), text=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    if result.returncode or 'readable' not in (result.stdout or ''):
        raise Fault('容器内的 frappe 用户（uid 1000）读不到 .runtime/control 里的密钥文件。'
                    'Linux 上执行 `sudo chown 1000 .runtime/control/*`（只改文件，目录仍归你；bind mount 只看文件权限）后重试；'
                    'macOS Docker Desktop 不需要。最后输出：' + '\n'.join((result.stderr or '').strip().splitlines()[-3:]))


def up(stack, *, provision_too=False, skip_runtime_volume=False):
    check_runtime_dir(stack)
    check_hosts(stack)
    check_images(stack)
    report = {'secrets': admin.ensure_secrets(stack.resolved, stack.root, names=CONTROL_SECRETS)}
    stack.run(stack.compose('up', '-d'), timeout=600)
    wait_ready(stack)
    check_secrets_readable(stack)
    if provision_too:
        report['provision'] = provision(stack, skip=('agent-runtime-volume',) if skip_runtime_volume else ())
    return report


def status(stack):
    services = stack.run(stack.compose('ps', '--services', '--status', 'running'), timeout=60).split()
    pings = {}
    for kind, url, host in PINGS:
        try:
            with stack.http(Request(url, headers={'Host': host}), timeout=10) as response:
                pings[kind] = response.status
        except URLError as error:
            pings[kind] = getattr(error, 'code', None) or type(error).__name__
        except OSError as error:
            pings[kind] = type(error).__name__
    hosts = {}
    for name in HOSTS:
        try:
            hosts[name] = sorted({row[4][0] for row in stack.resolve(name, None)})
        except OSError:
            hosts[name] = []
    return {'ledger': sorted(stack.load_ledger()['steps']), 'running_services': sorted(services), 'pings': pings, 'hosts': hosts}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q` → 全部通过

- [ ] **Step 5: 提交**

```bash
git add infra/dev_stack.py tests/test_dev_stack.py
git commit -m "feat: dev_stack up——主机名/镜像/密钥核对、compose up、db/redis/三后端就绪等待、密钥可读探针；status"
```

### Task B.4: `down --volumes`、`scan-artifacts`、台账无密钥、CLI、`native-tests` 桩

**Files:** Modify `infra/dev_stack.py`、`tests/test_dev_stack.py`

**Interfaces:**
- Produces: `require_worker_stopped(stack)`；`down(stack, *, volumes) -> {'removed': [...]}`（`--volumes` 时删数据卷、agent 运行时卷、`produced_files()` 与台账；**从不删** `.runtime/control/*`、`business-sessions/`、worker 的 pid/log）；`leaked_secrets(runtime_dir, paths, *, as_text=False) -> list[(密钥名, 路径)]`（按值去重，只报名字，从不报值）；`run_native_tests(resolved, *, junit, runner=subprocess.run) -> dict`（**本切片为显式桩**，切片 D 替换函数体，签名不变）；`main(argv)`：`secrets / up [--provision] [--skip-runtime-volume] / provision / status / native-tests --junit PATH / down [--volumes] / scan-artifacts PATH...`；`Fault` → stderr + 退出码 2；`scan-artifacts` 有泄漏退出 1。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_down_with_volumes_removes_exactly_the_produced_files_and_the_ledger(stack):
    dev_stack.up(stack, provision_too=True)
    keep = [stack.runtime / "control" / "db_root_password", stack.runtime / "business-sessions" / "s1" / "note",
            stack.runtime / "agent-worker.log"]
    for path in keep[1:]:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("keep")
    produced = [stack.runtime / name for name in dev_stack.produced_files()]
    assert all(path.exists() for path in produced) and stack.ledger_path.exists()
    report = dev_stack.down(stack, volumes=True)
    assert not any(path.exists() for path in produced) and not stack.ledger_path.exists()
    assert all(path.exists() for path in keep)
    assert set(report["removed"]) == set(dev_stack.produced_files()) | {dev_stack.LEDGER_NAME}
    compose = [" ".join(argv) for argv in stack.host.calls if argv[:2] == ["docker", "compose"]]
    assert any("down --remove-orphans -v" in line for line in compose)
    assert ["docker", "volume", "rm", dev_stack.AGENT_RUNTIME_VOLUME] in stack.host.calls
    assert dev_stack.produced_files() == [
        "erp-users.json", "erp-reader.json", "erp-denied.json", "platform-users.json", "beta-users.json",
        "context-worker-daily.json", "context-worker.json", "context-writer.json", "context-worker-sites.json",
        "preview-operator.json", "configuration-preview.json"]


def test_down_without_volumes_only_stops_containers(stack):
    dev_stack.up(stack, provision_too=True)
    dev_stack.down(stack, volumes=False)
    assert stack.ledger_path.exists() and (stack.runtime / "erp-users.json").exists()
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in stack.host.calls)


def test_down_refuses_while_the_resident_worker_is_alive(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True)
    (stack.runtime / dev_stack.WORKER_PID).write_text(str(os.getpid()))
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.down(stack, volumes=True)
    assert "launchctl bootout" in str(error.value) and dev_stack.WORKER_LABEL in str(error.value)
    assert not any(argv[:2] == ["docker", "compose"] for argv in stack.host.calls)


def test_a_dead_pid_file_does_not_block_down(stack):
    stack.runtime.mkdir(mode=0o700, exist_ok=True)
    (stack.runtime / dev_stack.WORKER_PID).write_text("999999999")
    dev_stack.down(stack, volumes=False)


def test_the_ledger_never_contains_a_secret_value(stack):
    dev_stack.up(stack, provision_too=True)
    assert dev_stack.leaked_secrets(stack.runtime, [stack.ledger_path]) == []
    text = stack.ledger_path.read_text()
    assert "SECRET-VALUE" not in text and "PASSWORD-VALUE" not in text
    for name in dev_stack.CONTROL_SECRETS:
        assert (stack.runtime / "control" / name).read_text().strip() not in text


def test_scan_artifacts_names_the_leaking_secret_and_the_file_but_never_the_value(stack, tmp_path, capsys, monkeypatch):
    dev_stack.up(stack, provision_too=True)
    log = tmp_path / "compose.log"
    log.write_text("gunicorn ... token rk:READER-SECRET-VALUE ...\n" + (stack.runtime / "control" / "db_root_password").read_text())
    clean = tmp_path / "junit.xml"; clean.write_text("<testsuite tests='1'/>")
    found = dev_stack.leaked_secrets(stack.runtime, [log, clean])
    # one name per value: erp-reader.json sorts before erp-users.json and holds the same secret
    assert found == [("control/db_root_password", str(log)), ("erp-reader.json:api_secret", str(log))]
    monkeypatch.setattr(dev_stack, "Stack", lambda resolved: stack)
    monkeypatch.setattr(dev_stack.deploy_env, "settings", lambda *a, **k: stack.resolved)
    code = dev_stack.main(["scan-artifacts", str(log), str(clean)])
    out = capsys.readouterr()
    assert code == 1 and "erp-reader.json:api_secret" in out.out and "READER-SECRET-VALUE" not in out.out + out.err
    assert dev_stack.main(["scan-artifacts", str(clean)]) == 0


def test_native_tests_is_an_explicit_stub_until_the_frappe_slice_wires_it(stack, tmp_path):
    with pytest.raises(dev_stack.Fault) as error:
        dev_stack.run_native_tests(stack.resolved, junit=tmp_path / "junit-native.xml", runner=stack.host)
    assert "尚未接线" in str(error.value)
    assert not (tmp_path / "junit-native.xml").exists()


def test_the_cli_maps_a_fault_to_exit_code_2_and_prints_it_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(dev_stack.deploy_env, "settings", lambda *a, **k: {"env": "prod", "project": "x"})
    assert dev_stack.main(["status"]) == 2
    assert "DSHERP_ENV=dev" in capsys.readouterr().err
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q -k "down or ledger or scan or native or cli"` → `AttributeError: … 'down'`

- [ ] **Step 3: 实现**（追加）

```python
def require_worker_stopped(stack):
    pid_file = stack.runtime / WORKER_PID
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError):
        return
    except PermissionError:
        pass  # 进程存在但属于别的用户：同样算活着
    raise Fault(f'常驻 worker 仍在运行（pid {pid}）。先 `launchctl bootout gui/$(id -u)/{WORKER_LABEL}`，'
                f'确认 {pid_file} 消失或进程不在，再拆栈。')


def down(stack, *, volumes):
    require_worker_stopped(stack)
    stack.run(stack.compose('down', '--remove-orphans', *(['-v'] if volumes else [])), timeout=600)
    removed = []
    if not volumes:
        return {'removed': removed}
    if stack.volume_exists(AGENT_RUNTIME_VOLUME):
        stack.run(['docker', 'volume', 'rm', AGENT_RUNTIME_VOLUME], timeout=120)
    for name in produced_files():
        path = stack.runtime / name
        if path.exists():
            path.unlink()
            removed.append(name)
    if stack.ledger_path.exists():
        stack.ledger_path.unlink()
        removed.append(LEDGER_NAME)
    return {'removed': removed}


def _secret_values(runtime_dir):
    """(name, value) for every secret the runtime directory holds, one name per distinct value.
    Names, never values, are reported."""
    runtime_dir = Path(runtime_dir)
    found, seen = [], set()

    def add(name, value):
        if value and value not in seen:
            seen.add(value)
            found.append((name, value))

    control = runtime_dir / 'control'
    if control.is_dir():
        for path in sorted(control.iterdir()):
            if path.is_file():
                try:
                    add(f'control/{path.name}', path.read_text().strip())
                except PermissionError:
                    raise Fault(f'读不到 {path}（它的属主是容器用户？）；用 sudo 运行 scan-artifacts')

    def walk(node, trail, file):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in SECRET_KEYS and isinstance(child, str):
                    add(f'{file}:{".".join(trail + [key])}', child)
                else:
                    walk(child, trail + [str(key)], file)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, trail + [str(index)], file)

    for path in sorted(runtime_dir.glob('*.json')):
        if path.name == LEDGER_NAME:
            continue
        try:
            walk(json.loads(path.read_text()), [], path.name)
        except ValueError:
            continue
    return found


def leaked_secrets(runtime_dir, paths, *, as_text=False):
    secrets = _secret_values(runtime_dir)
    leaks = []
    for item in paths:
        text = item if as_text else Path(item).read_text(errors='replace')
        for name, value in secrets:
            if value in text:
                leaks.append((name, 'text' if as_text else str(item)))
    return leaks


def run_native_tests(resolved, *, junit, runner=subprocess.run):
    """Frappe's own test runner inside the containers, writing one junit file. Wired by the
    Frappe-native-tests slice of plan 5; until then this is a loud stub, never a green one."""
    raise Fault('native-tests 尚未接线：由计划 5 的 Frappe 原生测试切片实现'
                '（dsherp-test.localhost 上 bench run-tests --app dsherp_bridge --junit-xml-output …，'
                f'平台测试站同理）；junit 应写到 {junit}')


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('secrets', help='只生成缺失的七个控制面密钥；从不覆盖、从不打印')
    up_parser = sub.add_parser('up', help='核对主机名与镜像、生成密钥、compose up、等就绪；--provision 接着开通四站')
    up_parser.add_argument('--provision', action='store_true')
    up_parser.add_argument('--skip-runtime-volume', action='store_true', help='跳过 agent 运行时卷（本机已有时省 3 分钟）')
    provision_parser = sub.add_parser('provision', help='栈已 up 时只做开通步骤')
    provision_parser.add_argument('--skip-runtime-volume', action='store_true')
    sub.add_parser('status', help='台账、运行中的服务、四站 ping、主机名解析；不含任何密钥')
    native = sub.add_parser('native-tests', help='容器内跑 Frappe 原生测试并写 junit')
    native.add_argument('--junit', required=True, type=Path)
    down_parser = sub.add_parser('down', help='停栈；--volumes 连数据卷、agent 运行时卷、台账与开通产出的凭据文件一起删（控制面密钥保留）')
    down_parser.add_argument('--volumes', action='store_true')
    scan = sub.add_parser('scan-artifacts', help='检查要上传的文件是否含 .runtime 里任一密钥值；有则退出 1，只报名字')
    scan.add_argument('paths', nargs='+', type=Path)
    arguments = parser.parse_args(argv)
    try:
        resolved = deploy_env.settings()
        if resolved['env'] != 'dev':
            raise Fault('dev_stack 只操作 DSHERP_ENV=dev 的隔离栈')
        stack = Stack(resolved)
        if arguments.command == 'secrets':
            _print(admin.ensure_secrets(stack.resolved, stack.root, names=CONTROL_SECRETS))
        elif arguments.command == 'up':
            _print(up(stack, provision_too=arguments.provision, skip_runtime_volume=arguments.skip_runtime_volume))
        elif arguments.command == 'provision':
            check_runtime_dir(stack)
            _print(provision(stack, skip=('agent-runtime-volume',) if arguments.skip_runtime_volume else ()))
        elif arguments.command == 'status':
            _print(status(stack))
        elif arguments.command == 'native-tests':
            _print(run_native_tests(stack.resolved, junit=arguments.junit))
        elif arguments.command == 'down':
            _print(down(stack, volumes=arguments.volumes))
        elif arguments.command == 'scan-artifacts':
            leaks = leaked_secrets(stack.runtime, arguments.paths)
            for name, path in leaks:
                print(f'{path}: 含 {name} 的值')
            return 1 if leaks else 0
        return 0
    except Fault as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_dev_stack.py -q` → 全部通过（约 24 条）
Run: `.venv/bin/python infra/dev_stack.py native-tests --junit work/x.xml; echo $?` → stderr 一行「尚未接线…」，退出码 `2`
Run（本机活栈，只读）: `.venv/bin/python infra/dev_stack.py status | jq '.pings, (.running_services|length), (.ledger|length)'` → 四个 200、8、0

- [ ] **Step 5: 提交**

```bash
git add infra/dev_stack.py tests/test_dev_stack.py
git commit -m "feat: dev_stack down --volumes 只删开通产出与台账、worker 活着即拒绝；scan-artifacts 只报密钥名；CLI 与 native-tests 桩"
```

### Task B.5: `.github/workflows/nightly.yml`（本切片不含原生测试步）

**Files:** Create `.github/workflows/nightly.yml`

**Interfaces:**
- Consumes: `infra/dev_stack.py`；切片 0 的 pytest 约定。
- Produces: 工件 `nightly-<run_number>`：`work/junit-integration.xml`、`work/compose.log`、`work/dev-stack-ledger.json`、`work/dev-stack-status.json`、`work/docker-stats.txt`、`work/disk.txt`。切片 D 在「集成测试」之后加一步 `native-tests`。

- [ ] **Step 3: 实现**

```yaml
# .github/workflows/nightly.yml
# 每夜从零：拉起四站 → 全部集成测试 →（切片 D 起）Frappe 原生测试 → 工件 → 拆栈。
# 没有任何重试。红了就是当天要处理的事（一天内修掉或删掉那条用例，见 quality-gates-evidence）。
name: nightly

on:
  schedule:
    - cron: "0 18 * * *"     # 18:00 UTC = 02:00 Asia/Shanghai
  workflow_dispatch:

# 两个每夜永不重叠：compose 项目名 dsherp-validation 与宿主端口 1808x 都是固定的。
concurrency:
  group: nightly
  cancel-in-progress: false

permissions:
  contents: read

env:
  DSHERP_ENV: dev
  PYTHONDONTWRITEBYTECODE: "1"

jobs:
  integration:
    runs-on: ubuntu-latest        # 公开仓库标准 runner：按 4 vCPU / 16 GiB / 14 GB SSD 假设，首跑必须记录实际值
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@<sha of actions/checkout v4>  # v4.2.2

      - name: 宿主名解析（测试用 preview/daily/platform.localhost 连回环）
        run: echo "127.0.0.1 preview.localhost daily.localhost platform.localhost dsherp-validation.localhost" | sudo tee -a /etc/hosts

      - name: 预拉三个固定 digest 镜像（compose 是 pull_policy: never）
        run: |
          set -eu
          pull() {
            for attempt in 1 2 3 4 5; do
              docker pull --quiet "$1" && return 0
              echo "第 ${attempt} 次拉取失败：$1（Docker Hub 匿名限额或网络）"; sleep $((attempt * 30))
            done
            return 1
          }
          pull frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
          pull mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3
          pull redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015

      - uses: astral-sh/setup-uv@<sha of astral-sh/setup-uv v6>  # v6.x
        with:
          version: "0.9.27"
          enable-cache: true
          cache-dependency-glob: requirements.lock

      - name: 与 README 完全相同的环境
        run: |
          uv python install 3.12.11
          uv venv --python 3.12.11 .venv
          uv pip sync --python .venv/bin/python --require-hashes requirements.lock

      - name: Agent 容器身份 = 运行本作业的用户（runner，非 root）
        run: |
          echo "DSHERP_AGENT_UID=$(id -u)" >> "$GITHUB_ENV"
          echo "DSHERP_AGENT_GID=$(id -g)" >> "$GITHUB_ENV"

      - name: 生成七个控制面密钥（只生成缺失项，不打印）
        run: .venv/bin/python infra/dev_stack.py secrets

      - name: 容器内的 frappe（uid 1000）要能读密钥文件（compose 以 bind mount 提供文件密钥，归属沿用宿主）
        run: sudo chown 1000 .runtime/control/*

      - name: 从零拉起并开通四站
        run: .venv/bin/python infra/dev_stack.py up --provision

      - name: 集成测试（约 210 条；每条 600 秒安全网，无重试）
        run: |
          mkdir -p work
          .venv/bin/python -m pytest tests/integration -m integration -q \
            --junitxml work/junit-integration.xml --timeout 600 --durations=40

      - name: 现场快照（总是）
        if: always()
        run: |
          mkdir -p work
          .venv/bin/python infra/dev_stack.py status > work/dev-stack-status.json || true
          docker compose -f infra/compose.validation.yml logs --no-color --timestamps > work/compose.log 2>&1 || true
          cp .runtime/dev-stack.json work/dev-stack-ledger.json 2>/dev/null || true
          docker stats --no-stream > work/docker-stats.txt 2>&1 || true
          df -h / > work/disk.txt 2>&1 || true

      - name: 泄漏自检：工件里不能出现 .runtime 中任何密钥值（否则不上传）
        if: always()
        run: sudo -E .venv/bin/python infra/dev_stack.py scan-artifacts work/junit-*.xml work/compose.log work/dev-stack-ledger.json work/dev-stack-status.json

      - uses: actions/upload-artifact@<sha of actions/upload-artifact v4>  # v4.x
        if: always()
        with:
          name: nightly-${{ github.run_number }}
          path: |
            work/junit-*.xml
            work/compose.log
            work/dev-stack-ledger.json
            work/dev-stack-status.json
            work/docker-stats.txt
            work/disk.txt
          retention-days: 30

      - name: 拆栈（总是；拆不掉也算本夜失败，因为本机重建靠它）
        if: always()
        run: .venv/bin/python infra/dev_stack.py down --volumes
```

说明：`scan-artifacts` 用 `sudo -E` 因为 `.runtime/control/*` 已交给 uid 1000；不配置任何 provider 密钥（集成套件用本地模型替身；`test_agent_boundary.py:97-110` 需要真实 `api.deepseek.com` 的 401，runner 出网即可）；`--durations=40` 与 junit `time` 是唯一允许的调超时依据。

- [ ] **Step 4: 运行确认通过（用户检查点：push 后 `gh workflow run nightly.yml --ref plan5/dev-stack`）**

首跑记录：总时长、各步耗时（`up --provision` 单独看）、`docker-stats.txt` 峰值内存、`disk.txt`。
- **> 60 分钟**的处置（按序、每次只动一项）：① 看 `--durations=40`，前 5 条合计 > 15 分钟且都是同类 `subprocess.run(timeout=)` 外层等待 → 先查根因不调超时；② agent 运行时卷的 pip 安装接 GitHub cache（key = `requirements.lock` 哈希）；③ `up --provision` > 25 分钟 → 三镜像用 `actions/cache` 的 `docker save/load`；④ 仍超才把 `timeout-minutes` 提到 150 并记数据。不拆并行 job（四站共享一个项目名）。
- 首跑停在 `check_secrets_readable` → `chown` 未生效或 compose 版本改了密钥实现，按驱动的信息处理并记证据；停在某个 fail-closed 步（如 `Site already exists`）→ runner 一次性，只可能是驱动重跑逻辑或顺序错——修驱动，不加"跳过"。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/nightly.yml
git commit -m "feat: 每夜工作流——从零拉起四站、集成测试、junit/日志/台账工件、泄漏自检、总是拆栈"
```

### Task B.6: `.github/workflows/supply-chain.yml`（每周，G9 之外）

**Files:** Create `.github/workflows/supply-chain.yml`

**Interfaces:**
- Consumes: `infra/release_images.py`（`--platform linux/amd64 --git-commit <sha>`；tag 从 `DSHERP_IMAGE_TAG`；要求 `.git`、干净树、tag 指向 HEAD）；`infra/supply_chain.py`（`DSHERP_ENV=prod`；`--threshold high --out`）。
- Produces: 工件 `supply-chain-<run_number>`：`work/supply-chain/*.cdx.json`、`*.grype.json`、`infra/releases/ci-*.json`。

- [ ] **Step 3: 实现**

```yaml
# .github/workflows/supply-chain.yml
# 每周：用当前 main 构建两个发布镜像，生成 SBOM 并按 high 阈值扫 CVE。
# 允许红（上游基础镜像的 CVE 不在本仓库控制内）；明确不计入 G9 的 30 天绿——只有 ci.yml 与 nightly.yml 计入。
name: supply-chain

on:
  schedule:
    - cron: "0 19 * * 1"     # 每周一 03:00 Asia/Shanghai
  workflow_dispatch:

concurrency:
  group: supply-chain
  cancel-in-progress: false

permissions:
  contents: read

env:
  DSHERP_ENV: prod
  SYFT_VERSION: "1.20.0"     # 实施时用 gh release list --repo anchore/syft 核对并升到当前稳定版；写具体版本号
  GRYPE_VERSION: "0.87.0"    # 同上；grype 的漏洞库 schema 随版本演进

jobs:
  sbom-and-cve:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@<sha of actions/checkout v4>  # v4.2.2
        with:
          fetch-depth: 0     # release_images.py 需要完整 git 与干净树

      - uses: astral-sh/setup-uv@<sha of astral-sh/setup-uv v6>  # v6.x
        with:
          version: "0.9.27"
          enable-cache: true
          cache-dependency-glob: requirements.lock

      - name: 与 README 完全相同的环境
        run: |
          uv python install 3.12.11
          uv venv --python 3.12.11 .venv
          uv pip sync --python .venv/bin/python --require-hashes requirements.lock

      - name: 本次构建的 tag = ci-<12 位 sha>（只在本地打，不推送；release_images 要求 tag 指向 HEAD）
        run: |
          tag="ci-$(git rev-parse --short=12 HEAD)"
          git tag "$tag" HEAD
          echo "DSHERP_IMAGE_TAG=${tag}" >> "$GITHUB_ENV"

      - name: 构建两个发布镜像（linux/amd64）并写清单
        run: .venv/bin/python infra/release_images.py --platform linux/amd64 --git-commit "$(git rev-parse HEAD)"

      - name: 安装固定版本的 syft 与 grype（按发布页校验和核对）
        run: |
          set -eu
          cd "$(mktemp -d)"
          curl -sSfLO "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz"
          curl -sSfLO "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_checksums.txt"
          grep "syft_${SYFT_VERSION}_linux_amd64.tar.gz" "syft_${SYFT_VERSION}_checksums.txt" | sha256sum -c -
          tar -xzf "syft_${SYFT_VERSION}_linux_amd64.tar.gz" syft && sudo install -m 755 syft /usr/local/bin/syft
          curl -sSfLO "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz"
          curl -sSfLO "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_checksums.txt"
          grep "grype_${GRYPE_VERSION}_linux_amd64.tar.gz" "grype_${GRYPE_VERSION}_checksums.txt" | sha256sum -c -
          tar -xzf "grype_${GRYPE_VERSION}_linux_amd64.tar.gz" grype && sudo install -m 755 grype /usr/local/bin/grype
          syft version && grype version

      - name: SBOM 与 CVE 门（high 及以上即失败）
        run: .venv/bin/python -m infra.supply_chain --threshold high --out work/supply-chain

      - uses: actions/upload-artifact@<sha of actions/upload-artifact v4>  # v4.x
        if: always()
        with:
          name: supply-chain-${{ github.run_number }}
          path: |
            work/supply-chain/
            infra/releases/ci-*.json
          retention-days: 90
```

- [ ] **Step 4: 运行确认通过（用户检查点）** `gh workflow run supply-chain.yml --ref plan5/dev-stack`；若红，把 `*.grype.json` 里 high/critical 的包名列入证据并注明"允许红、不计 G9"。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/supply-chain.yml
git commit -m "feat: 每周供应链工作流——构建 ci-<sha> 发布镜像、syft SBOM、grype high 门，明确不计入 G9"
```

### Task B.7: 本机开发栈用驱动从零重建（**用户同意检查点**）

前置：nightly 已在 CI 上把 `up --provision` + 集成 pytest 跑绿一次（驱动在真实 Linux 上的证明先于碰本机）。会**销毁本机合成数据**（四站数据库与文件卷、agent 运行时卷）、重新生成 `.runtime/*.json`（控制面口令保留）。

- [ ] **Step 1: 停常驻 worker**

```sh
launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16
launchctl print gui/$(id -u)/com.dsherp.agent-worker-v16 ; echo "exit=$? (期望 113 = 不存在)"
pgrep -fl dsherp.context_worker || echo "无 worker 进程"
docker compose -f infra/compose.validation.yml --profile scheduled ps --services --status running   # scheduler 若在，一并 stop
```

- [ ] **Step 2: 拆栈**

```sh
.venv/bin/python infra/dev_stack.py down --volumes
docker volume ls --format '{{.Name}}' | grep -E '^dsherp-validation_v16-|^dsherp-v16-agent-runtime$' ; echo "exit=$? (期望 1 = 无残留)"
ls .runtime/*.json 2>/dev/null ; echo "(期望为空)"
ls -la .runtime/control    # 七个口令仍在
```

- [ ] **Step 3: 从零拉起**

```sh
time .venv/bin/python infra/dev_stack.py up --provision      # 本机含 agent 运行时卷 pip 安装，预计 15–25 分钟
.venv/bin/python infra/dev_stack.py status | jq '.pings, (.running_services|length), (.ledger|length)'
```

Expected: `pings` 四个 200；`running_services` 8；`ledger` 27。

- [ ] **Step 4: 核验**

```sh
.venv/bin/python infra/dev_stack.py up --provision            # 第二次：ran: []，全部 skipped
.venv/bin/python -m pytest tests/integration -m integration -q --timeout 600 --junitxml work/junit-integration-rebuild.xml
.venv/bin/python infra/render_context_worker_launch_agent.py
launchctl bootstrap gui/$(id -u) .runtime/com.dsherp.agent-worker-v16.plist
sleep 20; launchctl print gui/$(id -u)/com.dsherp.agent-worker-v16 | grep -E 'state|pid'
curl -s http://127.0.0.1:9109/metrics | grep -E '^dsherp_(queue_depth|slots_busy|provider_circuit_open)'
```

Expected: 集成约 210 passed；worker `state = running`；三条指标有值。把 `time`、passed 数、worker PID 写入证据。集成有失败：先看是否与 CI 首跑同一条（用例问题）否则是本机差异——都按"一天内修或删"，不重试。

### 切片 B 结束门

切片 0 的四条本机命令 + `docker compose --profile control config --services` + 三个工作流各至少一次真实运行（`ci` 绿；`nightly` 全步绿且 `down --volumes` 成功；`supply-chain` 有结果）+ Task B.7 完成。**检查点：PR 合入 main；随后 Task A.2 分支保护生效。**

---

## 切片 C：集成清理（登记式残留、统一入口、精确重签、九个高风险测试）

约定：所有迁移都把脚本中的 `import os,frappe / os.chdir / frappe.init / frappe.connect / frappe.destroy()` 去掉（由 `site_exec` 提供），`json/uuid/frappe` 已在前奏导入；tag 在宿主生成并注入；**登记语句必须在创建动作之前**。审计 DocType 只用 `frappe.db.delete`（`data-governance-evidence.md` 记录的许可路径）；持久夹具 `DSHERP-TEST-*`、`DSHERP-HITL-*`、`DSHERP-MFG-SYN-*`、`DSHERP-UI-*`、`DSHERP-BETA-*`、`SAL-ORD-2026-00001` 与固定演员邮箱永远不得登记。

### Task C.1: 站点脚本统一入口 `site_exec.py`

**Files:** Create `tests/integration/site_exec.py`、`tests/test_integration_site_exec.py`

**Interfaces:**
- Produces: `SITES: dict[str, str]`；`service_of(site) -> str`（未知站点 `ValueError`）；`site_script(site, body, *, connect=True, user='Administrator') -> str`（纯函数）；`command_for(site) -> list[str]`；`run_site_script(site, body, *, timeout=120, connect=True, user='Administrator', run=subprocess.run) -> str`；`run_site_json(site, body, **kwargs)`；`ON_TIMEOUT: list[callable(site, body)]`
- Consumes: `docker compose -f infra/compose.validation.yml exec -T <service> /home/frappe/frappe-bench/env/bin/python -`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration_site_exec.py
"""Every integration script enters a Site the same way: one wrapper, sites mapped to compose
services, the body compiled from a literal so it keeps its own indentation, destroy guaranteed."""
import subprocess

import pytest

from tests.integration import site_exec


class Result:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_sites_map_to_compose_services_and_unknown_sites_are_refused():
    assert site_exec.service_of('dsherp-validation.localhost') == 'backend'
    assert site_exec.service_of('dsherp-daily.localhost') == 'backend'
    assert site_exec.service_of('dsherp-test.localhost') == 'backend'
    assert site_exec.service_of('dsherp-beta.localhost') == 'beta-backend'
    assert site_exec.service_of('dsherp-platform.localhost') == 'platform-backend'
    assert site_exec.service_of('dsherp-platform-test.localhost') == 'platform-backend'
    with pytest.raises(ValueError):
        site_exec.service_of('dsherp-validation-backend-1')
    assert site_exec.command_for('dsherp-beta.localhost') == [
        'docker', 'compose', '-f', 'infra/compose.validation.yml', 'exec', '-T', 'beta-backend',
        '/home/frappe/frappe-bench/env/bin/python', '-']


def test_the_script_keeps_the_body_verbatim_connects_as_the_user_and_always_destroys():
    body = 'x = """three\n  quotes"""\nprint(json.dumps({"ok": frappe.session.user}))\n'
    text = site_exec.site_script('dsherp-validation.localhost', body, user='dsherp-reader@example.invalid')
    assert "frappe.init(site='dsherp-validation.localhost'" in text
    assert 'frappe.connect()' in text and "frappe.set_user('dsherp-reader@example.invalid')" in text
    assert repr(body) in text and text.rstrip().endswith('finally:\n    frappe.destroy()')
    offline = site_exec.site_script('dsherp-validation.localhost', body, connect=False)
    assert 'frappe.connect()' not in offline and 'frappe.set_user' not in offline
    compile(text, '<wrapper>', 'exec')  # the wrapper itself is valid Python


def test_a_failing_script_raises_with_the_site_the_service_and_the_stderr_tail():
    def run(command, **kwargs):
        return Result(1, '', 'line1\n' + '\n'.join(f'frame {i}' for i in range(30)) + '\nValidationError: 策略目标 DocType 不存在')
    with pytest.raises(AssertionError) as caught:
        site_exec.run_site_script('dsherp-beta.localhost', "print('x')", run=run)
    message = str(caught.value)
    assert 'dsherp-beta.localhost' in message and 'beta-backend' in message
    assert '策略目标 DocType 不存在' in message and 'frame 0' not in message


def test_a_timeout_is_announced_to_observers_then_re_raised():
    seen = []
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    site_exec.ON_TIMEOUT.append(lambda site, body: seen.append((site, body)))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            site_exec.run_site_script('dsherp-validation.localhost', 'frappe.db.commit()', timeout=7, run=run)
    finally:
        site_exec.ON_TIMEOUT.clear()
    assert seen == [('dsherp-validation.localhost', 'frappe.db.commit()')]


def test_run_site_json_returns_the_last_line_and_refuses_silence():
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs['input'], kwargs['cwd'], kwargs['timeout']))
        return Result(0, 'noise\n{"ok": true}\n')
    assert site_exec.run_site_json('dsherp-platform.localhost', 'print(1)', timeout=33, run=run) == {'ok': True}
    assert calls[0][0][6] == 'platform-backend' and calls[0][3] == 33 and calls[0][2] == site_exec.ROOT
    with pytest.raises(AssertionError):
        site_exec.run_site_json('dsherp-platform.localhost', 'pass', run=lambda *a, **k: Result(0, '\n'))
```

- [ ] **Step 2: 运行确认失败** `.venv/bin/python -m pytest tests/test_integration_site_exec.py -q` → `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# tests/integration/site_exec.py
"""One way into a Site's interpreter for every integration test.

`run_site_script` wraps `docker compose -f infra/compose.validation.yml exec -T <service>
/home/frappe/frappe-bench/env/bin/python -`. The body runs inside a connected Frappe context
as Administrator (or the user asked for), and `frappe.destroy()` is guaranteed on the
container side. Sites map to compose services here, so no test names a container. A body
must not call frappe.init/connect/destroy itself.

A host-side timeout kills the docker client, not the interpreter inside the container: the
body's own `finally` may never run. That is why the residue registry (residue.py) subscribes
to ON_TIMEOUT, and why anything a script commits must be registered before the script runs."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ['docker', 'compose', '-f', 'infra/compose.validation.yml']
BENCH_PYTHON = '/home/frappe/frappe-bench/env/bin/python'
SITES_DIR = '/home/frappe/frappe-bench/sites'
SITES = {
    'dsherp-validation.localhost': 'backend',
    'dsherp-daily.localhost': 'backend',
    'dsherp-test.localhost': 'backend',
    'dsherp-beta.localhost': 'beta-backend',
    'dsherp-platform.localhost': 'platform-backend',
    'dsherp-platform-test.localhost': 'platform-backend',
}
# Called as observer(site, body) when the host timeout kills a script.
ON_TIMEOUT = []


def service_of(site):
    try:
        return SITES[site]
    except KeyError:
        raise ValueError(f'未知站点 {site!r}；只认识 ' + ', '.join(SITES)) from None


def site_script(site, body, *, connect=True, user='Administrator'):
    """The exact text fed to the interpreter. The body is compiled from a string literal, so
    it keeps its own indentation and may contain triple-quoted strings."""
    lines = ['import json,os,sys,uuid', 'import frappe', f'os.chdir({SITES_DIR!r})',
             f'frappe.init(site={site!r},sites_path={SITES_DIR!r})']
    if connect:
        lines += ['frappe.connect()', f'frappe.set_user({user!r})']
    lines += ['try:',
              f"    exec(compile({body!r},'<site-script>','exec'),globals())",
              'finally:',
              '    frappe.destroy()']
    return '\n'.join(lines) + '\n'


def command_for(site):
    return [*COMPOSE, 'exec', '-T', service_of(site), BENCH_PYTHON, '-']


def run_site_script(site, body, *, timeout=120, connect=True, user='Administrator', run=subprocess.run):
    """Run `body` on `site`; return its stdout. A non-zero exit is an AssertionError carrying
    the last lines of stderr; a timeout is announced to ON_TIMEOUT and re-raised."""
    command = command_for(site)
    try:
        result = run(command, cwd=ROOT, input=site_script(site, body, connect=connect, user=user),
                     text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        for observer in list(ON_TIMEOUT):
            observer(site, body)
        raise
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-15:])
        raise AssertionError(f'站点脚本失败（{site} / {service_of(site)}，退出码 {result.returncode}）：\n{tail}')
    return result.stdout


def run_site_json(site, body, **kwargs):
    """The script's last non-empty stdout line parsed as JSON - the convention every script uses."""
    lines = [line for line in run_site_script(site, body, **kwargs).splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f'站点脚本没有输出（{site}）')
    return json.loads(lines[-1])
```

- [ ] **Step 4: 运行确认通过** → `5 passed`
- [ ] **Step 5: 提交**

```bash
git checkout -b plan5/cleanup
git add tests/integration/site_exec.py tests/test_integration_site_exec.py
git commit -m "test: 集成测试站点脚本统一入口，站点映射到 compose 服务并保证 destroy"
```

### Task C.2: 凭据分类器与精确重发

**Files:** Create `tests/integration/credentials_check.py`、`tests/test_integration_credentials_check.py`；Modify `tests/integration/conftest.py`（替换 `_key_answers_as` 与 `fixture_credentials_agree_with_the_sites`）

**Interfaces:**
- Produces: `classify(profile, *, opener=None, timeout=15) -> (state, detail)`，常量 `OK='ok'`、`UNAUTHENTICATED='unauthenticated'`、`UNREACHABLE='unreachable'`、`PROBE`。只有 `UNAUTHENTICATED` 触发 `infra.run_validation_provision.reissue(actor)`；`UNREACHABLE` → `pytest.fail` 点名站点与端口。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration_credentials_check.py
"""Reissue only a key the Site refuses. A Site that does not answer is a broken stack, and
reissuing into it would rewrite the profile files against a Site that may come back with the
old key intact."""
import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from tests.integration.credentials_check import OK, PROBE, UNAUTHENTICATED, UNREACHABLE, classify

PROFILE = {'user': 'dsherp-reader@example.invalid', 'api_key': 'k', 'api_secret': 's',
           'base_url': 'http://127.0.0.1:18081', 'site': 'dsherp-validation.localhost'}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Opener:
    def __init__(self, outcome):
        self.outcome, self.requests = outcome, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return Response(self.outcome)


def test_a_key_that_answers_as_its_user_is_ok_and_the_probe_carries_site_and_token():
    opener = Opener(json.dumps({'message': PROFILE['user']}).encode())
    assert classify(PROFILE, opener=opener) == (OK, PROFILE['user'])
    request, timeout = opener.requests[0]
    assert request.full_url == PROFILE['base_url'] + PROBE and timeout == 15
    assert request.get_header('X-frappe-site-name') == PROFILE['site']
    assert request.get_header('Authorization') == 'token k:s'


@pytest.mark.parametrize('code', [401, 403])
def test_a_refused_key_is_unauthenticated(code):
    state, detail = classify(PROFILE, opener=Opener(HTTPError('u', code, 'refused', {}, None)))
    assert state == UNAUTHENTICATED and str(code) in detail


def test_a_key_the_site_maps_to_someone_else_is_unauthenticated_too():
    state, _ = classify(PROFILE, opener=Opener(json.dumps({'message': 'Guest'}).encode()))
    assert state == UNAUTHENTICATED


@pytest.mark.parametrize('outcome', [
    URLError(ConnectionRefusedError(61, 'refused')), socket.timeout('timed out'), OSError('boom'),
    HTTPError('u', 502, 'bad gateway', {}, None), HTTPError('u', 404, 'gone', {}, None),
    b'<html>nginx 502</html>', b'[]',
])
def test_anything_else_is_unreachable_never_a_reason_to_reissue(outcome):
    state, detail = classify(PROFILE, opener=Opener(outcome))
    assert state == UNREACHABLE and detail
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# tests/integration/credentials_check.py
"""Does a stored actor profile still authenticate, and if not, why not.

Three answers, two of them actionable in opposite ways: a key the Site refuses (401/403, or
answers as someone else) is reissued through the provisioner; a Site that cannot be reached,
answers with another status, or does not speak JSON is a broken stack and the run stops
naming the site and its port - reissuing into it would rewrite .runtime/erp-*.json against a
Site that may come back holding the old key."""
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

OK, UNAUTHENTICATED, UNREACHABLE = 'ok', 'unauthenticated', 'unreachable'
PROBE = '/api/method/frappe.auth.get_logged_user'


def default_opener():
    return build_opener(ProxyHandler({}))


def classify(profile, *, opener=None, timeout=15):
    """('ok' | 'unauthenticated' | 'unreachable', detail)"""
    opener = opener or default_opener()
    request = Request(profile['base_url'] + PROBE, headers={
        'X-Frappe-Site-Name': profile['site'],
        'Authorization': 'token ' + profile['api_key'] + ':' + profile['api_secret']})
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            return UNAUTHENTICATED, f'HTTP {error.code}'
        return UNREACHABLE, f'HTTP {error.code}'
    except OSError as error:  # URLError, ConnectionRefusedError, socket.timeout are all OSError
        return UNREACHABLE, f'{type(error).__name__}: {getattr(error, "reason", error)}'
    except ValueError as error:
        return UNREACHABLE, f'应答不是 JSON：{error}'
    if not isinstance(payload, dict):
        return UNREACHABLE, '应答不是对象'
    if payload.get('message') == profile['user']:
        return OK, profile['user']
    return UNAUTHENTICATED, f'站点把这把密钥认作 {payload.get("message")!r}'
```

`tests/integration/conftest.py` 中替换（删除 `_key_answers_as` 与旧 fixture）：

```python
from credentials_check import UNAUTHENTICATED, UNREACHABLE, classify

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fixture_credentials_agree_with_the_sites():
    """The synthetic actors' API secrets live in .runtime/erp-*.json. A business credential is
    short-lived (S2): a platform login renews it and a membership revocation kills it - both
    legitimately, both inside this suite - and the files are left behind. Before each test
    the profiles are tried. One the Site refuses is reissued through the provisioner, the one
    path that puts the Site, the files and the platform binding back on one secret. A Site
    that does not answer is not a credential problem: the run stops here, naming it."""
    from infra.run_validation_provision import reissue
    path = ROOT / '.runtime' / 'erp-users.json'
    if path.is_file():
        profiles = json.loads(path.read_text())
        for actor in ('reader', 'denied'):
            if actor not in profiles:
                continue
            state, detail = classify(profiles[actor])
            if state == UNAUTHENTICATED:
                reissue(actor)
            elif state == UNREACHABLE:
                profile = profiles[actor]
                pytest.fail(f"站点 {profile['site']}（{profile['base_url']}）不可达或应答异常：{detail}。"
                            "这不是凭据问题，不重发；先确认 dsherp-validation 栈在运行、backend 端口 18081 可达")
    yield
```

- [ ] **Step 4: 运行确认通过** `.venv/bin/python -m pytest tests/test_integration_credentials_check.py -q` → `11 passed`；本机栈运行中 `pytest tests/integration/test_erp_read.py -q` 通过；把 backend 停掉再跑同一文件，预期首个测试 `FAILED … 不可达`，且 `.runtime/erp-users.json` 的 mtime 未变。
- [ ] **Step 5: 提交** `git commit -m "fix: 集成夹具只在站点拒绝密钥时重发凭据，站点不可达直接失败并点名端口"`

### Task C.3: `residue.py`：登记、清扫计划、清扫脚本、台账

**Files:** Create `tests/integration/residue.py`、`tests/test_integration_residue.py`

**Interfaces:**
- Produces: `Registry(owner, scope='function', *, ledger=LEDGER, run_script=site_exec.run_site_script, run=subprocess.run, clock=time.time)`，方法 `doc(site, doctype, name_or_filters, *, label=None)`、`user(site, email)`、`doctype(site, name)`、`container(name)`、`restore(site, label, script)`、`backup_set(site, set_id)`、`backup_sets_after(site)`、`on_timeout(site, body)`、`active()`、`sweep() -> dict`；模块函数 `sweep_plan(entries)`、`sweep_script(plan)`、`sweep_entries(entries, *, run_script, run)`、`sweep_previous(ledger, *, run_script, run)`、`read_ledger(path)`；异常 `ResidueError(AssertionError)`；常量 `LEDGER = ROOT/'.runtime'/'integration-residue.json'`、`PROTECTED_*`、`OWN_DOCTYPES`
- Consumes: `site_exec`，`dsherp.backup_sets.parse_set_id`，`dsherp.backup.status_path`，`dsherp.backup_status.load/save`，`dsherp.deploy_env.settings`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration_residue.py
"""Say what you are about to leave behind before you leave it; the suite takes it away in
the order the controllers dictate and refuses to touch the persistent fixtures."""
import json
import stat
import subprocess

import pytest

from tests.integration import residue

SITE = 'dsherp-validation.localhost'


class Fake:
    """Records sweep scripts and docker calls; answers as a clean Site unless told otherwise."""

    def __init__(self, leftovers=None, listing=('20260901_020000-dsherp-platform_localhost-aaaaaa',)):
        self.scripts, self.commands = [], []
        self.leftovers = leftovers or []
        self.listing = list(listing)

    def run_script(self, site, body, timeout=120, **kwargs):
        self.scripts.append((site, body, timeout))
        return 'noise\nDSHERP_RESIDUE ' + json.dumps({'removed': {'DS Conversation': 1}, 'leftovers': self.leftovers}) + '\n'

    def run(self, command, **kwargs):
        self.commands.append(list(command))
        text = ' '.join(command)
        out = ''
        if 'ls -1 /home/frappe/backups/sets/' in text:
            out = '\n'.join(self.listing) + '\n'
        elif 'rm -rf' in text:
            doomed = text.split('rm -rf ', 1)[1].split()[0].rsplit('/', 1)[-1]
            self.listing = [name for name in self.listing if name != doomed]
        return subprocess.CompletedProcess(command, 0, out, '')


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / 'runtime' / 'integration-residue.json'


def registry(ledger, fake=None, scope='function', owner='tests/integration/test_x.py::test_one'):
    fake = fake or Fake()
    return residue.Registry(owner, scope, ledger=ledger, run_script=fake.run_script, run=fake.run), fake


def test_a_registration_is_on_disk_private_and_readable_back_before_the_test_creates_anything(ledger):
    reg, _ = registry(ledger)
    entry_id = reg.doc(SITE, 'DS Conversation', {'owner': 'dsherp-reader@example.invalid', 'title': 'probe abc'})
    assert ledger.is_file() and stat.S_IMODE(ledger.stat().st_mode) == 0o600
    payload = residue.read_ledger(ledger)
    assert payload['version'] == 1 and [e['id'] for e in payload['entries']] == [entry_id]
    entry = payload['entries'][0]
    assert entry['kind'] == 'doc' and entry['site'] == SITE and entry['doctype'] == 'DS Conversation'
    assert entry['selector'] == {'owner': 'dsherp-reader@example.invalid', 'title': 'probe abc'}
    assert entry['owner'] == 'tests/integration/test_x.py::test_one' and entry['scope'] == 'function'


@pytest.mark.parametrize('call', [
    lambda r: r.doc(SITE, 'Item', 'DSHERP-TEST-ITEM'),
    lambda r: r.doc(SITE, 'Item', 'DSHERP-MFG-SYN-RM'),
    lambda r: r.doc(SITE, 'Sales Order', 'SAL-ORD-2026-00001'),
    lambda r: r.doc(SITE, 'Item', {'item_code': ['like', 'DSHERP-HITL-%']}),
    lambda r: r.doc(SITE, 'Item', {}),
    lambda r: r.doc(SITE, 'Item', ''),
    lambda r: r.doc('dsherp-validation-backend-1', 'Item', 'X'),
    lambda r: r.user(SITE, 'dsherp-reader@example.invalid'),
    lambda r: r.user(SITE, 'member@example.invalid'),
    lambda r: r.user(SITE, 'someone@example.com'),
    lambda r: r.doctype(SITE, 'DS Model Run'),
    lambda r: r.doctype(SITE, 'Item'),
    lambda r: r.doctype(SITE, 'DS Bad`Name'),
    lambda r: r.container('dsherp-validation-backend-1'),
    lambda r: r.container('dsherp-context-' + 'g' * 32),
    lambda r: r.restore(SITE, 'x', '   '),
    lambda r: r.backup_set('dsherp-platform.localhost', '../evil'),
])
def test_persistent_fixtures_fixed_actors_and_malformed_names_are_refused(ledger, call):
    reg, _ = registry(ledger)
    with pytest.raises(ValueError):
        call(reg)
    assert not ledger.exists()


def test_the_plan_follows_the_guard_order_and_the_script_carries_the_auth_and_ddl_lines(ledger):
    reg, _ = registry(ledger)
    reg.user(SITE, 'unknown-impact-abc@example.invalid')
    reg.doctype(SITE, 'DS Unknown Impact abc')
    reg.doc(SITE, 'DS Unknown Impact abc', {'subject': 'Must remain draft'})
    reg.doc(SITE, 'Stock Entry', {'owner': 'impact-permission-abc@example.invalid', 'docstatus': 0})
    reg.doc(SITE, 'DS Conversation', {'title': 'Unknown stock impact abc'})
    reg.doc(SITE, 'DS Model Run', 'a' * 64)
    plan = residue.sweep_plan(reg.entries)
    assert [step['op'] for step in plan] == ['ds', 'ds', 'document', 'document', 'user', 'doctype', 'verify']
    assert [step['doctype'] for step in plan[:2]] == ['DS Conversation', 'DS Model Run']
    assert plan[-1]['entries'][0]['kind'] == 'user'
    script = residue.sweep_script(plan)
    assert "delete from `__Auth` where doctype='User' and name=%s" in script
    assert "frappe.db.sql_ddl('DROP TABLE `tab' + name + '`')" in script
    assert "frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)" in script
    assert "frappe.db.delete(doctype, {'name': name})" in script
    assert 'doc.cancel()' in script and 'DSHERP_RESIDUE' in script
    chain = script.index("'DS Conversation': [('DS Model Run', 'conversation')")
    assert chain < script.index("'DS Model Run': [('DS Run Event', 'run')")
    compile(script, '<sweep>', 'exec')
    assert residue.sweep_plan(reg.entries) == plan  # deterministic


def test_sweeping_runs_one_script_per_site_then_containers_then_restores_and_forgets_only_its_own_entries(ledger):
    fake = Fake()
    reg, _ = registry(ledger, fake)
    other, _ = registry(ledger, fake, scope='module', owner='tests/integration/test_y.py')
    other.doc('dsherp-beta.localhost', 'DS Doctype Policy', 'BOM')
    reg.doc(SITE, 'DS Conversation', {'title': 'probe abc'})
    reg.doc('dsherp-platform.localhost', 'DS Enterprise', 'matrix-abc')
    reg.container('dsherp-context-' + 'b' * 32)
    reg.restore(SITE, 'heartbeat', "frappe.cache().set_value('dsherp_worker_heartbeat','x')")
    report = reg.sweep()
    sites = [site for site, _, _ in fake.scripts]
    assert sites == [SITE, 'dsherp-platform.localhost', SITE]         # two sweeps, then the restore
    assert 'DSHERP_RESIDUE' in fake.scripts[0][1] and fake.scripts[2][1].startswith("frappe.cache()")
    assert fake.commands == [['docker', 'rm', '-f', 'dsherp-context-' + 'b' * 32]]
    assert report['restored'] == ['heartbeat'] and report['leftovers'] == [] and report['failed'] == []
    remaining = residue.read_ledger(ledger)['entries']
    assert [e['doctype'] for e in remaining] == ['DS Doctype Policy']   # the module's entry survives
    assert reg.entries == []
    reg.sweep()
    assert len(fake.scripts) == 3                                        # nothing left: no second script


def test_leftovers_fail_the_sweep_and_stay_in_the_ledger(ledger):
    fake = Fake(leftovers=[{'id': 'x', 'doctype': 'DS Conversation', 'names': ['abc']}])
    reg, _ = registry(ledger, fake)
    entry_id = reg.doc(SITE, 'DS Conversation', 'abc')
    fake.leftovers[0]['id'] = entry_id
    with pytest.raises(residue.ResidueError) as caught:
        reg.sweep()
    assert 'abc' in str(caught.value)
    assert [e['id'] for e in residue.read_ledger(ledger)['entries']] == [entry_id]


def test_a_failing_sweep_script_is_a_failure_not_a_pass(ledger):
    class Broken(Fake):
        def run_script(self, site, body, timeout=120, **kwargs):
            raise AssertionError('站点脚本失败')
    reg, _ = registry(ledger, Broken())
    reg.user(SITE, 'sso-machine-abc@example.invalid')
    with pytest.raises(residue.ResidueError):
        reg.sweep()


def test_a_timeout_is_noted_in_the_report(ledger):
    reg, _ = registry(ledger)
    reg.doc(SITE, 'DS Conversation', 'abc')
    with reg.active():
        from tests.integration import site_exec
        assert reg.on_timeout in site_exec.ON_TIMEOUT
        reg.on_timeout(SITE, 'frappe.db.commit()')
    assert reg.on_timeout not in site_exec.ON_TIMEOUT
    assert reg.sweep()['interrupted'] == [{'site': SITE, 'body': 'frappe.db.commit()'}]


def test_a_ledger_left_by_a_killed_session_is_swept_at_the_next_start(ledger):
    fake = Fake()
    dead, _ = registry(ledger, fake, owner='tests/integration/test_dead.py::test_killed')
    dead.doc(SITE, 'DS Conversation', {'title': 'killed abc'})
    dead.container('dsherp-context-' + 'c' * 32)
    dead.restore('dsherp-platform.localhost', 'membership', 'print(1)')
    fake.scripts.clear(); fake.commands.clear()
    report = residue.sweep_previous(ledger, run_script=fake.run_script, run=fake.run)
    assert report['entries'] == 3 and report['kept'] == 0
    assert [site for site, _, _ in fake.scripts] == [SITE, 'dsherp-platform.localhost']
    assert fake.commands == [['docker', 'rm', '-f', 'dsherp-context-' + 'c' * 32]]
    assert residue.read_ledger(ledger)['entries'] == []
    assert residue.sweep_previous(ledger, run_script=fake.run_script, run=fake.run) == {'entries': 0}


def test_backup_sets_that_appeared_after_registration_are_removed_both_halves_and_forgotten(ledger, tmp_path, monkeypatch):
    site = 'dsherp-platform.localhost'
    old, new = '20260901_020000-dsherp-platform_localhost-aaaaaa', '20260907_030000-dsherp-platform_localhost-bbbbbb'
    fake = Fake(listing=[old])
    reg, _ = registry(ledger, fake)
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps({'version': 1, 'sets': {
        old: {'site': site, 'state': 'offsite', 'stamp': '20260901_020000'},
        new: {'site': site, 'state': 'staged', 'stamp': '20260907_030000'}}, 'sites': {}, 'runs': {}}))
    monkeypatch.setattr(residue, '_status_file', lambda: status_file)
    reg.backup_sets_after(site)
    fake.listing.append(new)
    fake.listing.append('junk-not-a-set-id')
    report = reg.sweep()
    assert report['backup_sets'] == [new]
    rm = [c for c in fake.commands if 'rm -rf' in ' '.join(c)]
    assert len(rm) == 1 and f'/home/frappe/backups/sets/{site}/{new}' in rm[0][-1] \
        and f'/home/frappe/backup-secrets/{site}/{new}' in rm[0][-1]
    left = json.loads(status_file.read_text())['sets']
    assert new not in left and old in left


def test_the_session_sweep_runs_before_queue_hygiene():
    """Declared order in conftest, checked as AST rather than text."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'tests/integration/conftest.py').read_text())
    hygiene = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'validation_queue_hygiene')
    assert [arg.arg for arg in hygiene.args.args] == ['residue_ledger_swept']
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {'residue', 'module_residue', 'residue_ledger_swept'} <= names
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError: No module named 'tests.integration.residue'`

- [ ] **Step 3: 实现**

```python
# tests/integration/residue.py
"""Residue registry for the integration suite: declare what a test is about to leave on a
Site before it leaves it, and take it away again afterwards - even when the test was killed.

Why registration-first. Most integration tests inject a script into a Site container and
commit; the script's own `finally` is its cleanup. A host-side `subprocess.run(timeout=...)`
kills the docker client, not the interpreter inside the container, so that `finally` never
runs and nothing on the host remembers what was created. So every registration is written to
`.runtime/integration-residue.json` (fsync, 0600) before the test creates anything; teardown
sweeps this test's entries in the order the controllers dictate, verifies nothing is left,
and errors the test otherwise; a ledger left behind by a killed session is swept at the next
session start, and what cannot be swept stays in the ledger until a person deals with it.

How things are removed. Audit DocTypes refuse `on_trash` unconditionally, so they go through
`frappe.db.delete` - the sanctioned test-cleanup path recorded in
docs/engineering/data-governance-evidence.md. Everything else goes through the Document API:
submitted business documents are cancelled first, users lose their `__Auth` row, a synthetic
DocType loses its rows, its DocType document and finally its table.

Hash-named records the product creates through its own API (conversations, runs, bundles)
cannot be registered by name before they exist: register the filters that will identify them
(owner + a unique tag in the title, or creation >= the Site's clock read just before). The
sweep resolves filters to names on the Site, cascades through the audit chain, and verifies
with the same filters."""
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import site_exec  # inside tests/integration, where pytest puts this directory on sys.path
except ImportError:  # imported as tests.integration.residue by the host suite
    from tests.integration import site_exec
from dsherp import backup, backup_sets, backup_status, deploy_env

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / '.runtime' / 'integration-residue.json'
VERSION = 1

PROTECTED_PREFIXES = ('DSHERP-TEST-', 'DSHERP-HITL-', 'DSHERP-MFG-SYN-', 'DSHERP-UI-', 'DSHERP-BETA-')
PROTECTED_NAMES = frozenset({'SAL-ORD-2026-00001', 'Administrator', 'Guest'})
PROTECTED_USERS = frozenset({
    'dsherp-reader@example.invalid', 'dsherp-denied@example.invalid', 'dsherp-writer@example.invalid',
    'dsherp-preview@example.invalid', 'dsherp-context-runtime@example.invalid',
    'daily-operator@example.invalid', 'beta-reader@example.invalid',
    'member@example.invalid', 'operator@example.invalid', 'outsider@example.invalid',
})
# Our own DocTypes are never "synthetic": a test may not drop their tables.
OWN_DOCTYPES = frozenset({
    'DS Model Run', 'DS Run Event', 'DS Operation Proposal', 'DS Execution Record',
    'DS Configuration Bundle', 'DS Configuration Confirmation', 'DS Configuration Execution',
    'DS Configuration Transfer', 'DS Conversation', 'DS Doctype Policy', 'DS Doctype Policy Route',
    'DS Business Credential', 'DS Ops Snapshot', 'DS Enterprise', 'DS Membership', 'DS Agent Task',
})
SYNTHETIC_DOCTYPE = re.compile(r'^DS [A-Za-z0-9][A-Za-z0-9 ]{2,60}$')
CONTAINER = re.compile(r'^dsherp-context-[0-9a-f]{32}$')
# Audit-chain roots: registering one sweeps what hangs off it (CHAIN inside the script).
DS_CHAIN = ('DS Conversation', 'DS Model Run', 'DS Run Event', 'DS Operation Proposal', 'DS Execution Record',
            'DS Configuration Bundle', 'DS Configuration Confirmation', 'DS Configuration Execution',
            'DS Configuration Transfer')
BACKUPS, BACKUP_SECRETS = '/home/frappe/backups', '/home/frappe/backup-secrets'
MARK = 'DSHERP_RESIDUE '


class ResidueError(AssertionError):
    """Something registered is still there after the sweep, or the sweep itself failed."""


# ---- protection ------------------------------------------------------------------------

def _protected_text(value):
    text = str(value)
    return (text in PROTECTED_NAMES or text in PROTECTED_USERS
            or any(prefix in text for prefix in PROTECTED_PREFIXES))


def _refuse_protected(doctype, selector):
    if isinstance(selector, str):
        if not selector.strip():
            raise ValueError('登记的名称不能为空')
        if _protected_text(selector):
            raise ValueError(f'{doctype} {selector!r} 是持久夹具或固定演员，不能登记为残留')
        return
    if not isinstance(selector, dict) or not selector:
        raise ValueError('按条件登记必须给出非空的 filters 字典（空字典会匹配整张表）')
    for key, value in selector.items():
        for atom in (value if isinstance(value, (list, tuple)) else [value]):
            if _protected_text(atom):
                raise ValueError(f'{doctype} 的条件 {key}={atom!r} 会命中持久夹具，拒绝登记')


# ---- the plan and the script ----------------------------------------------------------

def sweep_plan(entries):
    """Ordered steps for one Site's doc/user/doctype entries (pure).

    Audit-chain roots first (they cascade), other documents, users, synthetic DocTypes,
    then the verification of every entry. Steps are data; the script interprets them."""
    plan = []
    for entry in entries:
        if entry['kind'] == 'doc' and entry['doctype'] in DS_CHAIN:
            plan.append({'op': 'ds', 'doctype': entry['doctype'], 'selector': entry['selector'], 'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'doc' and entry['doctype'] not in DS_CHAIN:
            plan.append({'op': 'document', 'doctype': entry['doctype'], 'selector': entry['selector'], 'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'user':
            plan.append({'op': 'user', 'name': entry['name'], 'id': entry['id']})
    for entry in entries:
        if entry['kind'] == 'doctype':
            plan.append({'op': 'doctype', 'name': entry['name'], 'id': entry['id']})
    plan.append({'op': 'verify', 'entries': [
        {'id': entry['id'], 'kind': entry['kind'], 'doctype': entry.get('doctype'),
         'selector': entry.get('selector', entry.get('name'))} for entry in entries]})
    return plan


SWEEP_TEMPLATE = r'''
PLAN = json.loads(PLAN_JSON)
CHAIN = {
    'DS Conversation': [('DS Model Run', 'conversation'), ('DS Operation Proposal', 'conversation'),
                        ('DS Configuration Bundle', 'conversation')],
    'DS Model Run': [('DS Run Event', 'run'), ('DS Operation Proposal', 'model_run')],
    'DS Operation Proposal': [('DS Execution Record', 'proposal')],
    'DS Configuration Bundle': [('DS Configuration Confirmation', 'bundle'), ('DS Configuration Transfer', 'bundle')],
    'DS Configuration Confirmation': [('DS Configuration Execution', 'confirmation')],
}
removed = {}
resolved = {}


def count(key, n=1):
    removed[key] = removed.get(key, 0) + n


def names_of(doctype, selector):
    if isinstance(selector, str):
        return [selector] if frappe.db.exists(doctype, selector) else []
    return frappe.get_all(doctype, filters=selector, pluck='name')


def sweep_ds(doctype, names):
    for name in names:
        for child, link in CHAIN.get(doctype, []):
            sweep_ds(child, frappe.get_all(child, filters={link: name}, pluck='name'))
        if doctype == 'DS Conversation':
            frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)
        else:
            frappe.db.delete(doctype, {'name': name})
        count(doctype)


def sweep_document(doctype, names):
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
        count(doctype)


def sweep_user(email):
    frappe.db.delete('DS Business Credential', {'user': email})
    if frappe.db.exists('User', email):
        frappe.delete_doc('User', email, force=True, ignore_permissions=True)
        count('User')
    frappe.db.sql("delete from `__Auth` where doctype='User' and name=%s", (email,))


def sweep_doctype(name):
    frappe.db.delete('DS Doctype Policy', {'target_doctype': name})
    if frappe.db.exists('DocType', name):
        if frappe.db.table_exists(name, cached=False):
            sweep_document(name, frappe.get_all(name, pluck='name'))
        frappe.delete_doc('DocType', name, force=True, ignore_permissions=True)
        count('DocType')
    frappe.db.commit()
    if frappe.db.table_exists(name, cached=False):
        frappe.db.sql_ddl('DROP TABLE `tab' + name + '`')
    frappe.clear_cache(doctype=name)


for step in PLAN:
    if step['op'] in ('ds', 'document'):
        resolved[step['id']] = names_of(step['doctype'], step['selector'])
for step in PLAN:
    if step['op'] == 'ds':
        sweep_ds(step['doctype'], resolved[step['id']])
    elif step['op'] == 'document':
        sweep_document(step['doctype'], resolved[step['id']])
    elif step['op'] == 'user':
        sweep_user(step['name'])
    elif step['op'] == 'doctype':
        sweep_doctype(step['name'])
frappe.db.commit()

leftovers = []
for item in PLAN[-1]['entries']:
    if item['kind'] == 'doc':
        left = names_of(item['doctype'], item['selector'])
        for name in resolved.get(item['id'], []):
            if frappe.db.exists(item['doctype'], name):
                left.append(name)
            for child, link in CHAIN.get(item['doctype'], []):
                left += [child + ':' + row for row in frappe.get_all(child, filters={link: name}, pluck='name')]
        if left:
            leftovers.append({'id': item['id'], 'doctype': item['doctype'], 'names': sorted(set(left))})
    elif item['kind'] == 'user':
        auth = frappe.db.sql("select count(*) from `__Auth` where doctype='User' and name=%s", (item['selector'],))[0][0]
        if frappe.db.exists('User', item['selector']) or auth:
            leftovers.append({'id': item['id'], 'doctype': 'User', 'names': [item['selector']]})
    elif item['kind'] == 'doctype':
        if (frappe.db.exists('DocType', item['selector']) or frappe.db.table_exists(item['selector'], cached=False)
                or frappe.db.exists('DS Doctype Policy', {'target_doctype': item['selector']})):
            leftovers.append({'id': item['id'], 'doctype': 'DocType', 'names': [item['selector']]})
print('DSHERP_RESIDUE ' + json.dumps({'removed': removed, 'leftovers': leftovers}, ensure_ascii=False))
'''


def sweep_script(plan):
    """The body run_site_script executes on one Site for one plan."""
    return SWEEP_TEMPLATE.replace('PLAN_JSON', repr(json.dumps(plan)), 1)


def _marked(output):
    lines = [line for line in output.splitlines() if line.startswith(MARK)]
    if not lines:
        raise ResidueError('清扫脚本没有输出 DSHERP_RESIDUE 行：' + output[-300:])
    return json.loads(lines[-1][len(MARK):])


# ---- ledger --------------------------------------------------------------------------------

def _write_ledger(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_ledger(path=LEDGER):
    path = Path(path)
    if not path.exists():
        return {'version': VERSION, 'entries': []}
    payload = json.loads(path.read_text())
    if payload.get('version') != VERSION or not isinstance(payload.get('entries'), list):
        raise ResidueError(f'残留台账 {path} 不是版本 {VERSION} 的台账；先人工检查再处理它')
    return payload


# ---- backup sets (platform bench volumes + host status file) ------------------------------

def _list_sets(run, site):
    result = run([*site_exec.COMPOSE, 'exec', '-T', 'platform-backend', 'sh', '-c',
                  f'ls -1 {BACKUPS}/sets/{site} 2>/dev/null || true'],
                 cwd=ROOT, text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise ResidueError('读不到平台备份集目录：' + (result.stderr or '')[-300:])
    return sorted(name for name in result.stdout.split() if backup_sets.parse_set_id(name))


def _status_file():
    return backup.status_path(deploy_env.settings({'DSHERP_ENV': 'dev'}))


def _forget_staged(doomed):
    """A set that never left the host is forgotten with its files; anything that reached a
    repository keeps its row, as production's own local prune does."""
    path = _status_file()
    status = backup_status.load(path)
    if not status:
        return
    for set_id in doomed:
        row = status['sets'].get(set_id)
        if row and row.get('state') == 'staged':
            del status['sets'][set_id]
    backup_status.save(path, status)


def _prune_sets(entry, run):
    site = entry['site']
    listing = _list_sets(run, site)
    if entry['kind'] == 'backup_sets_after':
        doomed = [set_id for set_id in listing if set_id not in entry['known']]
    else:
        doomed = [set_id for set_id in entry['set_ids'] if set_id in listing]
    for set_id in doomed:
        if not backup_sets.parse_set_id(set_id):
            raise ResidueError(f'{set_id!r} 不是备份集 id，拒绝删除')
        result = run([*site_exec.COMPOSE, 'exec', '-T', 'platform-backend', 'sh', '-c',
                      f'rm -rf {BACKUPS}/sets/{site}/{set_id} {BACKUP_SECRETS}/{site}/{set_id}'],
                     cwd=ROOT, text=True, capture_output=True, timeout=600)
        if result.returncode:
            raise ResidueError(f'删除备份集 {set_id} 失败：' + (result.stderr or '')[-300:])
    remaining = set(_list_sets(run, site)) & set(doomed)
    if remaining:
        raise ResidueError('备份集删除后仍在：' + ', '.join(sorted(remaining)))
    _forget_staged(doomed)
    return doomed


# ---- sweeping ----------------------------------------------------------------------------

def sweep_entries(entries, *, run_script=site_exec.run_site_script, run=subprocess.run):
    """Docs/users/doctypes per Site, then containers and backup sets, then restore scripts.
    `clean` lists the ids that are known to be gone; everything else stays in the ledger."""
    report = {'removed': {}, 'leftovers': [], 'failed': [], 'restored': [], 'containers': [],
              'backup_sets': [], 'clean': []}
    by_site = {}
    for entry in entries:
        if entry['kind'] in ('doc', 'user', 'doctype'):
            by_site.setdefault(entry['site'], []).append(entry)
    for site, rows in by_site.items():
        try:
            outcome = _marked(run_script(site, sweep_script(sweep_plan(rows)), timeout=300))
        except Exception as error:  # the script itself failed: nothing here is known to be gone
            report['failed'].append({'site': site, 'error': str(error)[-500:], 'ids': [row['id'] for row in rows]})
            continue
        report['removed'][site] = outcome['removed']
        bad = {item['id'] for item in outcome['leftovers']}
        report['leftovers'] += [{'site': site, **item} for item in outcome['leftovers']]
        report['clean'] += [row['id'] for row in rows if row['id'] not in bad]
    for entry in entries:
        if entry['kind'] == 'container':
            result = run(['docker', 'rm', '-f', entry['name']], text=True, capture_output=True, timeout=60)
            gone = result.returncode == 0 or 'No such container' in (result.stderr or '')
            if gone:
                report['containers'].append(entry['name'])
                report['clean'].append(entry['id'])
            else:
                report['failed'].append({'container': entry['name'], 'error': (result.stderr or '')[-200:]})
        elif entry['kind'] in ('backup_set', 'backup_sets_after'):
            try:
                report['backup_sets'] += _prune_sets(entry, run)
                report['clean'].append(entry['id'])
            except ResidueError as error:
                report['failed'].append({'site': entry['site'], 'error': str(error)})
    for entry in entries:
        if entry['kind'] == 'restore':
            try:
                run_script(entry['site'], entry['script'], timeout=120)
                report['restored'].append(entry['label'])
                report['clean'].append(entry['id'])
            except Exception as error:
                report['failed'].append({'site': entry['site'], 'label': entry['label'], 'error': str(error)[-500:]})
    return report


def sweep_previous(ledger=LEDGER, *, run_script=site_exec.run_site_script, run=subprocess.run):
    """Sweep what a killed session left in the ledger; keep what could not be swept."""
    payload = read_ledger(ledger)
    if not payload['entries']:
        return {'entries': 0}
    report = sweep_entries(payload['entries'], run_script=run_script, run=run)
    clean = set(report['clean'])
    payload['entries'] = [entry for entry in payload['entries'] if entry['id'] not in clean]
    _write_ledger(ledger, payload)
    report['entries'] = len(clean) + len(payload['entries'])
    report['kept'] = len(payload['entries'])
    if report['leftovers'] or report['failed']:
        raise ResidueError('上一会话的残留清扫不完整；人工处理后修正或删除台账再运行：'
                           + json.dumps(report, ensure_ascii=False))
    return report


class Registry:
    """What one test (or one module) is about to leave behind, and how to take it away."""

    def __init__(self, owner, scope='function', *, ledger=LEDGER, run_script=site_exec.run_site_script,
                 run=subprocess.run, clock=time.time):
        self.owner, self.scope = owner, scope
        self.ledger = Path(ledger)
        self.run_script, self.run, self.clock = run_script, run, clock
        self.entries = []
        self.interrupted = []

    # -- registration: durable before it returns --------------------------------------
    def _add(self, **fields):
        entry = {'id': uuid.uuid4().hex, 'owner': self.owner, 'scope': self.scope,
                 'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock())), **fields}
        ledger = read_ledger(self.ledger)
        ledger['entries'].append(entry)
        _write_ledger(self.ledger, ledger)
        self.entries.append(entry)
        return entry['id']

    def doc(self, site, doctype, name_or_filters, *, label=None):
        site_exec.service_of(site)
        _refuse_protected(doctype, name_or_filters)
        return self._add(kind='doc', site=site, doctype=doctype, selector=name_or_filters, label=label)

    def user(self, site, email):
        site_exec.service_of(site)
        if email in PROTECTED_USERS or not email.endswith('@example.invalid'):
            raise ValueError(f'{email!r} 不是可清扫的临时用户（固定演员或不在 example.invalid）')
        return self._add(kind='user', site=site, name=email)

    def doctype(self, site, name):
        site_exec.service_of(site)
        if name in OWN_DOCTYPES or not SYNTHETIC_DOCTYPE.fullmatch(name):
            raise ValueError(f'{name!r} 不是可丢弃的合成 DocType（须形如 "DS Xxx"，且不是本产品的 DocType）')
        return self._add(kind='doctype', site=site, name=name)

    def container(self, name):
        if not CONTAINER.fullmatch(name):
            raise ValueError(f'{name!r} 不是运行容器名（dsherp-context-<32hex>）')
        return self._add(kind='container', name=name)

    def restore(self, site, label, script):
        """A body re-run at teardown to put shared state back; it must be idempotent."""
        site_exec.service_of(site)
        if not script.strip():
            raise ValueError('恢复脚本不能为空')
        return self._add(kind='restore', site=site, label=label, script=script)

    def backup_set(self, site, set_id):
        if not backup_sets.parse_set_id(set_id):
            raise ValueError(f'{set_id!r} 不是备份集 id')
        return self._add(kind='backup_set', site=site, set_ids=[set_id])

    def backup_sets_after(self, site):
        """Every set that appears under this Site's set directory from now on."""
        return self._add(kind='backup_sets_after', site=site, known=_list_sets(self.run, site))

    # -- timeouts ----------------------------------------------------------------------
    def on_timeout(self, site, body):
        self.interrupted.append({'site': site, 'body': body.strip()[:160]})

    @contextmanager
    def active(self):
        site_exec.ON_TIMEOUT.append(self.on_timeout)
        try:
            yield self
        finally:
            site_exec.ON_TIMEOUT.remove(self.on_timeout)

    # -- teardown -----------------------------------------------------------------------
    def sweep(self):
        """Remove everything this registry holds; forget what is gone; raise on the rest."""
        if not self.entries:
            return {'removed': {}, 'leftovers': [], 'failed': [], 'restored': [], 'containers': [],
                    'backup_sets': [], 'clean': [], 'interrupted': list(self.interrupted)}
        report = sweep_entries(self.entries, run_script=self.run_script, run=self.run)
        report['interrupted'] = list(self.interrupted)
        clean = set(report['clean'])
        ledger = read_ledger(self.ledger)
        ledger['entries'] = [entry for entry in ledger['entries'] if entry['id'] not in clean]
        _write_ledger(self.ledger, ledger)
        self.entries = [entry for entry in self.entries if entry['id'] not in clean]
        if report['leftovers'] or report['failed']:
            raise ResidueError('清扫后仍有残留或清扫失败（条目保留在台账里）：' + json.dumps(report, ensure_ascii=False))
        return report
```

- [ ] **Step 4: 运行确认通过** `.venv/bin/python -m pytest tests/test_integration_residue.py -q` → 26 passed（`test_the_session_sweep_runs_before_queue_hygiene` 在 Task C.4 后绿）。容器内核对一次并记证据：`grep -n 'def table_exists' /home/frappe/frappe-bench/apps/frappe/frappe/database/database.py` 签名含 `cached=True`；若不含，把模板里的 `cached=False` 改为 DROP 后 `frappe.db.get_tables(cached=False)`。
- [ ] **Step 5: 提交** `git commit -m "test: 集成残留登记台账——先登记再创建，按守卫顺序清扫并核验零残留"`

### Task C.4: conftest 接入：`residue`/`module_residue`、会话起始清扫

**Files:** Modify `tests/integration/conftest.py`

- [ ] **Step 1–2:** Task C.3 的 `test_the_session_sweep_runs_before_queue_hygiene` 此时红。
- [ ] **Step 3: 实现**（顶部 `import residue as residue_module`）

```python
@pytest.fixture(scope='session', autouse=True)
def residue_ledger_swept(request):
    """A session that was killed left its registrations in the ledger; take them away first,
    and say what went. Anything that cannot be swept stops the session here."""
    report = residue_module.sweep_previous()
    if report.get('entries'):
        reporter = request.config.pluginmanager.get_plugin('terminalreporter')
        if reporter is not None:
            reporter.write_line('[residue] 清扫了上一会话遗留的登记：' + json.dumps(
                {key: report[key] for key in ('removed', 'restored', 'containers', 'backup_sets')},
                ensure_ascii=False))
    yield


@pytest.fixture(scope='session', autouse=True)
def validation_queue_hygiene(residue_ledger_swept):
    purge_validation_jobs()
    seed_validation_worker_heartbeat()
    try:
        yield
    finally:
        purge_validation_jobs()


@pytest.fixture
def residue(request):
    """Register what this test is about to create, before creating it; it is swept and
    verified at teardown, and leftovers error the test."""
    registry = residue_module.Registry(request.node.nodeid, 'function')
    with registry.active():
        yield registry
    registry.sweep()


@pytest.fixture(scope='module')
def module_residue(request):
    registry = residue_module.Registry(request.node.nodeid, 'module')
    with registry.active():
        yield registry
    registry.sweep()
```

- [ ] **Step 4: 运行确认通过** 宿主 `tests/test_integration_residue.py` 全绿；本机（worker/scheduled 停）`pytest tests/integration/test_context_sessions.py -q` 通过；手工验证会话起始清扫：

```bash
printf '{"version":1,"entries":[{"id":"manual1","owner":"manual","scope":"function","registered_at":"2026-09-07T00:00:00Z","kind":"doc","site":"dsherp-validation.localhost","doctype":"DS Conversation","selector":{"title":"residue-manual-probe"}}]}' > .runtime/integration-residue.json
.venv/bin/python -m pytest tests/integration/test_erp_read.py -m integration -q -s | grep residue
cat .runtime/integration-residue.json
```
Expected: 输出 `[residue] 清扫了上一会话遗留的登记：…`；台账 `"entries": []`，权限 0600。
- [ ] **Step 5: 提交** `git commit -m "test: 集成 conftest 接入残留登记——会话先清扫上次遗留，测试级/模块级登记在 teardown 清扫核验"`

### Task C.5: `test_run_grants.py` 与 `test_queue_backpressure.py`（会话按条件登记 + 心跳恢复）

**Interfaces:** `residue.doc(site, 'DS Conversation', {'owner': actor, 'title': <带 tag 的提问>})`（`send_message` 的会话标题就是提问前 100 字，`context_api.py:351`）、`residue.restore(site, 'worker heartbeat', HEARTBEAT_RESTORE)`；`site_exec.run_site_json`。

- [ ] **Step 1: 改写**

`test_run_grants.py`（去掉脚本末尾的直行删除——正是"超时后永不执行"的那段）：

```python
"""The grant a background executor acts under lives beside the run, not in it (R7), and an
executor with no grant on file is refused wherever SSO is enforced (R6). Real Site."""
import uuid

from site_exec import run_site_json

SITE = 'dsherp-validation.localhost'
ACTOR = 'dsherp-reader@example.invalid'
CONTEXT = "{'schema_version':1,'page_type':'unknown','route':[]}"


def test_the_grant_is_cached_for_the_run_never_stored_in_it_and_dropped_when_it_ends(residue):
    question = f'Grant placement probe {uuid.uuid4().hex}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    out = run_site_json(SITE, r'''
from frappe.utils.password import encrypt
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor=ACTOR;frappe.set_user(actor)
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha','binding_version':'1','enterprise_version':'v1'}
sso.identity_for_token=lambda token:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
doc=api.send_message(QUESTION,CONTEXT,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={}
out['column_present']='platform_grant' in frappe.db.get_table_columns('DS Model Run')
out['cached']=grants.of(run)==grant
out['row_mentions_token']='synthetic-token' in json.dumps(frappe.db.get_value('DS Model Run',run,'*',as_dict=True),default=str)
frappe.session.data.pop('dsherp_platform_grant',None)
frappe.conf.dsherp_runtime_user=actor
claim=execution.claim_run('a'*64);frappe.db.commit()
out['claimed']=bool(claim)
frappe.set_user('Guest')
execution.finish_run(run_id=claim['run_id'],capability=claim['capability'],status='Failed',error='probe end');frappe.db.commit()
out['cached_after_finish']=grants.of(run)
print(json.dumps(out))
'''.replace('ACTOR', repr(ACTOR)).replace('QUESTION', repr(question)).replace('CONTEXT', CONTEXT), timeout=180)
    assert out['column_present'] is False and out['cached'] is True, out
    assert out['row_mentions_token'] is False, out
    assert out['claimed'] is True and out['cached_after_finish'] is None, out


def test_an_executor_without_a_grant_is_refused_where_sso_is_enforced_and_the_run_fails_closed(residue):
    question = f'No-grant probe {uuid.uuid4().hex}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    out = run_site_json(SITE, r'''
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor=ACTOR;frappe.set_user(actor)
frappe.session.data.pop('dsherp_platform_grant',None)
doc=api.send_message(QUESTION,CONTEXT,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={'cached':grants.of(run)}
frappe.conf.dsherp_sso_required=1
frappe.conf.dsherp_runtime_user=actor
claim=execution.claim_run('a'*64);frappe.db.commit()
out['claim']=claim
out['status']=frappe.db.get_value('DS Model Run',run,'status')
out['error']=frappe.db.get_value('DS Model Run',run,'error')
print(json.dumps(out,ensure_ascii=False))
'''.replace('ACTOR', repr(ACTOR)).replace('QUESTION', repr(question)).replace('CONTEXT', CONTEXT), timeout=180)
    assert out['cached'] is None and out['claim'] is None, out
    assert out['status'] == 'Failed', out
```

`test_queue_backpressure.py`：模块常量与两处登记（其余脚本体保留，只删 init/connect/destroy 行；脚本内 `finally` 删除保留作双保险）：

```python
from site_exec import run_site_json, run_site_script

SITE = 'dsherp-validation.localhost'
ACTOR = 'dsherp-reader@example.invalid'
# Idempotent: puts a fresh heartbeat back whatever the script left.
HEARTBEAT_RESTORE = r'''
from frappe.utils import now_datetime
value=now_datetime().isoformat()
frappe.cache().set_value('dsherp_worker_heartbeat',value,expires_in_sec=3600)
print(json.dumps({'heartbeat':value}))
'''


def test_send_message_rejects_unavailable_worker_and_sets_queue_expiry(residue):
    question = f'fresh heartbeat {uuid.uuid4().hex}'
    residue.restore(SITE, 'worker heartbeat', HEARTBEAT_RESTORE)
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    output = run_site_script(SITE, SCRIPT.replace('FRESH_QUESTION', repr(question)), timeout=90)
    assert 'QUEUE_BACKPRESSURE_OK' in output, output


def test_send_message_http_rejects_unavailable_worker_with_503(residue):
    reader = json.loads(Path('.runtime/erp-users.json').read_text())['reader']
    residue.restore(SITE, 'worker heartbeat', HEARTBEAT_RESTORE)
    state = run_site_json(SITE, r'''
cache=frappe.cache()
previous=cache.get_value('dsherp_worker_heartbeat')
cache.delete_value('dsherp_worker_heartbeat')
if isinstance(previous,(bytes,bytearray)):previous=previous.decode()
elif previous is not None:previous=str(previous)
print(json.dumps({'previous':previous,'runs':frappe.get_all('DS Model Run',pluck='name')}))
''')
    try:
        ...（httpx 段不变）...
        after = run_site_json(SITE, "print(json.dumps(frappe.get_all('DS Model Run',pluck='name')))")
        assert set(after) == set(state['runs'])
    finally:
        run_site_script(SITE, ("cache=frappe.cache()\nprevious=%r\n"
                               "if previous is None:cache.delete_value('dsherp_worker_heartbeat')\n"
                               "else:cache.set_value('dsherp_worker_heartbeat',previous,expires_in_sec=3600)\n") % (state['previous'],))
```

- [ ] **Step 2–4: 运行**（本机，worker/scheduled 已停）`pytest tests/integration/test_run_grants.py tests/integration/test_queue_backpressure.py -m integration -q` → 3 passed；台账为空；容器内核对三种标题前缀的会话计数为 `0 0 0`。**超时路径**：把第一个测试 `timeout=180` 临时改为 `timeout=1` 跑一次，预期 `TimeoutExpired`、teardown 报告含 `interrupted`、计数仍为 0；改回。
- [ ] **Step 5: 提交** `git commit -m "test: 授权缓存与队列背压测试改为先登记会话与心跳恢复再创建，超时也能清扫"`

### Task C.6: `test_stock_impact_fastfail.py`（合成 DocType 的 DDL 残留）

```python
import uuid

from site_exec import run_site_script

SITE = 'dsherp-validation.localhost'
CLEAR_STOCK_META = "frappe.clear_cache(doctype='Stock Entry');frappe.clear_cache(doctype='Stock Entry Detail');print('cleared')"


def test_action_impact_uses_exact_stock_and_no_stock_registries():
    run_site_script(SITE, SCRIPT_1, timeout=30)   # rollback-only: nothing to register


def test_unknown_submittable_doctype_cannot_propose_an_unverified_stock_impact(residue):
    tag = uuid.uuid4().hex
    doctype = 'DS Unknown Impact ' + tag[:10]
    actor = 'unknown-impact-' + tag + '@example.invalid'
    residue.doctype(SITE, doctype)                                   # rows, policy row, DocType, DROP TABLE
    residue.user(SITE, actor)
    residue.doc(SITE, 'DS Conversation', {'title': 'Unknown stock impact ' + tag})
    run_site_script(SITE, SCRIPT_2.replace('TAG', repr(tag)), timeout=60)


def test_registered_stock_impact_checks_parent_and_child_field_access_before_values(residue):
    tag = uuid.uuid4().hex
    actor = 'impact-permission-' + tag + '@example.invalid'
    residue.doc(SITE, 'Property Setter', {'doc_type': ['in', ['Stock Entry', 'Stock Entry Detail']],
                                          'field_name': ['in', ['items', 's_warehouse']], 'property': 'permlevel'})
    residue.restore(SITE, 'stock entry meta cache', CLEAR_STOCK_META)
    residue.doc(SITE, 'DS Conversation', {'title': 'Impact field permission ' + tag})
    residue.doc(SITE, 'Stock Entry', {'owner': actor, 'docstatus': 0})
    residue.user(SITE, actor)
    run_site_script(SITE, SCRIPT_3.replace('TAG', repr(tag)), timeout=80)
```
`SCRIPT_1/2/3` 为原脚本去壳，`tag=uuid.uuid4().hex` 改为 `tag=TAG`；`finally` 段整体保留。运行：`3 passed`；台账清空；容器内 `DocType like 'DS Unknown Impact %'`、`show tables like 'tabDS Unknown Impact %'`、两类临时用户均为空。超时路径：第二个测试 `timeout=2` 跑一次，预期 teardown 清扫掉 DocType 与表；改回。提交 `test: 未知库存影响测试先登记合成 DocType、用户与会话，DDL 残留由清扫兜底`。

### Task C.7: `test_configuration_transfer_http.py`（两站点残留）

```python
"""Two actual Sites and signed internal HTTP; no model or business DDL."""
import uuid

from site_exec import run_site_json, run_site_script

SOURCE, PREVIEW = 'dsherp-validation.localhost', 'dsherp-beta.localhost'
PREVIEW_ACTOR = 'dsherp-preview@example.invalid'
PACKAGE = ("{'version':1,'doctypes':[{'name':'DS HTTP Transfer Test','module':'DSHERP Bridge',"
           "'fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],"
           "'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}")


def test_alpha_package_is_imported_once_into_beta_with_live_source_authorization(residue):
    title = f'Two Site transfer test {uuid.uuid4().hex}'
    residue.doc(SOURCE, 'DS Conversation', {'owner': PREVIEW_ACTOR, 'title': title})
    prepared = run_site_json(SOURCE, r'''
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer
conversation=frappe.get_doc({'doctype':'DS Conversation','title':TITLE}).insert(ignore_permissions=True)
package=PACKAGE
bundle=propose_bundle(conversation.name,package);frappe.db.commit()
assert not bundle['preview_available'] and bundle['preview_transfer_available']
transfer=prepare_transfer(bundle['id'],bundle['digest'],'two-site-transfer-request')
frappe.db.commit();print(json.dumps({'session':conversation.name,'bundle':bundle['id'],'transfer':transfer['id'],'digest':bundle['digest']}))
'''.replace('TITLE', repr(title)).replace('PACKAGE', PACKAGE), user=PREVIEW_ACTOR, timeout=50)
    # What accept_transfer will create on the preview Site is hash-named; register it by the
    # preview Site's own clock read now, before the call.
    since = run_site_json(PREVIEW, "from frappe.utils import now_datetime\nprint(json.dumps(str(now_datetime())))")
    residue.doc(PREVIEW, 'DS Configuration Bundle', {'owner': PREVIEW_ACTOR, 'creation': ['>=', since]})
    residue.doc(PREVIEW, 'DS Conversation', {'owner': PREVIEW_ACTOR, 'title': '隔离配置预览', 'creation': ['>=', since]})
    imported = run_site_json(PREVIEW, r'''
from dsherp_bridge.configuration_transfer import accept_transfer
from dsherp_bridge.configuration_execution import prepare_preview
result=accept_transfer(TRANSFER)
again=accept_transfer(TRANSFER);assert again['bundle']['id']==result['bundle']['id']
doc=frappe.get_doc('DS Configuration Bundle',result['bundle']['id']);origin=json.loads(doc.payload)['origin']
assert origin['source_site']=='dsherp-validation.localhost' and origin['bundle_digest']==DIGEST
confirmation=prepare_preview(doc.name,doc.digest)
assert not frappe.db.exists('DocType','DS HTTP Transfer Test')
frappe.db.commit();print(json.dumps({'session':result['session_id'],'bundle':doc.name,'confirmation':confirmation['id']}))
'''.replace('TRANSFER', repr(prepared['transfer'])).replace('DIGEST', repr(prepared['digest'])), user=PREVIEW_ACTOR, timeout=50)
    assert imported['bundle'] and imported['confirmation']
    run_site_script(SOURCE, "assert not frappe.db.exists('DocType','DS HTTP Transfer Test')")
```
原来两段 `finally` 清理删除（只在赋值后才跑，超时不跑，且与登记清扫重复）。切片 E 会在此文件再加过期中继的断言（届时按切片 E 的说明改）。运行 → `1 passed`，台账清空，两站会话计数与跑前基线一致；超时路径 `timeout=1` 验证两站各清扫一次。提交 `test: 两站配置交接测试按来源会话与预览站时钟登记残留，预览失败时来源侧也清扫`。

### Task C.8: `test_policy_seed.py` 与 `test_manufacturing_fixture.py`（共享状态恢复）

`test_policy_seed.py`：

```python
from site_exec import run_site_json, run_site_script

ALPHA = 'dsherp-validation.localhost'
# Idempotent: only writes (with a fresh change_reason, which the policy demands) when the
# routes differ from the provisioned ones.
ROUTES_RESTORE = r'''
expected=json.loads(EXPECTED)
policy=frappe.get_doc('DS Doctype Policy','Sales Order')
current=[{'route_name':r.route_name,'method_path':r.method_path,'target_doctype':r.target_doctype} for r in policy.routes]
if current!=expected:
    policy.set('routes',expected);policy.change_reason='集成测试恢复 '+frappe.generate_hash(length=8);policy.save();frappe.db.commit()
print(json.dumps({'restored':current!=expected}))
'''


def _read_policy_rows(site, service=None):
    return run_site_json(site, READ_ROWS_BODY, timeout=30)


def _set_alpha_sales_order_routes(routes):
    run_site_script(ALPHA, SET_ROUTES_BODY.replace('__ROUTES__', repr(json.dumps(routes))), timeout=30)


def routes_restore():
    return ROUTES_RESTORE.replace('EXPECTED', repr(json.dumps(EXPECTED_ALPHA_SALES_ORDER['routes'])))
```
四处改动：(1) `test_policy_readback_does_not_hide_an_unexpected_extra_row(residue)`：`create` 之前 `residue.doc('dsherp-beta.localhost', 'DS Doctype Policy', 'BOM')`；(2)(3) 两个改写 alpha 路由的测试在 `_set_alpha_sales_order_routes(...)` 之前 `residue.restore(ALPHA, 'alpha Sales Order routes', routes_restore())`；(4) `finally` 全部保留。

`test_manufacturing_fixture.py`：`TRANSITION` 常量（原 rf-string 体去壳，`mode = {mode!r}` 改 `mode = __MODE__`），`CURRENT_RESTORE = TRANSITION.replace('__MODE__', repr('current'))`，`_transition_persistent_fixture(mode)` 走 `run_site_json`；三个调用漂移模式的测试加 `residue` 参数并在过渡调用**之前** `residue.restore(ALPHA, 'manufacturing fixture current', CURRENT_RESTORE)`；`_read_probe_state`/`_read_fixture_state` 改用 `run_site_json`。

运行：两文件全部通过（policy_seed 共 27 条含参数化）；台账清空。恢复路径：手工 `_set_alpha_sales_order_routes([])` 后让一个已登记 restore 的测试 `timeout=1` 超时，预期 `restored: ['alpha Sales Order routes']` 且 `_read_policy_rows` 回到 `EXPECTED_ALL_ROWS`；制造夹具同理。提交 `test: 策略种子与制造夹具测试先登记恢复脚本再改共享状态，超时也能复原`。

### Task C.9: `test_platform_identity_revocation.py`（平台侧共享状态）

```python
from site_exec import run_site_script

PLATFORM = 'dsherp-platform.localhost'
MEMBER = 'member@example.invalid'
# Idempotent. Re-enabling bumps binding_version (enabled is a binding field): that is the
# behaviour the test already relied on through its own finally.
MEMBERSHIP_RESTORE = r'''
name=frappe.db.get_value('DS Membership',{'enterprise':'alpha','platform_user':'member@example.invalid'},'name')
assert name,'alpha membership for member@example.invalid is missing'
doc=frappe.get_doc('DS Membership',name)
if not doc.enabled:
    doc.enabled=1;doc.save();frappe.db.commit()
print(json.dumps({'enabled':frappe.db.get_value('DS Membership',name,'enabled')}))
'''
CONCURRENT_REVOCATION = r'''…原体去壳（用户由 site_exec 设置；writer 子进程不变；finally 里去掉 frappe.destroy()）…'''


def test_revocation_committed_during_identity_check_stops_business_read(residue):
    residue.restore(PLATFORM, 'alpha membership of member@example.invalid enabled', MEMBERSHIP_RESTORE)
    run_site_script(PLATFORM, CONCURRENT_REVOCATION, user=MEMBER, timeout=90)
```
运行 `test_platform_identity_revocation.py` + `test_platform_identity.py` 通过；平台容器内该绑定 `enabled` 为 1；超时路径 `timeout=1` 验证 `restored`（停用触发的 S2 吊销由下一测试的凭据夹具以 `unauthenticated` 路径重发——设计内的合法轮换）。提交 `test: 并发撤销测试先登记成员绑定恢复脚本，中断后绑定仍会回到启用`。

### Task C.10: `test_agent_boundary.py`（容器）与 `test_backup_sets_real.py`（备份集）

```python
# test_agent_boundary.py
@pytest.fixture(scope="module")
def boundary(tmp_path_factory, module_residue):
    # `docker run --rm` removes the container when the probe exits; it stays when the docker
    # client is killed by the timeout below while the probe still runs, and a same-name
    # leftover makes the next `docker run --name` fail outright. Register before running.
    module_residue.container(NAME)
    directory = tmp_path_factory.mktemp("boundary")
    ...（其余不变）...
```

```python
# test_backup_sets_real.py
@pytest.fixture(scope="module", autouse=True)
def staged_sets_are_taken_away(module_residue):
    """Both tests read the one set the first one stages, so the set lives for the module;
    every set that appears from now on is removed (both halves) and, being never synced,
    forgotten from the status file at the end of the module."""
    module_residue.backup_sets_after(deploy_env.settings({"DSHERP_ENV": "dev"})["platform_site"])
```
运行两文件通过；`docker ps -a --filter name=dsherp-context-bbbb` 为空；平台备份集目录与跑前一致；状态文件无新增 `staged` 行。提交 `test: 边界探针容器与真实备份集按模块登记残留，跑完不留容器与备份集`。

### Task C.11: `test_policy_seed` 60 秒超时的决策（有判据，不盲改）

**Consumes:** 切片 B 第一次 nightly 的集成 junit（`<testcase classname="…test_policy_seed" name="…" time="…">`）。

判据脚本（只读 junit）：

```bash
.venv/bin/python - <<'EOF' path/to/nightly-integration-junit.xml
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
# one provisioner call per test, minus one 30 s-bounded read (~2-3 s): the read is subtracted as 3 s
samples = [float(c.get('time')) - 3 for c in root.iter('testcase')
           if c.get('classname','').endswith('test_policy_seed') and c.get('name','').startswith('test_policy_seed_creates_or_verifies_exact_legacy_rows')]
samples.sort()
p95 = samples[max(0, int(round(0.95 * len(samples))) - 1)] if samples else None
print({'n': len(samples), 'max': max(samples) if samples else None, 'p95': p95})
EOF
```

规则：`p95 > 45` → `tests/integration/test_policy_seed.py:28` 的 `timeout=60` 改 `timeout=180`，把 `{'n','max','p95'}` 与 junit 日期写进证据文档「test_policy_seed 超时决策」；`p95 <= 45` → 不改代码，仍把数字写进证据（"60 s 保留，P95=…"）。样本 n<20 时用 max 代替 p95 并注明。提交 `docs: 策略种子超时按首次 nightly 的实测 P95 决定（记录数字）`。

### 切片 C 结束门

全量非集成 pytest 绿；`ruff check .`；本机（worker/scheduled 停）九个文件全绿：`pytest tests/integration/test_run_grants.py tests/integration/test_queue_backpressure.py tests/integration/test_stock_impact_fastfail.py tests/integration/test_configuration_transfer_http.py tests/integration/test_policy_seed.py tests/integration/test_manufacturing_fixture.py tests/integration/test_platform_identity_revocation.py tests/integration/test_agent_boundary.py tests/integration/test_backup_sets_real.py -m integration -q`；`.runtime/integration-residue.json` 条目为空；`grep -c "dsherp-validation-backend-1" tests/integration/*.py` 比迁移前少（前后数字记证据）；全量集成一次。**检查点：PR 合入 main。**

---

## 切片 D：原生测试站、原生测试骨架与 G7 权限矩阵

### Task D.1: 测试站建站脚本与 compose 服务

**Files:** Create `infra/provision_test_site.py`、`tests/test_provision_test_site.py`、`frappe_app/dsherp_bridge/tests/__init__.py`、`frappe_app/dsherp_platform/tests/__init__.py`（空）；Modify `infra/compose.validation.yml`（`test-provision`、`platform-test-provision` + secret `test_admin_password`）、`dsherp/admin.py`（`CONTROL_SECRETS_BY_ENV['dev']` 追加 `'test_admin_password'`）、`.dockerignore`（追加 `frappe_app/*/tests`）、`tests/test_admin_cli.py`（若断言 dev 密钥集合则同步）

**Interfaces:**
- Produces: `provision_test_site.TARGETS`、`preflight(sites_dir, target) -> list[str]`、`main(kind)`；compose 服务 `test-provision`（`backend` 的卷）、`platform-test-provision`（`platform-backend` 的卷）；secret `test_admin_password: file: ../.runtime/control/test_admin_password`
- Consumes: `/run/secrets/db_root_password`、`/run/secrets/test_admin_password`；`frappe.installer._new_site`；`frappe.desk.page.setup_wizard.setup_wizard.setup_complete`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_provision_test_site.py
"""A throwaway test Site is created on an existing bench: never the default site, never on a
bench that lacks the apps, never twice; passwords stay off argv."""
import ast
import json
from pathlib import Path

from infra import provision_test_site as p


def test_targets_name_the_two_test_sites_and_their_apps():
    assert p.TARGETS['bridge']['site'] == 'dsherp-test.localhost'
    assert p.TARGETS['bridge']['apps'] == ('erpnext', 'dsherp_bridge') and p.TARGETS['bridge']['abbr'] == 'DNT'
    assert p.TARGETS['platform']['site'] == 'dsherp-platform-test.localhost'
    assert p.TARGETS['platform']['apps'] == ('dsherp_platform',) and p.TARGETS['platform']['company'] is None


def test_preflight_refuses_an_existing_site_a_bench_without_the_apps_or_without_a_database(tmp_path):
    sites = tmp_path / 'sites'
    sites.mkdir()
    (sites / 'apps.txt').write_text('frappe\nerpnext\n')
    (sites / 'common_site_config.json').write_text('{}')
    problems = p.preflight(sites, p.TARGETS['bridge'])
    assert any('dsherp_bridge' in problem for problem in problems) and any('db_host' in problem for problem in problems)
    (sites / 'apps.txt').write_text('frappe\nerpnext\ndsherp_bridge\n')
    (sites / 'common_site_config.json').write_text(json.dumps({'db_host': 'db'}))
    assert p.preflight(sites, p.TARGETS['bridge']) == []
    (sites / 'dsherp-test.localhost').mkdir()
    assert p.preflight(sites, p.TARGETS['bridge']) == ['dsherp-test.localhost already exists; inspect before retrying']


def test_no_password_ever_reaches_a_subprocess_argv():
    tree = ast.parse(Path(p.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'run':
            for argument in ast.walk(node):
                if isinstance(argument, ast.Name):
                    assert 'password' not in argument.id, ast.unparse(node)
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    assert '--db-root-password' not in argument.value and '--admin-password' not in argument.value
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# infra/provision_test_site.py
"""Create one throwaway test Site with `allow_tests` on an existing bench.

Runs only inside the `test-provision` / `platform-test-provision` control containers, on the
same sites volume the running backend serves. The bench already has apps.txt and
common_site_config.json from its first provisioning: they are checked, never rewritten, and
the new Site is never made the default. The Site is created in-process through
`frappe.installer._new_site`, so the root and admin passwords are Python values and never an
argv (restore_drill does the same). The bridge variant completes ERPNext's native setup
wizard with a synthetic company so ERPNext masters exist for native tests. The nginx front
ends do not know these Sites; tests run in-process via `bench run-tests`."""
import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

TARGETS = {
    'bridge': {'site': 'dsherp-test.localhost', 'apps': ('erpnext', 'dsherp_bridge'),
               'company': 'DSHERP 原生测试公司', 'abbr': 'DNT'},
    'platform': {'site': 'dsherp-platform-test.localhost', 'apps': ('dsherp_platform',),
                 'company': None, 'abbr': None},
}
BENCH = Path('/home/frappe/frappe-bench')
SITES = BENCH / 'sites'
SETUP = {'language': '简体中文', 'lang': 'zh', 'country': 'China', 'timezone': 'Asia/Shanghai',
         'currency': 'CNY', 'enable_telemetry': 0, 'chart_of_accounts': 'Standard',
         'fy_start_date': '2026-01-01', 'fy_end_date': '2026-12-31', 'setup_demo': 0}
SITE_CONFIG = {'allow_tests': 1, 'disable_scheduler': 1, 'mute_emails': 1, 'pause_scheduler': 1}


def preflight(sites, target):
    """What stops the creation, in words; empty means go."""
    problems = []
    sites = Path(sites)
    if (sites / target['site']).exists():
        return [f"{target['site']} already exists; inspect before retrying"]
    listed = set((sites / 'apps.txt').read_text().split()) if (sites / 'apps.txt').exists() else set()
    for app in target['apps']:
        if app not in listed:
            problems.append(f'bench apps.txt lacks {app}; provision the bench first')
    config = {}
    if (sites / 'common_site_config.json').exists():
        config = json.loads((sites / 'common_site_config.json').read_text() or '{}')
    if not config.get('db_host'):
        problems.append('common_site_config.json has no db_host; provision the bench first')
    return problems


def bench(*arguments):
    """bench sub-commands that carry no secret; output goes to stderr."""
    subprocess.run(['bench', *arguments], cwd=BENCH, check=True, stdout=sys.stderr)


def create_site(target, db_root_password, admin_password):
    os.chdir(SITES)
    import frappe
    from frappe.installer import _new_site
    frappe.init(site=target['site'], new_site=True)
    try:
        _new_site(None, target['site'], db_root_username='root', db_root_password=db_root_password,
                  admin_password=admin_password, verbose=False,
                  install_apps=[app for app in target['apps'] if app == 'erpnext'] or None,
                  db_host='db', mariadb_user_host_login_scope='%')
    finally:
        frappe.destroy()


def complete_setup(site, company, abbr):
    os.chdir(SITES)
    import frappe
    frappe.init(site=site, sites_path=str(SITES))
    frappe.connect()
    frappe.set_user('Administrator')
    try:
        if frappe.is_setup_complete() or frappe.db.count('Company'):
            raise SystemExit('Company already initialized; inspect instead of retrying')
        from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
        result = setup_complete({**SETUP, 'company_name': company, 'company_abbr': abbr})
        if result != {'status': 'ok'}:
            raise SystemExit('Native setup returned an unexpected result')
        if not frappe.is_setup_complete() or not frappe.db.exists('Company', company):
            raise SystemExit('Native setup did not persist the synthetic company')
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def main(kind):
    target = TARGETS[kind]
    problems = preflight(SITES, target)
    if problems:
        raise SystemExit('; '.join(problems))
    db_root_password = Path('/run/secrets/db_root_password').read_text().strip()
    admin_password = Path('/run/secrets/test_admin_password').read_text().strip()
    with contextlib.redirect_stdout(sys.stderr):
        create_site(target, db_root_password, admin_password)
        for app in target['apps']:
            if app != 'erpnext':
                bench('--site', target['site'], 'install-app', app)
        for key, value in SITE_CONFIG.items():
            bench('--site', target['site'], 'set-config', '--parse', key, str(value))
        if target['company']:
            complete_setup(target['site'], target['company'], target['abbr'])
    print(json.dumps({'site': target['site'], 'apps': list(target['apps']), 'company': target['company']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create a throwaway allow_tests Site on an existing bench')
    parser.add_argument('kind', choices=tuple(TARGETS))
    main(parser.parse_args().kind)
```

（`_new_site` 的关键字参数按容器内 `frappe/installer.py` 实际签名核对——实施时 `docker compose exec -T backend sh -c "grep -n 'def _new_site' -A12 /home/frappe/frappe-bench/apps/frappe/frappe/installer.py"`，与 `dsherp/restore_drill.py:34-53` 的既有调用对齐；`setup_complete` 的负载键以 `infra/provision_validation_company.py` 为准。）

`infra/compose.validation.yml` 在 `validation-provision` 之后追加（与它完全同形）：

```yaml
  test-provision:
    profiles: [control]
    image: frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
    pull_policy: never
    mem_limit: 640m
    memswap_limit: 640m
    cpus: 0.5
    restart: "no"
    entrypoint: []
    command: ["/home/frappe/frappe-bench/env/bin/python", "/opt/provision_test_site.py", "bridge"]
    environment: {PYTHONPATH: /opt/dsherp-frappe}
    secrets: [db_root_password, test_admin_password]
    volumes:
      - v16-sites:/home/frappe/frappe-bench/sites
      - v16-logs:/home/frappe/frappe-bench/logs
      - ../frappe_app:/opt/dsherp-frappe:ro
      - ./provision_test_site.py:/opt/provision_test_site.py:ro
    depends_on: [db, redis]
    networks: [validation]
    logging: *logging

  platform-test-provision:
    profiles: [control]
    image: frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
    pull_policy: never
    mem_limit: 640m
    memswap_limit: 640m
    cpus: 0.5
    restart: "no"
    entrypoint: []
    command: ["/home/frappe/frappe-bench/env/bin/python", "/opt/provision_test_site.py", "platform"]
    environment: {PYTHONPATH: /opt/dsherp-frappe}
    secrets: [db_root_password, test_admin_password]
    volumes:
      - v16-platform-sites:/home/frappe/frappe-bench/sites
      - v16-platform-logs:/home/frappe/frappe-bench/logs
      - ../frappe_app:/opt/dsherp-frappe:ro
      - ./provision_test_site.py:/opt/provision_test_site.py:ro
    depends_on: [db, redis]
    networks: [validation]
    logging: *logging
```
`secrets:` 段追加 `test_admin_password: {file: ../.runtime/control/test_admin_password}`；`dsherp/admin.py` 的 dev 元组加 `'test_admin_password'`（放在 `daily_admin_password` 之后）；`.dockerignore` 追加：

```
# Native test packages and the permission matrix are test data; the image never carries them.
frappe_app/*/tests
```

- [ ] **Step 4: 运行确认通过** `pytest tests/test_provision_test_site.py tests/test_admin_cli.py tests/test_deployment_contract.py tests/test_dev_stack.py -q`。真实建站（本机）：

```bash
.venv/bin/python infra/dev_stack.py secrets      # 现在会补出 test_admin_password
docker compose -f infra/compose.validation.yml --profile control run --rm --no-deps test-provision
docker compose -f infra/compose.validation.yml --profile control run --rm --no-deps platform-test-provision
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-test.localhost list-apps
docker compose -f infra/compose.validation.yml exec -T platform-backend bench --site dsherp-platform-test.localhost list-apps
docker compose -f infra/compose.validation.yml exec -T backend sh -c 'grep default_site /home/frappe/frappe-bench/sites/common_site_config.json'
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: dsherp-test.localhost' http://127.0.0.1:18082/api/method/ping
```
Expected: list-apps 分别为 `frappe erpnext dsherp_bridge` / `frappe dsherp_platform`；`allow_tests` 为 1；`default_site` 仍为 `dsherp-validation.localhost`；再次 `run test-provision` 退出非 0 并打印 `already exists`；curl 为 `404`/`421`（记实际码）。
- [ ] **Step 5: 提交** `git checkout -b plan5/native-tests && git commit -m "feat: 两个 allow_tests 测试站的建站脚本与 control 服务，口令不上 argv，测试包不进镜像"`

### Task D.2: `dsherp/native_tests.py`：`provision_test_sites` 与 `run_native_tests`；接线 `dev_stack`

**Files:** Create `dsherp/native_tests.py`、`tests/test_native_tests.py`；Modify `infra/dev_stack.py`（`run_native_tests` 桩改为委托；`STEPS` 追加 `test-sites`）、`tests/test_dev_stack.py`（FakeHost 与 `EXPECTED_ORDER`）

**Interfaces:**
- Produces: `TEST_SITES`；`provision_test_sites(resolved, *, root=ROOT, runner=subprocess.run, kinds=('bridge','platform')) -> dict[str, 'created'|'kept']`；`run_native_tests(resolved, *, junit, runner=subprocess.run, root=ROOT, kinds=('bridge','platform'), module=None, failfast=False) -> dict`（`junit` 是**目录**，写 `<app>.xml`；失败 `raise admin.Fault`，异常带 `.report`）；`junit_counts(path) -> dict`
- 判定：**以 junit 为准**——Frappe 的 `run_tests` 只在 `CI` 环境变量下 `sys.exit` 非零，站点未 `allow_tests` 时打印 "Testing is disabled" 退出 0——所以 junit 缺失、`tests=0`、`failures/errors>0`、或退出码非 0，任一即 `Fault`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_native_tests.py
"""Native tests are judged by the junit file, never by bench's exit code; the runner sees
exactly the compose commands, and no password is ever on an argv."""
import subprocess
from pathlib import Path

import pytest

from dsherp import admin, deploy_env, native_tests

DEV = deploy_env.settings({'DSHERP_ENV': 'dev'})
JUNIT_OK = '<testsuites><testsuite name="a" tests="3" failures="0" errors="0" skipped="1"/></testsuites>'
JUNIT_BAD = '<testsuites><testsuite name="a" tests="3" failures="1" errors="0"/><testsuite name="b" tests="2" failures="0" errors="1"/></testsuites>'


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('DSHERP_RUNTIME_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('DSHERP_SECRETS_DIR', str(tmp_path / 'state' / 'control'))
    (tmp_path / 'infra').mkdir()
    (tmp_path / 'infra' / 'compose.validation.yml').write_text('name: dsherp-validation\n')
    return tmp_path


class Runner:
    def __init__(self, junit=JUNIT_OK, returncode=0, existing=(), copy=True):
        self.calls, self.junit, self.returncode, self.existing, self.copy = [], junit, returncode, set(existing), copy

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        text = ' '.join(command)
        if 'site_config.json' in text and 'echo present' in text:
            site = text.split('/sites/', 1)[1].split('/', 1)[0]
            return subprocess.CompletedProcess(command, 0, ('present' if site in self.existing else 'absent') + '\n', '')
        if 'cp' in command and command.index('cp') == len(command) - 3:
            if self.copy:
                Path(command[-1]).write_text(self.junit)
            return subprocess.CompletedProcess(command, 0 if self.copy else 1, '', 'no such file')
        if 'run-tests' in command:
            return subprocess.CompletedProcess(command, self.returncode, 'Ran 3 tests\n', '')
        return subprocess.CompletedProcess(command, 0, '', '')


def _compose(host):
    return ['docker', 'compose', '-p', DEV['project'], '-f', str(host / 'infra' / 'compose.validation.yml')]


def test_run_native_tests_runs_both_apps_copies_junit_out_and_reports_counts(host):
    runner = Runner()
    report = native_tests.run_native_tests(DEV, junit=host / 'junit', runner=runner, root=host)
    assert report['ok'] and report['apps']['dsherp_bridge'] == {'tests': 3, 'failures': 0, 'errors': 0, 'skipped': 1, 'returncode': 0}
    assert set(report['apps']) == {'dsherp_bridge', 'dsherp_platform'}
    assert runner.calls[0] == [*_compose(host), 'exec', '-T', 'backend', 'bench', '--site', 'dsherp-test.localhost',
                               'run-tests', '--app', 'dsherp_bridge', '--junit-xml-output', '/tmp/dsherp-junit-dsherp_bridge.xml']
    assert runner.calls[1] == [*_compose(host), 'cp', 'backend:/tmp/dsherp-junit-dsherp_bridge.xml', str(host / 'junit' / 'dsherp_bridge.xml')]
    assert runner.calls[2][7:10] == ['platform-backend', 'bench', '--site'] and runner.calls[2][10] == 'dsherp-platform-test.localhost'
    assert (host / 'junit' / 'dsherp_platform.xml').read_text() == JUNIT_OK
    assert not any('CI=1' in word for call in runner.calls for word in call)


def test_a_single_module_and_failfast_are_passed_through(host):
    runner = Runner()
    native_tests.run_native_tests(DEV, junit=host / 'junit', runner=runner, root=host, kinds=('bridge',),
                                  module='dsherp_bridge.tests.test_permission_matrix', failfast=True)
    call = runner.calls[0]
    assert '--module' in call and call[call.index('--module') + 1] == 'dsherp_bridge.tests.test_permission_matrix' and '--failfast' in call
    assert len([c for c in runner.calls if 'run-tests' in c]) == 1


def test_failures_or_errors_in_the_junit_are_a_fault_even_when_bench_exits_zero(host):
    runner = Runner(junit=JUNIT_BAD, returncode=0)
    with pytest.raises(admin.Fault) as caught:
        native_tests.run_native_tests(DEV, junit=host / 'junit', runner=runner, root=host)
    assert caught.value.report['apps']['dsherp_bridge']['failures'] == 1 and caught.value.report['apps']['dsherp_bridge']['errors'] == 1
    assert (host / 'junit' / 'dsherp_platform.xml').exists()   # the second app still ran and its junit is kept


def test_a_non_zero_bench_exit_with_a_clean_junit_is_still_a_fault(host):
    with pytest.raises(admin.Fault):
        native_tests.run_native_tests(DEV, junit=host / 'junit', runner=Runner(returncode=1), root=host, kinds=('bridge',))


def test_a_missing_junit_or_zero_tests_is_a_fault_not_a_pass(host):
    with pytest.raises(admin.Fault, match='junit'):
        native_tests.run_native_tests(DEV, junit=host / 'junit', runner=Runner(copy=False), root=host, kinds=('bridge',))
    empty = '<testsuites><testsuite name="a" tests="0" failures="0" errors="0"/></testsuites>'
    with pytest.raises(admin.Fault, match='没有运行任何测试'):
        native_tests.run_native_tests(DEV, junit=host / 'junit', runner=Runner(junit=empty), root=host, kinds=('bridge',))


def test_junit_counts_accept_a_bare_testsuite_root(tmp_path):
    path = tmp_path / 'j.xml'
    path.write_text('<testsuite name="a" tests="2" failures="1" errors="0"/>')
    assert native_tests.junit_counts(path) == {'tests': 2, 'failures': 1, 'errors': 0, 'skipped': 0}


def test_provisioning_creates_the_secret_once_runs_the_control_services_and_keeps_existing_sites(host):
    runner = Runner(existing=('dsherp-platform-test.localhost',))
    outcome = native_tests.provision_test_sites(DEV, root=host, runner=runner)
    assert outcome == {'bridge': 'created', 'platform': 'kept'}
    secret = admin.secrets_dir(DEV, host) / 'test_admin_password'
    assert secret.is_file() and (secret.stat().st_mode & 0o077) == 0
    runs = [c for c in runner.calls if 'run' in c and '--rm' in c]
    assert runs == [[*_compose(host), '--profile', 'control', 'run', '--rm', '--no-deps', 'test-provision']]
    assert not any(secret.read_text().strip() in word for call in runner.calls for word in call)
    again = native_tests.provision_test_sites(DEV, root=host, runner=Runner(existing=('dsherp-test.localhost', 'dsherp-platform-test.localhost')))
    assert again == {'bridge': 'kept', 'platform': 'kept'} and secret.read_text()  # kept, never regenerated


def test_native_tests_refuse_a_production_environment(host):
    prod = dict(DEV, env='prod')
    with pytest.raises(admin.Fault):
        native_tests.run_native_tests(prod, junit=host / 'junit', runner=Runner(), root=host)
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError: No module named 'dsherp.native_tests'`

- [ ] **Step 3: 实现**

```python
# dsherp/native_tests.py
"""Frappe-native tests on the two throwaway test Sites (plan 5).

`provision_test_sites` creates dsherp-test.localhost (bench `backend`) and
dsherp-platform-test.localhost (bench `platform-backend`) through the control-profile
services. `run_native_tests` runs `bench run-tests --app <app>` on each, copies the junit
file out and judges the run by that file alone: Frappe's testing command exits 0 on failures
unless the CI variable is set, and exits 0 with no junit when the Site refuses testing
("Testing is disabled for the site!"), so the exit code proves nothing. A missing junit file,
zero tests, any failure or error, or a non-zero exit is a Fault carrying the report."""
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from dsherp import admin, deploy_env
from dsherp.admin import Fault, ROOT

TEST_SITES = {
    'bridge': {'site': 'dsherp-test.localhost', 'service': 'backend', 'bench': 'tenant',
               'app': 'dsherp_bridge', 'provision_service': 'test-provision'},
    'platform': {'site': 'dsherp-platform-test.localhost', 'service': 'platform-backend', 'bench': 'platform',
                 'app': 'dsherp_platform', 'provision_service': 'platform-test-provision'},
}
JUNIT_IN_CONTAINER = '/tmp/dsherp-junit-{app}.xml'
RUN_TIMEOUT = 3600


def _compose(resolved, root):
    """The same prefix admin.Bench uses, so compose interpolates the same values."""
    if resolved['env'] != 'dev':
        raise Fault('原生测试只在 dev 栈上运行；生产栈没有测试站')
    command = ['docker', 'compose', '-p', resolved['project']]
    env_file = deploy_env.env_file(resolved['env'], root)
    if env_file.exists():
        command += ['--env-file', str(env_file)]
    return command + ['-f', str(Path(root) / admin.COMPOSE['dev'])]


def _run(runner, command, *, root, timeout):
    return runner(command, cwd=root, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _tail(result, lines=8):
    return '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-lines:])


def provision_test_sites(resolved, *, root=ROOT, runner=subprocess.run, kinds=('bridge', 'platform')):
    """Create the test Sites that are absent; keep the ones present; never touch a live Site."""
    admin.ensure_secrets(resolved, root, names=('test_admin_password',))
    outcome = {}
    for kind in kinds:
        target = TEST_SITES[kind]
        bench = admin.Bench(resolved, target['bench'], root=root, runner=runner)
        if bench.site_exists(target['site']):
            outcome[kind] = 'kept'
            continue
        result = _run(runner, [*_compose(resolved, root), '--profile', 'control', 'run', '--rm', '--no-deps',
                               target['provision_service']], root=root, timeout=1800)
        if result.returncode:
            raise Fault(f"测试站 {target['site']} 建站失败；检查 {target['provision_service']} 的输出后再试，"
                        f"半成品不会自动清理：\n{_tail(result)}")
        outcome[kind] = 'created'
    return outcome


def junit_counts(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise Fault(f'读不到 junit 文件 {path}：{error}') from error
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key) or 0)
    return counts


def run_native_tests(resolved, *, junit, runner=subprocess.run, root=ROOT, kinds=('bridge', 'platform'),
                     module=None, failfast=False):
    """Run each app's tests on its test Site; return counts per app; Fault (with .report) on any failure."""
    junit = Path(junit)
    junit.mkdir(parents=True, exist_ok=True)
    report = {'ok': True, 'apps': {}, 'junit': str(junit)}
    for kind in kinds:
        target = TEST_SITES[kind]
        inside = JUNIT_IN_CONTAINER.format(app=target['app'])
        command = [*_compose(resolved, root), 'exec', '-T', target['service'], 'bench', '--site', target['site'],
                   'run-tests', '--app', target['app']]
        if module:
            command += ['--module', module]
        if failfast:
            command += ['--failfast']
        command += ['--junit-xml-output', inside]
        result = _run(runner, command, root=root, timeout=RUN_TIMEOUT)
        host_file = junit / f"{target['app']}.xml"
        copied = _run(runner, [*_compose(resolved, root), 'cp', f"{target['service']}:{inside}", str(host_file)],
                      root=root, timeout=120)
        if copied.returncode or not host_file.exists():
            raise Fault(f"{target['app']} 在 {target['site']} 上没有产出 junit 文件（bench run-tests 退出码 "
                        f"{result.returncode}）；站点可能没有 allow_tests，或没有发现测试：\n{_tail(result)}")
        counts = junit_counts(host_file)
        counts['returncode'] = result.returncode
        report['apps'][target['app']] = counts
        if counts['tests'] == 0:
            raise Fault(f"{target['app']} 没有运行任何测试（{host_file}）；检查 tests 包与 discovery")
        if counts['failures'] or counts['errors'] or result.returncode:
            report['ok'] = False
    if not report['ok']:
        error = Fault('原生测试失败：' + ', '.join(
            f"{app} tests={c['tests']} failures={c['failures']} errors={c['errors']} rc={c['returncode']}"
            for app, c in report['apps'].items()) + f'；junit 在 {junit}')
        error.report = report
        raise error
    return report
```

`infra/dev_stack.py`：把桩改为委托，并在 `STEPS` 末尾追加测试站步骤：

```python
from dsherp import native_tests

def run_native_tests(resolved, *, junit, runner=subprocess.run):
    return native_tests.run_native_tests(resolved, junit=junit, runner=runner)

# STEPS 追加（daily-backup 之后）：
    Step('test-sites', lambda s: native_tests.provision_test_sites(s.resolved, root=s.root, runner=s.runner), rerun_safe=True),
```

`tests/test_dev_stack.py`：FakeHost 的 compose `run` 映射加 `"test-provision": "dsherp-test.localhost", "platform-test-provision": "dsherp-platform-test.localhost"`；`EXPECTED_ORDER` 末尾加 `"compose run test-provision", "compose run platform-test-provision"`；`test_native_tests_is_an_explicit_stub…` 改为断言委托（monkeypatch `dev_stack.native_tests.run_native_tests` 记录参数）。

- [ ] **Step 4: 运行确认通过** `pytest tests/test_native_tests.py tests/test_dev_stack.py -q` 全绿。
- [ ] **Step 5: 提交** `git commit -m "feat: 原生测试驱动——建测试站、跑 bench run-tests、以 junit 判定成败；dev_stack 接线"`

### Task D.3: 金丝雀原生测试与真实首跑（含退出码核对）

**Files:** Create `frappe_app/dsherp_bridge/tests/test_harness.py`、`frappe_app/dsherp_platform/tests/test_harness.py`；Modify `README.md`（原生测试小节）

```python
# frappe_app/dsherp_bridge/tests/test_harness.py
"""The harness itself: tests run on the throwaway Site, with testing allowed, on a Site that
completed ERPNext's setup with the synthetic company - and only there."""
import frappe
from frappe.tests import IntegrationTestCase


class TestHarness(IntegrationTestCase):
    def test_runs_on_the_test_site_with_tests_allowed_and_a_synthetic_company(self):
        self.assertEqual(frappe.local.site, 'dsherp-test.localhost')
        self.assertTrue(frappe.conf.get('allow_tests'))
        self.assertIn('dsherp_bridge', frappe.get_installed_apps())
        self.assertEqual(frappe.db.get_value('Company', {'abbr': 'DNT'}, 'name'), 'DSHERP 原生测试公司')

    def test_a_row_inserted_here_is_gone_for_the_next_class(self):
        frappe.get_doc({'doctype': 'DS Conversation', 'title': 'harness canary'}).insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count('DS Conversation', {'title': 'harness canary'}), 1)


class TestHarnessRollback(IntegrationTestCase):
    def test_the_previous_class_left_nothing(self):
        self.assertEqual(frappe.db.count('DS Conversation', {'title': 'harness canary'}), 0)
```

```python
# frappe_app/dsherp_platform/tests/test_harness.py
import frappe
from frappe.tests import IntegrationTestCase


class TestHarness(IntegrationTestCase):
    def test_runs_on_the_platform_test_site_without_erpnext(self):
        self.assertEqual(frappe.local.site, 'dsherp-platform-test.localhost')
        self.assertTrue(frappe.conf.get('allow_tests'))
        self.assertIn('dsherp_platform', frappe.get_installed_apps())
        self.assertNotIn('erpnext', frappe.get_installed_apps())
```

- [ ] **Step 2: 运行确认失败**（证明门在）：`docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-validation.localhost run-tests --app dsherp_bridge --module dsherp_bridge.tests.test_harness; echo rc=$?` → 打印 `Testing is disabled for the site!`，`rc=0`（不能信退出码的证据，记证据文档）。
- [ ] **Step 3: 实现** 写入两个文件；README 增加：

```markdown
### 原生测试（Frappe 内）

两个专用测试站 `dsherp-test.localhost`（backend，frappe/erpnext/dsherp_bridge，`allow_tests: 1`，合成公司 DNT）与
`dsherp-platform-test.localhost`（platform-backend，frappe/dsherp_platform）只给 `frappe_app/*/tests` 下的新测试用；
nginx 不暴露它们。

    .venv/bin/python infra/dev_stack.py native-tests --junit work/junit-native   # 两个 App，junit 写到该目录
    docker compose -f infra/compose.validation.yml exec -T backend \
      bench --site dsherp-test.localhost run-tests --module dsherp_bridge.tests.test_permission_matrix

改了 `frappe_app/` 之后先 `docker restart dsherp-validation-backend-1`（gunicorn 缓存模块）；`bench run-tests` 是
单独进程，本身不缓存。结果以 junit 为准（`dsherp.native_tests.run_native_tests`），不是 bench 的退出码。
```

- [ ] **Step 4: 运行确认通过** `.venv/bin/python infra/dev_stack.py native-tests --junit work/junit-native` → 报告 `ok: True`，`dsherp_bridge` 3 条、`dsherp_platform` 1 条；容器内 `sed -n '/^def run_tests/,/^@click/p' …/frappe/commands/testing.py | tail -30` 的"是否 sys.exit、何条件"逐字记入证据。
- [ ] **Step 5: 提交** `git commit -m "test: 原生测试骨架金丝雀——测试站身份、allow_tests、类级回滚；记录 run-tests 退出码实测"`

### Task D.4: 矩阵 JSON 与宿主静态测试（含修 `test_audit_immutability.py:14`）

**Files:** Create `frappe_app/dsherp_bridge/tests/permission_matrix.json`、`tests/test_permission_matrix.py`；Modify `tests/test_audit_immutability.py:14` → `PLATFORM = ROOT / "frappe_app/dsherp_platform/platform/doctype"`

记录的两项决定：`DS Conversation` **不加** `track_changes`（会话标题是用户提问前 100 字，是个人内容；`delete-user-data` 已要顺带清 Version 行；审计事实在 DS Model Run/DS Run Event）；`DS Agent Task` 去掉 `delete`（README：旧任务记录只读保留），`write` 维持现状并被矩阵钉住。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_permission_matrix.py
"""G7: the permission matrix is one file, and the DocType definitions, the controllers, the
guest-endpoint inventory and the report gate all agree with it. The behaviour on a real Site
is in frappe_app/*/tests/test_permission_matrix.py (native harness)."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / 'frappe_app'
MATRIX = APPS / 'dsherp_bridge' / 'tests' / 'permission_matrix.json'
DOCTYPE_ROOTS = {'dsherp_bridge': APPS / 'dsherp_bridge/dsherp_bridge/doctype',
                 'dsherp_platform': APPS / 'dsherp_platform/platform/doctype'}
FLAGS = ('read', 'write', 'create', 'delete', 'submit', 'cancel', 'amend', 'report',
         'export', 'import', 'print', 'email', 'share', 'select')
ACTIONS = ('read', 'create', 'write', 'delete')
WHITELIST = re.compile(r'^\s*@frappe\.whitelist\((?P<args>[^)]*)\)\s*$')
DEF = re.compile(r'^\s*def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')


def matrix():
    return json.loads(MATRIX.read_text())


def _folder(row):
    return DOCTYPE_ROOTS[row['app']] / row['doctype'].lower().replace(' ', '_')


def _definition(row):
    path = _folder(row) / (_folder(row).name + '.json')
    assert path.is_file(), path
    return json.loads(path.read_text())


def _controller(row):
    path = _folder(row) / (_folder(row).name + '.py')
    assert path.is_file(), path
    return path.read_text()


def _normalise(rows):
    return sorted((r['role'], int(r.get('permlevel') or 0), tuple(f for f in FLAGS if r.get(f))) for r in rows)


ROWS = matrix()['doctypes']
IDS = [row['doctype'] for row in ROWS]


def test_both_doctype_roots_exist_and_the_matrix_covers_every_ds_doctype_exactly_once():
    for root in DOCTYPE_ROOTS.values():
        assert root.is_dir(), root
    on_disk = {(app, path.name) for app, root in DOCTYPE_ROOTS.items()
               for path in root.iterdir() if path.is_dir() and not path.name.startswith('_')}
    listed = [(row['app'], _folder(row).name) for row in ROWS]
    assert set(listed) == on_disk and len(listed) == len(set(listed)) == 16


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_permission_rows_track_changes_and_child_status_match_the_definition(row):
    definition = _definition(row)
    assert _normalise(definition.get('permissions', [])) == _normalise(row['permissions']), row['doctype']
    assert bool(definition.get('track_changes')) is row['track_changes'], row['doctype']
    assert bool(definition.get('istable')) is bool(row.get('parent_doctype')), row['doctype']


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_the_controller_refuses_delete_exactly_where_the_matrix_says(row):
    source = _controller(row)
    mode = row['controller_refuses_delete']
    assert mode in ('always', 'while_enabled', 'while_referenced', 'never'), row['doctype']
    if mode == 'never':
        assert 'def on_trash' not in source, row['doctype']
    else:
        assert 'def on_trash' in source and 'frappe.throw' in source.split('def on_trash', 1)[1], row['doctype']


@pytest.mark.parametrize('row', ROWS, ids=IDS)
def test_actor_expectations_are_derived_from_the_rows_not_free_text(row):
    actors = matrix()['actors']
    by_role = {}
    for role, level, flags in _normalise(row['permissions']):
        if level == 0:
            by_role.setdefault(role, set()).update(flags)
    parent = next((r for r in ROWS if r['doctype'] == row.get('parent_doctype')), None)
    for actor, expected in row['actions'].items():
        assert set(expected) == set(ACTIONS), (row['doctype'], actor)
        for action in ACTIONS:
            want = expected[action]
            if actor == 'administrator':
                if action == 'delete' and row['controller_refuses_delete'] in ('always', 'while_enabled'):
                    assert want == 'refused_by_controller', row['doctype']
                else:
                    assert want is True, (row['doctype'], action)
            elif actor == 'guest':
                assert want is False, (row['doctype'], action)
            else:
                if parent:
                    derived = parent['actions'][actor][action]
                else:
                    derived = any(action in by_role.get(role, ()) for role in actors[actor]['roles'])
                assert want is derived, (row['doctype'], actor, action)


def test_permlevel_one_expectations_match_the_rows():
    for row in ROWS:
        levels = {(r['role'], int(r.get('permlevel') or 0)): set(f for f in FLAGS if r.get(f)) for r in row['permissions']}
        for actor, flags in row.get('permlevel_1', {}).items():
            roles = matrix()['actors'][actor]['roles']
            derived = set().union(*(levels.get((role, 1), set()) for role in roles)) if roles else set()
            assert set(flags) == derived, (row['doctype'], actor)


def guest_endpoints_on_disk():
    found = set()
    for path in sorted(APPS.rglob('*.py')):
        if '/tests/' in path.as_posix() or '__pycache__' in path.parts:
            continue
        module = path.relative_to(APPS).with_suffix('').as_posix().replace('/', '.')
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            match = WHITELIST.match(line)
            if not match or 'allow_guest=True' not in match.group('args').replace(' ', ''):
                continue
            for following in lines[index + 1:index + 4]:
                name = DEF.match(following)
                if name:
                    found.add(module + '.' + name.group('name'))
                    break
            else:
                raise AssertionError(f'{path}:{index + 1} allow_guest 装饰器后面没有函数定义')
    return found


def test_the_guest_endpoint_inventory_is_exactly_the_declared_one():
    declared = matrix()['guest_endpoints']
    assert guest_endpoints_on_disk() == {row['method'] for row in declared}
    assert all(row['guard'] in ('oauth_state', 'run_capability', 'hmac_envelope') for row in declared)


def test_the_audit_report_is_gated_by_role_in_its_definition():
    for report in matrix()['reports']:
        definition = json.loads((APPS / report['path']).read_text())
        assert [row['role'] for row in definition['roles']] == report['roles'], report['name']
```

- [ ] **Step 2: 运行确认失败** → `FileNotFoundError: … permission_matrix.json`

- [ ] **Step 3: 实现** 矩阵（完整）：

```json
{
  "version": 1,
  "actors": {
    "guest": {"user": "Guest"},
    "member": {"roles": []},
    "manager": {"roles": ["System Manager"]},
    "administrator": {"user": "Administrator"}
  },
  "doctypes": [
    {"app": "dsherp_bridge", "doctype": "DS Model Run", "permissions": [{"role": "System Manager", "permlevel": 0, "read": 1, "report": 1}],
     "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "运行是助手做过什么的凭据：只有操作员可读可报表；终态不可改写、任何人不可删（validate + on_trash）"},
    {"app": "dsherp_bridge", "doctype": "DS Run Event", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "事件只由服务端写入，通过 list_run_events 读；Document API 上无人有权，改写与删除一律拒绝"},
    {"app": "dsherp_bridge", "doctype": "DS Operation Proposal", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "确认与执行都以提案为准；冻结字段，不可删"},
    {"app": "dsherp_bridge", "doctype": "DS Execution Record", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "对外可追责的执行事实；只有 target_state 可在事后标注"},
    {"app": "dsherp_bridge", "doctype": "DS Configuration Bundle", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "预览与发布都以配置包为准"},
    {"app": "dsherp_bridge", "doctype": "DS Configuration Confirmation", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "人做过确认的凭据"},
    {"app": "dsherp_bridge", "doctype": "DS Configuration Execution", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "配置执行结果不可改写不可删"},
    {"app": "dsherp_bridge", "doctype": "DS Configuration Transfer", "permissions": [], "track_changes": true, "controller_refuses_delete": "always",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "两站交接的绑定记录（切片 E 起带有效期，平台令牌只在缓存）；不可改不可删"},
    {"app": "dsherp_bridge", "doctype": "DS Conversation", "permissions": [], "track_changes": false, "controller_refuses_delete": "while_referenced",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "会话是用户自己的线程，只经 context_api 读写（ignore_permissions，服务端校验 owner）；不开 track_changes：标题是提问前 100 字，是个人内容，delete-user-data 已要顺带清 Version 行，再复制一份进 tabVersion 只增加要清的东西；有运行引用时 on_trash 拒绝（集成测试已证），无引用时 Administrator 可删"},
    {"app": "dsherp_bridge", "doctype": "DS Doctype Policy", "permissions": [{"role": "System Manager", "permlevel": 0, "read": 1, "write": 1, "create": 1, "delete": 1}],
     "track_changes": true, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": true, "write": true, "delete": true},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "治理数据：操作员可改可删，每次改动要新的 change_reason 且留版本；删除等于收回助手对该 DocType 的写权限，是关闭式的"},
    {"app": "dsherp_bridge", "doctype": "DS Doctype Policy Route", "parent_doctype": "DS Doctype Policy", "permissions": [],
     "track_changes": false, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": true, "write": true, "delete": true},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "子表：权限随父表 DS Doctype Policy"},
    {"app": "dsherp_bridge", "doctype": "DS Business Credential", "permissions": [], "track_changes": true, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "凭据窗口记录只由 credentials 模块写；没有记录的密钥会被站点拒绝（S2），删除是关闭式的"},
    {"app": "dsherp_bridge", "doctype": "DS Ops Snapshot", "permissions": [], "track_changes": false, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": false, "create": false, "write": false, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "运维快照，7 天后由 collect_snapshot 自行修剪；只经 ops_status 读"},
    {"app": "dsherp_platform", "doctype": "DS Enterprise",
     "permissions": [{"role": "System Manager", "permlevel": 0, "read": 1, "write": 1, "create": 1, "delete": 1},
                     {"role": "System Manager", "permlevel": 1, "read": 1, "write": 1}],
     "permlevel_1": {"manager": ["read", "write"], "member": []},
     "track_changes": true, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": true, "write": true, "delete": true},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "平台操作员管理企业；成员经 api.context 只看到自己有绑定的企业"},
    {"app": "dsherp_platform", "doctype": "DS Membership",
     "permissions": [{"role": "System Manager", "permlevel": 0, "read": 1, "write": 1, "create": 1, "delete": 1},
                     {"role": "System Manager", "permlevel": 1, "read": 1, "write": 1}],
     "permlevel_1": {"manager": ["read", "write"], "member": []},
     "track_changes": true, "controller_refuses_delete": "while_enabled",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": true, "write": true, "delete": true},
                 "administrator": {"read": true, "create": true, "write": true, "delete": "refused_by_controller"}},
     "why": "api_key/api_secret 在 permlevel 1，只有操作员可见；启用中的绑定不能删（先停用，否则借出的凭据不会被吊销）"},
    {"app": "dsherp_platform", "doctype": "DS Agent Task", "permissions": [{"role": "System Manager", "permlevel": 0, "read": 1, "write": 1}],
     "track_changes": false, "controller_refuses_delete": "never",
     "actions": {"guest": {"read": false, "create": false, "write": false, "delete": false},
                 "member": {"read": false, "create": false, "write": false, "delete": false},
                 "manager": {"read": true, "create": false, "write": true, "delete": false},
                 "administrator": {"read": true, "create": true, "write": true, "delete": true}},
     "why": "退役的任务历史只读保留（README）：去掉 delete；write 维持既有授权并由本矩阵钉住；无 on_trash，Administrator 绕过角色权限"}
  ],
  "guest_endpoints": [
    {"method": "dsherp_bridge.sso.start", "guard": "oauth_state"},
    {"method": "dsherp_bridge.sso.callback", "guard": "oauth_state"},
    {"method": "dsherp_bridge.context_execution.run_status", "guard": "run_capability"},
    {"method": "dsherp_bridge.context_execution.reserve_model_call", "guard": "run_capability"},
    {"method": "dsherp_bridge.context_execution.run_tool", "guard": "run_capability"},
    {"method": "dsherp_bridge.context_execution.record_run_event", "guard": "run_capability"},
    {"method": "dsherp_bridge.context_execution.finish_run", "guard": "run_capability"},
    {"method": "dsherp_bridge.configuration_transfer.export_transfer", "guard": "hmac_envelope"},
    {"method": "dsherp_bridge.configuration_transfer.receipt_transfer", "guard": "hmac_envelope"}
  ],
  "reports": [
    {"name": "DS Agent Audit", "path": "dsherp_bridge/dsherp_bridge/report/ds_agent_audit/ds_agent_audit.json", "roles": ["System Manager"]}
  ]
}
```

（`DS Agent Task` 的 JSON 改动与本矩阵同一提交，并带 `no-patch:` 说明；见 Task D.6。矩阵里 `permissions` 数组写的是**改后**状态。）

- [ ] **Step 4: 运行确认通过** `pytest tests/test_permission_matrix.py tests/test_audit_immutability.py -q` 全绿（D.6 的 JSON 改动并入本提交即绿）；游客端点 9 个。
- [ ] **Step 5: 提交** 与 D.6 合并提交（见 D.6）。

### Task D.5: bridge 原生矩阵测试

**Files:** Create `frappe_app/dsherp_bridge/tests/test_permission_matrix.py`

```python
# frappe_app/dsherp_bridge/tests/test_permission_matrix.py
"""G7 on a real Site: what Frappe's permission layer answers for each actor and action, and
what the controllers refuse regardless of it. Everything inserted here lives inside the
class transaction IntegrationTestCase rolls back; nothing commits."""
import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

MATRIX = Path(__file__).with_name('permission_matrix.json')
APP = 'dsherp_bridge'
ACTIONS = ('read', 'create', 'write', 'delete')


def load_rows(app):
    matrix = json.loads(MATRIX.read_text())
    return matrix, [row for row in matrix['doctypes'] if row['app'] == app]


def make_actors(matrix, tag):
    """Users for the actors that are defined by roles; Guest and Administrator are themselves."""
    users = {}
    for actor, spec in matrix['actors'].items():
        if 'roles' not in spec:
            users[actor] = spec['user']
            continue
        email = f'matrix-{actor}-{tag}@example.invalid'
        frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': f'Matrix {actor}', 'enabled': 1,
                        'send_welcome_email': 0, 'roles': [{'role': role} for role in spec['roles']]}
                       ).insert(ignore_permissions=True)
        users[actor] = email
    return users


def insert(payload):
    return frappe.get_doc(payload).insert(ignore_permissions=True).name


class TestPermissionMatrix(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user('Administrator')
        cls.matrix, cls.rows = load_rows(APP)
        cls.tag = frappe.generate_hash(length=8)
        cls.users = make_actors(cls.matrix, cls.tag)
        cls.fixtures = cls.minimal_documents()

    @classmethod
    def minimal_documents(cls):
        """One row per DocType, built from each definition's reqd fields, in link order."""
        now = now_datetime()
        later = add_to_date(now, hours=1)
        f = {}
        f['DS Conversation'] = insert({'doctype': 'DS Conversation', 'title': 'matrix ' + cls.tag})
        f['DS Model Run'] = insert({'doctype': 'DS Model Run', 'conversation': f['DS Conversation'], 'domain': 'query',
                                    'status': 'Queued', 'request_id': cls.tag, 'question': 'matrix'})
        f['DS Run Event'] = insert({'doctype': 'DS Run Event', 'run': f['DS Model Run'], 'seq': 1, 'kind': 'matrix',
                                    'source': 'server', 'recorded_at': now})
        f['DS Operation Proposal'] = insert({'doctype': 'DS Operation Proposal', 'conversation': f['DS Conversation'],
                                             'model_run': f['DS Model Run'], 'payload': '{}', 'digest': '0' * 64,
                                             'expires_at': later, 'status': 'Pending'})
        f['DS Execution Record'] = insert({'doctype': 'DS Execution Record', 'proposal': f['DS Operation Proposal'],
                                           'request_id': cls.tag, 'status': 'Running'})
        f['DS Configuration Bundle'] = insert({'doctype': 'DS Configuration Bundle', 'conversation': f['DS Conversation'],
                                               'payload': '{}', 'digest': '1' * 64, 'baseline': '2' * 64})
        f['DS Configuration Confirmation'] = insert({'doctype': 'DS Configuration Confirmation',
                                                     'bundle': f['DS Configuration Bundle'], 'payload': '{}',
                                                     'digest': '3' * 64, 'expires_at': later, 'status': 'Pending'})
        f['DS Configuration Execution'] = insert({'doctype': 'DS Configuration Execution',
                                                  'confirmation': f['DS Configuration Confirmation'],
                                                  'request_id': cls.tag, 'status': 'Running', 'steps': '[]'})
        f['DS Configuration Transfer'] = insert({'doctype': 'DS Configuration Transfer', 'request_id': cls.tag,
                                                 'bundle': f['DS Configuration Bundle'], 'payload': '{}'})
        f['DS Doctype Policy'] = insert({'doctype': 'DS Doctype Policy', 'target_doctype': 'ToDo',
                                         'change_reason': 'matrix ' + cls.tag, 'enabled': 1, 'allow_read': 1})
        f['DS Business Credential'] = insert({'doctype': 'DS Business Credential', 'user': cls.users['member'],
                                              'api_key': 'matrix-' + cls.tag, 'issued_at': now, 'expires_at': later,
                                              'version': 1})
        f['DS Ops Snapshot'] = insert({'doctype': 'DS Ops Snapshot', 'collected_at': now, 'payload': '{}'})
        return f

    def test_every_doctype_actor_and_action_answers_as_the_matrix_says(self):
        mismatches = []
        for row in self.rows:
            for actor, expected in row['actions'].items():
                for action in ACTIONS:
                    want = expected[action]
                    if want == 'refused_by_controller':
                        want = True  # the permission layer lets Administrator through; the controller does not
                    got = bool(frappe.has_permission(row['doctype'], action, user=self.users[actor],
                                                     parent_doctype=row.get('parent_doctype')))
                    if got != want:
                        mismatches.append((row['doctype'], actor, action, got, want))
        self.assertEqual(mismatches, [])

    def test_administrator_cannot_delete_what_the_controllers_protect_and_can_delete_the_rest(self):
        for row in self.rows:
            name = self.fixtures.get(row['doctype'])
            mode = row['controller_refuses_delete']
            if mode == 'always' or mode == 'while_referenced':
                with self.assertRaises(frappe.ValidationError, msg=row['doctype']):
                    frappe.delete_doc(row['doctype'], name, force=True, ignore_permissions=True)
                self.assertTrue(frappe.db.exists(row['doctype'], name), row['doctype'])
            elif mode == 'never' and name:
                frappe.db.savepoint('matrix_delete')
                frappe.delete_doc(row['doctype'], name, force=True, ignore_permissions=True)
                self.assertFalse(frappe.db.exists(row['doctype'], name), row['doctype'])
                frappe.db.rollback(save_point='matrix_delete')
                self.assertTrue(frappe.db.exists(row['doctype'], name), row['doctype'])

    def test_an_unreferenced_conversation_is_deletable_by_administrator(self):
        frappe.db.savepoint('matrix_conversation')
        name = insert({'doctype': 'DS Conversation', 'title': 'matrix unreferenced ' + self.tag})
        frappe.delete_doc('DS Conversation', name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists('DS Conversation', name))
        frappe.db.rollback(save_point='matrix_conversation')

    def test_the_audit_report_is_refused_to_a_member_and_served_to_a_manager(self):
        from frappe.desk.query_report import run
        frappe.set_user(self.users['member'])
        try:
            with self.assertRaises(frappe.PermissionError):
                run('DS Agent Audit', filters={})
        finally:
            frappe.set_user('Administrator')
        frappe.set_user(self.users['manager'])
        try:
            self.assertIn('result', run('DS Agent Audit', filters={}))
        finally:
            frappe.set_user('Administrator')
```

（各 DocType 最小夹具的 `reqd` 字段以各 JSON 为准实施时核对；切片 E 之后 `DS Configuration Transfer` 需加 `expires_at`。）

- [ ] **Step 2: 运行确认失败** 首跑前把矩阵里任意一条 `manager.read` 临时改反（如 DS Model Run 改 `false`）→ `docker compose … exec -T backend bench --site dsherp-test.localhost run-tests --module dsherp_bridge.tests.test_permission_matrix --junit-xml-output /tmp/j.xml` 的 junit `failures="1"`；改回。
- [ ] **Step 4: 运行确认通过** `tests="4" failures="0" errors="0"`；`test_harness` 仍绿（矩阵类的插入已回滚）；容器里 `frappe.db.count('User',{'name':['like','matrix-%']})` 为 0。若 `parent_doctype` 关键字不被接受，对子表跳过 `has_permission` 并在矩阵 `why` 里记"子表权限由父表推导，仅静态核对"。
- [ ] **Step 5: 提交** `git commit -m "test: bridge 原生权限矩阵——13 个 DocType × 四类身份 × 四动作，控制器拒删与报表门在真实站点上核对"`

### Task D.6: 平台原生矩阵测试与 `DS Agent Task` 去 delete

**Files:** Create `frappe_app/dsherp_platform/tests/test_permission_matrix.py`；Modify `frappe_app/dsherp_platform/platform/doctype/ds_agent_task/ds_agent_task.json`（`permissions[0]` 删掉 `"delete": 1`）

```python
# frappe_app/dsherp_platform/tests/test_permission_matrix.py
"""The platform half of the G7 matrix, read from the one matrix file the bridge app carries
(both benches mount ../frappe_app at /opt/dsherp-frappe)."""
import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

MATRIX = Path(__file__).resolve().parents[2] / 'dsherp_bridge' / 'tests' / 'permission_matrix.json'
APP = 'dsherp_platform'
ACTIONS = ('read', 'create', 'write', 'delete')


def insert(payload):
    return frappe.get_doc(payload).insert(ignore_permissions=True).name


class TestPlatformPermissionMatrix(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user('Administrator')
        matrix = json.loads(MATRIX.read_text())
        cls.rows = [row for row in matrix['doctypes'] if row['app'] == APP]
        cls.tag = frappe.generate_hash(length=8)
        cls.users = {}
        for actor, spec in matrix['actors'].items():
            if 'roles' not in spec:
                cls.users[actor] = spec['user']
                continue
            email = f'matrix-{actor}-{cls.tag}@example.invalid'
            frappe.get_doc({'doctype': 'User', 'email': email, 'first_name': f'Matrix {actor}', 'enabled': 1,
                            'send_welcome_email': 0, 'roles': [{'role': role} for role in spec['roles']]}
                           ).insert(ignore_permissions=True)
            cls.users[actor] = email
        cls.fixtures = {}
        cls.fixtures['DS Enterprise'] = insert({'doctype': 'DS Enterprise', 'enterprise_id': 'matrix-' + cls.tag,
                                                'title': 'Matrix', 'site': 'matrix.localhost',
                                                'base_url': 'http://matrix.localhost', 'status': 'Provisioning'})
        cls.fixtures['DS Membership'] = insert({'doctype': 'DS Membership', 'enterprise': cls.fixtures['DS Enterprise'],
                                                'platform_user': cls.users['member'], 'enabled': 1,
                                                'erp_user': f'matrix-erp-{cls.tag}@example.invalid',
                                                'api_key': 'k' + cls.tag, 'api_secret': 's' + cls.tag})
        cls.fixtures['DS Agent Task'] = insert({'doctype': 'DS Agent Task', 'enterprise': cls.fixtures['DS Enterprise'],
                                                'question': 'matrix', 'status': 'Succeeded'})

    def test_every_platform_doctype_actor_and_action_answers_as_the_matrix_says(self):
        mismatches = []
        for row in self.rows:
            for actor, expected in row['actions'].items():
                for action in ACTIONS:
                    want = expected[action] if expected[action] != 'refused_by_controller' else True
                    got = bool(frappe.has_permission(row['doctype'], action, user=self.users[actor]))
                    if got != want:
                        mismatches.append((row['doctype'], actor, action, got, want))
        self.assertEqual(mismatches, [])

    def test_the_secret_columns_of_a_membership_are_permlevel_one(self):
        meta = frappe.get_meta('DS Membership')
        manager = set(meta.get_permitted_fieldnames(user=self.users['manager'], permission_type='read'))
        member = set(meta.get_permitted_fieldnames(user=self.users['member'], permission_type='read'))
        self.assertTrue({'api_key', 'api_secret'} <= manager)
        self.assertEqual(member & {'api_key', 'api_secret'}, set())
        self.assertEqual(sorted(f.fieldname for f in meta.fields if f.permlevel == 1), ['api_key', 'api_secret'])

    def test_an_enabled_membership_refuses_deletion_and_a_disabled_one_does_not(self):
        name = self.fixtures['DS Membership']
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc('DS Membership', name, force=True, ignore_permissions=True)
        self.assertTrue(frappe.db.exists('DS Membership', name))

    def test_a_manager_cannot_delete_retired_task_history_while_administrator_still_can(self):
        self.assertFalse(frappe.has_permission('DS Agent Task', 'delete', user=self.users['manager']))
        self.assertTrue(frappe.has_permission('DS Agent Task', 'delete', user='Administrator'))
        frappe.set_user(self.users['manager'])
        try:
            with self.assertRaises(frappe.PermissionError):
                frappe.delete_doc('DS Agent Task', self.fixtures['DS Agent Task'])
        finally:
            frappe.set_user('Administrator')
        self.assertTrue(frappe.db.exists('DS Agent Task', self.fixtures['DS Agent Task']))
```

- [ ] **Step 2: 运行确认失败** 平台测试站 `run-tests --module dsherp_platform.tests.test_permission_matrix` → `test_a_manager_cannot_delete_retired_task_history_…` 与矩阵比对失败（JSON 未改时 System Manager 仍有 delete）。
- [ ] **Step 3: 实现** `ds_agent_task.json` 的 `permissions` 改为 `[{"role": "System Manager", "read": 1, "write": 1}]`（其余键不动）。六站 migrate 与重启：

```bash
for s in backend:dsherp-validation.localhost backend:dsherp-daily.localhost beta-backend:dsherp-beta.localhost platform-backend:dsherp-platform.localhost platform-backend:dsherp-platform-test.localhost backend:dsherp-test.localhost; do
  docker compose -f infra/compose.validation.yml exec -T ${s%%:*} bench --site ${s##*:} migrate; done
docker restart dsherp-validation-backend-1 dsherp-validation-beta-backend-1 dsherp-validation-platform-backend-1
```

- [ ] **Step 4: 运行确认通过** 平台原生 `tests="4"` 绿；`pytest tests/test_permission_matrix.py -q` 全绿；`dev_stack.py native-tests` 报告 `ok: True`；`infra.check_doctype_patches HEAD~1 HEAD` 退出 0（提交信息带 `no-patch:`）；集成 `test_platform_history_readonly.py`、`test_platform_identity.py` 绿。
- [ ] **Step 5: 提交**

```bash
git add frappe_app/dsherp_bridge/tests/permission_matrix.json tests/test_permission_matrix.py tests/test_audit_immutability.py frappe_app/dsherp_platform/tests/test_permission_matrix.py frappe_app/dsherp_platform/platform/doctype/ds_agent_task/ds_agent_task.json
git commit -m "test: G7 权限矩阵单一来源、宿主静态核对与平台原生矩阵；退役任务历史对操作员只读

no-patch: 只改 DS Agent Task 的角色授权位，不改字段与存量数据，bench migrate 同步 DocType 即可"
```

### Task D.7: nightly 加原生测试步；证据

**Files:** Modify `.github/workflows/nightly.yml`（在「集成测试」之后加一步，工件加 `work/junit-native/*.xml`，泄漏自检加同路径）；`docs/engineering/data-governance-evidence.md`（"frappe.db.delete 绕过 on_trash"一条追加"集成测试的清扫现由 tests/integration/residue.py 统一执行并核验"）；`README.md`（集成测试小节提到台账与"先登记再创建"）

```yaml
      - name: Frappe 原生测试（两个测试站；以 junit 判定）
        run: .venv/bin/python infra/dev_stack.py native-tests --junit work/junit-native
```
（`up --provision` 已含 `test-sites` 步；工件 `path` 加 `work/junit-native/*.xml`；`scan-artifacts` 加 `work/junit-native/*.xml`。）

`workflow_dispatch` 一次：全部步骤绿。**这一夜就是 G9 起算点**（日期与 run URL 记证据）。

### 切片 D 结束门

全量非集成绿；`ruff check .`；`native-tests` 报告 `ok: True`（junit 附证据）；本机（worker/scheduled 停）全量集成绿，跑完 `.runtime/integration-residue.json` 条目为空、`docker ps -a --filter name=dsherp-context-` 为空；`git diff origin/main --stat` 中 DocType JSON 只有 `ds_agent_task.json`，`infra.check_doctype_patches origin/main HEAD` 退出 0；nightly 含原生步绿。**检查点：PR 合入 main。**

---

## 切片 E：交接令牌落表收口（有效期窗口 + 缓存租约）

**起点**：`DS Configuration Transfer` 行长期保存成员的平台 OAuth 令牌密文（`platform_grant`），行是审计记录永不删除。**做法**：行加 `expires_at`（必填、只读、冻结），窗口常量一处 `configuration_transfer.TRANSFER_WINDOW_SECONDS = 7200`（用户裁决 2 小时）；grant 改存缓存 `dsherp_transfer_grant:<交接 id>`、寿命 = 窗口（复用 R7）；`export_transfer` 在窗口外或缓存丢失时明确拒绝；预览站 `_request` 把对端 `frappe.throw` 的原因中继给用户；`get_bundle` 只返回未过期交接；patch 回填并第一次真实使用 `EXPECTED_CHANGES`。**决定**：缓存只靠 TTL、不在发布成功时提前删（发布每一步都经回执链用到它；Partial 发布要重新 `prepare_publish` 仍需租约；多一条删缓存路径只多一种"删早了导致发布中途 fail-closed"）。不加定时清理："过期"是读取时判定。新增的两条拒绝都用 `frappe.throw`（417，前端 `firstServerMessage` 可见），身份/站点不匹配仍是 `PermissionError`（403，不泄露）。

### Task E.1: 定义与迁移合同（DocType JSON、控制器、patch、patches.txt）

**Files:** Modify `…/ds_configuration_transfer/ds_configuration_transfer.json`、`ds_configuration_transfer.py`、`frappe_app/dsherp_bridge/patches.txt`、`frappe_app/dsherp_bridge/configuration_transfer.py`（先加常量）；Create `frappe_app/dsherp_bridge/patches/v1/expire_transfer_grant.py`、`tests/test_transfer_grant.py`

**Interfaces:**
- Consumes: `dsherp.release_compare.compare(before, after, expectations)`（`_validate` 键：`patch`、`doctype`、`fields`、`rows`、`columns_removed`；再导出 `row_hash`）；`dsherp/admin.py:730-741` 的收集把每个 patch 模块的 `EXPECTED_CHANGES` 条目并成 `{'patch': 模块路径, **entry}`——条目里**不要**写 `patch`。
- Produces: 行字段 `expires_at`；`EXPECTED_CHANGES`；patch 行 `dsherp_bridge.patches.v1.expire_transfer_grant`。patch 模块把 `import frappe` 放进 `execute()`，宿主测试才能导入读 `EXPECTED_CHANGES`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_transfer_grant.py
"""A configuration transfer has a validity window and never carries the platform token (plan 5).

The transfer row used to keep the member's encrypted platform grant for as long as the row
existed - forever, it is an audit record. The grant now lives in the Site cache under the
transfer's id for exactly the transfer's window, as a run's grant does (R7); the row gains the
window as a visible, frozen field. This file checks the definition and the migration contract
on the host; the real-Site behaviour is in frappe_app/dsherp_bridge/tests/test_transfer_grant.py
and tests/integration/test_transfer_grants.py."""
import importlib
import json
from pathlib import Path

from dsherp import release_compare

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "frappe_app/dsherp_bridge"
PATCH = "dsherp_bridge.patches.v1.expire_transfer_grant"


def _definition():
    path = BRIDGE / "dsherp_bridge/doctype/ds_configuration_transfer/ds_configuration_transfer.json"
    return json.loads(path.read_text())


def test_the_transfer_row_has_a_window_and_no_column_for_the_grant():
    definition = _definition()
    fields = {field["fieldname"]: field for field in definition["fields"]}
    assert "platform_grant" not in fields and "platform_grant" not in definition["field_order"]
    assert fields["expires_at"]["fieldtype"] == "Datetime" and fields["expires_at"].get("reqd") == 1
    assert "expires_at" in definition["field_order"]


def test_existing_rows_are_migrated_by_a_named_patch_after_the_definition_is_synced():
    lines = [line.split("#")[0].strip() for line in (BRIDGE / "patches.txt").read_text().splitlines()]
    assert PATCH in lines, "existing rows must gain a window and lose the grant, not only the definition"
    assert lines.index(PATCH) > lines.index("[post_model_sync]"), "the new column exists only after model sync"


def _snapshot(columns, rows):
    table = {"columns": list(columns), "rows": {
        name: {"hash": release_compare.row_hash(values), "values": dict(values)} for name, values in rows.items()}}
    return {"tables": {"tabDS Configuration Transfer": table}, "singles": {}, "auth": {}}


def test_the_patch_declares_what_g2_will_see_and_the_declaration_makes_the_upgrade_clean():
    """The judge is the real comparison: dropping the grant column is drift until this patch
    declares it, and clean once it has."""
    module = importlib.import_module("frappe_app." + PATCH)
    declared = [{"patch": PATCH, **entry} for entry in module.EXPECTED_CHANGES]
    before = _snapshot(["name", "bundle", "platform_grant"],
                       {"t1": {"name": "t1", "bundle": "b1", "platform_grant": "cipher-text"}})
    after = _snapshot(["name", "bundle", "expires_at"],
                      {"t1": {"name": "t1", "bundle": "b1", "expires_at": "2026-09-07 12:00:00"}})
    undeclared = release_compare.compare(before, after)
    assert undeclared["clean"] is False
    assert undeclared["removed_columns"] == {"tabDS Configuration Transfer": ["platform_grant"]}
    report = release_compare.compare(before, after, declared)
    assert report["clean"] is True, report["differences"]
    assert report["differences"] and all(row["declared"] == PATCH for row in report["differences"])
    entry = next(item for item in declared if item["doctype"] == "DS Configuration Transfer")
    assert entry["columns_removed"] == ["platform_grant"]
    assert entry["fields"] == ["expires_at"] and entry["rows"] == "existing"
```

（`compare()` 返回结构的键名 `clean/removed_columns/differences/declared` 以 `dsherp/release_compare.py` 实际返回为准，实施时先读该文件对齐断言；若快照的列集变化以别的键报告，按实际键改断言，不改判定语义。）

- [ ] **Step 2: 运行确认失败** `pytest tests/test_transfer_grant.py -q` → 3 failed

- [ ] **Step 3: 实现**

DocType JSON（整文件）：

```json
{
 "doctype":"DocType","name":"DS Configuration Transfer","module":"DSHERP Bridge",
 "custom":0,"engine":"InnoDB","autoname":"hash","sort_field":"creation","sort_order":"DESC","track_changes":1,
 "field_order":["request_id","bundle","payload","expires_at"],
 "fields":[
  {"fieldname":"request_id","label":"Request identity digest","fieldtype":"Data","reqd":1,"unique":1,"hidden":1},
  {"fieldname":"bundle","label":"Configuration bundle","fieldtype":"Link","options":"DS Configuration Bundle","reqd":1},
  {"fieldname":"payload","label":"Immutable transfer binding","fieldtype":"Long Text","reqd":1},
  {"fieldname":"expires_at","label":"Transfer valid until","fieldtype":"Datetime","reqd":1,"read_only":1,"in_list_view":1}
 ],"permissions":[]
}
```

控制器（整文件）：

```python
import frappe
from frappe.model.document import Document

# The binding, the owner and the window are one immutable promise; only a new transfer may
# carry a different one. The grant is not here any more (plan 5): it lives in the Site cache
# for exactly this window (dsherp_bridge.grants.stash_transfer).
FROZEN=('owner','request_id','bundle','payload','expires_at')


class DSConfigurationTransfer(Document):
    def validate(self):
        previous=self.get_doc_before_save()
        if previous and any(self.get(key)!=previous.get(key) for key in FROZEN):
            frappe.throw('配置交接绑定不可修改')

    def on_trash(self):
        # Ruling #3: an audit record is an external promise. Permissions can be bypassed
        # (ignore_permissions, Administrator); this cannot.
        frappe.throw("配置交接记录不可删除")
```

patch（整文件）：

```python
# frappe_app/dsherp_bridge/patches/v1/expire_transfer_grant.py
"""A configuration transfer gets a validity window and its platform authorization leaves the row.

The transfer is an audit record (ruling #3): nobody may delete it, so whatever it stores is
stored for good. It used to store the member's encrypted platform OAuth token so that the
preview Site could later ask this Site to export the package under that member's authority.
That authority is now held in the Site cache under the transfer's id for the transfer's window
(dsherp_bridge.grants.stash_transfer), the way a run's is (R7), and the row records the window
as `expires_at`.

Existing rows: `expires_at` is back-filled as creation + window. For every row written before
this patch that moment has long passed, so they are simply expired - a transfer that was in
flight at upgrade time has to be started again. Their grant values are cleared and the column
dropped; a definition reload alone does not remove a column.

`frappe` is imported inside execute() on purpose: the host-side test imports this module to
check EXPECTED_CHANGES against the real G2 comparison without a Frappe installation."""

# What the release comparison (G2) will see on a Site that had transfers, declared so that the
# upgrade reads as clean: existing rows gain a value in `expires_at`, and `platform_grant` is gone.
EXPECTED_CHANGES = [
    {"doctype": "DS Configuration Transfer", "fields": ["expires_at"], "rows": "existing",
     "columns_removed": ["platform_grant"]},
]


def execute():
    import frappe
    from dsherp_bridge.configuration_transfer import TRANSFER_WINDOW_SECONDS

    if not frappe.db.exists("DocType", "DS Configuration Transfer"):
        return
    columns = frappe.db.get_table_columns("DS Configuration Transfer")
    if "expires_at" not in columns:
        # post_model_sync: the definition must already have materialised the column.
        raise RuntimeError("DS Configuration Transfer 没有 expires_at 列：DocType 定义未同步，不回填")
    frappe.db.sql(
        "update `tabDS Configuration Transfer` set expires_at = date_add(creation, interval %s second) "
        "where expires_at is null",
        (int(TRANSFER_WINDOW_SECONDS),),
    )
    if "platform_grant" in columns:
        frappe.db.sql("update `tabDS Configuration Transfer` set platform_grant = NULL where platform_grant is not NULL")
        frappe.db.commit()
        frappe.db.sql_ddl("alter table `tabDS Configuration Transfer` drop column platform_grant")
    frappe.db.commit()
```

`patches.txt` 的 `[post_model_sync]` 段末尾追加 `dsherp_bridge.patches.v1.expire_transfer_grant`；`configuration_transfer.py` 的 import 之后加：

```python
# How long one transfer may be used: from prepare on the source Site, through accept and
# preview on the preview Site, to publish back on the source. Its platform authorization is
# cached for exactly this long (grants.stash_transfer) and the row records the same moment as
# expires_at. A ruling point for the user (2026-09-07: 2 hours): a different value changes only this line.
TRANSFER_WINDOW_SECONDS=7200
```

- [ ] **Step 4: 运行确认通过** `pytest tests/test_transfer_grant.py tests/test_run_grants.py tests/test_audit_immutability.py tests/test_permission_matrix.py tests/test_doctype_patch_guard.py -q` 全绿（JSON 与 `patches.txt` 同在提交范围）。
- [ ] **Step 5: 提交**

```bash
git checkout -b plan5/transfer-window
git add frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_configuration_transfer/ frappe_app/dsherp_bridge/patches/v1/expire_transfer_grant.py frappe_app/dsherp_bridge/patches.txt frappe_app/dsherp_bridge/configuration_transfer.py tests/test_transfer_grant.py
git commit -m "feat: 配置交接行加有效期窗口、去平台授权列；patch 回填并向 G2 声明预期变化"
```

### Task E.2: 缓存租约与对端原因中继的纯函数（`grants.py`、`configuration_transport.py`）

**Interfaces:**
- `grants.py`（运行令牌的 `PREFIX/SLACK_SECONDS/lifetime/stash/of/drop` 一字不动）：`TRANSFER_PREFIX = 'dsherp_transfer_grant:'`；`stash_transfer(transfer_id, grant, seconds)`（**无论 grant 是否为空都写** `{'grant': grant or None}`——空条目要能区分"到期/丢失"与"从未有过令牌"，允许密码登录的开发站没有令牌也要能交接）；`of_transfer(transfer_id) -> dict | None`；`drop_transfer(transfer_id)`。
- `configuration_transport.peer_reason(body) -> str`：只读 `_server_messages`，每条 `message` 第一行、去 Traceback、每条截 200 字、`；` 连接；形状不对返回 `''`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_configuration_transport.py`）

```python
import json
from frappe_app.dsherp_bridge.configuration_transport import peer_reason


def test_peer_reason_relays_only_what_the_peer_said_through_frappe_throw():
    """A paired Site refuses with frappe.throw so that the person sees why; nothing else in an
    error body (exception class, traceback) is repeated to anyone."""
    said=json.dumps([json.dumps({'message':'配置交接已过期，请重新发起交接','title':'Message','indicator':'red'})])
    assert peer_reason({'exc_type':'ValidationError','exception':'Traceback (most recent call last)...','_server_messages':said})=='配置交接已过期，请重新发起交接'
    assert peer_reason({'exc_type':'PermissionError','exception':'frappe.exceptions.PermissionError: 配置交接身份或站点不匹配'})==''
    assert peer_reason(None)=='' and peer_reason({'_server_messages':'not json'})=='' and peer_reason({'_server_messages':json.dumps([42])})==''
    assert peer_reason({'_server_messages':json.dumps([json.dumps({'message':'Traceback (most recent call last)'})])})==''
    two=json.dumps([json.dumps({'message':'第一条\n第二行'}),json.dumps({'message':'第二条'})])
    assert peer_reason({'_server_messages':two})=='第一条；第二条'
    assert peer_reason({'_server_messages':json.dumps([json.dumps({'message':'x'*500})])})=='x'*200
```

- [ ] **Step 2: 运行确认失败** → `ImportError: cannot import name 'peer_reason'`
- [ ] **Step 3: 实现**

`grants.py` 末尾追加（docstring 末尾加一段说明交接租约同理）：

```python
TRANSFER_PREFIX = 'dsherp_transfer_grant:'


def stash_transfer(transfer_id, grant, seconds):
    """Write the transfer's lease. It is written even when there is no grant (a Site that still
    allows password login), so that a missing entry always means 'expired or lost', never
    'never had one'."""
    frappe.cache().set_value(TRANSFER_PREFIX + transfer_id, {'grant': grant or None}, expires_in_sec=int(seconds))


def of_transfer(transfer_id):
    """The lease as {'grant': str | None}; None once it has expired or was lost."""
    return frappe.cache().get_value(TRANSFER_PREFIX + transfer_id)


def drop_transfer(transfer_id):
    frappe.cache().delete_value(TRANSFER_PREFIX + transfer_id)
```

`configuration_transport.py` 末尾追加：

```python
def peer_reason(body):
    """What a paired Site said through frappe.throw, joined; '' when it said nothing usable.

    Only `_server_messages` is read: it is the one place Frappe puts a message the peer chose
    to show, and the peer is the Site we share a secret with. Exception classes and tracebacks
    are never relayed."""
    if not isinstance(body,dict):return ''
    raw=body.get('_server_messages')
    try:items=json.loads(raw) if isinstance(raw,str) else raw
    except ValueError:return ''
    if not isinstance(items,list):return ''
    reasons=[]
    for item in items:
        try:entry=json.loads(item) if isinstance(item,str) else item
        except ValueError:continue
        text=entry.get('message') if isinstance(entry,dict) else None
        if not isinstance(text,str) or not text.strip():continue
        line=text.strip().splitlines()[0].strip()
        if line and 'Traceback' not in line:reasons.append(line[:200])
    return '；'.join(reasons)
```

- [ ] **Step 4: 运行确认通过** `pytest tests/test_configuration_transport.py -q` → 3 passed
- [ ] **Step 5: 提交** `git commit -m "feat: 交接授权租约存站点缓存（窗口即寿命）；对端 frappe.throw 的原因可被中继"`

### Task E.3: 交接行为——窗口、租约、导出拒绝、对端中继、发布前查窗口（含原生测试类）

**Files:** Modify `configuration_transfer.py`、`configuration.py`（`get_bundle`）、`configuration_execution.py`（`prepare_publish`、`_confirm`）；Create `frappe_app/dsherp_bridge/tests/test_transfer_grant.py`

**Interfaces:**
- `check_window(transfer)`：`expires_at` 为空或 `<= now_datetime()` → `frappe.throw('配置交接已过期，请重新发起交接')`
- `prepare_transfer` 返回 `{'id','preview_url','expires_at'}`；幂等重放命中已过期记录 → 同样抛"已过期"
- `export_transfer` 顺序：信封 → 请求形状 → 身份/站点（PermissionError）→ **窗口** → **租约**（`frappe.throw('配置交接授权已失效，请重新发起交接')`）→ `check_authorization(bundle, grant=held['grant'])`
- `_request` 非 200：对端有 `_server_messages` → `frappe.throw('配置交接对端站点拒绝：<原因>')`；否则维持 `PermissionError('配置交接源站未授权或不可用')`
- `get_bundle().transfer`：只返回 `expires_at > now` 的最新一条，并带 `expires_at`
- `prepare_publish`、`_confirm(purpose='publish')` 在归属校验后 `check_window(transfer)`

- [ ] **Step 1: 写失败测试（原生测试类）**

```python
# frappe_app/dsherp_bridge/tests/test_transfer_grant.py
"""A configuration transfer is usable only inside its window and only while its authorization
lease is in the Site cache; the row never holds the platform token (plan 5).

prepare_transfer starts with frappe.db.rollback(), which would discard this class's uncommitted
fixtures, so the transfer row is inserted directly and the export side is exercised."""
import time

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from dsherp_bridge import grants
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.configuration_transfer import TRANSFER_WINDOW_SECONDS, check_window, export_transfer
from dsherp_bridge.configuration_transport import open_envelope, seal
from dsherp_bridge.context_api import _json

PEER = {'site': 'isolated-preview.localhost', 'url': 'http://preview-backend:8000',
        'public_url': 'http://preview.localhost:18085', 'secret': 'native-pair-secret'}
PACKAGE = {'version': 1, 'doctypes': [{'name': 'DS Native Transfer Test', 'module': 'DSHERP Bridge',
           'fields': [{'fieldname': 'result', 'label': 'Result', 'fieldtype': 'Data'}],
           'permissions': [{'role': 'System Manager', 'read': 1, 'write': 1, 'create': 1}]}],
           'extensions': [], 'workflows': []}
ACTOR = 'native-transfer@example.invalid'


class TestTransferGrant(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists('User', ACTOR):
            frappe.get_doc({'doctype': 'User', 'email': ACTOR, 'first_name': 'Native Transfer',
                            'user_type': 'System User', 'roles': [{'role': 'System Manager'}]}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        self.leases = []
        frappe.conf.dsherp_configuration_preview = PEER
        frappe.set_user(ACTOR)
        conversation = frappe.get_doc({'doctype': 'DS Conversation', 'title': 'Native transfer test'}).insert(ignore_permissions=True)
        self.bundle = propose_bundle(conversation.name, PACKAGE)

    def tearDown(self):
        for name in self.leases:
            grants.drop_transfer(name)
        frappe.conf.pop('dsherp_configuration_preview', None)
        frappe.set_user('Administrator')
        super().tearDown()

    def _transfer(self, expires_at):
        payload = {'source_site': frappe.local.site, 'preview_site': PEER['site'], 'actor': ACTOR,
                   'bundle_digest': self.bundle['digest'], 'package_digest': freeze_bundle(self.bundle['package'])['digest']}
        doc = frappe.get_doc({'doctype': 'DS Configuration Transfer', 'request_id': frappe.generate_hash(length=64),
                              'bundle': self.bundle['id'], 'payload': _json(payload), 'expires_at': expires_at}).insert(ignore_permissions=True)
        self.leases.append(doc.name)
        return doc

    def _export(self, doc):
        request = seal({'transfer_id': doc.name, 'actor': ACTOR, 'source_site': frappe.local.site,
                        'preview_site': PEER['site']}, PEER['secret'], 'export-request')
        frappe.set_user('Guest')
        try:
            return export_transfer(request)
        finally:
            frappe.set_user(ACTOR)

    def test_inside_the_window_with_a_lease_the_frozen_package_is_exported_and_the_row_holds_no_grant(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        data = open_envelope(self._export(doc), PEER['secret'], 'export-response')
        self.assertEqual(data['package'], PACKAGE)
        self.assertEqual(data['actor'], ACTOR)
        self.assertNotIn('platform_grant', frappe.db.get_table_columns('DS Configuration Transfer'))

    def test_an_expired_transfer_is_refused_by_name_before_any_authorization_check(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=-1))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        with self.assertRaises(frappe.ValidationError) as caught:
            self._export(doc)
        self.assertIn('配置交接已过期', str(caught.exception))
        with self.assertRaises(frappe.ValidationError):
            check_window(doc)

    def test_a_transfer_whose_lease_is_gone_is_refused_even_inside_its_window(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        with self.assertRaises(frappe.ValidationError) as caught:
            self._export(doc)
        self.assertIn('配置交接授权已失效', str(caught.exception))
        grants.stash_transfer(doc.name, None, TRANSFER_WINDOW_SECONDS)
        grants.drop_transfer(doc.name)
        with self.assertRaises(frappe.ValidationError):
            self._export(doc)

    def test_the_lease_carries_the_grant_and_dies_with_its_ttl(self):
        grants.stash_transfer('native-lease-probe', 'encrypted-grant', 60)
        self.leases.append('native-lease-probe')
        self.assertEqual(grants.of_transfer('native-lease-probe'), {'grant': 'encrypted-grant'})
        grants.stash_transfer('native-lease-empty', None, 60)
        self.leases.append('native-lease-empty')
        self.assertEqual(grants.of_transfer('native-lease-empty'), {'grant': None}, 'no grant is still a lease')
        grants.stash_transfer('native-lease-short', 'x', 1)
        time.sleep(1.5)
        self.assertIsNone(grants.of_transfer('native-lease-short'))

    def test_the_window_is_part_of_the_frozen_binding(self):
        doc = self._transfer(add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS))
        doc.expires_at = add_to_date(doc.expires_at, hours=1)
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
```

- [ ] **Step 2: 运行确认失败** `docker restart dsherp-validation-backend-1 && docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-test.localhost run-tests --app dsherp_bridge --module dsherp_bridge.tests.test_transfer_grant` → `ImportError: cannot import name 'check_window'`

- [ ] **Step 3: 实现**

`configuration_transfer.py` 头部与三个函数（其余不变）：

```python
"""Data-only handoff to one configured preview Site; no business credentials."""
import hashlib
import json
from urllib.parse import urlsplit
import requests
import frappe
from frappe.utils import add_to_date,get_datetime,now_datetime
from dsherp_bridge import grants
from dsherp_bridge.context_api import _user,_json
from dsherp_bridge.configuration import check_bundle,check_authorization,get_bundle,_propose_bundle
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.configuration_transport import seal,open_envelope,peer_reason

TRANSFER_WINDOW_SECONDS=7200   # 见 Task E.1 的注释


def check_window(transfer):
    """A transfer is usable only inside its window; a row without one (pre-migration) is closed."""
    if not transfer.expires_at or get_datetime(transfer.expires_at)<=now_datetime():
        frappe.throw('配置交接已过期，请重新发起交接')


def _public(transfer,public):
    return {'id':transfer.name,'preview_url':public.rstrip('/')+'/desk/dsherp-configuration-preview/'+transfer.name,
        'expires_at':str(transfer.expires_at)}


def _request(peer,purpose,payload):
    with requests.Session() as client:
        client.trust_env=False
        response=client.post(peer['url'].rstrip('/')+'/api/method/dsherp_bridge.configuration_transfer.'+purpose+'_transfer',
            headers={'X-Frappe-Site-Name':peer['site']},json={'envelope':seal(payload,peer['secret'],purpose+'-request')},
            timeout=15,allow_redirects=False)
    if response.status_code!=200:
        try:reason=peer_reason(response.json())
        except ValueError:reason=''
        # The peer's own words (frappe.throw, 417) reach the person; anything else stays opaque.
        if reason:frappe.throw('配置交接对端站点拒绝：'+reason)
        raise frappe.PermissionError('配置交接源站未授权或不可用')
    body=response.json()
    if 'message' not in body:frappe.throw('配置交接响应不完整')
    return _decode(body['message'],peer,purpose+'-response')


@frappe.whitelist(methods=['POST'])
def prepare_transfer(bundle_id,digest,request_id):
    user=_user();peer=_peer('preview')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:frappe.throw('请求标识无效')
    request_key=hashlib.sha256((user+'\0'+request_id).encode()).hexdigest()
    frappe.db.rollback();frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    bundle=check_bundle(bundle_id,digest)
    if not bundle['execution_ready']:frappe.throw('来源运行尚未成功完成，不能交接配置')
    public=peer.get('public_url');url=urlsplit(public or '')
    if url.scheme not in ('http','https') or not url.netloc or url.username or url.password or url.query or url.fragment:
        frappe.throw('隔离预览入口配置无效')
    payload={'source_site':frappe.local.site,'preview_site':peer['site'],'actor':user,
        'bundle_digest':bundle['digest'],'package_digest':freeze_bundle(bundle['package'])['digest']}
    existing=frappe.db.get_value('DS Configuration Transfer',{'request_id':request_key},'name',for_update=True)
    if existing:
        transfer=frappe.get_doc('DS Configuration Transfer',existing)
        if transfer.owner!=user or transfer.bundle!=bundle_id:frappe.throw('请求标识已用于其他配置交接')
        check_window(transfer)
        return _public(transfer,public)
    transfer=frappe.get_doc({'doctype':'DS Configuration Transfer','request_id':request_key,'bundle':bundle_id,'payload':_json(payload),
        'expires_at':add_to_date(now_datetime(),seconds=TRANSFER_WINDOW_SECONDS)}).insert(ignore_permissions=True)
    # The authorization the export acts under lives beside the transfer, not in it (R7 for runs).
    grants.stash_transfer(transfer.name,frappe.session.data.get('dsherp_platform_grant'),TRANSFER_WINDOW_SECONDS)
    return _public(transfer,public)


@frappe.whitelist(allow_guest=True,methods=['POST'])
def export_transfer(envelope):
    peer=_peer('preview');request=_decode(envelope,peer,'export-request')
    keys={'transfer_id','actor','source_site','preview_site'}
    if not isinstance(request,dict) or set(request)!=keys or not all(isinstance(value,str) for value in request.values()):
        raise frappe.PermissionError('配置交接请求无效')
    transfer=frappe.get_doc('DS Configuration Transfer',request['transfer_id'])
    payload=json.loads(transfer.payload)
    if (request['source_site']!=frappe.local.site or request['preview_site']!=peer['site']
        or request['actor']!=transfer.owner or any(request[key]!=payload[key] for key in keys-{'transfer_id'})):
        raise frappe.PermissionError('配置交接身份或站点不匹配')
    check_window(transfer)
    held=grants.of_transfer(transfer.name)
    if held is None:frappe.throw('配置交接授权已失效，请重新发起交接')
    original=frappe.session.user
    try:
        frappe.set_user(transfer.owner)
        bundle=check_authorization(transfer.bundle,grant=held['grant'])
        if not bundle['execution_ready']:frappe.throw('来源运行尚未成功完成')
        if bundle['digest']!=payload['bundle_digest']:frappe.throw('配置交接内容已变化')
        return seal({**payload,'transfer_id':transfer.name,'package':bundle['package']},peer['secret'],'export-response')
    finally:frappe.set_user(original)
```

`configuration.py` 的 `get_bundle`，把 `transfer=None` 起的取交接段换成：

```python
    from frappe.utils import now_datetime
    transfer=None
    row=frappe.db.get_value('DS Configuration Transfer',{'bundle':doc.name,'owner':user,'expires_at':['>',now_datetime()]},
        ['name','expires_at'],order_by='creation desc',as_dict=True)
    if row:
        peer=frappe.conf.get('dsherp_configuration_preview') or {};public=peer.get('public_url','').rstrip('/')
        transfer={'id':row.name,'preview_url':public+'/desk/dsherp-configuration-preview/'+row.name,'expires_at':str(row.expires_at)}
```

`configuration_execution.py`：`from dsherp_bridge.configuration_transfer import read_receipt,check_window`；`prepare_publish` 在 `if transfer.owner!=user:...` 之后加 `check_window(transfer)`；`_confirm` 的发布分支在 `transfer.owner` 校验之后加 `check_window(transfer)`。已知形态（写证据"如实说明"）：发布进行中窗口恰好到期，下一步 `read_receipt` 被源站拒绝、该步 Failed、执行记录 Partial，与"任何变化即停止发布"一致，2 小时内实际不会碰到。

- [ ] **Step 4: 运行确认通过** 三后端 restart；测试站先 migrate（Task E.4 的命令）；`run-tests --module dsherp_bridge.tests.test_transfer_grant` → `Ran 5 tests ... OK`
- [ ] **Step 5: 提交** `git commit -m "feat: 配置交接在窗口内、租约在时才可导出；过期与失效明确拒绝并可被预览站中继给用户"`

### Task E.4: 六站迁移、集成测试（回滚式 + alpha 真令牌 + 双站 HTTP 中继）

**Files:** Modify `tests/integration/test_configuration_transfer.py`、`tests/integration/test_configuration_confirmation.py`（插行补 `expires_at`）、`tests/integration/test_configuration_transfer_http.py`、`frappe_app/dsherp_bridge/tests/test_permission_matrix.py`（最小夹具加 `expires_at`）；Create `tests/integration/test_transfer_grants.py`

迁移（跑集成前，六站逐条）：

```sh
for s in backend:dsherp-validation.localhost backend:dsherp-daily.localhost backend:dsherp-test.localhost beta-backend:dsherp-beta.localhost platform-backend:dsherp-platform.localhost platform-backend:dsherp-platform-test.localhost; do
  docker compose -f infra/compose.validation.yml exec -T ${s%%:*} bench --site ${s##*:} migrate; done
docker restart dsherp-validation-backend-1 dsherp-validation-beta-backend-1 dsherp-validation-platform-backend-1
```

迁移后核对（alpha 与 beta 各一次，用 `site_exec.run_site_json`）：

```python
columns=frappe.db.get_table_columns('DS Configuration Transfer')
print(json.dumps({'platform_grant_present':'platform_grant' in columns,'expires_at_present':'expires_at' in columns,
 'rows_without_window':frappe.db.count('DS Configuration Transfer',{'expires_at':['is','not set']}),
 'patch_logged':bool(frappe.db.exists('Patch Log',{'patch':'dsherp_bridge.patches.v1.expire_transfer_grant'}))}))
```
Expected: `{"platform_grant_present": false, "expires_at_present": true, "rows_without_window": 0, "patch_logged": true}`

- [ ] **Step 1: 写失败测试**

(a) `tests/integration/test_configuration_transfer.py` 第一个测试，行创建后的断言替换为：

```python
    from dsherp_bridge import grants
    from dsherp_bridge.configuration_transfer import TRANSFER_WINDOW_SECONDS
    doc=frappe.get_doc('DS Configuration Transfer',transfer['id'])
    assert doc.owner==actor and doc.bundle==bundle['id']
    assert 'platform_grant' not in frappe.db.get_table_columns('DS Configuration Transfer')
    assert abs((doc.expires_at-doc.creation).total_seconds()-TRANSFER_WINDOW_SECONDS)<5
    assert transfer['expires_at']==str(doc.expires_at)
    assert grants.of_transfer(doc.name)=={'grant':None}   # no platform grant in this session: still a lease
    doc.payload='{}'
    try:doc.save(ignore_permissions=True);raise AssertionError('mutable transfer')
    except frappe.ValidationError:doc.reload()
    doc.expires_at=frappe.utils.add_to_date(doc.expires_at,hours=1)
    try:doc.save(ignore_permissions=True);raise AssertionError('window rewritten')
    except frappe.ValidationError:doc.reload()
```

在"无 DDL"断言之后、`forged=...` 之前插入：

```python
    grants.drop_transfer(doc.name)
    try:export_transfer(request);raise AssertionError('exported without a lease')
    except frappe.ValidationError as error:assert '配置交接授权已失效' in str(error)
    grants.stash_transfer(doc.name,None,TRANSFER_WINDOW_SECONDS)
    frappe.db.set_value('DS Configuration Transfer',doc.name,'expires_at',frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=-1),update_modified=False);frappe.db.commit()
    try:export_transfer(request);raise AssertionError('exported after the window')
    except frappe.ValidationError as error:assert '配置交接已过期' in str(error)
    frappe.set_user(actor)
    try:prepare_transfer(bundle['id'],bundle['digest'],'transfer-request-1');raise AssertionError('expired transfer replayed')
    except frappe.ValidationError as error:assert '配置交接已过期' in str(error)
    from dsherp_bridge.configuration import get_bundle
    assert get_bundle(bundle['id'])['transfer'] is None, 'an expired transfer is not offered again'
    frappe.db.set_value('DS Configuration Transfer',doc.name,'expires_at',frappe.utils.add_to_date(frappe.utils.now_datetime(),seconds=TRANSFER_WINDOW_SECONDS),update_modified=False);frappe.db.commit()
    assert get_bundle(bundle['id'])['transfer']['id']==doc.name
    frappe.set_user('Guest')
```
`finally` 里加 `grants.drop_transfer(transfer['id'])`（当 `transfer` 已赋值时）。

(b) `test_configuration_confirmation.py` 直接插入交接行的地方加 `'expires_at': add_to_date(now_datetime(), seconds=TRANSFER_WINDOW_SECONDS)`。

(c) 新文件 `tests/integration/test_transfer_grants.py`（用切片 C 的 `site_exec`/`residue`）：

```python
"""The authorization a configuration transfer is exported under lives beside the transfer, not
in it, for the transfer's window and no longer (plan 5; R7 did the same for runs). Real Site."""
import uuid

from site_exec import run_site_json

SITE = 'dsherp-validation.localhost'
ACTOR = 'dsherp-reader@example.invalid'


def test_the_grant_is_cached_for_the_window_never_stored_in_the_row_and_the_export_acts_under_it(residue):
    title = f'Transfer grant probe {uuid.uuid4().hex}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': title})
    out = run_site_json(SITE, r'''
from frappe.utils.password import encrypt
from dsherp_bridge import sso,grants
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer,export_transfer,TRANSFER_WINDOW_SECONDS
from dsherp_bridge.configuration_transport import seal,open_envelope
actor=ACTOR;frappe.set_user(actor)
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha','binding_version':'1','enterprise_version':'v1'}
sso.identity_for_token=lambda token,verify_business=False:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
frappe.conf.dsherp_configuration_preview={'site':'isolated-preview.localhost','url':'http://preview-backend:8000','public_url':'http://preview.localhost:18085','secret':'test-pair-secret'}
out={'configured':TRANSFER_WINDOW_SECONDS}
conversation=frappe.get_doc({'doctype':'DS Conversation','title':TITLE}).insert(ignore_permissions=True)
package={'version':1,'doctypes':[{'name':'DS Transfer Grant Probe','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
bundle=propose_bundle(conversation.name,package);frappe.db.commit()
transfer=prepare_transfer(bundle['id'],bundle['digest'],'transfer-grant-probe');frappe.db.commit()
try:
    row=frappe.db.get_value('DS Configuration Transfer',transfer['id'],'*',as_dict=True)
    out['column_present']='platform_grant' in frappe.db.get_table_columns('DS Configuration Transfer')
    out['row_mentions_token']='synthetic-token' in json.dumps(row,default=str)
    out['cached']=grants.of_transfer(transfer['id'])=={'grant':grant}
    out['window']=(row['expires_at']-row['creation']).total_seconds()
    request=seal({'transfer_id':transfer['id'],'actor':actor,'source_site':frappe.local.site,'preview_site':'isolated-preview.localhost'},'test-pair-secret','export-request')
    frappe.session.data.pop('dsherp_platform_grant',None);frappe.set_user('Guest')
    data=open_envelope(export_transfer(request),'test-pair-secret','export-response')
    out['exported']=data['package']==package and data['actor']==actor
    grants.drop_transfer(transfer['id'])
    try:export_transfer(request);out['without_lease']='exported'
    except frappe.ValidationError as error:out['without_lease']=str(error)
finally:
    grants.drop_transfer(transfer['id'])
print(json.dumps(out,ensure_ascii=False))
'''.replace('ACTOR', repr(ACTOR)).replace('TITLE', repr(title)), timeout=180)
    assert out['column_present'] is False and out['row_mentions_token'] is False, out
    assert out['cached'] is True and abs(out['window'] - out['configured']) < 5, out
    assert out['exported'] is True and '配置交接授权已失效' in out['without_lease'], out
```

(d) `test_configuration_transfer_http.py`（切片 C 迁移后的版本）在 `imported=...` 之后、源站"无 DDL"断言之前插入过期中继验证：

```python
    run_site_script(SOURCE, """
from frappe.utils import add_to_date,now_datetime
frappe.db.set_value('DS Configuration Transfer',TRANSFER,'expires_at',add_to_date(now_datetime(),seconds=-1),update_modified=False)
frappe.db.commit()
""".replace('TRANSFER', repr(prepared['transfer'])))
    run_site_script(PREVIEW, """
from dsherp_bridge.configuration_transfer import accept_transfer
try:accept_transfer(TRANSFER);raise AssertionError('expired transfer accepted over HTTP')
except frappe.ValidationError as error:
    assert '配置交接已过期' in str(error) and '对端站点拒绝' in str(error),str(error)
assert not frappe.db.exists('DocType','DS HTTP Transfer Test')
""".replace('TRANSFER', repr(prepared['transfer'])), user=PREVIEW_ACTOR)
```
并在测试开头 `residue.restore(SOURCE, 'drop transfer lease', "from dsherp_bridge import grants\nfor name in frappe.get_all('DS Configuration Transfer',filters={'owner':OWNER,'creation':['>=',SINCE]},pluck='name'):grants.drop_transfer(name)\nprint('ok')")`（`OWNER/SINCE` 按同法注入），保证租约不留在缓存。

(e) 原生矩阵测试的 `DS Configuration Transfer` 最小夹具加 `'expires_at': later`。

- [ ] **Step 2: 运行确认失败** 先**不要**迁移，直接跑 (a)(c)：预期 `Unknown column 'expires_at'` / `MandatoryError`——这正是 guard 要防的"定义改了、站没迁"。
- [ ] **Step 3: 实现** 执行迁移与核对；restart。
- [ ] **Step 4: 运行确认通过**

```sh
.venv/bin/python -m pytest tests/integration/test_configuration_transfer.py tests/integration/test_configuration_transfer_http.py tests/integration/test_configuration_confirmation.py tests/integration/test_configuration_origin.py tests/integration/test_configuration_verification.py tests/integration/test_transfer_grants.py tests/integration/test_run_grants.py -m integration -q
```
全部 passed；再跑全量集成与两个 App 的原生测试（矩阵类含新字段），数字记证据。
- [ ] **Step 5: 提交** `git commit -m "test: 交接窗口与租约的真实站行为——缓存有、行内无令牌、丢租约即拒、过期经 HTTP 中继给预览站"`

### Task E.5: 个人数据边界的文案与"任何行都不含平台令牌"的测试

**Files:** Modify `dsherp/user_data.py`、`tests/test_user_data.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_no_row_of_either_app_holds_the_platform_token_and_the_boundary_says_where_it_lives():
    """The boundary claims the platform OAuth token is in no row. That used to be false for
    DS Configuration Transfer (plan 5 moved it to the cache for the transfer's window), so the
    claim is checked against every DocType definition of both Apps, not asserted in prose."""
    definitions = sorted((ROOT / "frappe_app").glob("*/*/doctype/*/*.json"))
    assert definitions, "no DocType definitions found"
    for path in definitions:
        fields = {field.get("fieldname") for field in json.loads(path.read_text()).get("fields", [])}
        assert "platform_grant" not in fields, path
    transfer = user_data.BOUNDARY["DS Configuration Transfer"]
    assert transfer["clear"] == {} and "expires_at" in transfer["keep"] and "缓存" in transfer["why"]
    assert "缓存" in user_data.BOUNDARY["DS Model Run"]["why"]
    assert "DS Configuration Transfer" in user_data.plan(user="a@example.invalid", conversations=[], runs=[],
                                                          proposals=[], executions=[], sessions=0)["untouched"]
```

- [ ] **Step 2: 运行确认失败** → `KeyError: 'DS Configuration Transfer'`
- [ ] **Step 3: 实现** `BOUNDARY['DS Model Run']['why']` 末句改为"平台授权令牌不在任何行里：运行期间只存于缓存（R7），配置交接期间也只存于缓存、随交接窗口到期（计划 5）。"；追加：

```python
    'DS Configuration Transfer': {
        'clear': {},
        'keep': ('name', 'owner', 'request_id', 'bundle', 'payload', 'expires_at', 'creation'),
        'why': '交接是"把哪个冻结配置包、以谁的身份交给了哪个隔离站"的事实，逐字保留，有效期是它的一部分；'
               '平台授权令牌不在行里（只在交接窗口内存于缓存，到期即失效），删除时无需处理。',
    },
```
（`plan()` 的签名与 `untouched` 的生成方式以 `dsherp/user_data.py` 实际为准对齐。）
- [ ] **Step 4: 运行确认通过** `pytest tests/test_user_data.py tests/test_admin_cli.py -q -k "user_data or export_user or delete_user"`
- [ ] **Step 5: 提交** `git commit -m "fix: 个人数据边界如实声明配置交接为不动的审计记录；平台令牌不在任何行里由定义文件证明"`

### Task E.6: 前端有效期提示（一行）与 dist 重建

**Files:** Modify `frontend/src/ConfigurationBundle.jsx`、`frontend/src/ConfigurationBundle.test.jsx`、`frappe_app/dsherp_bridge/public/dist/context-agent.js`（`npm run build` 产出）

- [ ] **Step 1: 写失败测试** 第 3、4 个用例的 transfer 数据加 `expires_at:'2099-01-01 00:00:00'`，并断言 `screen.getByText(/交接有效至 2099-01-01 00:00:00/)`。
- [ ] **Step 2: 运行确认失败** `cd frontend && npm test -- ConfigurationBundle` → 2 failed
- [ ] **Step 3: 实现** 预览链接之后插入：

```jsx
  {transfer?.expires_at&&<Typography.Text type="secondary">交接有效至 {transfer.expires_at}（站点时间）；过期后需重新发送到隔离预览</Typography.Text>}
```
`cd frontend && npm run build`，确认只有 `context-agent.js` 变化。
- [ ] **Step 4: 运行确认通过** `npm test` 全绿（22 文件）。
- [ ] **Step 5: 提交** `git commit -m "feat: 配置包页在隔离预览链接旁显示交接有效期；重建 context-agent 产物"`

### 切片 E 结束门

非集成全绿；`ruff check .`；`npm test`；`infra.check_doctype_patches origin/main HEAD` 退出 0；全量集成；`dev_stack.py native-tests` `ok: True`；六站 migrate 核对 JSON 记证据。**检查点：PR 合入 main。**

---

## 切片 F：文档与规则收口

### Task F.1: README「当前状态」改成短表，历史原样保留

在「## 当前状态」下、原第一段之前插入下表，原段落逐字移到 `### 历史记录（保留，按时间倒序）` 之下：

```markdown
环境：本机隔离合成四站，ERPNext `16.33.0` / Frappe `16.31.0`，容器 Python `3.14.7`，DSH SDK/Runtime `0.1.1rc1`。全部证据来自合成数据，不代表生产可用；任何环境都未接入真实租户。

| 计划 | 范围 | 状态 | 证据 |
|---|---|---|---|
| 1 可观测与失败回放 | 工作流 D；评估集导出 | 2026-09-03 通过 C4 | [observability-evidence](docs/engineering/observability-evidence.md) |
| 2 运行底座可靠性 | 工作流 C；错误透传与 ErrorBoundary | 收尾切片已合入 main，放行以此为准（12 个独立复核：7 项成立、6 项推翻并修复） | [runtime-reliability-evidence](docs/engineering/runtime-reliability-evidence.md) |
| 3 部署制品与安全边界 | 工作流 A；出口控制、非 root、SSO 强制、CSP | 主体实现完成、验收未闭合：G1 字面判据与 ACME 待合规主机由审计方执行（已裁决 #9） | [deployment-security-evidence](docs/engineering/deployment-security-evidence.md)、[runbook](docs/engineering/deployment-runbook.md) |
| 4 数据治理与容灾 | 工作流 E；凭证托管与轮换 | 全部合入 main（PR #7、#9、#10）；G3 正式验收与真机 `restore-site` 未闭合 | [data-governance-evidence](docs/engineering/data-governance-evidence.md)、runbook 第 12–14 节 |
| 5 质量门禁（瘦身版） | CI 与 nightly、一条命令开发栈、登记式清理、原生测试骨架与权限矩阵、交接令牌落表收口；偏离见偏离表 | 进行中；G9 从第一个含原生测试的绿色 nightly 起算（日期与 run 见证据） | [quality-gates-evidence](docs/engineering/quality-gates-evidence.md) |
| 6 Agent 质量与成本 | 工作流 F；注入信封 | 未开始（串行于计划 5 之后，已裁决 #7） | [生产化总体设计](docs/superpowers/specs/2026-09-03-production-hardening-design.md) |
| 终验 生产浸泡 | 全部 | 未开始 | — |

已推迟、不在计划 5 范围：`dsherp/admin.py` 体量拆分（复盘 Q5）。
```
「文档」列表加计划 4、计划 5 证据两行。验收：`grep -c "^### 历史记录" README.md` 为 1，`git diff README.md | grep -c "^-[^-]"` 为 1（只改写了一行）。提交 `docs: README 当前状态改为短表，历史记录原样保留`。

### Task F.2: AGENTS.md「实现原则」加抖动测试规则

在用户保留的 Codex 改写行（"测试顺序按风险分级…"）之后插入：

```markdown
- 抖动的测试当天修或删，不许重试到绿；CI 不配置任何重试（`retries`、`--reruns`、失败后自动 re-run 都不允许）。未复现的失败按复盘 Q3 的原则处理：记录失败的 run 链接、日志与现场数据，开缺陷项并留在证据文档的"未闭合"里，不得"未复现即结案"。
```
与 Codex 那处修改一并提交：`docs: 实现原则——测试顺序按风险分级；抖动测试当天修或删、CI 无重试、未复现不结案`。

### Task F.3: 上位设计——计划 5 偏离表与工作流 E 的口径

`docs/superpowers/specs/2026-09-03-production-hardening-design.md`：
(a) T4 句"会话归档 90 天后清理"改为"原生会话目录按最后写入时间（目录及其内容的 mtime 最大值）超过 90 天清理（`dsherp-admin sessions --sweep`，常量 `dsherp/sessions.py:RETENTION_DAYS`），不按'归档'时间；旧布局的未归属目录只报告不删"；
(b) T8 句"时间统一 UTC 存储"改为"时间沿用 Frappe 的存储口径（站点系统时区、无时区标记）；跨站月报把每站时间按其时区换算成 UTC 后分月（`dsherp/usage.py`），未声明时区的站按 UTC 读并在报告里标 `assumed_utc`；不改存储口径"；
(c) 实施顺序表第 5 行"计划"改为"质量门禁（瘦身版，2026-09-07 起；与本节原文的偏离逐项见下方「计划 5 偏离表」）"；
(d) 表后插入：

```markdown
### 计划 5 偏离表（瘦身版，2026-09-07）

| 项 | 原文（工作流 G） | 本计划 | 理由 |
|---|---|---|---|
| mypy | 每次 push 跑 ruff + mypy（宿主包） | 不跑 mypy | 仓库没有类型注解基线，首跑即大量报错，只能加忽略或整文件补注解，都是非行为改动；另评估 |
| 风格 lint | ruff | ruff 只启用错误类规则（F、E9），不启用风格规则 | 既有紧凑书写（单行多语句）有 700+ 处，风格规则会触发大面积无行为改动，淹没真实问题 |
| 浏览器 e2e | Playwright 五条路径并自动落截图 | 不做；保留 vitest 组件测试与既有浏览器截图证据 | 需在 CI 内走真实登录链，成本超出瘦身范围；列入"未闭合" |
| Frappe 原生测试 | bridge/platform 业务逻辑测试全部改用 `bench run-tests` | 建立 `frappe_app/*/tests/` 骨架与首批用例（矩阵、交接令牌），存量注入脚本保留；集成测试改为先登记后创建 | 存量 57 个注入脚本整体改写是整文件重构（复盘 Q5 明确避免）；新逻辑先原生，存量按触碰逐步迁 |
| SBOM / CVE | 每次 push | 每周独立 workflow，不计入 G9 连续绿 | 上游漏洞库变化会让无代码变更的 push 变红，污染"30 天绿"的口径 |
| 权限矩阵 | `tests/permission_matrix.yml` | JSON，放在 `frappe_app/dsherp_bridge/tests/`，宿主静态测试与容器原生测试各读一次 | 容器内不引入 YAML 依赖；与读取它的测试同目录 |
| 每日集成 | 集成与 e2e 每日跑 | 每日在 GitHub 托管 runner 上从零开通四站跑集成 + 原生测试；e2e 不做 | 顺带补上一条命令的可复现开发环境 |
| G9 判据 | CI 配置存在且历史 30 天绿 | 只对 `ci.yml` 与 `nightly.yml` 计算，从第一个含原生测试步的绿色 nightly 起算（日期与 run 记证据） | 周报型 workflow 不代表代码状态；起算点要可指认 |
```
提交 `docs: 上位设计记计划 5 偏离表；工作流 E 的会话清理与时区口径改为当前实现`。

### Task F.4: 计划 5 证据文档

`docs/engineering/quality-gates-evidence.md`：总判定（不宣称 G9 通过；G9 由 workflow 历史证明）；**G9 起算**行（`2026-MM-DD · run URL`）；每个切片一节（做了什么 / 场景与结果表 / 未闭合与如实说明），各切片的数字与 run URL：切片 0（基线数字、最慢 20 条集成用例）、A（三绿→一红→三绿的 run、分支保护输出）、B（nightly 首跑时长/内存/磁盘、`up --provision` 时长、本机重建的 `time` 与 passed 数与 worker PID、supply-chain 结果与"不计 G9"）、C（台账样例脱敏、九个文件迁移前后 `dsherp-validation-backend-1` 出现次数、超时路径演练输出、policy_seed P95 决策数字）、D（`bench run-tests` 退出码实测原文、`IntegrationTestCase` 路径、测试站建站输出、nginx 对测试站的响应码、矩阵两次 junit 计数）、E（六站 migrate 核对 JSON、五条原生、集成三类、`EXPECTED_CHANGES` 首次真实使用）；**门禁**表；**未闭合与如实说明（整体）**：Playwright/mypy/存量注入脚本整体改写不做；`admin.py` 拆分推迟；G1/G3 仍未闭合；发布进行中窗口到期的形态；Redis 不可达时租约静默未写成、导出以"授权已失效"拒绝（fail-closed，但 prepare 时不暴露）；存量交接行迁移后全部过期；抖动用例处置逐条记录；**偏离表**（与 spec 同一份）。提交 `docs: 计划 5 证据文档——各切片场景表、门禁、未闭合、偏离表与 G9 起算`。

### Task F.5: runbook 第 15 节「质量门禁与 CI」与第 13 节一句

第 13 节"平台授权令牌不在运行行里…"改为"平台授权令牌不在任何行里：运行期间存于站点缓存、运行结束即删；配置交接期间也只存于缓存、随交接窗口（默认 2 小时，`TRANSFER_WINDOW_SECONDS`）到期，删除时都无需处理。"

新增「## 15. 质量门禁与 CI」（放在「## 与其他服务共用的主机」之前）：什么在哪跑（`ci.yml` 每次 push/PR：ruff、非集成 pytest、vitest、dist 一致性、迁移守卫；`nightly.yml` 每夜：`infra/dev_stack.py up --provision` → 集成 → 原生测试；`supply-chain.yml` 每周，不计 G9；任何 workflow 无重试）；怎么读一个红的 nightly（环境 / 夹具 / 真实回归三类；当天修或删；不能复现的记证据"未闭合"）；本地重跑 nightly（`dev_stack.py up --provision` / `pytest tests/integration -m integration -q` / `down --volumes`）；开发栈已在跑时的四条命令；原生测试命令；改了 DocType JSON 或 patch 之后六站 migrate；无重试规则与 G9 计算口径。提交 `docs: runbook 加第 15 节质量门禁与 CI；第 13 节补交接令牌只在缓存`。

### 切片 F 结束门

`grep` 核对各文档命令；非集成全绿；`ruff check .`。**检查点：PR 合入 main；计划 5 关闭，G9 由 nightly 历史继续证明。**

---

## 风险与缓解

| 风险 | 表现 | 缓解 |
|---|---|---|
| Docker Hub 匿名拉取限额（共享 runner IP） | `docker pull` 报 toomanyrequests | 工作流里五次退避重试；仍频繁失败时由用户建免费 Docker Hub 账号加 `docker/login-action`（按 SHA 固定）与仓库 secrets——用户检查点 |
| PyPI / api.deepseek.com 不可达 | `prepare_agent_runtime.sh` pip 失败；`test_agent_boundary` 拿不到 401 | 当夜红，按规则处置，不加重试；连续两夜同因则把 agent 运行时卷的 pip 结果接 `actions/cache`（key = lock 哈希） |
| runner CPU 慢于开发 Mac，触发用例内 `subprocess.run(timeout=)` | junit 里 `TimeoutExpired` | 只按 junit `time` 与 `--durations` 数据调；`test_policy_seed` 的 60 秒按 Task C.11 的判据 |
| Compose 密钥实现差异（bind mount vs 复制） | `check_secrets_readable` 失败 | 驱动 fastfail 并给出 `chown 1000`；若 CI 的 compose 改为复制实现而 chown 反而让客户端读不到，去掉 chown 步——由首跑决定，写进证据 |
| Node 26.7.0 在 setup-node 清单中不存在 | frontend/runtime job 装不上 | 改 `.nvmrc` 与 `engines` 到存在的 26.x，本机重验 dist 确定性 |
| `frappe.installer._new_site`/`setup_complete`/`has_permission(parent_doctype=)`/`table_exists(cached=)` 关键字与 16.31 实际不符 | 建站或测试报 TypeError | 每处都有"容器内核对"步骤；以容器内源码为准改调用，记证据 |
| 类级回滚被被测代码的 `commit()` 击穿 | 原生测试污染测试站 | 原生测试只用 Document API 插最小记录，不调 `send_message`/`prepare_transfer`/`operations.*`；`test_harness` 的回滚金丝雀每夜验证 |
| 泄漏：日志/工件含密钥 | `scan-artifacts` 退出 1 | 不上传即失败；`.runtime/control` 由 root 读；驱动失败时只回显 stderr |
| 本机重建销毁合成数据 | 不可逆 | 先在 CI 证明驱动，再经用户确认；控制面口令不删；worker 先停后启 |
| 30 天计数被误算 | 供应链红被当作破绿 | 证据文档写死：只有 `ci.yml`、`nightly.yml` 计入；起算点 = 第一个含原生步的绿夜 |
| 存量交接行迁移后全部过期 | 升级窗口内在途交接要重新发起 | patch docstring 与证据如实写明；交接是分钟级操作 |

## 自审（写完计划后的检查）

- **范围覆盖**：瘦身版五项保留（PR 门 + 守卫基线 → 切片 0/A；每日从零拉栈 → 切片 B；登记式清理 → 切片 C；权限矩阵 → 切片 D；交接令牌落表 → 切片 E）+ 文档（F）；砍掉的四项在偏离表逐条有理由；用户三项裁决落在 Global Constraints 与 Task B.7、E.1。
- **占位符**：只有 GitHub Actions 的 `<sha of …>`（允许）；其余版本号、digest、路径、命令均为具体值。代码块标为候选实现；四处 Frappe 关键字（`_new_site`、`setup_complete`、`parent_doctype`、`cached=`）与 `release_compare.compare` 返回键有"容器内/源码核对"步骤而不是猜测。
- **类型/接口一致性**：`run_native_tests(resolved, *, junit, runner)` 在 B（桩）、D（实现）、nightly（`--junit work/junit-native` 目录）三处一致；`CONTROL_SECRETS` 长度断言改为 `len(dev)+2` 以容纳 D 加的 `test_admin_password`；`site_exec.SITES` 含两个测试站；矩阵 `DS Configuration Transfer` 的 `why` 与 E 一致（无 `platform_grant`）；证据文档统一为 `docs/engineering/quality-gates-evidence.md`；分支名按切片。
- **顺序依赖**：nightly（B）先不含原生步，D 加入后的首个绿夜才起算 G9；C 的 `test_configuration_transfer_http.py` 迁移在 E 再加过期断言；D 的矩阵夹具在 E 后补 `expires_at`。

## 验收方式（端到端）

1. 每个切片末：`.venv/bin/ruff check .`、`.venv/bin/python -m pytest -q`、`(cd frontend && npm test)`、`node --test runtime/*.test.cjs`，加该切片的真实路径（本机集成 / workflow 运行 / 容器内核对）。
2. 切片 A：PR 上三绿 → 故意一红 → 三绿的三次 run URL；分支保护 `contexts == ["python","frontend","runtime"]`。
3. 切片 B：nightly `workflow_dispatch` 首跑全步绿并成功 `down --volumes`；本机 `down --volumes` → `up --provision` → 第二次 `ran: []` → 集成约 210 passed → worker 心跳恢复。
4. 切片 C：九个文件全绿；台账为空；每个迁移测试各做一次 `timeout=1` 的超时路径演练，teardown 清扫/恢复后容器内计数回到基线。
5. 切片 D：`dev_stack.py native-tests` `ok: True`（bridge 4+3 条、platform 4+1 条）；矩阵改错一处即红；nightly 含原生步绿 = G9 起算。
6. 切片 E：六站 migrate 核对 JSON；原生 5 条、集成新增三类、`EXPECTED_CHANGES` 经真实 `compare()` 判干净。
7. 切片 F：文档 `grep` 核对；README 只改写一行、历史逐字保留。
