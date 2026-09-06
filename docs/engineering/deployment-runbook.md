# dsherp 部署 runbook（单 Linux 主机 + Compose + 自建镜像）

适用范围：一台干净的 **Linux x86_64** 主机，从仓库 tag、镜像仓库地址与密钥文件出发，拉起 platform 站 + 1 个租户站 + 宿主 worker。这是[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md) G1 的执行文档；每一步都对应仓库内一条可重复执行的命令，不需要人工 `docker exec`。

本文不覆盖数据治理与容灾（计划 4）、CI（计划 5）与 Agent 质量（计划 6）。真实租户接入需终验通过后另行授权。

## 0. 前置

| 项 | 值 | 备注 |
|---|---|---|
| 操作系统 | Linux x86_64 | 四个基础镜像 digest 均为多架构清单，含 `linux/amd64`（见[运行契约](runtime-baseline.md)） |
| Docker | Engine 25+ 与 compose 插件 | `docker compose version` 必须可用 |
| Python | 3.12（见仓库 `.python-version`） | 宿主 worker 用，不进容器 |
| 宿主 glibc | **≥ 2.28**（RHEL/Rocky 8、Debian 10、Ubuntu 18.10 及以上） | `deepseek-harness-runtime-bin` 只发布 `manylinux_2_28` wheel，`uv pip sync requirements.lock` 在 glibc 2.17（CentOS 7）上无解。宿主 worker 本身不引入 SDK，但同一份锁装不上就起不了 CLI；容器内是 Debian bookworm，不受影响 |
| Node | 见 `.nvmrc` | 仅构建前端产物时需要；发布镜像已内含 dist |
| git | **构建机**必须是干净的 git checkout，且 `$TAG` 指向 HEAD——没有 `.git` 的源码树一律拒绝构建（导出树无法证明导出后没被改过）；**目标主机**可以没有 git：它只 `docker load` 镜像，源码树只用来跑 CLI | 脏工作树、tag 不在 HEAD、无 `.git`，构建都会被拒绝；`--git-commit` 只是交叉核对 |
| 镜像来源 | 私有 registry，或在本机 `docker load` 导入的镜像 tar | `compose.prod.yml` 用 `pull_policy: if_not_present` |
| 账号 | 一个非 root 系统账号（本文用 `dsherp`），在 `docker` 组内 | worker 与容器都不以 root 运行 |
| systemd | ≥ 242 才能启用 unit 里的全部沙箱指令（`ProtectSystem=strict` 232+、`ReadWritePaths` 232+、`RestrictSUIDSGID` 242+）；更老的版本会忽略这些行并在 journal 告警，进程照常受 `Restart`/`WatchdogSec` 管，但沙箱**静默退化**——219 上 `ProtectSystem=strict` 被解析成 `no` | CentOS 7 的 systemd 219 实测：保留 notify/看门狗/Restart/PrivateTmp/NoNewPrivileges/ProtectHome，丢掉其余五条 |
| 安装根 | 本文用 `/opt/dsherp`，但只是参数：`render_worker_units.py --root` 与 `bin/dsherp-admin` 都跟随实际目录。**不要放在 `/home` 下**：unit 的 `ProtectHome=read-only` 会把它锁成只读，能解锁 `.runtime`/`work` 的 `ReadWritePaths` 要 systemd ≥ 232 | 某些主机的 `/opt` 带 immutable 属性，root 也写不进，这时用 `/srv/dsherp` |

约定：下文所有命令在仓库根执行，`$TAG` 为发布 tag，`$SLUG` 为租户短名（小写字母开头，`[a-z0-9-]`）。

## 账号模型

本文的命令由**一个有 sudo 的运维账号**执行；`dsherp` 是服务账号，只在明确写了 `sudo -u dsherp` 的地方以它的身份运行，凡是它要读写的文件（安装根、`.runtime/`、`infra/env/prod.env`、密钥目录）都归它所有。分不清账号就会得到「worker 读不到 prod.env」「compose 读不到密钥」这类静默错误。

## 1. 取代码与建立账号

```sh
sudo useradd --system --create-home --shell /bin/bash dsherp   # 要能 sudo -iu 进去，不能是 nologin
sudo usermod -aG docker dsherp
sudo mkdir -p /opt/dsherp && sudo chown dsherp:dsherp /opt/dsherp   # 安装根由 root 建、交给 dsherp
sudo -u dsherp git clone --branch "$TAG" <仓库地址> /opt/dsherp
cd /opt/dsherp
```

