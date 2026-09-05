# dsherp 部署 runbook（单 Linux 主机 + Compose + 自建镜像）

适用范围：一台干净的 **Linux x86_64** 主机，从仓库 tag、镜像仓库地址与密钥文件出发，拉起 platform 站 + 1 个租户站 + 宿主 worker。这是[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md) G1 的执行文档；每一步都对应仓库内一条可重复执行的命令，不需要人工 `docker exec`。

本文不覆盖数据治理与容灾（计划 4）、CI（计划 5）与 Agent 质量（计划 6）。真实租户接入需终验通过后另行授权。

## 0. 前置

| 项 | 值 | 备注 |
|---|---|---|
| 操作系统 | Linux x86_64 | 四个基础镜像 digest 均为多架构清单，含 `linux/amd64`（见[运行契约](runtime-baseline.md)） |
| Docker | Engine 25+ 与 compose 插件 | `docker compose version` 必须可用 |
| Python | 3.12（见仓库 `.python-version`） | 宿主 worker 用，不进容器 |
| Node | 见 `.nvmrc` | 仅构建前端产物时需要；发布镜像已内含 dist |
| 镜像来源 | 私有 registry，或在本机 `docker load` 导入的镜像 tar | `compose.prod.yml` 用 `pull_policy: if_not_present` |
| 账号 | 一个非 root 系统账号（本文用 `dsherp`），在 `docker` 组内 | worker 与容器都不以 root 运行 |

约定：下文所有命令在仓库根执行，`$TAG` 为发布 tag，`$SLUG` 为租户短名（小写字母开头，`[a-z0-9-]`）。

## 1. 取代码与建立账号

```sh
sudo useradd --system --create-home --shell /usr/sbin/nologin dsherp
sudo usermod -aG docker dsherp
sudo -u dsherp git clone --branch "$TAG" <仓库地址> /opt/dsherp
cd /opt/dsherp
```

## 2. 构建并推送镜像（构建机上执行一次）

