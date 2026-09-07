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

**清单必须随镜像一起交付。** `infra/releases/$TAG.json` 记录的就是 tag 指向的构建提交，所以它只能在 tag 之后才提交进仓库；按 tag 取源码的目标主机（`git archive` 导出树）没有这个文件，而 `release`/`rollback` 拿不到有效清单就拒绝改动站点（见第 10 步）。用 registry 时把 `$TAG.json` 另行传到目标主机；没有 registry 时用 `--bundle` 一次产出镜像 tar 与清单，两者一起传（gzip 到处都有，zstd 不一定）：

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.release_images --platform linux/amd64 --bundle ../dsherp-dist/
# ../dsherp-dist/dsherp-$TAG.tar 与 ../dsherp-dist/$TAG.json 一起传到目标主机；目标主机：
docker load -i dsherp-$TAG.tar
mkdir -p "$RUNTIME/manifests" && cp $TAG.json "$RUNTIME/manifests/"   # RUNTIME 是 prod.env 里的 DSHERP_RUNTIME_DIR；或发布时 --manifest $TAG.json
```

`--bundle` 的目录必须在源码树之外（源码树就是构建上下文，脚本拒绝树内目录）；同名 tar 或清单已存在时拒绝覆盖；`docker save` 之后先读 tar 里的 `manifest.json`，两个镜像的 config 摘要必须等于清单记录的 id，否则不把清单放到 tar 旁边——交付出去的一对一定是互相对应的。

`release`/`rollback` 找清单的顺序：命令行 `--manifest FILE`（给了就只看它）→ 缺省同时看 `<DSHERP_RUNTIME_DIR>/manifests/<tag>.json` 与源码树里的 `infra/releases/<tag>.json`，两处都有时记录的 id 必须一致，不一致就拒绝并列出两份（本地重建后重生成的仓库清单会与交付的那份不同，这时删掉过期的一份或用 `--manifest` 指定）。找到的清单必须是这个 tag 的、含该镜像 `sha256:` 开头的 id，且与两个 bench 运行容器的镜像 id 一致；本地重建的同 tag 镜像 id 不同，会被拒绝。**构建机与目标主机的 Docker 必须用同一种镜像存储**：经典存储（overlay2）下 `docker image inspect` 的 `Id` 是 config 摘要，containerd 镜像存储下是 manifest 摘要，同一制品在两边会报出不同的 id，发布会被如实拒绝——本项目按经典存储核对，主机不要开启 containerd 镜像存储。

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


备份相关的密钥与凭据（配置了异地仓库后必需，`doctor` 会检查）：`secrets init` 生成两个仓库口令
`backup_repository_password` 与 `backup_secrets_repository_password`；运维另行放入两份**不同**的对象存储身份
`backup_storage_credentials`、`backup_secrets_storage_credentials`（各两行 `AWS_ACCESS_KEY_ID=`/`AWS_SECRET_ACCESS_KEY=`，
桶策略上分别只能读写数据桶与密钥桶）；私有 CA 放 `backup_storage_ca.pem`（可选，restic 会真正校验）。
**这五份材料必须另存在对象存储之外**（密码库或离线介质）：丢了它们等于丢了全部异地备份，恢复主机靠人带入。

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

worker 在生产启动时**先核验宿主隔离再服务**：`/run/dsherp-agent-firewall/<agent 网络名>` 必须存在且记录的网桥等于当前网络的网桥，然后用发布镜像在 agent 网络里起一个探针容器同时连网关的 :22 与 :9——连通、被拒绝（RST）、被重置都说明宿主没挡，路由错误等其它异常算"无法证明"，只有超时才算隔离；三者任一不成立 worker 直接退出并把原因写进 journal（`Restart=always` 会每 10 秒重试，直到防火墙单元就位）。运行中每个 tick 重查记录与网桥，网络 id 变了就重新探针，健康时也每 30 秒重探一次（同一网桥上规则被清掉在 30 秒内被发现），失败期间每个 tick 都探；隔离失效期间心跳照发（用户看到的是排队而不是 503）但**不领取新运行**，`/metrics` 的 `dsherp_host_isolation_ok` 为 0，告警 `host_isolation_failed`（critical）。没有 systemd 的本机演练因此起不了生产形态的 worker，这是有意为之：隔离只在真实 Linux 主机上成立。

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

## 10. 升级与回滚（G2）

发布前提：没有运行在飞（`release` 会检查每站的 Queued/Running/Cancelling 计数，非零即拒绝），所以先停 worker：`sudo systemctl stop dsherp-agent-worker`。发布期间每个站被置为维护模式并暂停调度（`maintenance_mode`/`pause_scheduler` 写进 site_config，结束时恢复原值），用户在此期间看到 503。

升级前先把主机上的源码树换到 `$NEW_TAG`（按第 1 步的取源方式重新导出或 checkout，并重跑第 7 步的 `.venv` 同步）：`bin/dsherp-admin`、compose 文件和下面的预检都来自这棵树，不换就是在用旧 tag 的工具发布新 tag。再按第 2 步把新 tag 的清单交付到主机；**第一次发布**还要把 `--from` 那个旧 tag 的清单一并交付——首次发布没有 `current.json`，它的回滚只能以旧 tag 清单为锚，`release` 会在改动任何站点之前核对旧清单可用，拿不到就拒绝发布。

```sh
$EDITOR infra/env/prod.env          # DSHERP_IMAGE_TAG 改为新 tag
compose pull && compose up -d       # 两个 bench 都换到新镜像
DSHERP_ENV=prod ./bin/dsherp-admin release "$NEW_TAG" --from "$OLD_TAG"   # 第一次发布必须给 --from；之后从 current.json 取
sudo systemctl start dsherp-agent-worker
```

退出码：0 = 各站数据与升级前一致（或差异都被本次执行的 patch 声明），站点已重新开放；1 = 有未声明差异，**有差异的站保持维护模式**，人核对报告后要么 `rollback`，要么确认接受再 `resume-site <站>`；2 = 中途失败，失败的站保持维护模式并有带 `failed` 的部分报告。

`release` 先做预检：两个异地仓库已配置（生产未配置即拒绝，见第 12 节）、`prod.env` 的 tag 就是要发布的 tag、两个 bench 服务各恰好一个运行容器（多于一个拒绝，不会只查第一个）且**运行容器**镜像全名就是该 tag 的发布镜像（读容器而不是读环境文件）、该 tag 的发布清单可用且两个容器的镜像 id 与清单记录一致（清单缺失、读不出、tag 不符、没有该镜像的记录或 id 不是 `sha256:` 开头的非空串，都在改动任何站点之前拒绝——不会退化成"没有预期 id 就不核对"；清单查找顺序见第 2 步，`--manifest FILE` 可直接指定）、`--from` 与 `current.json` 的记录一致（有记录时给出不同的 `--from` 会被拒绝：记录对就不要给，记录错就先改对或删掉）、`current.json` 里的镜像记录完整（否则将来的回滚用不了，现在就拒绝）或——没有 `current.json` 时——旧 tag 的清单可用、每站没有在飞运行、这个 tag 还没有升级前基线（基线只写一次，重来要换 tag 或先 `forget-release`，后者只删发布记录目录下的合法子目录）。然后写发布记录（新旧 tag、两个容器的镜像与镜像 id），再对每个站（租户站与平台站）按序：静默 → `bench backup --with-files` → 把四件套备份集复制到 `/home/frappe/frappe-bench/archived/releases/<tag>/<站>/`（`tenant-archive`/`platform-archive` 卷；Frappe 自己会在 23 小时后清掉 `private/backups`）→ 升级前快照 → `bench migrate` → 升级后快照（按升级前的列集求哈希）→ 从 Patch Log 算出本次实际执行的 patch，只采纳它们声明的预期变化 → 比对 → 只有干净才恢复站点标志。全部干净后把 `current.json` 记为新 tag。报告在 `.runtime/releases/release-<tag>-<时间戳>.json`（`release-<tag>.json` 是最新一份），快照与备份记录在 `.runtime/releases/<tag>/{release.json,<站>/before.json,after.json,backup.json}`。

比对口径：站上每个 DocType 按元数据归入且只归入一桶——**严格**（erpnext 与两个 dsherp App 的全部 DocType、自定义 DocType、联系人与身份表、租户写的权限与流程：Role、Custom DocPerm、Workflow 族、非标准的 Notification/Report/Print Format/Web Form、Client/Server Script 等：逐行逐字段）、**日志**（Comment/Version/Deleted Document/Communication/Activity Log：只比哈希，允许新增）、**排除**（Frappe 自己的元数据、缓存与技术日志，每次 migrate 都会改写）；租户写过的元数据按行分区（`custom=1` 的 DocType 及其字段、`is_system_generated=0` 的 Custom Field/Property Setter、`is_standard` 为否的报表/通知/打印格式、User 与 Role Profile 下的 Has Role）。新出现的表和列是 schema 变化，报告为信息不算差异；消失的表和列、行的增删改、单值文档任何设置值的变化（含首次落库）都是差异，除非本次执行的某个 patch 在自己的模块里用 `EXPECTED_CHANGES = [{'doctype': ..., 'fields': [...] 或 ['*'], 'rows': 'existing'|'inserted'|'deleted'|'any'}]` 声明过——只留哈希的大表只能被 `['*']` 整行声明放行。任何一张表读不出来就中止，不会带着"部分快照"下结论。原生 SQL 分页读取，密码列与密钥类单值只存摘要；快照行数超过上限（默认 100 万）也中止。

任何阶段失败（备份、快照、migrate、声明读取、比对）都让该站保持维护模式，报告带 `failed`，命令退出码 2；此时按下面回滚。

```sh
# 回滚：改回旧 tag、起旧镜像，再撤销那次发布——它会恢复 release 归档的升级前备份并与升级前快照比对
$EDITOR infra/env/prod.env          # DSHERP_IMAGE_TAG 改回发布记录里的 previous_tag
compose up -d
DSHERP_ENV=prod ./bin/dsherp-admin rollback "$NEW_TAG"    # 参数是要撤销的发布 tag；退出码 0=数据与升级前一致
```

`rollback` 先核对：`prod.env` 的 tag 是发布记录里的 `previous_tag`，两个 bench 运行容器的镜像全名是该 tag 的镜像，且镜像 id 等于升级前实际运行的那一个：发布记录的 `previous_images`（来自 `current.json`）必须两个服务齐全、镜像名就是旧 tag 的镜像、id 非空，记录存在但不完整就直接拒绝；记录里根本没有 `previous_images`（只有第一次发布如此）时改用旧 tag 的发布清单（查找顺序同第 2 步，`--manifest FILE` 可指定），清单也拿不到有效 id 就不恢复；记录里有 `previous_images` 而又给了 `--manifest` 时，那份清单仍会被读取并须与记录一致，否则拒绝；`release.json` 缺 `previous_tag` 也拒绝——在新镜像、同名异构镜像或别的旧版本上恢复都会被拒绝，数据不动。然后从 `.runtime/releases/<tag>/<站>/backup.json` 找到归档的备份集，`bench restore <db> --with-public-files … --with-private-files … --force`，再快照并与 `before.json` 比对；唯一容忍的差异是 restore 自己回写的 `System Settings.enable_scheduler`。干净的站才重新开放，全部干净后 `current.json` 记回旧 tag；恢复失败或有差异的站保持维护模式（退出码 2 / 1）。`--backup SITE=FILE` 可改用别的数据库转储（同前缀的 files tar 一并恢复）。`resume-site <站>` 是人确认后解除维护的唯一途径；`forget-release <tag>` 删除宿主侧记录（容器内归档不动）。演练与排查用 `snapshot <站> --out FILE [--like 已有快照]` 与 `compare BEFORE AFTER`；只留哈希的大表在两份快照的哈希列集不同时不能比，`--like` 让第二份按第一份的列集求哈希。

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

## 12. 备份与容灾

两个 restic 服务带 `profiles: [ops]`，不随 `compose up` 常驻，只由 `dsherp-admin backup-sync`/`restore-drill` 以 `compose run --rm` 拉起，所以第 6 步的服务清单与健康检查不包含它们。

**判据**：任一站点（平台站与全部租户站）在任一时刻都有一份 24 小时内的异地备份可恢复；RTO 8 小时。年龄按备份**数据本身的时点**算，不是上传完成时刻。

**验收行**（第 9 节的表之外，这一节自己的）：

```sh
systemctl list-timers 'dsherp-backup*'          # 两个定时器都在，下一次触发时间合理
DSHERP_ENV=prod ./bin/dsherp-admin doctor       # 五份备份密钥材料齐全且 0600
curl -s 127.0.0.1:9109/metrics | grep dsherp_backup_
```

`dsherp_backup_sites_rpo_ok` 应等于 `dsherp_backup_sites_expected`，`dsherp_backup_offsite_oldest_hours` 小于 24 且不为 -1，`dsherp_backup_last_run_ok` 为 1。

**周期**：`dsherp-backup.timer` 每天 02:00 与 14:00（Asia/Shanghai）跑 `dsherp-admin backup --sync`；`dsherp-backup-drill.timer` 每周日 04:00 跑 `dsherp-admin restore-drill`。两者失败时 systemd 触发 `dsherp-backup-failure@.service`，它直接写 journal 并投递 profile 里的 `alert_webhook`——worker 停止时这条路仍在。装单元：

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.render_worker_units --root /opt/dsherp --user dsherp
sudo install -m 644 /opt/dsherp/.runtime/dsherp-backup*.service /opt/dsherp/.runtime/dsherp-backup*.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dsherp-backup.timer dsherp-backup-drill.timer
```