主机没有 git 时（真机演练就是这样）：在有 git 的机器上 `git archive --format=tar.gz -o dsherp-src.tgz "$TAG"`，传到主机后 `sudo -u dsherp tar -xzf dsherp-src.tgz -C /opt/dsherp`。这样的主机**不能当构建机**——`release_images.py` 拒绝没有 `.git` 的源码树，因为导出树无法证明导出之后没有被改动（审查时实际改了导出目录里的一个文件，早先按导出标记核实来源的做法仍然放行，已撤掉）；镜像在有 git 的构建机上构建，按第 2 步 `docker save/load` 传过来。

## 2. 构建并推送镜像（构建机上执行一次）

发布镜像必须指定架构；`release_images.py` 会把 tag、提交、基底 digest 与架构写进 `infra/releases/$TAG.json`。它先核实构建上下文就是声称的来源：必须是 git checkout，工作树干净（`infra/releases/` 下上一次写出的清单除外）且 `$TAG` 指向 HEAD；没有 `.git` 的树直接拒绝。构建后再核对两个镜像的 `org.opencontainers.image.version/revision` 标签与 tag、提交一致，不一致不写清单。`.dockerignore` 把 `.runtime/`、`infra/env/`、`.venv/` 等宿主状态挡在构建上下文之外。构建机上先完成第 3 步的 `prod.env` 与第 7 步的 `.venv`（脚本要从 `prod.env` 读 tag/registry，并需要 venv 里的依赖），再：

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.release_images --platform linux/amd64
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-frappe:$TAG"
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-worker:$TAG"
```

没有 registry 时改为把两个镜像导出后在目标主机导入（gzip 到处都有，zstd 不一定）：

```sh
docker save "$DSHERP_IMAGE_REGISTRY/dsherp-frappe:$TAG" "$DSHERP_IMAGE_REGISTRY/dsherp-worker:$TAG" | gzip > dsherp-$TAG.tar.gz
# 目标主机：
gunzip -c dsherp-$TAG.tar.gz | docker load
```

目标主机自己当构建机也可以（早先的真机演练就是），但那不是 G1 的形态，而且它必须有 git 与干净 checkout：要求主机有 buildx、能访问 PyPI，并把构建时间算进拉起时间。

## 3. 环境文件、宿主 venv 与控制面密钥

`bin/dsherp-admin` 与 `infra/release_images.py` 都跑在 `.venv` 里，所以 venv 是所有 CLI 步骤的前提（第 7 步的 worker 复用同一个）：

```sh
sudo -iu dsherp bash -c 'cd /opt/dsherp && uv venv --python 3.12.11 .venv && uv pip sync --python .venv/bin/python --require-hashes requirements.lock'
```

uv 不在主机上时：从 https://github.com/astral-sh/uv/releases 取 `uv-x86_64-unknown-linux-gnu.tar.gz` 与它的 `.sha256`，校验后把 `uv` 放到 `/home/dsherp/.local/bin/`（`sudo -iu` 的登录 shell 会把它加进 PATH）。主机下载慢时在别的机器下载后 scp 过去；CPython 发布包同理，可用 `UV_PYTHON_INSTALL_MIRROR=file:///path` 让 uv 从本地目录安装。

```sh
sudo -u dsherp cp infra/env/prod.env.example infra/env/prod.env
sudo -u dsherp $EDITOR infra/env/prod.env
sudo -u dsherp chmod 600 infra/env/prod.env
sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod ./bin/dsherp-admin secrets init && DSHERP_ENV=prod ./bin/dsherp-admin doctor'   # doctor 必须输出空 findings
```

`prod.env` 必须改的项：`DSHERP_BASE_DOMAIN`、`DSHERP_PLATFORM_SLUG`、`DSHERP_IMAGE_REGISTRY`、`DSHERP_IMAGE_TAG`、`DSHERP_ACME_EMAIL`；**`DSHERP_AGENT_UID`/`GID` 必须等于 `id -u dsherp` / `id -g dsherp`**（会话目录由 worker 以 dsherp 创建、由容器以这对 uid/gid 写入，代码不做 chown；`useradd --system` 给的 uid 通常小于 1000，示例里的 1000 只是占位）；安装根不是 `/opt/dsherp` 时改 `DSHERP_RUNTIME_DIR`/`DSHERP_SECRETS_DIR`；共用主机才改 `DSHERP_HTTP_PORT`/`HTTPS_PORT` 与资源项。

