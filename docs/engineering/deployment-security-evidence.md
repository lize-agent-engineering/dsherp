# 计划 3：部署制品与安全边界 证据

日期：2026-09-05。分支 `plan3/deployment-security`（基于 `main` `b8e7b13`；PR #3 已合入 main `750f043`），收尾——本地与 x86_64 演练、独立审计后的修复——在分支 `plan3/g1-x86`（PR #4）。执行方式：按用户 2026-09-05 指示，本计划不再写交给 Codex 的实施计划，由 Claude 直接实施并自行入档；因此本文同时承担计划说明与证据两个角色。覆盖范围按[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md)实施顺序表第 3 行：工作流 A 全部 + 工作流 B 的出口控制（S1）、非 root 与最小挂载（S7）、SSO 强制（S3）、guest 端点加固（S5/A5）、CSP 与渲染限制（S6/A2 的渲染部分）。放行门 G1、G4。

**总判定**（经 2026-09-05 独立审计修订，见「独立审计」节）：计划 3 的制品与安全边界齐备，G4 的判据在 dev 容器、本机生产形态、x86_64 服务器的 amd64 发布镜像上实测，审计找出并已修复三处 G4 真缺陷（宿主网关可达、SSO 机器认证前缀绕过、边缘屏蔽漏 `/api/v2` 与 `cmd=`）；G1 的 runbook 在本机 Docker 与 x86_64 服务器上各以生产形态完整执行一次并修掉 22 个断点，但**按 spec 判据字面（干净主机、只给 tag/registry、60 分钟）与第 29 行「执行方自报不算通过」，G1 不记为通过**，ACME 亦未验。本文不宣称生产可用；真实租户接入仍需终验。

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

一个环境文件决定镜像、域名、网络名、容器身份与内部服务地址；进程环境覆盖文件。dev 保留 `dsherp-validation` 项目名与历史端口（`DSHERP_ORIGINS` 覆盖），prod 必须给出明确 tag（拒绝 `latest`/`main`）。`deployment_digest()` 对 10 个部署面文件与 13 个环境键做 sha256。

验证：`tests/test_deploy_env.py` 12 条（收尾后）。

### 2. 自建镜像（`infra/docker/*/Dockerfile`，`infra/release_images.py`）

| 镜像 | 内容 | 本机实测 |
|---|---|---|
| `dsherp-frappe` | 钉死 digest 基底 + `frappe_app/` 两个 App（COPY 到 `/opt/dsherp-frappe`，`PYTHONPATH` 同 dev）+ dist 经 `assets/dsherp_bridge` 软链 + `compileall` | 构建通过，镜像 id `efac38f8…` |
| `dsherp-worker`（Agent Runtime） | 同基底 + `--require-hashes` 的 `/opt/runtime` venv + `dsherp/ config/ runtime/ business-skills/ requirements.lock`；不含 `infra/`、不含任何凭据；`chmod a=rX` | 构建通过，镜像 id `4a1f3dc0…`；以 `--user 501:20 --read-only` 导入 runner 全部模块成功 |

`release_images.py` 只接受 prod 环境、干净提交号与显式 `--platform`，并把 tag、提交、基底 digest、架构写入 `infra/releases/<tag>.json`；架构不符即拒绝落盘。验证：`tests/test_release_images.py` 7 条。

### 3. compose 与入口（`infra/compose.prod.yml`，`infra/compose.validation.yml`，`infra/caddy/`，`infra/nginx/`）

prod compose：全部第三方镜像按 digest、自建镜像按 `${REGISTRY}/dsherp-*:${TAG}`，`pull_policy: if_not_present`；零 bind mount（契约测试断言每个卷源都是命名卷）；9 个长驻服务全部有 healthcheck 与 `restart: unless-stopped`；七个网络中只有 `provider`（Caddy/ACME 与出口代理）和 `worker`（backend 向宿主发布回环端口所需）有默认路由；后者让租户 backend 也有了缺省路由，是单主机拓扑下的取舍，`agent`、`backplane`、`site`、`platform-site`、`edge` 全部 `internal: true`；`platform-backend` 不在 `agent` 网。`docker compose config` 渲染通过。

dev compose 只做三处加法：`agent`（internal）与 `egress` 网络、`agent-egress` 服务、两个入口覆盖 `security_headers.conf`；`backend` 额外加入 `agent` 网。现有 8 个 `v16-*` 卷与服务名一个未动。

Caddy：`caddy@sha256:4c6e91c6…`（多架构），`Caddyfile.template` 由 `render-ingress` 按租户清单逐站渲染。

验证：`tests/test_deployment_contract.py` 27 条（收尾后）（原 `test_v16_deployment_contract.py` 改名，硬计数 `== 11` 改为逐文件断言，守门文件集合扩到 7 个含 probe 栈）。

### 4. 出口控制与容器边界（S1、S7）—— G4

`tests/integration/test_agent_boundary.py` 用 `docker_command()` 生成的**同一条命令**（同镜像、同 `--user`、同网络、同挂载，只换入口程序）起真实容器观测：

| 判据 | 实测 |
|---|---|
| 非 root | `uid=501 gid=20`（宿主 uid；prod 由 `DSHERP_AGENT_UID` 指定，必须等于服务账号的 uid，x86 演练为 995） |
| 不能出公网 | `api.deepseek.com:443` gaierror，`1.1.1.1:443` OSError |
| 只能到 provider 代理与业务站 | `agent-egress:8890` 可达、`dsherp-validation-backend-1:8000` 可达、`platform-frontend:8080` 不可达 |
| 控制面文件不可见 | `/opt/dsherp/infra`、compose、prepare 脚本、`.env`、`.runtime` 均不存在；`/opt/dsherp` 不可写 |
| 网络 internal | `docker network inspect` `Internal=true` |
| provider 只经代理可达 | 经代理 `GET /models` 得到 provider 的 401（无效 token），说明请求出了网；同请求不经代理无法解析 |

