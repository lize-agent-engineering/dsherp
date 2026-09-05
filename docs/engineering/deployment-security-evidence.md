# 计划 3：部署制品与安全边界 证据

日期：2026-09-05。分支 `plan3/deployment-security`（基于 `main` `b8e7b13`）。执行方式：按用户 2026-09-05 指示，本计划不再写交给 Codex 的实施计划，由 Claude 直接实施并自行入档；因此本文同时承担计划说明与证据两个角色。覆盖范围按[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md)实施顺序表第 3 行：工作流 A 全部 + 工作流 B 的出口控制（S1）、非 root 与最小挂载（S7）、SSO 强制（S3）、guest 端点加固（S5/A5）、CSP 与渲染限制（S6/A2 的渲染部分）。放行门 G1、G4。

**总判定**：G4 的全部判据在本机真实容器上实测通过；G1 的制品（镜像、compose、CLI、systemd unit、runbook）齐备并逐件验证，但**"干净 Linux x86_64 主机 60 分钟内拉起"这一执行动作尚未发生**——本机是 macOS arm64，没有可用的 Linux VM。G1 需审计方在新 VM 上按 [runbook](deployment-runbook.md) 执行一次才算通过。本文不宣称生产可用。

## 勘察

实施前用 15 个子代理做了一轮只读勘察（7 个维度 × 勘察员 + 对抗复核员，外加 1 名完整性批评员），全部 file:line 级；关键结论与本轮处置：

| 勘察结论 | 处置 |
|---|---|
| 零 Dockerfile；两个 App 与 dist 靠 bind mount + PYTHONPATH 进容器 | 自建两个镜像（下节） |
| Agent 容器 `--user 0:0`、接非 internal 的 `api` 网、可达任意公网与控制面前端 | internal 的 `agent` 网 + 唯一出口代理 + 非 root |
| `compose.validation.yml`（含 `.runtime/control/*` 密码路径）与 `prepare_agent_runtime.sh` 被挂进租户容器，只为算指纹 | 指纹改为宿主计算的 `deployment_digest` 随 run.json 传入 |
| `validate_session` 无 grant 即放行；9 个 guest 端点无限速、无来源限制、拒绝路径无审计 | fail-closed + 原生 `disable_user_pass_login` + `_capability_guard` |
| 全仓零 CSP；Markdown 只 `skipHtml`，img/外链可用 | 版本化 security-headers.conf + img 屏蔽/外链纯文本化 |
| 无 patches.txt、无 CI 检查、无升级回滚脚本 | patches.txt + 迁移守门脚本 + `release`/`rollback` |
| npm 317 条 resolved 指向 npmmirror；无 .nvmrc/.python-version/engines | 改回官方源并实测 `npm ci`；三处钉住 |
| 基底 digest 是否多架构未确认（审计 D7、存疑项 6） | 实测四个 digest 均含 `linux/amd64`（见运行契约） |
| `infra/probe_v16/t1_2_agent_runtime.sh` 把仓库根（含 `.env`）挂进容器 | 收窄为只挂 `requirements.lock` |
| 重启的 worker 会 `docker rm -f` 整个 daemon 上所有 `dsherp-context-*` 容器 | 清理按 `dsherp.project` 标签限定 |
| CI 以 root 跑测试会让 `deploy_env` 在导入时抛异常 | root 调用方回落到 uid 1000；只有显式配 0 才 fastfail |

批评员列出的 10 个"需用户裁决"点，逐条对照已裁决表后没有一条需要新裁决：镜像仓库地址与生产域名是环境参数（`prod.env.example`）；证书方式、App 装载方式、单 provider host、密钥托管方式、dev 冻结、probe 栈去留均在本文"偏离"节给出依据。

## 交付物与验证

### 1. 环境分层（`dsherp/deploy_env.py`，`infra/env/`）

一个环境文件决定镜像、域名、网络名、容器身份与内部服务地址；进程环境覆盖文件。dev 保留 `dsherp-validation` 项目名与历史端口（`DSHERP_ORIGINS` 覆盖），prod 必须给出明确 tag（拒绝 `latest`/`main`）。`deployment_digest()` 对 9 个部署面文件与 13 个环境键做 sha256。

验证：`tests/test_deploy_env.py` 11 条。

### 2. 自建镜像（`infra/docker/*/Dockerfile`，`infra/release_images.py`）