`secrets init` 只生成缺失的密钥，永不覆盖已有文件；文件权限必须是 0600，否则命令直接失败。

后文 shell 步骤里用到的 `$DSHERP_PROJECT`、`$DSHERP_BASE_DOMAIN` 等变量来自同一份文件：`set -a; . infra/env/prod.env; set +a`。这与「目录变量不要单独 export」不冲突——source 整个文件得到的值和 compose 读到的完全一致，单独 export 一个不同的值才是问题。

## 4. 起数据面

compose 在解析时就要求每个 `configs:`/`secrets:` 文件存在，所以入口配置要先渲染一次（此时只有 platform 一个站块）：

```sh
set -a; . infra/env/prod.env; set +a
admin() { sudo -iu dsherp bash -c "cd /opt/dsherp && DSHERP_ENV=prod ./bin/dsherp-admin $*"; }
compose() { sudo -iu dsherp bash -c "cd /opt/dsherp && docker compose --env-file infra/env/prod.env -f infra/compose.prod.yml $*"; }
admin render-ingress
compose up -d db redis-cache redis-queue
compose ps    # 三个服务 healthy 后再继续
```

两个函数把每条命令都以 `dsherp` 身份、在安装根下执行——运维账号直接跑会让 `.runtime/`、密钥目录归错人。

用函数而不是 `$COMPOSE` 变量：zsh 默认不对变量做分词，同一行在 bash 与 zsh 下行为不同。

`DSHERP_RUNTIME_DIR` 与 `DSHERP_SECRETS_DIR`（租户清单、Caddyfile、密钥所在目录）**只写在 `infra/env/prod.env` 里，不要 export**：compose 的 `${…}` 插值与 CLI 都读这份文件，这是两者读到同一目录的唯一保证。本地演练时曾因只在 shell 里 export 而漏掉一次，compose 回落到 `../.runtime/control` 用开发密钥初始化了新库，CLI 随即以生产密钥被拒——`doctor` 现在对此报错。

## 5. 开通平台站与第一个租户站

```sh
compose up -d platform-backend backend
admin provision-platform
admin provision-tenant "$SLUG"
```

两条命令都是幂等步骤链：每一步先查现状，中断后重跑不会重复建站或重复装 App。`provision-tenant` 会同时关闭该站的密码登录（Frappe 原生 `disable_user_pass_login`）、启用 scheduler，并把运行凭据端点的来源白名单 `dsherp_agent_sources` 写成 agent 网络与 worker 网络两个网段（后者是宿主 worker 经回环进来时的对端地址；只写 agent 网段会让每个真实运行在 `finish_run` 上被拒）。平台 bench 与租户 bench 各用一个 redis 队列库（`/1` 与 `/0`）：队列名来自 bench 路径，两个 bench 在同一个库里会互相取走对方的作业。

命令输出里的 `runtime_identity` 是该站运行服务身份的 api_key/api_secret，**只在签发那一次出现**：重跑 `provision-tenant` 不会再签发也不会再显示（步骤报 `kept`）；丢了或要换就 `provision-tenant <slug> --rotate-runtime-key`，旧密钥随即作废。写入下一步的 worker profile 后即从终端历史中清除——更稳妥的做法是像演练脚本那样，用一段 Python 把 JSON 输出直接落成 0600 文件，密钥从不经过终端。

## 6. 起入口与出口

```sh
admin render-ingress          # 现在含租户站块
compose up -d scheduler queue platform-scheduler platform-queue   # 定时任务与后台队列
compose up -d frontend platform-frontend agent-egress caddy
compose ps    # 13 个服务全部 Up 且全部 healthy（四个 bench 进程是存活探针）
```

本地演练时曾漏起四个 scheduler/queue 服务而 `ps` 看起来"全绿"——`compose ps` 只列出已创建的服务，核对时要数服务数，不只看颜色。

Caddy 按当前租户清单逐站签发 HTTP-01 证书，因此 `$SLUG.$DSHERP_BASE_DOMAIN` 与 platform 域名必须已解析到本机 80/443。每次增删租户后重跑 `render-ingress` 并 `compose up -d caddy`。

## 7. 装宿主 worker

worker 不进容器：它需要 docker 才能拉起一次性 Runtime 容器。

venv 已在第 3 步建好。worker profile 直接由 CLI 输出落盘，密钥不经过终端也不经过编辑器：