6 条全绿（29.9s）。关于最后一条：这是一次对 provider 免费端点 `GET /models` 的连通性探测，携带无效 token，不产生费用、不调用 chat/completions；与计划 2 混沌演练中经授权使用的探针是同一端点。

出口代理是 nginx 反向代理（复用基底镜像，未引入新镜像），`proxy_ssl_verify on` 校验上游证书，只反代 `DSHERP_PROVIDER_HOST` 一个域名；容器拿到的 `DEEPSEEK_BASE_URL=http://agent-egress:8890`，宿主熔断探针仍走 `.env` 里自己的地址（`runtime_host.agent_settings()` 与 `load_settings()` 分开）。（**2026-09-06 更正**：这句在 `3f79ac0` 之后并不成立——该提交把探针的 settings 换成了 `agent_settings()`，宿主探针实际拿到的是容器的代理地址，宿主上无法解析，熔断打开后不会再关闭；计划 3 收口以 `context_worker.host_probe` 改回 `.env` 直连地址并加单元测试锁定，见「计划 3 收口」节。）

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
| `release <tag>` | 计划 3 交付的是骨架（按 DocType 比对整表摘要、排除子表与单值、读错误静默跳过、从未真实跑过；2026-09-06 更正）。计划 4 首片已重写：按元数据分桶、原生 SQL 分页、任何读错误即中止、逐行逐字段比对、patch 声明预期变化、备份集落归档卷，见 [data-governance-evidence.md](data-governance-evidence.md) |
| `rollback <tag>` | 计划 3 版本只恢复不比对。计划 4 首片已重写：从 release 归档找到备份集（可显式覆盖）、带 files 恢复、恢复后与升级前快照比对，见 [data-governance-evidence.md](data-governance-evidence.md) |

回调、授权、端点、业务站内部地址四类 URL 统一由 `deploy_env` 派生，`infra/provision_desk_oauth.py` 等脚本里的硬编码不再是 prod 路径。验证：`tests/test_admin_cli.py` 27 条（收尾后）（FakeBench 记录每一步对容器提出的命令）。本机 `bin/dsherp-admin doctor` 对 dev 返回空 findings。

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

## 本地 Docker 上的 G1 演练（2026-09-05，用户指示以本机 docker 代替 Linux 主机）

按 runbook 在本机 Docker Desktop 上以**生产形态**跑第二套栈：项目名 `dsherp`（dev 是 `dsherp-validation`）、`infra/compose.prod.yml`、`local/dsherp-frappe:v0.3.0-rc1` 与 `local/dsherp-worker:v0.3.0-rc1`（`release_images.py --platform linux/arm64`，manifest 记提交 `49c1cb2`）、`DSHERP_BASE_DOMAIN=localhost`、独立的 `.runtime/prod-local/` 运行态与密钥目录。与真正 Linux x86_64 主机的差别只有架构（arm64）、`.localhost` 用 Caddy 内置 CA、笔记本规格的数据面、以及没有 systemd（worker 前台核对）。

真正的价值在于**runbook 一执行就断在哪里**。以下每一条都是只读勘察与离线测试拦不住、只有实跑才会暴露的缺陷，全部已修并各有测试：

| # | 断点 | 现象 | 修法（提交） |
|---|---|---|---|
| 1 | CLI 的 compose 调用没带 `--env-file` | `compose.prod.yml` 的 `${…:?}` 在 `exec` 时插值失败 | `Bench._compose` 传入 deploy_env 解析所用的同一份文件（`49c1cb2`） |
| 2 | 空 sites 卷没有 `apps.txt`/`common_site_config.json` | `bench new-site` 无库地址 | `ensure_bench` 引导三个文件（`49c1cb2`） |
| 3 | `DSHERP Member` 角色只有 dev 种子脚本会建 | 新平台站的 OAuth Client 因链接不存在失败 | `provision-platform` 自建角色（`49c1cb2`） |
| 4 | 镜像自带 `apps.txt`（frappe/erpnext）与 `common_site_config.json={}` | `ensure_bench` 把"存在"当"已引导"，什么都没写 | 按内容判断：apps 逐行补缺，配置只在无 `db_host` 时写（`34f0d39`） |
| 5 | 首次 `new-site` 半途失败留下空壳目录 | 重跑被误判 kept | 站点以 `site_config.json` 为准，空壳目录明确报错（`34f0d39`） |
| 6 | Fault 吞掉 stderr | 真实原因不可见 | Fault 带脱敏后的 stderr 尾部（`34f0d39`）——第 7 条就是靠它找到的 |
| 7 | 镜像 `apps.txt` 结尾无换行 | 追加后变成 `erpnextdsherp_bridge`，导入失败 | 追加前补换行，已在真实镜像文件上 `od -c` 验证（`71b66f0`） |
| 8 | 运行态/密钥目录靠 shell export | 一次漏 export，compose 回落到 `../.runtime/control` 用**开发密钥**初始化了新库，CLI 以生产密钥被拒（db 容器内密钥 digest 与 dev 一致，已核实） | 两个目录进 `prod.env` 由 deploy_env 与 compose 共读，prod 必须绝对路径，`doctor` 对缺项报错（`b9a6032`） |
| 9 | runbook 用 `$COMPOSE` 变量、入口配置在第一次 `up` 之后才渲染 | zsh 不分词；compose 解析时就要求 configs 文件存在 | 改 shell 函数；`render-ingress` 提前到第一次 `up` 前（`49c1cb2`、`34f0d39`） |
| 10 | 新站 System Settings 无 `language`/`time_zone` | Frappe 拒绝保存，关密码登录失败 | 两项作为"为空才写"的引导值（`946a0b4`） |
| 11 | prod frontend 用镜像入口 | 入口启动时 `rm -rf sites/assets`，在 `:ro` 卷上崩溃循环 | 同 dev：`entrypoint: []` 直接跑 `nginx-entrypoint.sh`（`7482304`） |
| 12 | backend 只接 internal 网络 | 宿主 worker 无路可达；加了回环 `ports:` 仍不通——**Docker 不为只在 internal 网络上的容器发布端口** | backend 额外接非 internal 的 `worker` 网络，只在 `127.0.0.1:8000` 发布；契约测试断言只有 backend 与 Caddy 发布端口且回环绑定（`7482304`） |
| 13 | Caddy 探针 `wget --spider :80` | 80 只做 HTTPS 重定向，跟随后证书不匹配，永远 unhealthy | 改为 443 监听探测（`7482304`） |
| 14 | 边缘 `run_status` 返回 500 而非 404 | 屏蔽块只在 dev 模板里，prod 用的是镜像自带模板 | `infra/nginx/site.conf.template`（基底模板 + 屏蔽块）COPY 进镜像，两份模板同一断言（`7482304`） |
| 15 | runbook 的 `up` 列表从未起过 scheduler/queue | `compose ps` 只列已创建的服务，"9/9 全绿"实为 13 个里的 9 个 | 补进 `up` 列表，runbook 提醒数服务数 |
| — | `ensure_runtime_identity` 每次 `generate_keys` | 幂等重跑会静默作废运行密钥 | 只在无 api_key 时签发，`--rotate-runtime-key` 显式轮换；密钥只在签发那次出现（`946a0b4`）——本条不是实跑发现，是写 worker profile 时意识到的 |