| 镜像 | 内容 | 本机实测 |
|---|---|---|
| `dsherp-frappe` | 钉死 digest 基底 + `frappe_app/` 两个 App（COPY 到 `/opt/dsherp-frappe`，`PYTHONPATH` 同 dev）+ dist 经 `assets/dsherp_bridge` 软链 + `compileall` | 构建通过，镜像 id `efac38f8…` |
| `dsherp-worker`（Agent Runtime） | 同基底 + `--require-hashes` 的 `/opt/runtime` venv + `dsherp/ config/ runtime/ business-skills/ requirements.lock`；不含 `infra/`、不含任何凭据；`chmod a=rX` | 构建通过，镜像 id `4a1f3dc0…`；以 `--user 501:20 --read-only` 导入 runner 全部模块成功 |

`release_images.py` 只接受 prod 环境、干净提交号与显式 `--platform`，并把 tag、提交、基底 digest、架构写入 `infra/releases/<tag>.json`；架构不符即拒绝落盘。验证：`tests/test_release_images.py` 7 条。

### 3. compose 与入口（`infra/compose.prod.yml`，`infra/compose.validation.yml`，`infra/caddy/`，`infra/nginx/`）

prod compose：全部第三方镜像按 digest、自建镜像按 `${REGISTRY}/dsherp-*:${TAG}`，`pull_policy: if_not_present`；零 bind mount（契约测试断言每个卷源都是命名卷）；9 个长驻服务全部有 healthcheck 与 `restart: unless-stopped`；六个网络中只有 `provider` 有默认路由，`agent`、`backplane`、`site`、`platform-site`、`edge` 全部 `internal: true`；`platform-backend` 不在 `agent` 网。`docker compose config` 渲染通过。

dev compose 只做三处加法：`agent`（internal）与 `egress` 网络、`agent-egress` 服务、两个入口覆盖 `security_headers.conf`；`backend` 额外加入 `agent` 网。现有 8 个 `v16-*` 卷与服务名一个未动。

Caddy：`caddy@sha256:4c6e91c6…`（多架构），`Caddyfile.template` 由 `render-ingress` 按租户清单逐站渲染。

验证：`tests/test_deployment_contract.py` 23 条（原 `test_v16_deployment_contract.py` 改名，硬计数 `== 11` 改为逐文件断言，守门文件集合扩到 7 个含 probe 栈）。

### 4. 出口控制与容器边界（S1、S7）—— G4

`tests/integration/test_agent_boundary.py` 用 `docker_command()` 生成的**同一条命令**（同镜像、同 `--user`、同网络、同挂载，只换入口程序）起真实容器观测：

| 判据 | 实测 |
|---|---|
| 非 root | `uid=501 gid=20`（宿主 uid；prod 固定 1000） |
| 不能出公网 | `api.deepseek.com:443` gaierror，`1.1.1.1:443` OSError |
| 只能到 provider 代理与业务站 | `agent-egress:8890` 可达、`dsherp-validation-backend-1:8000` 可达、`platform-frontend:8080` 不可达 |
| 控制面文件不可见 | `/opt/dsherp/infra`、compose、prepare 脚本、`.env`、`.runtime` 均不存在；`/opt/dsherp` 不可写 |
| 网络 internal | `docker network inspect` `Internal=true` |
| provider 只经代理可达 | 经代理 `GET /models` 得到 provider 的 401（无效 token），说明请求出了网；同请求不经代理无法解析 |

6 条全绿（29.9s）。关于最后一条：这是一次对 provider 免费端点 `GET /models` 的连通性探测，携带无效 token，不产生费用、不调用 chat/completions；与计划 2 混沌演练中经授权使用的探针是同一端点。

出口代理是 nginx 反向代理（复用基底镜像，未引入新镜像），`proxy_ssl_verify on` 校验上游证书，只反代 `DSHERP_PROVIDER_HOST` 一个域名；容器拿到的 `DEEPSEEK_BASE_URL=http://agent-egress:8890`，宿主熔断探针仍走 `.env` 里自己的地址（`runtime_host.agent_settings()` 与 `load_settings()` 分开）。

### 5. SSO 强制与 guest 加固（S3、S5、A5、S8）

`frappe_app/dsherp_bridge/sso.py::validate_session` 在容器内逐分支实测：

| 请求 | 结果 |
|---|---|
| `/api/method/login`（站点已开 `disable_user_pass_login`） | 拒绝「本站只接受企业平台登录」 |
| Administrator 浏览器会话访问 `/app/*` | 拒绝「运维身份不得建立业务界面会话」 |
| Administrator 持 `token` 头调 API | 放行 |
| 成员浏览器会话、无 grant | 拒绝「需要通过企业平台登录后再访问」 |
| 成员持 `token` 头调 API | 放行 |
| Guest `/api/method/ping` | 放行 |