```sh
sudo -iu dsherp bash -c 'cd /opt/dsherp && umask 077 && DSHERP_ENV=prod ./bin/dsherp-admin provision-tenant '"$SLUG"' --rotate-runtime-key | .venv/bin/python - <<"PY"
import json, os, sys
d = json.load(sys.stdin); ident = d["runtime_identity"]
profile = {"slots": 3, "metrics_port": 9109, "sites": [{"site": ident["user"].split("@", 1)[1],
           "base_url": "http://127.0.0.1:8000", "business_url": "http://backend:8000",
           "api_key": ident["api_key"], "api_secret": ident["api_secret"]}]}
fd = os.open(".runtime/context-worker-sites.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps(profile).encode()); os.close(fd); print("profile written")
PY'
```

`--rotate-runtime-key` 作废该站的旧运行密钥并签发新的；第一次开站后 profile 也可以这样生成（当时签发的密钥只在那次输出里）。

profile 形如：`base_url` 是宿主 worker 自己领取运行、发心跳用的地址——它在宿主上，而 compose 的网络全是 internal，所以 backend 只在 `127.0.0.1:8000` 发布一个回环端口给它（`DSHERP_BACKEND_LOOPBACK_PORT` 可改；为此 backend 额外接了一个非 internal 的 `worker` 网络——Docker 不会为只在 internal 网络上的容器发布端口，本地演练时正是在这里断过）；`business_url` 是运行容器在 agent 网络内访问业务站的服务名：

```json
{"slots": 3, "metrics_port": 9109,
 "sites": [{"site": "<slug>.<base domain>", "base_url": "http://127.0.0.1:8000",
            "business_url": "http://backend:8000",
            "api_key": "<第 5 步输出>", "api_secret": "<第 5 步输出>"}]}
```

provider 凭据写入 `/opt/dsherp/.env`（`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`，0600）。容器拿到的 base URL 不是这一个：它由 `DSHERP_AGENT_PROVIDER_BASE_URL` 指向 agent 网络内的出口代理，宿主只用自己的地址做熔断探针。

渲染并启用两个 systemd unit——先是宿主防火墙（第 8 节的安全边界，worker 的 unit `Requires` 它），再是 worker：

```sh
sudo -u dsherp mkdir -p /opt/dsherp/.runtime /opt/dsherp/work
sudo -iu dsherp bash -c 'cd /opt/dsherp && .venv/bin/python infra/render_worker_units.py --root /opt/dsherp --user dsherp --group dsherp --target /opt/dsherp/.runtime/dsherp-agent-worker.service'   # 打印 worker 与 firewall 两个 unit 的路径
sudo install -m 755 /opt/dsherp/infra/systemd/dsherp-agent-firewall.sh /usr/local/sbin/dsherp-agent-firewall
sudo install -m 644 /opt/dsherp/.runtime/dsherp-agent-firewall.service /etc/systemd/system/dsherp-agent-firewall.service
sudo install -m 644 /opt/dsherp/.runtime/dsherp-agent-worker.service /etc/systemd/system/dsherp-agent-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now dsherp-agent-firewall
sudo /usr/local/sbin/dsherp-agent-firewall check "${DSHERP_PROJECT}_agent"   # ok on br-…
sudo systemctl enable --now dsherp-agent-worker
systemctl status dsherp-agent-worker      # active (running)，Watchdog 已生效
sudo journalctl -u dsherp-agent-worker -n 20   # 不应有 "Unknown lvalue"/"Failed to parse"：有就是 systemd 太老，沙箱没生效
```

unit 由 dsherp 渲染到自己的目录，再由 root 安装——渲染器写不了 `/etc/systemd/system`。防火墙脚本同样由 root 从仓库复制到 `/usr/local/sbin`：它以 root 运行，不能直接执行服务账号可写的文件。

worker 在生产启动时**先核验宿主隔离再服务**：`/run/dsherp-agent-firewall/<agent 网络名>` 必须存在且记录的网桥等于当前网络的网桥，然后用发布镜像在 agent 网络里起一个探针容器去连网关——被拒绝（RST）或连通都说明宿主没挡，只有超时才算隔离；三者任一不成立 worker 直接退出并把原因写进 journal（`Restart=always` 会每 10 秒重试，直到防火墙单元就位）。运行中每个 tick 重查记录与网桥，网络 id 变了就重新探针；隔离失效期间心跳照发（用户看到的是排队而不是 503）但**不领取新运行**，`/metrics` 的 `dsherp_host_isolation_ok` 为 0，告警 `host_isolation_failed`（critical）。没有 systemd 的本机演练因此起不了生产形态的 worker，这是有意为之：隔离只在真实 Linux 主机上成立。