### 演练结果（最终镜像：manifest 提交 `7482304`，`local/dsherp-frappe` `sha256:1711…`、`local/dsherp-worker` `sha256:4602…`，`linux/arm64`）

| G1 / G4 判据 | 实测 |
|---|---|
| 从 tag 到全绿 | `secrets init` → `doctor` 空 findings → `render-ingress` → 数据面 3 服务 healthy → `provision-platform`（18s）→ `provision-tenant g1`（50s；中途断在 #10 后**续跑**：断点前五步全 kept，运行身份未重签）→ 入口/出口/Caddy → **13 个服务全部 Up，9 个带探针的全部 healthy** |
| 幂等 | 平台重跑 5 步全 kept；租户第二次重跑 15 步除清单/渲染/healthcheck 外全 kept，输出不含密钥 |
| TLS | Caddy 为 `g1.localhost`、`platform.localhost` 签发（issuer `Caddy Local Authority - ECC Intermediate`），HTTP/2，HSTS；`http://` → 308 到 https |
| CSP | `https://g1.localhost/login` 200，含 `content-security-policy`（img-src/connect-src 仅 self） |
| SSO 强制 | `POST /api/method/login` → 401；登录页无密码输入框（`disable_user_pass_login` 由 `provision-tenant` 写入） |
| 边缘屏蔽 | `run_status`、`finish_run` 经 Caddy 转到 frontend nginx → 404（屏蔽在 frontend 的站点模板里，Caddyfile 只做反代；审计发现 `/api/v2/method/` 与 `?cmd=` 两条 Frappe 路由曾漏掉，已补） |
| 平台 | `https://platform.localhost/api/method/ping` 200，登录页 200（平台保留密码登录，它是 SSO 的身份源） |
| 容器边界（用发布镜像 `local/dsherp-worker` 在 `dsherp_agent` 网络实测） | uid 501/gid 20；公网 DNS 与 IP 均不可达；`agent-egress:8890`、`backend:8000` 可达；`platform-backend`、`db`、`redis-queue` 不可达；`/opt/dsherp/infra`、`.env`、`.runtime`、`/run/secrets` 不存在；`/opt/dsherp` 与 `/opt/runtime` 不可写；provider 仅经代理（401） |
| 宿主 worker | 停 dev worker 后以 `DSHERP_ENV=prod` 前台跑 40s：`prepare_host` 通过（发布镜像 + `dsherp_agent` 网络）、`/metrics` 9110 应答、**站侧 `dsherp_worker_heartbeat` 已写入**、零 `worker_error`、SIGTERM 干净退出；dev worker 随后恢复 |
| 门禁（本轮修复后） | 离线 `363 passed`、Node `10 pass`（前端与 dist 自上次无变化） |

本地演练与真正 Linux x86_64 主机的差别（架构、systemd）随后由 x86_64 服务器演练补上（下节）；ACME 两次都验不了（本地无公网域名，服务器 80 被占）。演练结束后本地 prod 栈连卷拆除、两个发布镜像删除，`.runtime/prod-local/` 一并清理。

## x86_64 服务器上的 G1 演练（2026-09-05，用户指定 120.24.29.56）

用户提供的是一台**共用**的阿里云 ECS（`yibao-ecs`：x86_64、CentOS 7、8 核 15 GB、Docker 26.1.4 + compose 2.27.1、systemd 219），上面跑着十几个其他项目的容器并占用 80/443。演练以生产形态在它上面走 runbook，端口挪到 18080/18443，所有其他服务不动。这条腿补的是本地演练缺的**架构**与 **systemd** 两项；ACME 仍不可验（HTTP-01 需要 80）。

主机侧又暴露出四个只有换一台真实机器才会碰到的前置条件，全部写进 runbook 前置表：

