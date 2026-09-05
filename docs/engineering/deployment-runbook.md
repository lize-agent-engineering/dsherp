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

```sh
export COMPOSE="docker compose --env-file infra/env/prod.env -f infra/compose.prod.yml"
$COMPOSE up -d db redis-cache redis-queue
$COMPOSE ps    # 三个服务 healthy 后再继续
```

## 5. 开通平台站与第一个租户站

```sh
$COMPOSE up -d platform-backend backend
DSHERP_ENV=prod ./bin/dsherp-admin provision-platform
DSHERP_ENV=prod ./bin/dsherp-admin provision-tenant "$SLUG"
```

两条命令都是幂等步骤链：每一步先查现状，中断后重跑不会重复建站或重复装 App。`provision-tenant` 会同时关闭该站的密码登录（Frappe 原生 `disable_user_pass_login`），此后进入业务站只能经平台 SSO。

命令输出里的 `runtime_identity` 是该站运行服务身份的 api_key/api_secret，**只出现这一次**，写入下一步的 worker profile 后即从终端历史中清除。

## 6. 起入口与出口

```sh
DSHERP_ENV=prod ./bin/dsherp-admin render-ingress
$COMPOSE up -d frontend platform-frontend agent-egress caddy
$COMPOSE ps    # 全部 healthy
```

Caddy 按当前租户清单逐站签发 HTTP-01 证书，因此 `$SLUG.$DSHERP_BASE_DOMAIN` 与 platform 域名必须已解析到本机 80/443。每次增删租户后重跑 `render-ingress` 并 `$COMPOSE up -d caddy`。

## 7. 装宿主 worker

worker 不进容器：它需要 docker 才能拉起一次性 Runtime 容器。

```sh
sudo -u dsherp python3.12 -m venv /opt/dsherp/.venv
sudo -u dsherp /opt/dsherp/.venv/bin/pip install --require-hashes -r requirements.lock
sudo -u dsherp install -m 600 /dev/null /opt/dsherp/.runtime/context-worker-sites.json
sudo -u dsherp $EDITOR /opt/dsherp/.runtime/context-worker-sites.json
```

profile 形如（`business_url` 走 agent 网络内的服务名，`base_url` 走内部入口）：

```json
{"slots": 3, "metrics_port": 9109,
 "sites": [{"site": "<slug>.<base domain>", "base_url": "http://backend:8000",
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
$COMPOSE exec -T backend /home/frappe/frappe-bench/env/bin/python - <<PY
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
| 全部 healthcheck 绿 | `$COMPOSE ps` | 每个长驻服务 `healthy` |
| 站点可达且走 TLS | `curl -sI https://$SLUG.$DSHERP_BASE_DOMAIN/login` | 200，含 `Content-Security-Policy`，`connect-src 'self'` |
| 密码登录已关 | `curl -s -X POST https://$SLUG.$DSHERP_BASE_DOMAIN/api/method/login -d 'usr=x&pwd=y'` | 拒绝，登录页不显示密码表单 |
| 容器不出网、非 root | `.venv/bin/python -m pytest tests/integration/test_agent_boundary.py -q` | 全绿 |
| worker 存活 | `systemctl is-active dsherp-agent-worker` | `active` |
| 发布可核验 | `cat infra/releases/$TAG.json` | tag、提交、基底 digest、架构齐全 |

## 10. 升级与回滚

```sh
# 升级：先备份，逐站 migrate，再逐字段比对；有差异退出码为 1
$EDITOR infra/env/prod.env          # DSHERP_IMAGE_TAG 改为新 tag
$COMPOSE pull && $COMPOSE up -d
DSHERP_ENV=prod ./bin/dsherp-admin release "$NEW_TAG"

# 回滚：改回旧 tag，起旧镜像，从升级前备份恢复（备份文件必须显式指明）
$EDITOR infra/env/prod.env
$COMPOSE up -d
DSHERP_ENV=prod ./bin/dsherp-admin rollback "$OLD_TAG" \
  --backup "$SLUG.$DSHERP_BASE_DOMAIN=/home/frappe/frappe-bench/sites/$SLUG.$DSHERP_BASE_DOMAIN/private/backups/<升级前备份>.sql.gz"
```

升级前备份是硬前置：`release` 在 migrate 之前对每个站执行 `bench backup --with-files`，比对报告落在 `.runtime/releases/release-<tag>.json`。

## 11. 下线租户

```sh
DSHERP_ENV=prod ./bin/dsherp-admin retire-tenant "$SLUG"     # 先整站归档再删站
DSHERP_ENV=prod ./bin/dsherp-admin render-ingress && $COMPOSE up -d caddy
```

## 已知边界

- **通配证书**：目标拓扑写的是 `*.base_domain` 通配证书；通配必须走 DNS-01，需要带 DNS 提供商插件的 Caddy 构建与 API 凭证。按已裁决 #2 的 ≤3 租户试点规模，本文改为逐站 HTTP-01：不需要插件、不需要 DNS 凭证，代价是每次增删租户要重跑 `render-ingress`。
- **G1 的执行环境**：本文的命令在本机 macOS arm64 上按 dev 形态逐条验证过（镜像构建、compose 渲染、CLI 幂等、容器边界、systemd unit 渲染），但**尚未在干净的 Linux x86_64 主机上整体执行过**。G1 需由审计方在新 VM 上按本文执行一次才算通过。
- **成员绑定**：`provision-tenant` 已覆盖建站、装 App、运行服务身份、站点配置、DS Enterprise、OAuth Client、Social Login Key、平台端点表、入口渲染与 healthcheck。**把某个平台用户加入某个企业（DS Membership）仍是人工步骤**：按已裁决 #4，成员的业务站短期密钥由 SSO 回调签发属于计划 4，本计划不改这条链路，因此成员绑定沿用平台站 Desk 上的手工创建。