unit 为 `Type=notify` + `WatchdogSec=60s`：worker 每轮 tick 回喂看门狗，卡死会被重启；`ProtectSystem=strict` 下它只能写 `.runtime` 与 `work` 两个目录。

## 8. 收口安全边界

来源白名单已由 `provision-tenant` 写入（`dsherp_agent_sources` = agent 网段 + worker 网段）。还差宿主自己：Docker 的 `internal: true` 只隔离转发链，**容器仍能连到网桥网关也就是宿主本身**（sshd :22 等一切绑在 0.0.0.0 的监听）。把运行容器挡在宿主之外要在宿主 INPUT 链上加两条规则；第 7 步启用的 `dsherp-agent-firewall.service` 在每次开机与每次 `start/restart` 时按**当前** agent 网桥应用它们（规则带 `dsherp-agent-firewall` 注释标签，脚本据此替换旧规则、`stop` 时精确删除），并把网桥名记在 `/run/dsherp-agent-firewall/<agent 网络名>`。worker 的 unit `Requires` 它，防火墙没起来 worker 不会起；worker 自己启动时还会比对记录的网桥与实际网络，不一致直接拒绝启动并提示重启防火墙单元。

```sh
sudo /usr/local/sbin/dsherp-agent-firewall check "${DSHERP_PROJECT}_agent"   # ok on br-…；missing or stale 时按提示 restart
admin agent-firewall            # 只打印同一组规则（含 nft 写法与撤销命令），给没有 systemd 的主机或人工核对用
```

**网络被重建时必须重启防火墙单元**：`compose down`、改网络定义后再 `up`，agent 网桥会换名，旧规则挡不住新网桥。`check` 会报 stale，worker 会停止领取并告警，`sudo systemctl restart dsherp-agent-firewall` 后它在下一个 tick 自行恢复。容器内的 DNS 由 Docker 内嵌解析器在容器命名空间里应答，不经过宿主 INPUT，规则不影响运行。脚本只用 iptables——Docker 自己就用它在每台主机上编程，nft-only 的发行版也带 iptables-nft 兼容层。

## 9. 验收（G1 与 G4）

| 检查 | 命令 | 期望 |
|---|---|---|
| 全部 healthcheck 绿 | `compose ps` | 每个长驻服务 `healthy` |
| 站点可达且走 TLS | `curl -sI https://$SLUG.$DSHERP_BASE_DOMAIN/login` | 200，含 `Content-Security-Policy`，`connect-src 'self'` |
| 密码登录已关 | `curl -s -X POST https://$SLUG.$DSHERP_BASE_DOMAIN/api/method/login -d 'usr=x&pwd=y'` | 拒绝，登录页不显示密码表单 |
| 容器不出网、非 root、到不了宿主 | `sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod PYTHONPATH=. .venv/bin/python infra/probe_agent_boundary.py'` | `failures: {}`（用真实运行容器参数在 agent 网络里探测公网、代理、业务站、平台、库、**宿主网关 :22**、控制面文件、能力集） |
| 宿主防火墙已应用且对应当前网桥 | `systemctl is-active dsherp-agent-firewall && sudo /usr/local/sbin/dsherp-agent-firewall check "${DSHERP_PROJECT}_agent"` | `active`，`ok on br-…`；`probe_agent_boundary.py` 的 `host_gateway_ssh`/`host_gateway_loopback_port` 均为 `False` |
| worker 自己核验过隔离 | `curl -s 127.0.0.1:9109/metrics \| grep dsherp_host_isolation_ok` | `1`；journal 里没有 `host_isolation_failed` |
| worker 存活 | `systemctl is-active dsherp-agent-worker` | `active` |
| 发布可核验 | `cat infra/releases/$TAG.json` | tag、提交、基底 digest、架构齐全 |

## 10. 升级与回滚