| # | 断点 | 现象 | 处置 |
|---|---|---|---|
| 16 | **宿主 glibc 2.17** | `deepseek-harness-runtime-bin==0.1.1rc1` 只发布 `manylinux_2_28` wheel，`uv pip sync requirements.lock` 无解 | runbook 硬前置：宿主 glibc ≥ 2.28（Rocky 8 / Debian 10 / Ubuntu 18.10+）。演练用去掉两个 `deepseek-harness*` 块、其余 547 行哈希原样的子集装宿主 venv——已核实宿主 worker 与 CLI 不引入 SDK（只有 `dsh_probe.py`、`session_runtime.py` 引入，都在容器侧）。用户知情同意后继续 |
| 17 | `/opt` 带 immutable 属性 | root 也建不了 `/opt/dsherp` | 安装根本就是参数，改用 `/home/dsherp/dsherp`；runbook 注明 |
| 18 | 主机没有 git | runbook 第 1 步 `git clone` 不可用 | `git archive` 传源码树，`release_images.py --git-commit` 显式记录提交；runbook 注明 |
| 19 | systemd 219 | `systemd-analyze verify` 实测：`ProtectSystem=strict` "Failed to parse… ignoring"（生效值变成 `no`），`ProtectKernelTunables`/`ProtectControlGroups`/`RestrictSUIDSGID`/`ReadWritePaths` 为 Unknown lvalue；保留 `Type=notify`、`WatchdogUSec=1min`、`Restart=always`、`User/Group`、`PrivateTmp`、`ProtectHome=read-only`、`NoNewPrivileges` | runbook 前置：systemd ≥ 242 才有完整沙箱，且说明退化是静默的 |
| 20 | 安装根在 `/home/dsherp/dsherp`（因 #17）+ systemd 219 | `ProtectHome=read-only` 生效而 `ReadWritePaths` 被忽略 → worker 写不了 `.runtime` 的锁与 pid，unit 起不来 | runbook：安装根不放 `/home`，`/opt` 不可用时用 `/srv/dsherp`；演练先按原样启动记录失败，再挪到 `/srv/dsherp` 证明正向路径 |
| 22 | systemd unit 里没有 `DSHERP_ENV` | 挪到 `/srv` 后 worker 仍起不来：`prepare_host` 按 dev 解析、去查只有开发环境才有的 `dsherp-v16-agent-runtime` 卷。本地 prod worker 都是手动 export 跑的，从未暴露 | 渲染器固定写 `Environment=DSHERP_ENV=prod`，契约测试断言 |
| 21 | `Bench.run` 无 `input` 时让容器命令继承父进程 stdin | 经 ssh 用 stdin 喂脚本驱动 CLI 时，`bench new-site` 把脚本剩余文本当输入吃掉，`provision-platform` 之后的步骤静默消失、退出码 0 | 无 input 的容器命令一律 `stdin=DEVNULL`（`1a3a35a`）；远端驱动改为 scp 脚本文件 + `ssh -n` |
| — | GitHub Releases 从该 ECS 下载 ~30 KB/s；Docker Hub 直连不通 | uv 与 CPython 发布包在 Mac 下载、sha256 校验后 scp；uv 用 `file://` 镜像装 3.12.11（uv 自带哈希校验）；基底镜像经主机已配置的国内加速器按 digest 拉取，四个均为 amd64 | 环境问题，不是产品缺陷 |

### 演练结果（x86_64；镜像 manifest 提交 `dde55f0`、`linux/amd64`、两镜像 `architecture: amd64`，原生构建 2060 秒，PyPI 限速所致）

| G1 / G4 判据 | 实测（120.24.29.56，生产形态，端口 18080/18443） |
|---|---|
| 从源码树到全绿 | `secrets init` → `doctor` 空 → `render-ingress` → 数据面 3 healthy → `provision-platform`（25s，全 created）→ `provision-tenant g1`（**71s 一次通过**，15 步全 created/issued/disabled/bootstrapped——本地演练修掉的 10 个开站断点一个都没再出现）→ scheduler/queue/入口/出口/Caddy → **13 服务全 Up、9 探针全 healthy** |
| 幂等 | 平台重跑 5 步全 kept；租户重跑 15 步除清单/渲染/healthcheck 外全 kept，输出不含密钥 |
| TLS | Caddy 内置 CA（issuer `Caddy Local Authority - ECC Intermediate`），HSTS；`http://…:18080` → 308 |
| CSP | `login` 200，`Content-Security-Policy` 与 HSTS 头在 |
| SSO 强制 | `POST /api/method/login` → 401；登录页无密码输入框 |
| 边缘屏蔽 | `run_status`、`finish_run` 经 Caddy 转到 frontend nginx → 404（屏蔽在 frontend 的站点模板里，Caddyfile 只做反代；审计发现 `/api/v2/method/` 与 `?cmd=` 两条 Frappe 路由曾漏掉，已补） |
| 平台 / 回环 | 平台 ping 200；宿主 `127.0.0.1:8000`（Host g1）→ 200（worker 网络 + 回环发布在 x86 上同样成立） |
| 容器边界（**amd64 发布镜像** `local/dsherp-worker:v0.3.0-rc2` 在 `dsherp_agent`） | `machine=x86_64`、uid 995；公网 DNS/IP 不可达；`agent-egress`、`backend` 可达；`platform-backend`、`db` 不可达；无控制面文件；代码不可写；provider 仅经代理（401） |
| 完整锁在 amd64 容器内 | `deepseek-harness-runtime-bin 0.1.1rc1` 在 worker 镜像里可导入——`manylinux_2_28` wheel 在 bookworm（glibc 2.36）容器内正常，只有宿主 CentOS 7 装不了 |
| 宿主 worker / systemd | 先按原样在 `/home` 根下 `systemctl start`：如 #20 所料 `OSError: [Errno 30] Read-only file system: …/.runtime/business-sessions`，`Restart=always` 反复拉起；挪到 `/srv/dsherp` 后又暴露 #22（无 `DSHERP_ENV`）；修后 `enable --now`：**`active (running)`、`Type=notify`（READY=1 被 systemd 接受）、`WatchdogUSec=1min`、跨过看门狗周期无重启**、`/metrics` 9109 应答、**站侧 `dsherp_worker_heartbeat` 经 `127.0.0.1:8000` 写入**、journal 仅有 219 对 `ProtectSystem=strict` 的解析告警与新站的 `ops_snapshot_stale`。宿主 venv 为不含 SDK 的子集（#16），provider 凭据为合成值，演练未调用 provider |