密码登录关闭用的是 Frappe 原生 `System Settings.disable_user_pass_login`（在 `LoginManager` 内拒绝、登录页隐藏密码表单），由 `provision-tenant` 在 prod 写入；hook 里对 `/api/method/login` 的拒绝只是同义重述。四个本机 dev 站未开该开关，现有 SSO 集成测试与手工浏览器流程不受影响。

grant 校验的平台依赖解耦（S8 的缓存半边）：**平台可达时每个请求都回源，撤销在下一个请求即生效**（现有集成用例 `test_desk_sso.py::test_real_native_oauth_code_exchange_logs_into_bound_business_user` 要求撤销后立刻 403，第一版按 spec 字面做的"命中缓存即放行"正是被它拦下的——spec 允许 60 秒延迟的前提是推送撤销通道，而那在计划 4）；平台不可达时，60 秒内的最近一次成功判定（键 = user + sub + binding_version + enterprise_version）继续放行，过期即 403 而非裸 `RequestException`。平台侧推送失效端点未做。

五个运行凭据端点（`run_status`、`reserve_model_call`、`run_tool`、`record_run_event`、`finish_run`）入口统一经 `_capability_guard`：`site_config.dsherp_agent_sources` 网段白名单按 **socket 对端地址**校验（不信 `X-Forwarded-For`，因为 Agent 容器直连 gunicorn）；每 run 每秒 `dsherp_capability_rate_per_second`（默认 20）；两类拒绝都落 `capability_denied` 事件（含端点、原因、来源）；`tool_call` 事件带来源，`finished` 事件带来源与本次运行的凭据调用总数。未配置白名单的站（dev）不限来源，runbook 第 8 步写入 prod 网段。

### 6. CSP 与渲染限制（S6/A2 渲染部分）

`infra/nginx/security-headers.conf` 替换基底镜像同名 snippet：dev 两个入口 bind mount 覆盖，`dsherp-frappe` 镜像在构建时 COPY 同一文件（契约测试断言该行存在，重建镜像后 `cat` 该路径可见 CSP）。公网入口 nginx 模板对五个运行凭据端点直接 404——它们的合法调用方只有直连 gunicorn 的 Agent 容器，浏览器边缘上不该存在（契约测试断言）。dev 入口重启后实测：边缘 `run_status`/`finish_run` → 404，`/api/method/ping` → 200，CSP 头仍在；直连 gunicorn 的 `run_status` 带假凭据 → 403（守卫放行到 `_run` 后被拒），空 POST → 500——后者是 Frappe 对缺少必填参数的固有行为（未加守卫的 `receipt_transfer` 空 POST 同为 500），不是守卫引入的，也不在本计划范围。CSP：`img-src 'self' data: blob:`、`connect-src 'self'`、`object-src 'none'`、`frame-ancestors 'self'`；Desk 自身需要 `'unsafe-inline' 'unsafe-eval'`。本机 `curl -I http://localhost:18082/login` 已见该头；在应用内浏览器打开登录页，控制台无 CSP 违规，页面样式、图标、字体正常。

前端 `agent-ui.jsx::Prose`：`img` 渲染为 `[图片：alt]` 文本，`a` 只在站内相对链接时成为锚点，其余退化为「文字（URL）」；`urlTransform` 保持原值但从不为外链建锚。vitest 新增 1 条（外图不渲染、外链无锚点、站内链接可点），dist 已重建入仓。

### 7. 进程守护（`infra/render_worker_units.py`，`dsherp/sd_notify.py`）

systemd unit：`Type=notify`、`WatchdogSec=60s`、`Restart=always`、`ProtectSystem=strict`、`ReadWritePaths` 仅 `.runtime` 与 `work`、`SupplementaryGroups=docker`、`TimeoutStopSec=120`（长于 shutdown drain）。worker 启动后 `READY=1`，每轮 tick `WATCHDOG=1`，`NOTIFY_SOCKET` 缺席时静默无操作。LaunchAgent 渲染器保留为 dev。契约测试对两种渲染器都有断言。

### 8. 开站 CLI（`dsherp/admin.py`，`bin/dsherp-admin`）