```sh
# 升级：先备份，逐站 migrate，再按 DocType 比对行数与摘要（子表与单值文档尚未覆盖，合法迁移的元数据变化也会判为差异——G2 的可核验校验由计划 4 首片补齐）；有差异退出码为 1
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

## 11. 下线租户与从归档恢复

```sh
DSHERP_ENV=prod ./bin/dsherp-admin retire-tenant "$SLUG"     # 备份 → drop-site 整站搬进归档 → 回读归档
DSHERP_ENV=prod ./bin/dsherp-admin render-ingress && compose up -d caddy
```

命令先证明归档目录可写，再 `bench backup --with-files`，再 `bench drop-site --archived-sites-path /home/frappe/frappe-bench/archived/sites`——drop-site 会把整个站目录（刚做的备份在它的 `private/backups` 里）搬到那里。输出的 `archive` 是回读到的归档目录，`backups` 是其中的备份文件名；归档目录没有出现或缺 `site_config.json` 时命令报错并**不改租户清单**，此时不要重跑，先去看目录。归档落在 backend 的 `tenant-archive` 卷上（镜像预建了该目录归 frappe 所有），backend 容器重建后仍在；此前它只在容器可写层里，一次升级就会丢。

从归档恢复一个已下线的租户（先重新开站，再用归档里的备份覆盖）：

```sh
admin provision-tenant "$SLUG"      # 空站 + App + 身份 + 平台记录
compose exec -T backend bench --site "$SLUG.$DSHERP_BASE_DOMAIN" restore \
  "<archive>/private/backups/<时间戳>-…-database.sql.gz" \
  --with-public-files "<archive>/private/backups/<时间戳>-…-files.tar" \
  --with-private-files "<archive>/private/backups/<时间戳>-…-private-files.tar" \
  --db-root-username root --db-root-password "$(cat $DSHERP_SECRETS_DIR/db_root_password)" --force
admin provision-tenant "$SLUG"      # 幂等重跑：核对运行身份、站点配置与 healthcheck
```

归档目录里还有整站的 `site_config.json`（含库口令与加密密钥），它与库转储同目录同权限——这是计划 4 的异地备份要分开存放的对象，归档不能原样同步出主机。

## 与其他服务共用的主机

`DSHERP_HTTP_PORT` / `DSHERP_HTTPS_PORT` 可以把 Caddy 挪开 80/443（例如 18080/18443），用于一台已经在跑别的服务的演练机；这样做 ACME HTTP-01 无法签发，Caddy 对 `.localhost` 主机名回落到内置 CA。生产主机不应这样用。

## 本地 Docker 上的 G1 演练（非 Linux 主机时）

同一份 runbook 可以在一台已装 Docker Desktop 的开发机上以生产形态跑通，作为拿不到干净 Linux 主机时的替代演练。差别只有四点，全部由 `infra/env/prod.env` 表达：`DSHERP_BASE_DOMAIN=localhost`（`*.localhost` 解析到回环，Caddy 对这类主机名自动用内置 CA 签发，用 `curl -k` 或导入其根证书验证 TLS）；`DSHERP_IMAGE_REGISTRY=local` 且镜像用 `--platform linux/arm64` 在本机构建（不是 x86_64）；数据面用 `DSHERP_DB_BUFFER_POOL=256M`、两个 redis `64mb`、gunicorn 1×2 的笔记本规格；worker 没有 systemd，改为前台启动一次核对 `prepare_host`、心跳与 `/metrics`。dev 栈与它并存：项目名 `dsherp` 对 `dsherp-validation`，网络、卷、容器名全部不同，端口只共用宿主 80/443（dev 不占）。

## 已知边界

- **通配证书**：目标拓扑写的是 `*.base_domain` 通配证书；通配必须走 DNS-01，需要带 DNS 提供商插件的 Caddy 构建与 API 凭证。按已裁决 #2 的 ≤3 租户试点规模，本文改为逐站 HTTP-01：不需要插件、不需要 DNS 凭证，代价是每次增删租户要重跑 `render-ingress`。
- **G1 的执行环境**：本文在本机 Docker Desktop 与一台 x86_64 CentOS 7 共用服务器上各以生产形态完整执行过一次（见[证据](deployment-security-evidence.md)），共修掉 22 个断点；两台主机都不满足 G1 判据字面（干净、专用、前置齐全），G1 与 ACME 仍待一台合规主机由审计方按本文计时执行。
- **成员绑定**：`provision-tenant` 已覆盖建站、装 App、运行服务身份、站点配置、DS Enterprise、OAuth Client、Social Login Key、平台端点表、入口渲染与 healthcheck。**把某个平台用户加入某个企业（DS Membership）仍是人工步骤**：按已裁决 #4，成员的业务站短期密钥由 SSO 回调签发属于计划 4，本计划不改这条链路，因此成员绑定沿用平台站 Desk 上的手工创建。