服务器上的源码树在演练时是 `dde55f0`（`git archive`）加 `3ca26ca` 的渲染器热修（`admin.py` 仍是 `dde55f0` 版本，#21 的 stdin 修复靠驱动脚本的 `</dev/null` 绕过，未在该主机验证）；两个镜像由 `dde55f0` 构建。**栈在演练后没有立即拆除**——先留给独立审计做只读复验，审计确认的问题修复后再次在同一栈上复验（见「独立审计」节），然后才清除。本文早先版本在栈仍在运行时就写了「已全部清除」，是不实陈述，由审计指出并在此更正。

**时长与判据字面**：从首个 `sudo` 到 worker active 共 68.5 分钟（其中原生构建 34 分钟、两次安装根/unit 返工约 5 分钟），G1 的「60 分钟内」**未满足**；主机不「干净」（共用 ECS、30 个他人容器、80/443 被占）且违反 runbook 自己的两条前置（glibc ≥ 2.28、systemd ≥ 242）；没有 git tag（只有镜像 tag）、没有 registry（目标机自建）。因此按 spec「验收门」的字面与第 29 行「执行方自报不算通过」，**G1 不能记为通过**；本文能宣称的是：runbook 的每一步在 x86_64 上都以生产形态执行过并留下可复核的结果，且执行中暴露的断点全部已修。

## 独立审计（2026-09-05，5 名互不知情的子代理 + 逐条复核，服务器栈保持存活供只读复验）

按 spec 第 29 行「执行方自报不算通过」，在合并前对本文与 PR #4 做了一轮独立审计：仓库核实、服务器只读复验、runbook 漂移、对抗性反驳 G4、对抗性反驳 G1，每条问题另派一名复核员确认或推翻。44 名代理完成，11 名 runbook 漂移复核员因会话额度中断（其问题由执行方逐条自行判定，全部采纳）。结果：**确认 38 条**（阻断 1、major 14、minor 22、info 1），无一被复核推翻。处置如下。

**代码级真缺陷（已修，均有测试）**

| 审计结论 | 修复 |
|---|---|
| **阻断**：平台 bench 与租户 bench 共用一个 redis 队列库，RQ 队列名来自相同的 bench 路径，平台 worker 取走租户作业并以 `IncorrectSitePath` 失败（实测 13 条）；两站 scheduler 均 disabled；四个 bench 进程无探针 | 两个 bench 各用一个队列库（`/0`、`/1`）；`provision-*` 启用 scheduler，healthcheck 断言其启用；四个服务加存活探针，契约测试要求 13 个服务全部有探针 |
| **major（G4）**：Docker `internal` 只隔离 FORWARD，运行容器能连到网桥网关即宿主（sshd :22 握手成功） | `dsherp-admin agent-firewall` 打印按 agent 网桥限定的 INPUT 规则；runbook 第 8 步加规则；`infra/probe_agent_boundary.py` 用真实运行容器参数探测并断言宿主网关不可达 |
| **major（G4）**：SSO 的「机器认证」只看 `Authorization` 前缀，cookie 会话加假 token 即跳过 grant | 核对会话用户自己的 api_key/api_secret（常量时间比较）；`tests/integration/test_sso_machine_auth.py` 覆盖无头/假 token/裸前缀/错 secret/真密钥 |
| **major（G4）**：边缘 404 漏了 `/api/v2/method/` 与 `?cmd=`；来源白名单未配置时 fail-open；且只写 agent 网段会拒掉宿主 worker 自己经回环的 `finish_run` | 两份模板加 v2 路由与 `$arg_cmd` 屏蔽；`provision-tenant` 写入 agent + worker 两个网段，healthcheck 断言存在 |
| minor（G4）：两个 bench 共用 `backups` 卷；real_ip 信任网段与 Caddy 实际地址不符；Caddy 以 root 带全部能力运行 | 卷分开；edge 子网钉死为 real_ip 信任源；Caddy `cap_drop ALL` + `NET_BIND_SERVICE` |
| minor：`ReadWritePaths` 指向尚不存在的 `work/`，在真正生效的 systemd 上会让命名空间搭建失败 | `-` 前缀，缺失即忽略 |

**runbook 按原文不可执行（已重写）**：CLI 步骤依赖尚未创建的 venv 与 prod.env（顺序调整）；`nologin` 账号与 `sudo -iu` 互斥、以 dsherp 向 root 目录 clone 必失败（账号模型一节 + 第 1 步）；第 3–8 步未说明执行账号（`admin()`/`compose()` 函数统一以 dsherp 执行）；`DSHERP_AGENT_UID` 必须等于服务账号 uid（明写）；unit 渲染到 `/etc/systemd/system` 无 sudo（dsherp 渲染、root 安装）；uv 与 Python 发布包来源未写；`$DSHERP_PROJECT` 等从未 export（`set -a; . prod.env`）；`zstd` 主机上没有（gzip）；第 9 步的 pytest 写死 dev 栈（改为 `probe_agent_boundary.py`）；密钥落盘方式只是散文（写成可执行的 Python 管道）。

**证据的不实与过期陈述（已更正）**：「演练结束后已全部清除」写在清除发生之前（栈当时仍在跑）；runbook 第 201 行仍说未在 x86_64 跑过；`prod 固定 1000`；网络数 6→7、部署面文件 9→10、三个测试文件条数；「经 Caddy → 404」未说明屏蔽在 frontend nginx；未给出总时长。