| 命令 | 语义 |
|---|---|
| `secrets init` | 只生成缺失密钥（0600），永不覆盖；权限过宽即失败 |
| `doctor` | 环境、密钥、部署指纹清单、prod 必填项自检，先报清单再失败 |
| `provision-platform` | 建站（不装 erpnext）→ 装 `dsherp_platform` → `host_name` |
| `provision-tenant <slug>` | 建站 → 装 `dsherp_bridge` → 运行服务身份 → `site_config` → 关密码登录（prod）→ DS Enterprise → OAuth Client → Social Login Key → `dsherp_platform_oauth` → 租户清单 → 平台端点表 → 入口渲染 → healthcheck；每步先查后改，重复执行全为 kept |
| `retire-tenant <slug>` | 先 `bench backup --with-files` 再 `drop-site`；站不存在即拒绝 |
| `render-ingress` | 按租户清单渲染 Caddyfile |
| `release <tag>` | 逐站备份 → 逐站 migrate → 逐 DocType 逐字段摘要比对 → 报告；有差异退出码 1 |
| `rollback <tag>` | 必须显式给出每站备份文件；恢复后快照入报告 |

回调、授权、端点、业务站内部地址四类 URL 统一由 `deploy_env` 派生，`infra/provision_desk_oauth.py` 等脚本里的硬编码不再是 prod 路径。验证：`tests/test_admin_cli.py` 16 条（FakeBench 记录每一步对容器提出的命令）。本机 `bin/dsherp-admin doctor` 对 dev 返回空 findings。

### 9. 升级回滚与 schema（G2 前置）

两个 App 建立 `patches.txt`；容器内 `frappe.get_pymodule_path('dsherp_bridge','patches.txt')` 解析到 `/opt/dsherp-frappe/dsherp_bridge/patches.txt`，`get_patches_from_app` 读取成功（当前为空），四站 `bench migrate` 退出码全 0——证明 PYTHONPATH 装载方式不妨碍 patch 机制。`infra/check_doctype_patches.py`：DocType JSON 变更必须新增 patch 行或提交信息含 `no-patch: 原因`；`schema_version` 变更不接受 no-patch。验证：`tests/test_doctype_patch_guard.py` 9 条。CI 接入属计划 5。

### 10. 供应链

`package-lock.json` 317 条 `resolved` 改回 `registry.npmjs.org`，在临时目录 `npm ci` 通过、`npm audit` 0 漏洞；新增 `.nvmrc`（v26.7.0）、`.python-version`（3.12.11）、`engines`（node `>=24 <27`）。`infra/supply_chain.py` 构造 syft/grype 命令并按严重级别拦截，解析失败不当作通过；本机未装 syft/grype，只跑了 `--architectures-only`。验证：`tests/test_supply_chain.py` 6 条。

## 门禁

| 门 | 命令 | 结果 |
|---|---|---|
| 非集成 | `.venv/bin/python -m pytest tests --ignore=tests/integration -q` | `353 passed`（65.8s；计划 2 收口时 285） |
| Node Runtime | `node --test runtime/*.test.cjs` | `tests 10 / pass 10 / fail 0` |
| 前端 | `cd frontend && npm test` | 22 个文件 `202 passed` |
| 集成 | `.venv/bin/python -m pytest tests/integration -q` | 197 条全部通过（见下节的三轮记录） |
| 四站 migrate | alpha / daily / beta / platform | 退出码 0 / 0 / 0 / 0 |
| dist 一致性 | `node build.mjs` 后 `git diff --exit-code -- frappe_app/dsherp_bridge/public/dist` | 差异为空 |

门跑完后恢复了本机运行态：`scheduler` 重新启动，常驻 worker 以本分支代码 `launchctl bootstrap` 拉起（pid 20589），`prepare_host` 通过了对 `dsherp-validation_agent` 网络的检查，`/metrics` 应答 `queue_depth 0 / slots_busy 0 / provider_circuit_open 0`，无残留 `dsherp-context-*` 容器。

集成门运行前的环境处置：常驻 LaunchAgent worker 已 `bootout`（进程内 `run_once` 用例要求）；`scheduled` profile 的 `scheduler-worker` 自 01:04 起停止消费，队列里积压了 9 条 Frappe 自身的 `run_scheduled_job`，`purge_validation_jobs` 按设计对未知作业 fastfail，于是重启 `scheduler-worker` 排空后停掉 `scheduler`（生产者）再跑门。这与本计划改动无关，但说明计划 2 的 `scheduled` profile 在本机是脆弱的。