**一次性初始化**：`DSHERP_ENV=prod ./bin/dsherp-admin backup-init`（幂等，两个仓库都建好后报 `kept`）。

**备份集**：一次备份产出一个集，`<UTC 时间戳>-<站名下划线形式>-<6 位随机>`。数据侧在 `tenant-backups`/`platform-backups` 卷的 `sets/<站>/<集>/`：三件数据（`database.sql.gz`、`files.tar`、`private-files.tar`）、`snapshot.json`（G2 口径的核验快照）、`set.json`（各件 sha256、快照摘要、窗口起止、**当时运行的镜像 tag 与镜像 id**、Frappe 版本）。密钥侧在 `tenant-backup-secrets`/`platform-backup-secrets` 卷的 `<站>/<集>/`：`site_config_backup.json`（0600，目录 0700）与 `pair.json`（把数据侧各摘要抄一份 + `config_sha256` + `set.json` 的摘要）。两侧互证同一个集。

**稳定窗口**：逐站先写保持文件（worker 不再领取，心跳照旧）→ `set-config dsherp_hold_until <窗口结束的 epoch 秒>`（服务端 `claim_run` 直接拒绝，关掉"worker 已过检查、claim 未落地"的缝；保持文件与该值都自带结束时刻，命令被杀也只挡到期为止）→ 等在途执行者（Running/Cancelling）归零，最多 10 分钟（排队的运行被冻结，不计入，也不会让备份永远推迟）→ 维护模式 + 暂停调度 → 排空已在里面的写入者（本站 RQ 作业与非空闲数据库连接连续两轮为 0，最多 2 分钟）→ `bench backup --with-files` 与快照 → **逆序撤销**每一步。失败或超时都按已完成的动作逆序清理，该站记失败或推迟，其他站照常。