**接受并如实记录的偏离**：G1 字面判据未满足（见「时长与判据字面」）；宿主 venv 为不含 SDK 的子集（#16，主机 glibc 所致）；Caddy 之后各跳为内部网络上的明文 HTTP（spec「全链路 TLS」的字面未达，TLS 终止在 Caddy，内部跳段在 internal 网络内）；租户 backend 因 `worker` 网络获得缺省路由；演练栈的 18443/18080 曾对公网开放（共用 ECS，未配安全组，演练后已拆）；心跳时间戳为站点本地时间（计划 2 的既有行为，记为 info）；从未在该主机上执行过一次真实运行（provider 凭据为合成值）。

**修复后的服务器复验**：在同一栈上把修复落地后逐项复验（frappe 镜像由 `f9bd051` 重建；worker 镜像保留 `dde55f0` 版本，容器内运行代码本次未改）：平台 bench 改用队列库 `/1`（在用 bench 的配置由 CLI 拒绝改写，演练里显式迁移）；`compose up -d` 后 **13/13 healthy**（四个 bench 进程探针生效；钉子网的 `edge` 网络需先显式重建，compose 对有容器挂着的网络重建会交互式询问）；`provision-platform`/`provision-tenant g1` 重跑报 `scheduler: enabled`、`site-config: changed:dsherp_agent_sources`；两站 `bench scheduler status` 均 enabled；租户 `frappe.enqueue('frappe.ping')` 在**租户**队列执行、3 分钟内 `IncorrectSitePath` 为 0；`dsherp_agent_sources = ['192.168.0.0/20','192.168.16.0/20']`，回环假凭据调用返回「Could not find run」而非来源拒绝；`agent-firewall` 两条 INPUT 规则加到 `br-db512087a978`；unit 重渲染（`-work`）重启后 active、心跳 13:30:15；`infra/probe_agent_boundary.py`（真实运行容器参数）`failures: {}`，其中 **`host_gateway_ssh: False`、`host_gateway_loopback_port: False`**、`no_new_privs: True`、能力集为空；边缘 `/api/method`、`/api/v2/method`、`?cmd=` 三种写法均 404；SSO 机器认证在生产 backend 用临时普通 System User 复验：`{"no_header": "refused", "bogus_token": "refused", "bare_prefix": "refused", "wrong_secret": "refused", "real_key": "allowed"}`（运行服务身份按设计豁免 grant，它无密码且站点已关密码登录）。复验后按收尾脚本拆除，含撤销防火墙规则。

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
| 干净 Linux x86_64 主机整体拉起 | runbook 在本机 Docker 与 x86_64 共用服务器各完整执行一次；G1 字面判据（干净主机、只给 tag/registry、≤60 分钟、全部探针）未满足，ACME 未验 | 需要一台满足前置（glibc ≥ 2.28、systemd ≥ 242、80/443 空闲、有公网域名）的专用主机，由审计方按 runbook 计时执行 |
| DS Membership 人工创建 | 沿用平台 Desk 手工 | 已裁决 #4 的短期密钥签发属计划 4，绑定链路不在本计划改 |
| 平台侧撤销推送端点 | 未做 | 计划 4 与凭证托管一起 |
| `infra/provision_*.py` 等 17 个 dev 脚本 | 保留 | 四站由它们建成、集成夹具依赖其产物；prod 路径已不经过它们。退役随 dev 收敛另立项 |
| syft/grype 实跑与 CI 接线 | 本机无工具 | 计划 5 |
| 登录页仍显示密码表单（dev 站） | 预期 | prod 站由 `disable_user_pass_login` 隐藏 |
| SBOM 与 CVE 结果入档 | 无 | 计划 5 首次 CI 运行后 |

## 提交

`7882fb0` 环境分层 → `cf939d9` 自建镜像 → `3f79ac0` 出口控制/非 root/契约 → `1e47cf3` 开站 CLI → `64ed22e` SSO/guest/CSP/渲染 → `5f8e71b` 升级回滚/patches/供应链 → `001c503` OAuth 链与 runbook → `e7a4ca9` 集成门修复 → `8857788` 证据 → 本地 G1 演练：`49c1cb2`、`34f0d39`、`71b66f0`、`b9a6032`、`946a0b4`、`7482304`、`6401f3c`（PR #3 `750f043` 合入）→ x86_64 演练（分支 `plan3/g1-x86`）：`4178ee2` 端口参数化、`dde55f0`、`1a3a35a` stdin、`3ca26ca` unit 环境 → 本文。

## 计划 3 收口（2026-09-06，分支 `plan3/closeout`）

依据：[项目状态审查](project-state-review-2026-09-05.md)（GPT，2026-09-05）经 Claude 逐条核验后成立的 A/B/C 三项，以及用户采纳的四项裁决（spec 已裁决 #8/#9/#10 与实施顺序表）。范围按裁决 #4 限定为 A/B/C、必要文档与对应验证；D（升级校验漏报与误报）留作计划 4 首片；新合成企业的用户闭环放进终验。复核计划 2 关闭项时发现的一处**计划 3 回归**（熔断探针地址）因属本计划引入，一并在此修复。所有代码改动先写失败测试再实现（非集成门从 370 增至 386 条）。

### 修复