发布镜像必须指定架构；`release_images.py` 会把 tag、提交、基底 digest 与架构写进 `infra/releases/$TAG.json`。

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.release_images --platform linux/amd64
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-frappe:$TAG"
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-worker:$TAG"
```

没有 registry 时改为把两个镜像导出后在目标主机导入：

```sh
docker save "$DSHERP_IMAGE_REGISTRY/dsherp-frappe:$TAG" "$DSHERP_IMAGE_REGISTRY/dsherp-worker:$TAG" | zstd -o dsherp-$TAG.tar.zst
# 目标主机：
zstd -dc dsherp-$TAG.tar.zst | docker load
```

## 3. 环境文件与控制面密钥

```sh
cp infra/env/prod.env.example infra/env/prod.env
$EDITOR infra/env/prod.env        # 域名、platform slug、registry、tag、ACME 邮箱、Agent uid/gid
chmod 600 infra/env/prod.env
DSHERP_ENV=prod ./bin/dsherp-admin secrets init
DSHERP_ENV=prod ./bin/dsherp-admin doctor      # 必须输出空 findings 才继续
```

`secrets init` 只生成缺失的密钥，永不覆盖已有文件；文件权限必须是 0600，否则命令直接失败。

## 4. 起数据面

compose 在解析时就要求每个 `configs:`/`secrets:` 文件存在，所以入口配置要先渲染一次（此时只有 platform 一个站块）：

```sh
export DSHERP_ENV=prod
./bin/dsherp-admin render-ingress
compose() { docker compose --env-file infra/env/prod.env -f infra/compose.prod.yml "$@"; }
compose up -d db redis-cache redis-queue
compose ps    # 三个服务 healthy 后再继续
```

用函数而不是 `$COMPOSE` 变量：zsh 默认不对变量做分词，同一行在 bash 与 zsh 下行为不同。

`DSHERP_RUNTIME_DIR` 与 `DSHERP_SECRETS_DIR`（租户清单、Caddyfile、密钥所在目录）**只写在 `infra/env/prod.env` 里，不要 export**：compose 的 `${…}` 插值与 CLI 都读这份文件，这是两者读到同一目录的唯一保证。本地演练时曾因只在 shell 里 export 而漏掉一次，compose 回落到 `../.runtime/control` 用开发密钥初始化了新库，CLI 随即以生产密钥被拒——`doctor` 现在对此报错。

## 5. 开通平台站与第一个租户站

```sh
compose up -d platform-backend backend
DSHERP_ENV=prod ./bin/dsherp-admin provision-platform
DSHERP_ENV=prod ./bin/dsherp-admin provision-tenant "$SLUG"
```

两条命令都是幂等步骤链：每一步先查现状，中断后重跑不会重复建站或重复装 App。`provision-tenant` 会同时关闭该站的密码登录（Frappe 原生 `disable_user_pass_login`），此后进入业务站只能经平台 SSO。

命令输出里的 `runtime_identity` 是该站运行服务身份的 api_key/api_secret，**只出现这一次**，写入下一步的 worker profile 后即从终端历史中清除。

## 6. 起入口与出口

```sh
./bin/dsherp-admin render-ingress          # 现在含租户站块
compose up -d scheduler queue platform-scheduler platform-queue   # 定时任务与后台队列
compose up -d frontend platform-frontend agent-egress caddy
compose ps    # 13 个服务全部 Up，9 个带探针的全部 healthy
```

本地演练时曾漏起四个 scheduler/queue 服务而 `ps` 看起来"全绿"——`compose ps` 只列出已创建的服务，核对时要数服务数，不只看颜色。

Caddy 按当前租户清单逐站签发 HTTP-01 证书，因此 `$SLUG.$DSHERP_BASE_DOMAIN` 与 platform 域名必须已解析到本机 80/443。每次增删租户后重跑 `render-ingress` 并 `compose up -d caddy`。

## 7. 装宿主 worker

worker 不进容器：它需要 docker 才能拉起一次性 Runtime 容器。

```sh
sudo -u dsherp python3.12 -m venv /opt/dsherp/.venv
sudo -u dsherp /opt/dsherp/.venv/bin/pip install --require-hashes -r requirements.lock
sudo -u dsherp install -m 600 /dev/null /opt/dsherp/.runtime/context-worker-sites.json
sudo -u dsherp $EDITOR /opt/dsherp/.runtime/context-worker-sites.json
```

profile 形如：`base_url` 是宿主 worker 自己领取运行、发心跳用的地址——它在宿主上，而 compose 的网络全是 internal，所以 backend 只在 `127.0.0.1:8000` 发布一个回环端口给它（`DSHERP_BACKEND_LOOPBACK_PORT` 可改；为此 backend 额外接了一个非 internal 的 `worker` 网络——Docker 不会为只在 internal 网络上的容器发布端口，本地演练时正是在这里断过）；`business_url` 是运行容器在 agent 网络内访问业务站的服务名：

```json
{"slots": 3, "metrics_port": 9109,
 "sites": [{"site": "<slug>.<base domain>", "base_url": "http://127.0.0.1:8000",
            "business_url": "http://backend:8000",
            "api_key": "<第 5 步输出>", "api_secret": "<第 5 步输出>"}]}
```

provider 凭据写入 `/opt/dsherp/.env`（`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`，0600）。容器拿到的 base URL 不是这一个：它由 `DSHERP_AGENT_PROVIDER_BASE_URL` 指向 agent 网络内的出口代理，宿主只用自己的地址做熔断探针。

渲染并启用 systemd unit：

```sh
DSHERP_ENV=prod .venv/bin/python infra/render_worker_units.py --user dsherp --group dsherp \
  --target /etc/systemd/system/dsherp-agent-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now dsherp-agent-worker
systemctl status dsherp-agent-worker      # active (running)，Watchdog 已生效
```

unit 为 `Type=notify` + `WatchdogSec=60s`：worker 每轮 tick 回喂看门狗，卡死会被重启；`ProtectSystem=strict` 下它只能写 `.runtime` 与 `work` 两个目录。

## 8. 收口安全边界

租户站写入 agent 网络的来源网段后，运行凭据端点只接受来自该网段的调用：

```sh
AGENT_CIDR=$(docker network inspect "${DSHERP_PROJECT}_agent" --format '{{(index .IPAM.Config 0).Subnet}}')
compose exec -T backend /home/frappe/frappe-bench/env/bin/python - <<PY
import os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='$SLUG.$DSHERP_BASE_DOMAIN'); frappe.connect()
from frappe.installer import update_site_config
update_site_config('dsherp_agent_sources', ['$AGENT_CIDR'])
frappe.db.commit(); frappe.destroy()
PY
```

## 9. 验收（G1 与 G4）

| 检查 | 命令 | 期望 |
|---|---|---|
| 全部 healthcheck 绿 | `compose ps` | 每个长驻服务 `healthy` |
| 站点可达且走 TLS | `curl -sI https://$SLUG.$DSHERP_BASE_DOMAIN/login` | 200，含 `Content-Security-Policy`，`connect-src 'self'` |
| 密码登录已关 | `curl -s -X POST https://$SLUG.$DSHERP_BASE_DOMAIN/api/method/login -d 'usr=x&pwd=y'` | 拒绝，登录页不显示密码表单 |
| 容器不出网、非 root | `.venv/bin/python -m pytest tests/integration/test_agent_boundary.py -q` | 全绿 |
| worker 存活 | `systemctl is-active dsherp-agent-worker` | `active` |
| 发布可核验 | `cat infra/releases/$TAG.json` | tag、提交、基底 digest、架构齐全 |