**异地**：两个仓库、两个口令、两个存储身份。`backup-sync` 先向两个仓库列快照核对记录（远端少一侧立即降级并在同一次运行补齐），再补传未完成的集，读回 `set.json` 与 `pair.json` 逐项核对后才算 `complete`——**两个快照 id 不算数**。远端有而记录没有的集会被收养并核对；远端内容与本机暂存不符时按本机重传一次。保留按站点从集的时间戳选 7 日 + 4 周 + 3 月，另外永远保留每站最新的完整集与最新的已验证集、`kind=retire` 的下线集与尚未配对的集；两侧都判淘汰才 `forget`（该集的**全部**快照）再 `prune`。每次运行按天轮换 `check --read-data-subset=<n>/7`，一周读完全部数据。**保留语义**：没有备份的周期不占名额；不运行 `backup-sync` 就不会有任何淘汰；`keep 3 monthly` 不等于"三个月后一定消失"。

**发布与下线**：`release` 的升级前备份与 `retire-tenant` 的最终备份用同一套协议产出 `kind=release`/`kind=retire` 的集并立即上传。生产环境两个命令都以"两个仓库已配置"为前置，未配置即在任何破坏性动作前拒绝；下线的最终集必须两侧配对完成才 `drop-site`。归档目录下 `site_config` 现在放在 `secrets/` 子目录（0700/0600），与转储不同目录不同权限。