| 项 | 改动 | 测试 |
|---|---|---|
| A 归档进容器临时层 | `retire_tenant` 先在容器里证明 `/home/frappe/frappe-bench/archived/sites` 可写，再 `bench backup --with-files`，再 `bench drop-site --archived-sites-path` 显式指到该目录，然后回读新增的归档目录（含 Frappe 重名时的数字后缀）与其中 `site_config.json`、`private/backups` 文件清单，最后才改租户清单与入口；归档缺失时报错且清单不动。`compose.prod.yml` 给 backend 挂 `tenant-archive` 卷；镜像 `install -d -o frappe -g frappe …/archived`——空命名卷在镜像里没有对应目录时会是 root 所有，drop-site 会在库已删掉之后才搬目录失败 | `test_admin_cli.py` 3 条（归档路径与备份清单、不可写时不动站点、重名后缀与归档丢失报错）、`test_deployment_contract.py` 1 条 |
| B 发布来源不绑定 | `release_images.source()`：要求构建上下文有 `.git`、`git rev-parse --show-toplevel` 等于该目录、`git status --porcelain` 只允许 `infra/releases/` 下的清单未跟踪、tag 必须指向 HEAD；`--git-commit` 只作交叉核对。`write_manifest` 核对两个镜像的 `org.opencontainers.image.revision/version` 标签。新增 `.dockerignore`（`.git`、`.runtime`、`.venv`、`infra/env`、`work`、`docs`、`tests` 等）。第一版曾接受 `git archive` 导出树（靠 `export-subst` 替换的 `infra/RELEASE_SOURCE`），审查者实际改了导出目录里的 `context_worker.py` 后来源检查仍放行——标记只记录导出时的来源，证明不了当前内容——已整个撤掉：没有 `.git` 的树一律拒绝，目标主机只装镜像 | `test_release_images.py` 5 条（含「无 `.git` 拒绝」）、`test_deployment_contract.py` 1 条
| C 宿主隔离不持久 | `infra/systemd/dsherp-agent-firewall.sh`（POSIX sh，`apply/check/remove`，规则带 `-m comment --comment dsherp-agent-firewall`，`apply` 先删旧标签规则再按当前网桥插入并把网桥名写到 `/run/dsherp-agent-firewall/<网络名>`）；`render_worker_units.py` 新增 `render_firewall_unit`（oneshot + RemainAfterExit，`Requires/After=docker.service`），worker unit 改为 `Requires=docker.service dsherp-agent-firewall.service`。worker 侧 `HostIsolation`（生产）：启动时记录文件缺失或网桥不符都拒绝启动（第一版缺失只记日志，审查指出后改为 fail-closed），并用发布镜像在 agent 网络里起探针容器连网关——`refused`/`connected` 都判失败，只有超时算隔离；运行中每 tick 重查记录与网桥，网络 id 变化即重新探针；失败期间不领取、`dsherp_host_isolation_ok=0`、告警 `host_isolation_failed`（critical）；`dsherp-admin agent-firewall` 打印的规则加同一标签并给出 unit/脚本路径 | `test_deployment_contract.py` 2 条、`test_context_worker.py` 4 条（缺记录/网桥过期拒绝、探针 refused/connected/无结论拒绝、网络变化重探针、隔离失败不领取）、`test_alerts.py` 1 条、`test_admin_cli.py` 断言扩展
| 计划 3 回归：熔断探针 | `3f79ac0` 把探针 settings 换成 `agent_settings()`，其 `DEEPSEEK_BASE_URL` 是容器用的 `http://agent-egress:8890`，宿主解析不了，`probe_models` 恒为 False，熔断打开后直到进程重启都不关闭。新增 `context_worker.host_probe()` 读 `.env` 直连地址；`main()` 改用它 | `test_context_worker.py` 1 条（锁定探针地址 ≠ 容器地址，且 `main()` 用 `host_probe`） |
| 本机验收凭据漂移 | 根因：`tests/integration/test_sso_machine_auth.py`（`f9bd051`）对共享 reader 调 `generate_keys`。测试改为自建/删除临时探针用户；`infra/run_validation_provision.py` 新增 `--reissue <actor>`，在 backend 容器内只对该演员 `generate_keys`、核对 api_key 未变后原地改写 `erp-users.json` 与 `erp-<actor>.json`（0600，原子替换） | `test_validation_provisioner.py` 2 条；真实执行后 reader `get_logged_user`/`read_schema(Customer)` 200、denied 403；改后的集成测试跑完 reader 仍 200、无残留探针用户 |

### 真实路径验证

**C：x86_64 共用服务器（`yibao-ecs`，CentOS 7、systemd 219、Docker 26），2026-09-06。** 用仓库脚本与渲染出的 unit 在一个临时 `--internal` 网络 `dsherp_agent` 上演练，全程只碰匹配我们网桥的 INPUT 规则，结束后全部清除（残留规则 0、残留 unit 0）：

| 步骤 | 实测 |
|---|---|
| 未装规则 | agent 网络内容器可连到网关 `192.168.112.1:22`（REACHABLE） |
| `systemctl start dsherp-agent-firewall` | `active`；`check` → `ok on br-ffb1d54825b3`；两条带注释规则在 INPUT 链首；状态文件为该网桥；容器再连网关 :22 → BLOCKED |
| `docker network rm/create` 重建网络（网桥换成 `br-6e15112f7d1c`） | `check` 退出 1，提示 stale；`systemctl restart` 后 `check` ok、状态文件更新、规则只剩新网桥的 2 条、网关 `192.168.128.1:22` BLOCKED |
| 模拟重启：手工删掉内核里的两条规则 | `check` 退出 1，网关 REACHABLE；`systemctl restart` 后 `check` ok，网关 BLOCKED |
| `systemctl stop` | 规则 0 条、状态文件删除，网关 REACHABLE |

补修 fail-closed 与探针后，在同一台服务器上用 `python:3.12-alpine` 容器跑 worker 的探针代码（`ISOLATION_PROBE` 原文），核对分类与内核行为一致（2026-09-06）：

| 状态 | 探针结论 | 耗时 |
|---|---|---|
| 未装规则 | `connected`（sshd :22 应答）→ 判失败 | 0.7s |
| 单元启动后 | `blocked`（两个端口各 3s 超时）→ 判隔离 | 6.5s |
| 网络重建、规则过期 | `connected` → 判失败 | 0.5s |
| `systemctl restart` 后 | `blocked` | 6.5s |