## 10. 升级与回滚

```sh
# 升级：先备份，逐站 migrate，再逐字段比对；有差异退出码为 1
$EDITOR infra/env/prod.env          # DSHERP_IMAGE_TAG 改为新 tag
compose pull && compose up -d
DSHERP_ENV=prod ./bin/dsherp-admin release "$NEW_TAG"

# 回滚：改回旧 tag，起旧镜像，从升级前备份恢复（备份文件必须显式指明）
$EDITOR infra/env/prod.env
compose up -d
DSHERP_ENV=prod ./bin/dsherp-admin rollback "$OLD_TAG" \
  --backup "$SLUG.$DSHERP_BASE_DOMAIN=/home/frappe/frappe-bench/sites/$SLUG.$DSHERP_BASE_DOMAIN/private/backups/<升级前备份>.sql.gz"
```

升级前备份是硬前置：`release` 在 migrate 之前对每个站执行 `bench backup --with-files`，比对报告落在 `.runtime/releases/release-<tag>.json`。

## 11. 下线租户

```sh
DSHERP_ENV=prod ./bin/dsherp-admin retire-tenant "$SLUG"     # 先整站归档再删站
DSHERP_ENV=prod ./bin/dsherp-admin render-ingress && compose up -d caddy
```

## 本地 Docker 上的 G1 演练（非 Linux 主机时）

同一份 runbook 可以在一台已装 Docker Desktop 的开发机上以生产形态跑通，作为拿不到干净 Linux 主机时的替代演练。差别只有四点，全部由 `infra/env/prod.env` 表达：`DSHERP_BASE_DOMAIN=localhost`（`*.localhost` 解析到回环，Caddy 对这类主机名自动用内置 CA 签发，用 `curl -k` 或导入其根证书验证 TLS）；`DSHERP_IMAGE_REGISTRY=local` 且镜像用 `--platform linux/arm64` 在本机构建（不是 x86_64）；数据面用 `DSHERP_DB_BUFFER_POOL=256M`、两个 redis `64mb`、gunicorn 1×2 的笔记本规格；worker 没有 systemd，改为前台启动一次核对 `prepare_host`、心跳与 `/metrics`。dev 栈与它并存：项目名 `dsherp` 对 `dsherp-validation`，网络、卷、容器名全部不同，端口只共用宿主 80/443（dev 不占）。

## 已知边界

- **通配证书**：目标拓扑写的是 `*.base_domain` 通配证书；通配必须走 DNS-01，需要带 DNS 提供商插件的 Caddy 构建与 API 凭证。按已裁决 #2 的 ≤3 租户试点规模，本文改为逐站 HTTP-01：不需要插件、不需要 DNS 凭证，代价是每次增删租户要重跑 `render-ingress`。
- **G1 的执行环境**：本文已在本机 Docker Desktop 上以**生产形态**整体执行过一次（见"本地 Docker 上的 G1 演练"与[证据](deployment-security-evidence.md)），途中修掉 15 个断点；尚未在真正的 Linux x86_64 主机上跑过，架构、ACME、systemd 三项仍待真机核验。
- **成员绑定**：`provision-tenant` 已覆盖建站、装 App、运行服务身份、站点配置、DS Enterprise、OAuth Client、Social Login Key、平台端点表、入口渲染与 healthcheck。**把某个平台用户加入某个企业（DS Membership）仍是人工步骤**：按已裁决 #4，成员的业务站短期密钥由 SSO 回调签发属于计划 4，本计划不改这条链路，因此成员绑定沿用平台站 Desk 上的手工创建。