**排障**：`backup-sync` 先用 60 秒探两个仓库是否应答，不通就立刻记失败并退出（restic 自己对不可达端点会重试一刻钟），并清掉那次运行留下的容器。上一次运行被强杀会在仓库里留锁，表现为 `check`/`backup` 报 "repository is already locked"；确认没有别的进程在跑之后：

```sh
DSHERP_ENV=prod .venv/bin/python -c "from dsherp import backup, deploy_env; import os; r=deploy_env.settings(dict(os.environ)); print(backup.restic(r,'data',['unlock','--remove-all']))"
```

**可见性**：`backup` 与 `backup-sync` 把每站每阶段的最后一次尝试与最后一次成功写进 `<runtime>/backups/status.json`（失败不抹掉成功）。worker 每 tick 读它，产出 `dsherp_backup_sites_expected`、`dsherp_backup_sites_rpo_ok`、`dsherp_backup_offsite_oldest_hours`（有站从未完整则为 -1）、`dsherp_backup_local_oldest_hours`、`dsherp_backup_status_age_seconds`、`dsherp_backup_last_run_ok`、`dsherp_backup_unverified_days_max`，并按规则告警：

| 告警键 | 级别 | 条件 |
|---|---|---|
| `backup_rpo_warning` | warning | 某站最新完整异地集的数据时点已满 20 小时 |
| `backup_rpo_unmet` | critical | 满 24 小时，或该站从未有过完整集（消息里列站名） |
| `backup_run_failed` | warning | 任一阶段最近一次尝试失败且晚于最近一次成功（推迟不算失败） |
| `backup_status_missing` | critical | 状态文件缺失或损坏，或备份任务超过 13 小时没运行（定时器失联） |
| `restore_unverified` | warning | 某站超过 8 天没有验证过恢复（新站以首次备份成功起算宽限） |
| `backup_scope_unknown` | critical | 读不到租户清单：不缩小范围计算，直接报 |