集成结果：第一轮全量 `20 failed, 177 passed`（14:48）——两簇根因：`finish_run` 用 `get_value` 读裸 `INCRBY` 计数器触发 `UnpicklingError`（15 条）、grant 缓存让撤销不再即时（`test_desk_sso` 5 条）；修复后（提交 `e7a4ca9`）第二轮全量 `4 failed, 193 passed`（14:33）——剩 4 条是三个集成用例手工拼 provider settings 缺 `deployment_digest`，且首次修法（把 digest 放进共享夹具）被 `test_context_worker_chain` 拦下：它把夹具挂进运行容器，容器里本就没有 compose 文件，digest 只能宿主算。改为宿主侧用例补 digest 后这 4 条 `4 passed`（3:21），期间未再改应用代码；合计 **197 条集成用例全部通过**。新增 `tests/integration/test_agent_boundary.py` 6 条包含在内。

## 偏离 spec 与理由

| spec 原文 | 实际 | 理由 |
|---|---|---|
| `compose.base.yml` + dev/prod 覆盖 | prod compose 全参数化独立成文；dev compose 保持字面量只做加法 | dev 栈是 191 条集成测试与四站数据的基底，重构成三层的收益（去重）不抵风险（容器名、卷名、网络名任何一处漂移都会打断全部集成证据）。不变量「任何环境的运行都来自镜像 tag」由 prod compose 与契约测试保证 |
| `*.base_domain` 通配证书 | 逐站 HTTP-01 | 通配必须 DNS-01，需要带 DNS 插件的 Caddy 构建与 DNS API 凭证；已裁决 #2 试点 ≤3 租户，逐站签发够用且零外部依赖 |
| App 以 `bench get-app` + `pip install` 装进镜像 | COPY 到 `/opt/dsherp-frappe` + `PYTHONPATH` | 与 dev 完全同构，零行为差异；patch 发现与四站 migrate 已实测不受影响。标准 bench app 安装要改 provision 的 `apps.txt` 写法并重验 `/assets` 路径，收益只是"更像上游" |
| 指纹改为对"渲染后的 compose"计算 | 对 9 个部署源文件 + 13 个环境键计算 | 渲染需要 docker，测试不能离线；源文件 + 环境键既确定又覆盖两个环境 |
| 每次 capability 使用记录到 DS Run Event | 拒绝路径每次记录；成功路径在已有 `tool_call`/`model_call_reserved` 事件上附来源；`finished` 记录调用总数 | `run_status` 每 2 秒一次，逐次记录会把计划 1 的回放流淹没；现方案审计信息完整且不膨胀 |
| S8 缓存 + 推送失效端点属"工作流 B"但未分配到计划 | 缓存做了，推送未做 | 缓存是 S3 的必要后果；60 秒 TTL 已把撤销延迟限在 60 秒内，推送是优化 |
| `infra/docker/worker/Dockerfile` 打包"宿主 worker" | 该镜像是 Agent Runtime 容器镜像；宿主 worker 仍在宿主用 systemd 跑 | 宿主 worker 必须能 `docker run`，进容器就要挂 docker socket——那是把控制面交给租户容器的反面教材 |
| guest 端点源地址限制在 nginx 层 | 应用层按 socket 对端校验 | Agent 容器直连 gunicorn 不经 nginx；nginx 层规则拦不住真实调用方 |

## 未完成项与后续

| 项 | 状态 | 去向 |
|---|---|---|
| 干净 Linux x86_64 主机整体拉起 | **未执行**（无 VM） | G1 由审计方按 runbook 执行；本机只验证了每一步的制品 |
| DS Membership 人工创建 | 沿用平台 Desk 手工 | 已裁决 #4 的短期密钥签发属计划 4，绑定链路不在本计划改 |
| 平台侧撤销推送端点 | 未做 | 计划 4 与凭证托管一起 |
| `infra/provision_*.py` 等 17 个 dev 脚本 | 保留 | 四站由它们建成、集成夹具依赖其产物；prod 路径已不经过它们。退役随 dev 收敛另立项 |
| syft/grype 实跑与 CI 接线 | 本机无工具 | 计划 5 |
| 登录页仍显示密码表单（dev 站） | 预期 | prod 站由 `disable_user_pass_login` 隐藏 |
| SBOM 与 CVE 结果入档 | 无 | 计划 5 首次 CI 运行后 |

## 提交

`7882fb0` 环境分层 → `cf939d9` 自建镜像 → `3f79ac0` 出口控制/非 root/契约 → `1e47cf3` 开站 CLI → `64ed22e` SSO/guest/CSP/渲染 → `5f8e71b` 升级回滚/patches/供应链 → `001c503` OAuth 链与 runbook → 本文。