演练后全部清除（残留规则 0）。worker 的完整启动/领取门只在单元测试里用假 docker 验证（真实 worker 需要宿主 venv，该机 glibc 2.17 装不上运行时锁），端到端仍待合规主机。

第二轮复审又指出两处（2026-09-06）：同一网桥上规则被清掉不会被发现（网络 id 不变时只重读记录文件，探针只跑一次）；探针把 `ConnectionResetError` 等一切 `OSError` 都当成隔离成功。修法：健康时每 `ISOLATION_PROBE_INTERVAL`（30s）重探一次、失败期间每次检查都探（假时钟单元测试：规则清除后在一个间隔内被发现并停止领取，恢复后回到间隔节奏）；探针改为同时连 :22 与 :9，只有超时算 `blocked`，`connected/refused/reset` 判宿主可达，其它异常记 `error:<类名>` 判无法证明，worker 一律拒绝（在进程内执行探针脚本、注入各类异常的单元测试逐一锁定）。

**B：本机。** 工作树有未提交改动时 `release_images.py` 拒绝并列出脏文件，不调用 docker；提交 `1cd8927` 打本地 tag `v0.3.1-rc1` 后构建成功，manifest 记 `git_commit=1cd8927dac49…`，两个镜像标签 `version=v0.3.1-rc1 / revision=1cd8927dac49…`（arm64）；镜像内 `/home/frappe/frappe-bench/archived` 为 `frappe:frappe` 所有。当时还验证了 `git archive` 导出树的标记替换与校验，但审查者随后证明导出树改文件后仍放行，该路径已撤销（见修复表）。清单文件 `infra/releases/v0.3.1-rc1.json` 的来源提交是 `1cd8927`，文件本身在 `3beb5cf` 入库；tag 未推送，B/C 补修后应另打 `v0.3.1-rc2` 重建。

**A：本机 Docker Desktop 上以生产形态演练「下线 → 重建容器 → 从归档恢复」（2026-09-06，项目名 `dsherp`，镜像 `local/dsherp-frappe:v0.3.1-rc1`，独立 `.runtime/prod-local/`）。** 只起 db、两个 redis、platform-backend 与 backend；platform-backend 的探针在平台站开通前必然是 404，本演练脚本在这里多等了 5 分钟才继续（runbook 本来就不在此等待，非缺陷）。

| 步骤 | 实测 |
|---|---|
| `secrets init` → `doctor`（空 findings）→ `render-ingress` → `provision-platform` → `provision-tenant g1` | 10:44:04 开始，10:45:28 两站可用（healthcheck `g1.localhost`） |
| 在 g1 插入一条标记 ToDo | 1 行 |
| `retire-tenant g1` | `backup: created` → `site: dropped` → `archive: /home/frappe/frappe-bench/archived/sites/g1.localhost`，`backups` 列出 `20260906_104530-g1_localhost-{database.sql.gz, files.tar, private-files.tar, site_config_backup.json}`；租户清单变空、Caddyfile 重渲染 |
| `compose up -d --force-recreate backend`（容器 id `8edb6b80…` → `179c461c…`） | 新容器内归档目录仍在：4 个备份文件（库转储 884,595 字节）+ `site_config.json`，`ARCHIVE-PRESENT` |
| 恢复：`provision-tenant g1`（新建空站）→ `bench --site g1.localhost restore <归档>/private/backups/…database.sql.gz --with-public-files … --with-private-files … --force` | `Site g1.localhost has been restored with files`；标记 ToDo 回来了：1 行 |
| `provision-tenant g1` 幂等重跑 | `site: kept`、`runtime-identity: kept`、healthcheck 通过 |

演练后按 `teardown` 拆除：`compose down -v`、删除两个发布镜像、清理 `.runtime/prod-local/` 与 `infra/env/prod.env`；dev 栈全程未动。`infra/releases/v0.3.1-rc1.json` 作为本次构建的清单入库（提交 `1cd8927`）。

### 门禁（本轮代码提交 `1cd8927` 上）

| 门 | 结果 |
|---|---|
| 非集成 `pytest tests --ignore=tests/integration` | `389 passed`（收口前 370；新增 19 条全部先红后绿，含 B/C 补修的 6 条） |
| Node Runtime `node --test runtime/*.test.cjs` | `tests 10 / pass 10 / fail 0` |
| 前端 `npm test` | 22 个文件 `202 passed`；dist 无变化 |
| 集成（只跑本轮改动的一条）`tests/integration/test_sso_machine_auth.py` | `1 passed`（跑前先停 `scheduler` 让 `scheduler-worker` 排空 25 条 `run_scheduled_job`，跑后重启）；跑完 reader 仍 200，无残留探针用户 |
| DocType/Report/hooks | 无变更，不需要 migrate |

### 对既有陈述的更正

- 第 69 行「宿主熔断探针仍走 .env 里自己的地址」在 `3f79ac0` 之后曾不成立（见该行随附更正），本轮修复后重新成立。
- 第 110 行 `release` 的「逐字段比对」改为按 DocType 的行数与整表摘要，并说明子表/单值文档缺失与合法迁移误判；G2 未实现。
- runbook 第 8 节此前说「规则不随重启保留，按发行版的方式持久化」并把持久化留给操作者，现由 unit 负责且 worker fail-closed；第 1、2 节的「手工记提交号」改为「构建机必须是干净 git checkout，无 git 的主机只装镜像」；第 11 节补了归档位置与恢复路径。

### 仍未闭合

- G1 字面判据（干净专用主机、只给 tag/registry、≤60 分钟）与 ACME：仍待合规专用主机由审计方执行；按已裁决 #9 不再阻塞计划 4 开工。
- D（G2 升级校验）：计划 4 首片。
- 计划 2 放行前独立复核的 5 项 major 残余：见 [runtime-reliability-evidence.md](runtime-reliability-evidence.md) 末节，处置待用户裁决。