监控范围来自**租户清单 + 平台站**，不是状态文件里出现过的站，也不是 worker profile。

**每周恢复验证**：`restore-drill [站…]` 在独立 compose 项目 `dsherp-restore` 里进行——`internal` 网络、无端口、无 worker/调度/队列/入口，只有两个取回容器能出网，且各自只挂自己那一半。流程：按各站最新完整集记录的 `(image_tag, image_id)` 分组 → 用该 tag 起栈并核对运行镜像 id 与集记录一致 → 取回两侧 → 核对配对与全部摘要（三件数据、`snapshot.json`、`site_config`）→ 进程内建站与恢复（root 与 admin 口令、`encryption_key` 只走解释器 stdin，不上命令行，`bench.log` 不再新增明文口令）→ 注入 `encryption_key`（只这一项；`db_password`、`host_name`、`dsherp_agent_sources` 等属于原主机，不带）→ **不 migrate**（升级是随后显式的 `release`）→ 与集内快照比对 → 对 `__Auth` 抽样解密。成功 `down -v` 只删本次演练自己的容器与卷；失败保留栈与 `<runtime>/backups/drills/<id>/` 诊断包（上限 14 天，但失败当时就告警、就处理），下一次演练拒绝启动直到 `--discard-failed`。

**异机恢复（G3）**：在新主机按第 1–9 步拉起，带入上面五份密钥材料，`backup-init` 应报 `kept`，然后逐站：

```sh
DSHERP_ENV=prod ./bin/dsherp-admin restore-site acme.tenant.example.com     # 也可 --set <备份集 id>；`backup --sync` 同步失败即以退出码 1 结束，定时单元的 OnFailure 会触发通知
```

`restore-site` 的契约：目标站在本机**必须不存在**（不覆盖、不自动清理）；本机记录里没有镜像身份的集直接拒绝（灾后重建的记录先 `backup-sync`，同步会从已核验的远端清单回填 `image_tag/image_id`）；先读回并核对两侧清单与全部摘要；本机运行的镜像 tag 与 id 必须等于该集记录的那次构建，取回清单后再以清单为准核对一次（不符即拒绝，先把 `prod.env` 改到那个 tag 再 `compose up -d`）；随后以关闭形态建站（建站即维护模式、企业由 Ready 转为 Provisioning、不发布入口；管理员停用或标为失败的企业保持原状）、恢复、注入 `encryption_key`、与集内快照比对、抽样解密，再以常规 `provision-*` 让主机相关配置按**这台**主机重算并把企业标为 Ready、发布入口；只有比对干净才解除维护。任一步失败站点保持维护模式，报告在 `<runtime>/backups/restore-<站>.json`。成功后若本机还没有 `releases/current.json`，会按该集记录的 tag 与本机运行镜像写一份，下一次 `release` 才知道从哪来。升级到更新的 tag 是随后显式的 `release`。

**保留与用户数据删除（裁决 #10）**：备份是个人数据的副本。删除只作用于在线数据；已生成的集不改写，按上面的保留规则随运行淘汰；`kind=retire` 的集是否最终清除，与审计保留切片一起裁决。`delete-user-data` 只影响其后产生的集。

## 13. 个人数据的导出与删除

```sh
DSHERP_ENV=prod ./bin/dsherp-admin export-user-data acme.tenant.example.com someone@example.com
DSHERP_ENV=prod ./bin/dsherp-admin delete-user-data acme.tenant.example.com someone@example.com            # 只打印计划，退出码 1
DSHERP_ENV=prod ./bin/dsherp-admin delete-user-data acme.tenant.example.com someone@example.com --confirm
```

导出只读，不改站点，写成 `<runtime>/user-data/export-<站点>-<用户>-<时间>.json`（0600），含本人的会话、
运行、提案与执行；**里面是个人内容，按交付流程转交，不要留在共享目录**。删除先导出，再让该用户的在途
运行停下来：排队中的直接取消，运行中的标为 Cancelling，命令等执行者放手（`--wait`，默认 120 秒）；到时
仍有在途就拒绝，不清任何内容，报告写成 `delete-blocked` 并列出那些运行（先停 worker 或等它们结束再重跑）。
整个确认路径在站点保持之下进行（写宿主保持文件并置 `dsherp_hold_until`，服务端不再交出领取；两者都在 30 分钟后自行失效），从结算到目录删除完成才归还；
只删除计划时枚举的会话目录，其后出现的目录不在本次范围内。清除范围逐 DocType 声明在 `dsherp/user_data.py`：本人内容（提问、页面快照、回答、错误正文、会话标题）
清除，追责事实（谁、何时、读了哪些记录、提案与执行结果）保留，并删除 `track_changes` 为这些列留下的
Version 行、按会话删除该用户的原生会话目录；清除脚本在与 `send_message` 相同的 User 行锁下再查一次
在途，有就回滚拒绝。报告里的 `residue` 逐条列出**知道留下了什么以及唯一的移除方式**：运行事件不改写
（只能走登记的受控脱敏迁移）、已生成的异地备份按保留策略到期淘汰。平台授权令牌不在运行行里（它只在
运行期间存于站点缓存，运行结束即删），删除时无需处理。

原生会话目录另有 `sessions` 命令查看与按 90 天清理（`--sweep`）；旧布局留下的扁平哈希目录报为
"未归属"，由人判断，不做猜测删除。

## 14. 凭据：短期业务凭据与轮换

平台不长期持有成员的业务站凭据：业务站在 SSO 回调时把当前凭据交给平台，并记一个 12 小时的窗口，
窗口过后业务站自己拒绝这把 key（在 `disable_user_pass_login` 生效的站点上；仍允许密码登录的开发站只记录
与报告，不拒绝），成员**重新登录即续签**（剩余 ≤4 小时才换新，所以同一成员的两个
会话不会互相踢掉）。续签会替换旧 secret：浏览器会话不受影响，但用旧凭据在途的一次 API 调用会失败，
下一次登录带来新凭据。一个业务用户只能有一个启用中的平台绑定，由 `DS Membership.active_binding`
的唯一索引保证（不是应用层先查后写）；绑定版本 `binding_version` 是递增整数，停用/重新启用/改绑都
+1，凭据续签不动它，所以旧授权不会因为绑定改回原样而重新有效。

```sh
DSHERP_ENV=prod ./bin/dsherp-admin credentials acme.tenant.example.com                  # 借出了什么、还能用多久
DSHERP_ENV=prod ./bin/dsherp-admin credentials acme.tenant.example.com --issue someone@example.com
```

`credentials` 在发现"有 API key 却没有登记窗口"的用户时以退出码 1 结束——那是一把不受期限约束的
钥匙。`--issue` 是成员无法通过平台登录续签时的运维路径：在业务站签发并交给平台，密钥只走标准输入
与容器 stdin，不出现在命令行或报告里。

升级顺序：**先 `bench migrate` 再切流量**。没迁移的站点没有 `DS Business Credential`，机器凭据一律被
拒（刻意 fail-closed）；两个 App 的 patch 会把既有 key 与既有绑定纳入一个窗口。

三类长期凭据用 `rotate` 轮换，账簿在 `<runtime>/rotations.json`（只记类别、目标、第几次、生效时间与
值的指纹，不记值）：

```sh
DSHERP_ENV=prod ./bin/dsherp-admin rotate provider --file /srv/dsherp/.env < new-key.txt
DSHERP_ENV=prod ./bin/dsherp-admin rotate runtime acme.tenant.example.com --profile /srv/dsherp/worker.json
DSHERP_ENV=prod ./bin/dsherp-admin rotate oauth-client acme
```

- `provider`：新 key 只从标准输入读，只改 worker 单元读的那个 `.env` 里的那一行；**改完重启 worker 单元**。
- `runtime`：按站点 `site_config` 声明的运行身份轮换；必须给交付目的地——`--profile`（可多次，所有
  文件先校验后签发）或 `--print-secret`（打印一次并留一份 0600 副本文件，用后删除）；签发后的密钥先写
  到 `<runtime>/rotations/runtime-<站点>-<时间>.json` 再写 profile，写入失败时错误信息指明该文件，
  全部写成功后自动删除。改完重启 worker 单元。
- `oauth-client`：平台与业务站两侧一起换；进行中的登录会失败一次，重新登录即可，已建立的会话不受影响。

`doctor` 会把从未登记轮换和超过窗口的目标列出来（provider/runtime 90 天、oauth-client 180 天）。轮换
不会作废原生会话：`runtime_revision` 不计入 provider key。

## 与其他服务共用的主机

`DSHERP_HTTP_PORT` / `DSHERP_HTTPS_PORT` 可以把 Caddy 挪开 80/443（例如 18080/18443），用于一台已经在跑别的服务的演练机；这样做 ACME HTTP-01 无法签发，Caddy 对 `.localhost` 主机名回落到内置 CA。生产主机不应这样用。

## 本地 Docker 上的 G1 演练（非 Linux 主机时）

同一份 runbook 可以在一台已装 Docker Desktop 的开发机上以生产形态跑通，作为拿不到干净 Linux 主机时的替代演练。差别只有四点，全部由 `infra/env/prod.env` 表达：`DSHERP_BASE_DOMAIN=localhost`（`*.localhost` 解析到回环，Caddy 对这类主机名自动用内置 CA 签发，用 `curl -k` 或导入其根证书验证 TLS）；`DSHERP_IMAGE_REGISTRY=local` 且镜像用 `--platform linux/arm64` 在本机构建（不是 x86_64）；数据面用 `DSHERP_DB_BUFFER_POOL=256M`、两个 redis `64mb`、gunicorn 1×2 的笔记本规格；worker 没有 systemd，改为前台启动一次核对 `prepare_host`、心跳与 `/metrics`。dev 栈与它并存：项目名 `dsherp` 对 `dsherp-validation`，网络、卷、容器名全部不同，端口只共用宿主 80/443（dev 不占）。

## 已知边界

- **通配证书**：目标拓扑写的是 `*.base_domain` 通配证书；通配必须走 DNS-01，需要带 DNS 提供商插件的 Caddy 构建与 API 凭证。按已裁决 #2 的 ≤3 租户试点规模，本文改为逐站 HTTP-01：不需要插件、不需要 DNS 凭证，代价是每次增删租户要重跑 `render-ingress`。
- **G1 的执行环境**：本文在本机 Docker Desktop 与一台 x86_64 CentOS 7 共用服务器上各以生产形态完整执行过一次（见[证据](deployment-security-evidence.md)），共修掉 22 个断点；两台主机都不满足 G1 判据字面（干净、专用、前置齐全），G1 与 ACME 仍待一台合规主机由审计方按本文计时执行。
- **成员绑定**：`provision-tenant` 已覆盖建站、装 App、运行服务身份、站点配置、DS Enterprise、OAuth Client、Social Login Key、平台端点表、入口渲染与 healthcheck。**把某个平台用户加入某个企业（DS Membership）仍是人工步骤**：按已裁决 #4，成员的业务站短期密钥由 SSO 回调签发属于计划 4，本计划不改这条链路，因此成员绑定沿用平台站 Desk 上的手工创建。
