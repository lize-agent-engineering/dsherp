# dsherp 部署 runbook（单 Linux 主机 + Compose + 自建镜像）

适用范围：一台干净的 **Linux x86_64** 主机，从仓库 tag、镜像仓库地址与密钥文件出发，拉起 platform 站 + 1 个租户站 + 宿主 worker。这是[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md) G1 的执行文档；每一步都对应仓库内一条可重复执行的命令，**开通链路（第 1–9 步）不需要人工 `docker exec` 进容器改东西**。两处例外都在其后的运维链路上、且都点了名：没有公网域名时把 Caddy 的内置 CA 根证书 `docker cp` 出来做验收（见文末那一节）；第 11 节从归档恢复一个已下线租户时的 `compose exec … bench restore`（第 12 节的 `restore-site` 不需要）。

本文不覆盖数据治理与容灾（计划 4）、CI（计划 5）与 Agent 质量（计划 6）。真实租户接入需终验通过后另行授权。

## 0. 前置

| 项 | 值 | 备注 |
|---|---|---|
| 操作系统 | Linux x86_64 | 四个基础镜像 digest 均为多架构清单，含 `linux/amd64`（见[运行契约](runtime-baseline.md)） |
| 主机规格 | **4 vCPU / 8 GiB 内存 / 100 GB 磁盘** | 见下一节「主机规格与依据」。低于 8 GiB 不要开工：数据库被 OOM 杀过两次 |
| Docker | Engine 25+ 与 compose 插件 | 干净主机上没有，按下面「装 Docker 与配置镜像通路」装；`docker compose version` 必须可用（运维账号不在 docker 组，要加 `sudo`） |
| Python | **宿主不需要预装** | 第 3 步的 `uv venv --python 3.12.11` 自己把 CPython 下到 `~dsherp/.local/share/uv/python/` 并据此建 venv，宿主的 `python3` 全程不参与（2026-09-14 G1 第二轮实测：主机只有 3.10.12，无碍）。解释器因此落在 `/home` 下，unit 的 `ProtectHome=read-only` 只读足够执行 |
| 宿主 glibc | **≥ 2.28**（RHEL/Rocky 8、Debian 10、Ubuntu 18.10 及以上） | `deepseek-harness-runtime-bin` 只发布 `manylinux_2_28` wheel，`uv pip sync requirements.lock` 在 glibc 2.17（CentOS 7）上无解。宿主 worker 本身不引入 SDK，但同一份锁装不上就起不了 CLI；容器内是 Debian bookworm，不受影响 |
| Node | 见 `.nvmrc` | 仅构建前端产物时需要；发布镜像已内含 dist |
| **镜像存储驱动** | **构建机与目标主机都必须用经典存储（`overlay2`）**，不要开 containerd 镜像存储。检测 `docker info --format '{{.DriverStatus}}'` 出现 `io.containerd.snapshotter.v1` 即为开启；关闭：`/etc/docker/daemon.json` 写 `{"features":{"containerd-snapshotter":false}}` 后 `sudo systemctl restart docker`（`docker load` 之前做，无迁移问题；与下一行的 `registry-mirrors` 是**同一个文件**，一次写完，样例见「装 Docker 与配置镜像通路」） | **Docker 29 官方源全新安装默认是开着的**。开着时 `docker inspect .Id` 返回 OCI index 摘要而非 config 摘要：构建机开着就会把 index 摘要写进清单，而按本表配置的目标主机 `docker load` 后得到 config 摘要，两者不等，`release`/`rollback` 的镜像 id 核对会如实拒绝（2026-09-14 G1 验证实测：构建机 `29b9bc09…` vs 目标主机 `1e86647d…`）。清单同时记录 `diff_ids`，它与存储驱动无关 |
| git | **构建机**必须是干净的 git checkout，且 `$TAG` 指向 HEAD——没有 `.git` 的源码树一律拒绝构建（导出树无法证明导出后没被改过）；**目标主机**可以没有 git：它只 `docker load` 镜像，源码树只用来跑 CLI | 脏工作树、tag 不在 HEAD、无 `.git`，构建都会被拒绝；`--git-commit` 只是交叉核对 |
| 镜像来源 | 私有 registry，或在本机 `docker load` 导入的镜像 tar | `compose.prod.yml` 用 `pull_policy: if_not_present` |
| **目标主机取基础镜像的通路** | **必需**：bundle 只装两个 dsherp 镜像，`mariadb`/`redis`/`caddy`/`restic` 仍由 compose 按 digest 拉。目标主机必须能到 Docker Hub，或在 `/etc/docker/daemon.json` 配 `registry-mirrors`——**探测方法、可用地址与 JSON 写法见「装 Docker 与配置镜像通路」** | 2026-09-14 G1 两轮验证实测：国内主机到 `registry-1.docker.io` / `auth.docker.io` / `production.cloudflare.docker.com` 连不上，部署停在第 4 步。第二轮测出了机理——**DNS 被污染**：经 8.8.8.8 解析 `registry-1.docker.io` 得到 Facebook 的地址（`2a03:2880:f117:83:face:b00c:0:25de` / `173.252.105.21`），连的根本不是 Docker Hub，表现为 4.5 秒后 `Connection timed out`。**两轮都没测过换 DNS 或写 hosts 能不能绕开**，也没测过真实地址是否同时被 TCP 封锁，所以别把镜像站当作唯一解法，只是当前已知可行的那条。按 digest 拉取时内容由 Docker 校验，走镜像站与直连等价。**离线主机**须另行 `docker save` 这四个基础镜像一并交付——`--bundle` 不含它们 |
| 账号 | 一个非 root 系统账号（本文用 `dsherp`），在 `docker` 组内 | 宿主 worker、四个 bench 容器（uid 1000）、db/redis（uid 999）与 Agent 运行容器（`DSHERP_AGENT_UID`）都不以 root 运行。**两个例外**：caddy（官方镜像主进程 uid 0，要绑 80/443，不挂宿主任何可写路径；2026-09-14 实测记录）；两个 restic 同步容器（root + 仅 `DAC_READ_SEARCH`，原因见第 12 节） |
| systemd | ≥ 242 才能启用 unit 里的全部沙箱指令（`ProtectSystem=strict` 232+、`ReadWritePaths` 232+、`RestrictSUIDSGID` 242+）；更老的版本会忽略这些行并在 journal 告警，进程照常受 `Restart`/`WatchdogSec` 管，但沙箱**静默退化**——219 上 `ProtectSystem=strict` 被解析成 `no` | CentOS 7 的 systemd 219 实测：保留 notify/看门狗/Restart/PrivateTmp/NoNewPrivileges/ProtectHome，丢掉其余五条 |
| 安装根 | 本文用 `/opt/dsherp`，但只是参数：`render_worker_units.py --root` 与 `bin/dsherp-admin` 都跟随实际目录。**不要放在 `/home` 下**：unit 的 `ProtectHome=read-only` 会把它锁成只读，能解锁 `.runtime`/`work` 的 `ReadWritePaths` 要 systemd ≥ 232 | 某些主机的 `/opt` 带 immutable 属性，root 也写不进，这时用 `/srv/dsherp` |

约定：下文所有命令在仓库根执行。这两个 shell 变量**要自己赋值**，本文不会替你赋，每开一个新 ssh 会话都要重来一遍：

```sh
TAG=v0.4.0-rcN      # 交付给你的那个发布 tag，照抄清单文件名里的
SLUG=acme           # 租户短名，小写字母开头，[a-z0-9-]
```

`$DSHERP_RUNTIME_DIR`、`$DSHERP_SECRETS_DIR`、`$DSHERP_PROJECT` 这些来自 `infra/env/prod.env`，第 3 步建好它之后用
`set -a; . <(sudo cat …)` 导进来（第 4 步给了可以直接存成文件的那一段）。**本文里不存在 `$RUNTIME` 这样的简写**——
要写全名。

### 交接清单

按本文从零拉起一台主机，下面这几件必须都收到。**少一件就有一步做不完**，其中 provider key 那一件
2026-09-14 G1 第二轮实测是硬阻断：没有它 worker 永远到不了 `active`（见第 7 步）。

| 件 | 用在 | 缺了会怎样 |
|---|---|---|
| 仓库 tag（本文 `$TAG`） | 第 1 步 clone | — |
| 镜像：私有 registry 地址 **或** `--bundle` 产出的 `dsherp-$TAG.tar` | 第 2 步 | — |
| 发布清单 `$TAG.json` | 第 2 步落盘、第 9 步核验、第 10 步发布 | `release`/`rollback` 拿不到清单，拒绝改动站点 |
| `backup_storage_credentials`、`backup_secrets_storage_credentials` | 第 3 步 | `doctor` 报缺文件；§12 备份跑不了 |
| `backup_storage_ca.pem`（私有 CA 时） | 第 3 步 | restic 连不上自签 TLS 的对象存储 |
| `repositories.env`（两个 restic 仓库 URL） | 第 3 步填进 `prod.env` | 备份没有去处 |
| **provider key**（`DEEPSEEK_API_KEY` 与 base URL） | 第 7 步写 `/opt/dsherp/.env` | **worker 启动即 `ValueError` 退出，单元永远 `activating`** |
| 四个基础镜像的 tar（**仅当目标主机既到不了 Docker Hub 也没有可达镜像站**） | 第 4、6 步 compose 按 digest 拉 | 第 4 步起不了数据面。先按「装 Docker 与配置镜像通路」探一遍，探不通才需要这一件 |
| CPython 3.12.11 的 python-build-standalone 包（**主机到 GitHub 慢时**，31 MB） | 第 3 步 `uv python install` 走本地镜像 | 不交付也能在线下，但慢网上这一项就能吃掉 60 分钟里的 36 分钟（2026-09-15 实测） |

以及三件不是文件的东西：主机本身（规格见下）、一个有 sudo 的运维账号、以及站点域名怎么解析到这台
主机（没有公网域名时见文末「内置 CA」一节）。**怎么传、落在哪由运维定**，本文一律写作运维账号家
目录下的 `~/handover/`；验收完把它删掉，那不是密钥的保管场所。

### 主机规格与依据

G1 的交接包要给审计方，审计方得先知道开一台什么机器才能开工，所以规格写在这里而不是留给经验。

| 用途 | 规格 |
|---|---|
| G1 白盒部署 + G10 24 小时浸泡 | 4 vCPU / **8 GiB** 内存 / 100 GB 磁盘 |
| G3 异机恢复 | 4 vCPU / 8 GiB 内存 / 40 GB 磁盘 |

**内存**。实测部分来自 2026-09-10 那次从零全绿的 nightly（[run 34403951406](https://github.com/lize-agent-engineering/dsherp/actions/runs/34403951406) 的 `docker-stats.txt` 工件），跑完一整轮集成之后的读数：

| 容器 | 稳态 |
|---|---|
| db | 639.1 MiB |
| 租户 backend | 259.2 MiB |
| platform backend | 171.0 MiB |
| redis | 22.6 MiB |
| 两个 frontend 合计 | 9.9 MiB |
| agent-egress | 5.6 MiB |

合计 ≈ **1.1 GiB**。但这台跑的是本机四站验证栈的 8 个容器，**生产形态是 13 个服务**（见第 6 步的 `compose ps`），多出来的都还要算：两个 bench 的 scheduler 与 queue 共 4 个进程（同一个镜像、同一种进程，按上表 171–259 MiB 的量级估 ≈ +0.7 GiB）、拆开的第二个 redis、以及 caddy。据此生产稳态曾外推为 ≈ **2 GiB**。**2026-09-14 G1 第二轮在干净 VM 上量到了真读数**（13 个容器全 healthy、worker 起来 2 分钟、无负载）：db 276.6、租户 backend 232.3、platform backend 175.6、queue 66.6、platform-queue 46.4、scheduler 49.7、platform-scheduler 49.4、caddy 13.4、agent-egress 7.4、两个 frontend 合计 13.4、两个 redis 合计 8.4 MiB，**容器合计 ≈ 939 MiB**，`free` 的 used 为 1330 MiB。比外推低一半，但**这是刚起来、页缓存还没填满的读数，不是稳态**——nightly 那台跑完一整轮集成后 db 一项就是 639 MiB，本轮的 276 MiB 正好说明差别在哪。稳态读数要等 G10 的 24 小时浸泡，那才是替换这一段的依据。

再往上：Agent 运行容器按 `dsherp/runtime_host.py:44` 限 `--memory 384m`，profile 的 `slots` 为 3，满载再 +1.15 GiB；加宿主 worker（空载实测 RSS：本机演练 25 MiB，2026-09-14 G1 真机 58 MiB）、restic、以及 OS 与 dockerd 自己，峰值 ≈ **3.5–4.5 GiB**。

**为什么不是 4 GiB。** 峰值估算本身就已经顶到 4 GiB，而这不是唯一理由——「余量看着够」这个判断在这个项目上被自己的事故推翻过一次：`quality-gates-evidence.md:344-360` 记着数据库被 OOM 杀过两次。第一次是连续运行 25 小时后撞上 1 GiB 上限，当时按「重启后 468.6 MiB、一轮全量集成后 608.7 MiB，每夜从零只跑一轮，还有 40% 余量」放行；同一个库随后在 **3 小时 14 分**内被第二次 OOM 杀掉，证明那个 609 MiB 是刚重启、缓存还没填满时的读数，不代表稳态。G10 的判据恰好是「连续运行 24h」——把浸泡门开在一台只剩几百 MiB 余量的机器上，测的是内存够不够，不是系统稳不稳。8 GiB 让稳态落在一半以下，页缓存也有地方放。

**磁盘**：一套镜像**解压后未去重**约 6.7 GB（dsherp-frappe 2.84 + dsherp-worker 3.39 + mariadb 0.36 + redis 0.03 + restic 0.04 GB），但两个 dsherp 镜像共享绝大部分层，**盘上实占** 2026-09-14 G1 第二轮 `docker system df` 读数是 **3.884 GB**（5 个镜像；restic 在 G1 范围内从未被拉取）。按未去重的数算是留余量。G2 要求能回滚到旧 tag，因此**两套镜像同时在盘上**，×2 ≈ 13.4 GB；再加数据库卷、每站每日四件套备份与 restic 缓存、24h 浸泡期间的日志。100 GB 是留了成长空间的取值，40 GB 是异机恢复主机的下限（它只需要一套镜像加一次恢复的落地空间）。

### 装 Docker 与配置镜像通路

干净主机上这是**第一件事**，也是计时的起点。两条路都满足 Engine 25+：

```sh
# A. Docker 官方源（本文用的这条；2026-09-14 G1 第二轮实测装出 29.8.0 + compose v5.5.1，33 秒）
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# B. 发行版自带（Ubuntu 22.04 的 jammy-updates 已是 docker.io 29.1.3 + docker-compose-v2 2.40.3，也满足）
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
```

`DEBIAN_FRONTEND` 要写在 `sudo` **之后**：默认 sudoers 不透传环境变量，`export` 了再 `sudo apt-get` 等于没设
（2026-09-15 实测 `sudo env | grep DEBIAN_FRONTEND` 为空，apt 打了 6 行 `debconf: unable to initialize frontend`）。

**再写 `/etc/docker/daemon.json`，一次写完两件事**——关 containerd 镜像存储（前置表「镜像存储驱动」一行，不关会让第 10 步的镜像 id 核对如实拒绝）与配镜像站（前置表「取基础镜像的通路」一行）：

```sh
# 先探一个可达的镜像站：能直连 Docker Hub 的主机跳过整个 registry-mirrors 部分
for m in docker.m.daocloud.io docker.1ms.run docker.xuanyuan.me dockerproxy.net docker.1panel.live; do
  printf '%s %s\n' "$m" "$(curl -s -o /dev/null -m 8 -w '%{http_code}' https://$m/v2/)"
done
# 401 或 200 都算通（401 是 registry 的正常鉴权应答）。**镜像站按镜像名设白名单**：2026-09-15 实测 docker.xuanyuan.me
# 对 library/redis 通、对 restic/restic 返回 403，第一次备份就停在拉不到 restic 上。所以要对本文真要拉的四个镜像各探一次：
for img in library/mariadb library/redis library/caddy restic/restic; do
  printf '%-16s %s\n' "$img" "$(curl -s -o /dev/null -m 15 -w '%{http_code}' -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' https://<选中的站>/v2/$img/manifests/latest)"
done   # 四个都要 200 或 401；有 403/404 的站只能当补充，不能当唯一

sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "features": {"containerd-snapshotter": false},
  "registry-mirrors": ["https://<你探到的第一个>", "https://<你探到的第二个>"]
}
JSON
sudo systemctl restart docker      # 只改 registry-mirrors 时用 `sudo systemctl reload docker`（SIGHUP 热重载，容器不重启；2026-09-15 实测 13/13 不掉）
sudo docker info --format 'driver={{.Driver}} mirrors={{.RegistryConfig.Mirrors}}'   # 期望 driver=overlay2
```

`registry-mirrors` 只对 Docker Hub 的镜像生效，本文要拉的 `mariadb`/`redis`/`caddy`/`restic` 正好都是；按 digest 拉时内容由 Docker 自己校验，走镜像站与直连等价。**地址必须填你自己探到的**：公开镜像站说没就没——2026-09-14 能用的 `docker.1ms.run` 到 2026-09-15 就 `000` 了，那天能用的是 `docker.m.daocloud.io` 与 `docker.xuanyuan.me`。manifest 那条探测返回 **401 也算通**（registry 要 bearer token，docker 客户端会自己去拿），200 更好。一个都不通就只能按前置表的「离线主机」那条走——在有通路的机器上 `docker save` 这四个基础镜像一并交付。

## 账号模型

本文的命令由**一个有 sudo 的运维账号**执行；`dsherp` 是服务账号，只在明确写了 `sudo -u dsherp` 的地方以它的身份运行，凡是它要读写的文件（安装根、`.runtime/`、`infra/env/prod.env`、密钥目录）都归它所有。分不清账号就会得到「worker 读不到 prod.env」「compose 读不到密钥」这类静默错误。

## 1. 取代码与建立账号

```sh
sudo useradd --system --create-home --shell /bin/bash dsherp   # 要能 sudo -iu 进去，不能是 nologin
sudo usermod -aG docker dsherp
sudo mkdir -p /opt/dsherp && sudo chown dsherp:dsherp /opt/dsherp   # 安装根由 root 建、交给 dsherp
sudo -u dsherp git clone --branch "$TAG" https://github.com/lize-agent-engineering/dsherp /opt/dsherp
cd /opt/dsherp
sudo -u dsherp git -C /opt/dsherp describe --tags   # 期望 $TAG；clone 后是 detached HEAD，正常
```

仓库是公开的，匿名 HTTPS clone 即可，不需要凭据。**`cd /opt/dsherp` 之后运维账号自己跑 `git` 会报 `fatal: detected dubious ownership`**（树归 dsherp）——本文所有 git 命令都带 `sudo -u dsherp`，第 10 步换 tag 时同理；真要以运维账号读，`git config --global --add safe.directory /opt/dsherp`。

主机没有 git 时（真机演练就是这样）：在有 git 的机器上 `git archive --format=tar.gz -o dsherp-src.tgz "$TAG"`，传到主机后 `sudo -u dsherp tar -xzf dsherp-src.tgz -C /opt/dsherp`。这样的主机**不能当构建机**——`release_images.py` 拒绝没有 `.git` 的源码树，因为导出树无法证明导出之后没有被改动（审查时实际改了导出目录里的一个文件，早先按导出标记核实来源的做法仍然放行，已撤掉）；镜像在有 git 的构建机上构建，按第 2 步 `docker save/load` 传过来。

## 2. 构建并推送镜像（构建机上执行一次）

发布镜像必须指定架构；`release_images.py` 会把 tag、提交、基底 digest 与架构写进 `infra/releases/$TAG.json`。它先核实构建上下文就是声称的来源：必须是 git checkout，工作树干净（`infra/releases/` 下上一次写出的清单除外）且 `$TAG` 指向 HEAD；没有 `.git` 的树直接拒绝。构建后再核对两个镜像的 `org.opencontainers.image.version/revision` 标签与 tag、提交一致，不一致不写清单。`.dockerignore` 把 `.runtime/`、`infra/env/`、`.venv/` 等宿主状态挡在构建上下文之外。构建机上先完成第 3 步的 `prod.env` 与 `.venv`（两样都在第 3 步；脚本要从 `prod.env` 读 tag/registry，并需要 venv 里的依赖），再：

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.release_images --platform linux/amd64
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-frappe:$TAG"
docker push "$DSHERP_IMAGE_REGISTRY/dsherp-worker:$TAG"
```

**清单必须随镜像一起交付。** `infra/releases/$TAG.json` 记录的就是 tag 指向的构建提交，所以它只能在 tag 之后才提交进仓库；按 tag 取源码的目标主机（`git archive` 导出树）没有这个文件，而 `release`/`rollback` 拿不到有效清单就拒绝改动站点（见第 10 步）。用 registry 时把 `$TAG.json` 另行传到目标主机；没有 registry 时用 `--bundle` 一次产出镜像 tar 与清单，两者一起传（gzip 到处都有，zstd 不一定）：

```sh
DSHERP_ENV=prod .venv/bin/python -m infra.release_images --platform linux/amd64 --bundle ../dsherp-dist/
```

产出 `../dsherp-dist/dsherp-$TAG.tar`（**未压缩约 3.4 GB**，gzip 后约 1.0 GB，以 `.tar.gz` 交付也可以，`docker load` 透明解压）与 `../dsherp-dist/$TAG.json`。**怎么传、落在哪由运维定**，本文用 scp 到运维账号家目录下一个自建目录（下文写作 `~/handover/`），与两份对象存储凭据、CA、`repositories.env` 放一起。目标主机上：

```sh
sudo docker load -i ~/handover/dsherp-$TAG.tar.gz     # 交付的是 gzip 过的就写 .tar.gz，docker load 透明解压；运维账号不在 docker 组（只有 dsherp 在），要 sudo
```

这一条是全流程最慢的一段（3.4 GB 未压缩，2026-09-14 G1 第二轮实测 3 分 35 秒）。它与第 3 步不互相依赖，赶时间时
可以放后台（`nohup … &`）与第 3、4 步并行，**第 5 步开站之前确认它已经结束**——镜像没进来 bench 起不了。

**清单落盘要等第 3 步**：它的目标目录来自 `prod.env` 里的 `DSHERP_RUNTIME_DIR`，而 `prod.env` 第 3 步才建；并且 `.runtime/` 必须归 dsherp，用 `sudo` 直接 `cp` 会留下一个 root 拥有的文件，正是「账号模型」一节说的那类静默错误。所以第 3 步 `secrets init` 之后再做这一条：

```sh
sudo -u dsherp mkdir -p "$DSHERP_RUNTIME_DIR/manifests"
sudo install -o dsherp -g dsherp -m 644 ~/handover/$TAG.json "$DSHERP_RUNTIME_DIR/manifests/"
```

（或者发布时用 `--manifest $TAG.json` 直接指定文件，不落这个目录。）

`--bundle` 的目录必须在源码树之外（源码树就是构建上下文，脚本拒绝树内目录）；同名 tar 或清单已存在时拒绝覆盖；`docker save` 之后先读 tar 里的 `manifest.json`，两个镜像的 config 摘要必须等于清单记录的 id，否则不把清单放到 tar 旁边——交付出去的一对一定是互相对应的。

`release`/`rollback` 找清单的顺序：命令行 `--manifest FILE`（给了就只看它）→ 缺省同时看 `<DSHERP_RUNTIME_DIR>/manifests/<tag>.json` 与源码树里的 `infra/releases/<tag>.json`，两处都有时记录的 id 必须一致，不一致就拒绝并列出两份（本地重建后重生成的仓库清单会与交付的那份不同，这时删掉过期的一份或用 `--manifest` 指定）。找到的清单必须是这个 tag 的、含该镜像 `sha256:` 开头的 id，且与两个 bench 运行容器的镜像 id 一致；本地重建的同 tag 镜像 id 不同，会被拒绝。**构建机与目标主机的 Docker 必须用同一种镜像存储**：经典存储（overlay2）下 `docker image inspect` 的 `Id` 是 config 摘要，containerd 镜像存储下是 manifest 摘要，同一制品在两边会报出不同的 id，发布会被如实拒绝——本项目按经典存储核对，主机不要开启 containerd 镜像存储。

目标主机自己当构建机也可以（早先的真机演练就是），但那不是 G1 的形态，而且它必须有 git 与干净 checkout：要求主机有 buildx、能访问 PyPI，并把构建时间算进拉起时间。

## 3. 环境文件、宿主 venv 与控制面密钥

`bin/dsherp-admin` 与 `infra/release_images.py` 都跑在 `.venv` 里，所以 venv 是所有 CLI 步骤的前提（第 7 步的 worker 复用同一个）：

**先装 uv**（干净主机上一定没有），钉一个版本：

```sh
UV=0.12.13    # 2026-09-14/15 G1 第二、三轮用的版本；用 releases/latest 会随时间漂移，复跑就不是同一份工具
sudo -iu dsherp bash -s <<EOF
set -e; cd /tmp
curl -fsSLO --retry 3 https://github.com/astral-sh/uv/releases/download/$UV/uv-x86_64-unknown-linux-gnu.tar.gz
curl -fsSLO --retry 3 https://github.com/astral-sh/uv/releases/download/$UV/uv-x86_64-unknown-linux-gnu.tar.gz.sha256
sha256sum -c uv-x86_64-unknown-linux-gnu.tar.gz.sha256
mkdir -p ~/.local/bin
tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz --strip-components=1 -C ~/.local/bin uv-x86_64-unknown-linux-gnu/uv
~/.local/bin/uv --version
EOF
```

**为什么是 heredoc 喂 `bash -s`，而不是 `bash -c "多行"`**：`sudo -i` 会把命令参数里的换行转义成 `\<换行>`，
到了 bash 里那是**续行符**，后面几行全变成第一条 `cd` 的参数——2026-09-15 G1 第三轮按当时文档的
`sudo -iu dsherp bash -c "set -e; cd /tmp<换行>…"` 原样执行，立刻 `bash: line 1: cd: too many arguments`
（sudo 1.9.9，与 ssh 无关，交互终端同样失败）。heredoc 走 stdin，不经过 sudo 的参数转义；上面这段
2026-09-15 在一台没有 uv 的 Ubuntu 22.04 上原样跑过，rc=0、`uv 0.12.13`。`--retry 3` 是因为到 GitHub 的
连接会被随机重置。`~/.local/bin` 要自己建，tar 里带一层同名子目录（`--strip-components=1`）；`sudo -iu` 的
登录 shell 会把这个目录加进 PATH。
主机下载慢时在别的机器下载后 scp 过去，**落到运维账号家目录下任意位置即可**（下面统一写作 `~/handover/`），
再用 `sudo install -o dsherp -g dsherp -m 755 ~/handover/uv /home/dsherp/.local/bin/uv`——运维家目录通常 0750，
`sudo -iu dsherp` 进去读不到，所以不要让 dsherp 自己去那里取。

**再建 venv**：

```sh
sudo -iu dsherp bash -c 'cd /opt/dsherp && uv venv --python 3.12.11 .venv && uv pip sync --python .venv/bin/python --require-hashes requirements.lock'
```

这一步要出网：`uv venv --python 3.12.11` 从 GitHub 下 CPython（31 MB），`uv pip sync` 从 PyPI 取 `requirements.lock` 里的 wheel。
**到 GitHub 慢就直接决定了 60 分钟过不过**：2026-09-15 G1 第三轮那台主机到 GitHub 只有 22–80 KiB/s，光下 CPython 就
36 分 36 秒，占了整轮 59 分 32 秒的 61%——同一份文档前一轮 9 分 34 秒跑完，差的全是这一项。所以**开工前先量一下**：

```sh
curl -sSL -o /dev/null -r 0-4194303 -m 60 -w 'github.com http=%{http_code} 取 4 MiB 用 %{time_total}s 速度 %{speed_download} B/s\n' \
  https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-unknown-linux-gnu.tar.gz
```

期望 `http=206`。**4 MiB 超过 20 秒（约 200 KiB/s 以下）就别在线下 CPython**，改走下面的本地镜像
（2026-09-15 那台主机这条探测是 57 秒、73 KiB/s，对应 CPython 全量要 7–24 分钟）。镜像这一步放在 `uv venv` 之前做，
之后 `uv venv --python 3.12.11` 直接用装好的解释器、不再联网：

```sh
# 在一台到 GitHub 快的机器上取这一个文件（uv 0.12.13 对 3.12.11 要的就是它，路径里的 20251007 是 python-build-standalone 的发布号）：
#   https://github.com/astral-sh/python-build-standalone/releases/download/20251007/cpython-3.12.11%2B20251007-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
# 传到主机后按「<镜像根>/<发布号>/<原文件名>」摆好（uv 用这个目录替换 GitHub 的 releases/download 前缀）：
sudo mkdir -p /opt/pymirror/20251007
sudo install -m 644 ~/handover/cpython-3.12.11+20251007-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz /opt/pymirror/20251007/
sudo -iu dsherp bash -c 'UV_PYTHON_INSTALL_MIRROR=file:///opt/pymirror uv python install 3.12.11'   # 2026-09-15 实测 2.8 秒
```

`uv python install -v` 会打印它实际取的 URL（`Downloading file:///opt/pymirror/20251007/…`），取错文件名它会报 404 而不是静默回落到网上。
PyPI 那一半没有离线办法：`uv pip sync --require-hashes` 要的 wheel 只能从 PyPI 取，**pypi.org 不通就别开工**。

```sh
sudo -u dsherp cp infra/env/prod.env.example infra/env/prod.env
sudo -u dsherp "${EDITOR:-nano}" infra/env/prod.env   # 干净主机上 $EDITOR 通常没定义，所以给了默认值；没有交互终端时用 sed 逐项改
sudo -u dsherp chmod 600 infra/env/prod.env
sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod ./bin/dsherp-admin secrets init && DSHERP_ENV=prod ./bin/dsherp-admin doctor'   # 见下：新主机此时 rc=1，属预期
```

`prod.env` 要过一遍的项：

| 项 | 填什么 |
|---|---|
| `DSHERP_BASE_DOMAIN` | 站点域名的后缀。**没有公网域名（局域网、验收 VM）时填 `localhost`**——见「没有公网域名时的入口证书（内置 CA）」，不填它 Caddy 在第 6 步签不出证书，那时才发现就要回头重来 |
| `DSHERP_IMAGE_REGISTRY` | 私有 registry 地址；**用 `--bundle` 交付时填 `local`**（bundle 里的镜像名就是 `local/dsherp-frappe:$TAG`，清单里也写着 `"registry": "local"`） |
| `DSHERP_IMAGE_TAG` | `$TAG` |
| `DSHERP_AGENT_UID` / `GID` | **必须等于 `id -u dsherp` / `id -g dsherp`**：会话目录由 worker 以 dsherp 创建、由容器以这对 uid/gid 写入，代码不做 chown；`useradd --system` 给的 uid 通常小于 1000，示例里的 1000 只是占位 |
| `DSHERP_PLATFORM_SLUG` | 平台站短名。**默认值 `platform` 就是对的**，除非本机已有别的站占了这个名字——内置 CA 一节与本文各处都写作 `platform.$DSHERP_BASE_DOMAIN` |
| `DSHERP_ACME_EMAIL` | Let's Encrypt 的联系邮箱。**内置 CA 下不会被用到**，但字段要有值 |
| `DSHERP_BACKUP_REPOSITORY` / `_SECRETS_REPOSITORY` | 两个 restic 仓库 URL。交接材料里的 `repositories.env` 就是这两行：先 `sed -i '/^DSHERP_BACKUP_/d'` 删掉模板里的占位行，再 `cat ~/handover/repositories.env \| sudo -u dsherp tee -a infra/env/prod.env`——s3 URL 里可能带 `user:pass@`，别让它经过命令行参数（这个文件名本文其余地方不出现，它只是这两个值的载体） |
| `DSHERP_RUNTIME_DIR` / `DSHERP_SECRETS_DIR` | 安装根不是 `/opt/dsherp` 时才改 |
| `DSHERP_HTTP_PORT` / `HTTPS_PORT` | 共用主机才改 |
| 资源项 | **模板里没有这几行，要自己加**，不加就用 compose 的默认值：`DSHERP_DB_BUFFER_POOL`（默认 `1G`）、`DSHERP_GUNICORN_WORKERS`（`2`）、`DSHERP_GUNICORN_THREADS`（`4`）、`DSHERP_PLATFORM_GUNICORN_WORKERS`（`1`）。笔记本规格的演练机才需要调小 |

`secrets init` 在 prod 下生成**五份**控制面密钥，都落在 `$DSHERP_SECRETS_DIR`（默认 `/opt/dsherp/.runtime/control`）、
0600 归 dsherp：`db_root_password`、`platform_admin_password`、`tenant_admin_password`、
`backup_repository_password`、`backup_secrets_repository_password`。它**只生成缺失的那几份，永不覆盖已有文件**；
权限不是 0600 命令直接失败。

**平台站 Desk 怎么登进去**（第 15 节的成员绑定是人工步骤，要用到）：用户名 `Administrator`，口令就是
`platform_admin_password` 的内容——它不经过终端历史，读的时候也别让它经过：

```sh
sudo -iu dsherp cat /opt/dsherp/.runtime/control/platform_admin_password
```

租户站的 Administrator 口令同理在 `tenant_admin_password`，但生产租户站已关掉密码登录（第 5 步），
那把口令只在 bench 内部用。**这五份口令连同两份对象存储身份与 CA，就是下一段说的「必须另存在对象存储之外」的材料。**

**此处 `doctor` 不会是空 findings，这是预期**，rc=1 也是。新主机上会剩两类，按下面的顺序自己消解：

| 阶段 | 预期剩下 |
|---|---|
| 刚 `secrets init` 完（上面那条命令） | `缺少对象存储凭据文件：backup_storage_credentials` / `…_secrets_storage_credentials` 两条 + `凭据轮换：provider host 从未登记过轮换` |
| 放齐下一段那三份材料后**再跑一次 `doctor`** | 只剩 `provider host` 那一条 |
| 第 5、7 步全部装完后 | `provider host` + `oauth-client <slug>` 两条 |

`secrets init && doctor` 写在同一行，而缺的那两份材料要下一段才放进去——**所以 `doctor` 本来就要跑两次**，第一次报缺文件是正常的，不是装错了。

轮换账簿（`<runtime>/rotations.json`）只由**真的换了一次值**的命令写：`provision-tenant --rotate-runtime-key`（第 7 步，写 runtime 那一条）与 §14 的 `dsherp-admin rotate provider|oauth-client`。所以 `provider host` 与 `oauth-client` 这两条在第一次人工轮换之前一直在，这是账簿的本意（「轮换一次即可把它纳入账簿」），不阻断任何步骤。

**此时应当为空的是别的**：缺密钥、权限宽于 0600、回落到开发密钥这三类 finding 一条都不该有；只要剩下的都在上表里就继续。

后文 shell 步骤里用到的 `$DSHERP_PROJECT`、`$DSHERP_BASE_DOMAIN` 等变量来自同一份文件：`set -a; . <(sudo cat infra/env/prod.env); set +a   # 该文件 0600 归 dsherp；直接 source 会 Permission denied 且 set +a 后 $? 仍是 0，变量静默为空`。这与「目录变量不要单独 export」不冲突——source 整个文件得到的值和 compose 读到的完全一致，单独 export 一个不同的值才是问题。


备份相关的密钥与凭据（配置了异地仓库后必需，`doctor` 会检查）：`secrets init` 生成两个仓库口令
`backup_repository_password` 与 `backup_secrets_repository_password`；运维另行放入两份**不同**的对象存储身份
`backup_storage_credentials`、`backup_secrets_storage_credentials`（各两行 `AWS_ACCESS_KEY_ID=`/`AWS_SECRET_ACCESS_KEY=`，
桶策略上分别只能读写数据桶与密钥桶）；私有 CA 放 `backup_storage_ca.pem`（可选，restic 会真正校验）。

**放哪、什么权限、归谁**：三份都放进 `$DSHERP_SECRETS_DIR`（默认 `/opt/dsherp/.runtime/control`），0600、归 dsherp。运维账号家目录通常是 0750，`sudo -u dsherp cp` 读不到，用 `install` 一步到位：

```sh
sudo install -o dsherp -g dsherp -m 600 \
  ~/handover/backup_storage_credentials ~/handover/backup_secrets_storage_credentials \
  ~/handover/backup_storage_ca.pem "$DSHERP_SECRETS_DIR"/
```

**这五份材料必须另存在对象存储之外**（密码库或离线介质）：丢了它们等于丢了全部异地备份，恢复主机靠人带入。验收完成后**把主机上 `~/handover/` 里的这几份删掉**——那份拷贝不是保管场所。

## 4. 起数据面

compose 在解析时就要求每个 `configs:`/`secrets:` 文件存在，所以入口配置要先渲染一次（此时只有 platform 一个站块）：

**先把这一段存成 `~/dsherpenv.sh`**：每开一个新 ssh 会话都要重新 source 一次（新 shell 的 cwd 是运维账号家目录，不是仓库根，所以这里用绝对路径）：

```sh
set -a; . <(sudo cat /opt/dsherp/infra/env/prod.env); set +a   # 同上：0600 归 dsherp，直接 source 会静默失败
admin()   { sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod ./bin/dsherp-admin "$@"' _ "$@"; }
compose() { sudo -iu dsherp bash -c 'cd /opt/dsherp && docker compose --env-file infra/env/prod.env -f infra/compose.prod.yml "$@"' _ "$@"; }
wait_healthy() {   # wait_healthy <期望 healthy 的服务数> [超时秒，默认 300]
  local want=$1 limit=${2:-300} spent=0 n
  while :; do
    n=$(compose ps --format '{{.Status}}' | grep -c '(healthy)')
    [ "$n" -ge "$want" ] && { echo "healthy=$n/$want（用时 ${spent}s）"; return 0; }
    [ "$spent" -ge "$limit" ] && { echo "超时：${limit}s 后只有 $n/$want healthy"; compose ps; return 1; }
    sleep 5; spent=$((spent+5))
  done
}
```

```sh
. ~/dsherpenv.sh
admin render-ingress
compose up -d db redis-cache redis-queue
wait_healthy 3            # 三个服务 healthy 后再继续
```

两个函数把每条命令都以 `dsherp` 身份、在安装根下执行——运维账号直接跑会让 `.runtime/`、密钥目录归错人。

用函数而不是 `$COMPOSE` 变量：zsh 默认不对变量做分词，同一行在 bash 与 zsh 下行为不同。函数体里是 `"$@"` 而不是 `$*`：`$*` 会把带空格的参数拆开，`compose ps --format "{{.Service}} {{.Status}}"` 会报 `no such service: {{.Status}}`（2026-09-14 G1 第二轮实测撞上；两种写法已在同一台主机上各跑一次对照）。

`wait_healthy` 不是 runbook 的判据，是给「healthy 后再继续」一个上限：本文各处都只说「healthy 后再继续」，没说等多久算失败。第 4 步期望 3、第 6 步期望 13。第二轮实测：第 4 步的 `compose up` 本身 45 秒返回（含经镜像站拉 mariadb/redis），**返回不等于 healthy**，三个服务都变绿还要再等约 100 秒；第 6 步从最后一个 `up` 到 13/13 是 17 秒。300 秒的默认上限留了大余量。超时它会打印完整 `compose ps` 并返回非零——**超时就是部署出了问题，不要把上限调大接着等**。

`DSHERP_RUNTIME_DIR` 与 `DSHERP_SECRETS_DIR`（租户清单、Caddyfile、密钥所在目录）**只写在 `infra/env/prod.env` 里，不要 export**：compose 的 `${…}` 插值与 CLI 都读这份文件，这是两者读到同一目录的唯一保证。本地演练时曾因只在 shell 里 export 而漏掉一次，compose 回落到 `../.runtime/control` 用开发密钥初始化了新库，CLI 随即以生产密钥被拒——`doctor` 现在对此报错。

## 5. 开通平台站与第一个租户站

```sh
compose up -d platform-backend backend
wait_healthy 4            # db + 两个 redis + 租户 backend；platform-backend 此时必然 unhealthy，见下
admin provision-platform
admin provision-tenant "$SLUG"
```

**为什么是 4 不是 5**：`platform-backend` 的探针带着平台站的 `Host` 打到 `/api/method/ping` 并要求 200——平台 bench 只服务一个站，
所以探针一路打到站点层（compose 里的注释就是这么写的）。**平台站要到 `provision-platform` 跑完才存在**，在那之前它一定是
`unhealthy`；`provision-platform` 完成后约 1 分钟它自己变绿（2026-09-15 G1 第三轮：15:16:22 开完站，15:17:34 起探针连续通过），
第 6 步的 `wait_healthy 13` 会把它算进去。租户 `backend` 的探针不带 Host、`<500` 即通过，所以没站也绿。
此前这里写的是 `wait_healthy 5`——第三轮原样执行必然跑满 300 秒超时（`4/5 healthy`），而文档又叮嘱「超时就是出了问题」，
一个按文档做事的人会在这里停下排查一个不存在的问题。

两条都会打印一条 JSON：`provision-platform` 六步（`bench` / `site` / `app` / `scheduler` / `member-role` / `site-config`），
`provision-tenant` 十六步（建站、装 App、scheduler、运行身份、站点配置、关密码登录、系统设置、企业、OAuth Client、
Social Login Key、平台 OAuth、租户清单、平台端点、入口、healthcheck）。**每一步都要有状态词**（`created`/`changed:…`/`kept`），
最后一条是 `healthcheck <站名>`——没走到 healthcheck 就是没开完。2026-09-14 G1 第二轮在一台 8 核 VM 上实测
30 秒与 1 分 32 秒；量级不对（比如几秒就返回）就去看 bench 容器的日志。

两条命令都是幂等步骤链：每一步先查现状，中断后重跑不会重复建站或重复装 App。`provision-tenant` 会同时关闭该站的密码登录（Frappe 原生 `disable_user_pass_login`）、启用 scheduler，并把运行凭据端点的来源白名单 `dsherp_agent_sources` 写成 agent 网络与 worker 网络两个网段（后者是宿主 worker 经回环进来时的对端地址；只写 agent 网段会让每个真实运行在 `finish_run` 上被拒）。平台 bench 与租户 bench 各用一个 redis 队列库（`/1` 与 `/0`）：队列名来自 bench 路径，两个 bench 在同一个库里会互相取走对方的作业。

### 租户额度

两条额度都**默认为 0 = 不限**，开站链路不写它们，所以在有人显式设置之前，一个租户身上唯一生效的成本上限是**单次 run 预算**（`dsherp_bridge.run_budget.DOMAINS`）。设置与查看：

```sh
admin tenant-quota "$SLUG"                                          # 只读：看当前生效值
admin tenant-quota "$SLUG" --user-daily-model-calls N --site-monthly-tokens M
```

- `user_daily_model_calls`：每个用户每天的模型调用次数。计数来自 `reserve_model_call`，**在飞的运行也计入且不退还**——限流要的就是这个，用户不能靠挂着运行绕过。
- `site_monthly_tokens`：本站本月已结算的输入+输出 token。只有运行结束才写，所以这是个**下限**，拒绝时的提示会说明在飞的部分尚未计入。
- 边界按站点自己的本地日/月（`frappe.utils.now_datetime`）。`0` 是把某一条关掉的写法，可以随时写回。
- 只给命令不给参数是只读；给了参数就是合并写入 `site_config` 的 `dsherp_quota`，另一条不动。
- 用量对照：`admin usage YYYY-MM`。

**具体取值待裁决**：本文不给推荐值——没有真实用量分布之前，给谁都拦是拦正常工作而不是拦滥用。接入第一个真实租户之前必须先定这两个数，否则那一天没有任何日/月维度的成本上限。

命令输出里的 `runtime_identity` 是该站运行服务身份的 api_key/api_secret，**只在签发那一次出现**：重跑 `provision-tenant` 不会再签发也不会再显示（步骤报 `kept`）；丢了或要换就 `provision-tenant <slug> --rotate-runtime-key`，旧密钥随即作废。写入下一步的 worker profile 后即从终端历史中清除——更稳妥的做法是像演练脚本那样，用一段 Python 把 JSON 输出直接落成 0600 文件，密钥从不经过终端。

## 6. 起入口与出口

```sh
admin render-ingress          # 现在含租户站块
compose up -d scheduler queue platform-scheduler platform-queue   # 定时任务与后台队列
compose up -d frontend platform-frontend agent-egress caddy
wait_healthy 13 && compose ps   # 13 个服务全部 Up 且全部 healthy（scheduler/queue 四个是进程存活探针；platform-backend 到这一步已经因为平台站存在而变绿）
```

本地演练时曾漏起四个 scheduler/queue 服务而 `ps` 看起来"全绿"——`compose ps` 只列出已创建的服务，核对时要数服务数，不只看颜色。13 个是：`agent-egress backend caddy db frontend platform-backend platform-frontend platform-queue platform-scheduler queue redis-cache redis-queue scheduler`。

Caddy 按当前租户清单逐站签发 HTTP-01 证书，因此 `$SLUG.$DSHERP_BASE_DOMAIN` 与 platform 域名必须已解析到本机 80/443。每次增删租户后重跑 `render-ingress` 并 `compose up -d caddy`。

## 7. 装宿主 worker

worker 不进容器：它需要 docker 才能拉起一次性 Runtime 容器。

venv 已在第 3 步建好。worker profile 直接由 CLI 输出落盘，密钥不经过终端也不经过编辑器：

```sh
sudo -iu dsherp bash -c 'cd /opt/dsherp && umask 077 && DSHERP_ENV=prod ./bin/dsherp-admin provision-tenant '"$SLUG"' --rotate-runtime-key | .venv/bin/python infra/write_worker_profile.py'
# {"path": "/opt/dsherp/.runtime/context-worker-sites.json", "site": "…", "state": "added", "sites": ["…"]}
```

`--rotate-runtime-key` 作废该站的旧运行密钥并签发新的，**并把这次轮换记进 `<runtime>/rotations.json`**（§14 那本账簿），
所以装完之后 `doctor` 不再对这个站报「从未登记过轮换」；第一次开站后 profile 也可以这样生成（当时签发的密钥只在那次输出里）。

`write_worker_profile.py` 是**读-合并-写**：它把这个站并进已有 profile，同名站替换那一条，其余站原样保留，再按 worker 自己的 `normalize_profile` 校验一遍才落盘（0600，先写临时文件再 `os.replace`）。**每多开一个租户站就重跑这一条**，并核对输出里的 `sites` 列出了全部租户站——早先这里是一段单元素列表 + `O_TRUNC` 的内联脚本，开第二个租户会把第一个从 profile 里抹掉，那个站从此排队没人领、也不报错。这次的 `provision-tenant` 没签发密钥（步骤报 `runtime-identity: kept`）时脚本直接报错退出，不会写一条没有凭据的记录。

**worker 只在启动时读一次 profile**：改完 profile 要 `sudo systemctl restart dsherp-agent-worker`（第一次装 worker 时还没有 unit，跳过），否则新租户站不会被领取。

profile 形如：`base_url` 是宿主 worker 自己领取运行、发心跳用的地址——它在宿主上，而 compose 的网络全是 internal，所以 backend 只在 `127.0.0.1:8000` 发布一个回环端口给它（`DSHERP_BACKEND_LOOPBACK_PORT` 可改；为此 backend 额外接了一个非 internal 的 `worker` 网络——Docker 不会为只在 internal 网络上的容器发布端口，本地演练时正是在这里断过）；`business_url` 是运行容器在 agent 网络内访问业务站的服务名：

```json
{"slots": 3, "metrics_port": 9109,
 "alert_webhook": "https://…（可选，见下）",
 "sites": [{"site": "<slug>.<base domain>", "base_url": "http://127.0.0.1:8000",
            "business_url": "http://backend:8000",
            "api_key": "<第 5 步输出>", "api_secret": "<第 5 步输出>"}]}
```

`alert_webhook` 是**可选的顶层键**（不是站里的），要告警出主机就自己往这个文件里加一行——`write_worker_profile.py`
是读-合并-写，重跑开站不会把它抹掉。worker 的告警（第 12 节那张表）与 `dsherp-backup-failure@.service` 都投递到它；
不配就只写 journal。**改完要 `sudo systemctl restart dsherp-agent-worker`**：worker 只在启动时读一次 profile。

**provider 凭据是 worker 起动的硬前提**，不是可选项：unit 的 `ExecStart` 带 `--provider-env /opt/dsherp/.env`，两个键任一为空或文件不存在，worker 就在启动时 `ValueError: Explicit provider file must contain API key and base URL` 退出，`Restart=always` 每 10 秒重试一次，单元永远到不了 `active`（2026-09-14 G1 第二轮做过可逆实验：移走该文件即复现，放回即 `active`）。所以它必须和镜像、密钥文件一起交接——见「交接清单」。

```sh
# 交接的就是一个两行的 env 文件时（交接清单里的 provider.env），直接放过去：
sudo install -o dsherp -g dsherp -m 600 ~/handover/provider.env /opt/dsherp/.env
# 只交接了 key 本身时，自己写：
sudo -iu dsherp bash -c 'umask 077 && cat > /opt/dsherp/.env' <<'ENV'
DEEPSEEK_API_KEY=<交接的 key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
ENV
```

容器拿到的 base URL 不是这一个：它由 `DSHERP_AGENT_PROVIDER_BASE_URL` 指向 agent 网络内的出口代理，宿主只用自己的地址做熔断探针。

渲染并启用两个 systemd unit——先是宿主防火墙（第 8 节的安全边界，worker 的 unit `Requires` 它），再是 worker：

```sh
sudo -u dsherp mkdir -p /opt/dsherp/.runtime /opt/dsherp/work
sudo -iu dsherp bash -c 'cd /opt/dsherp && .venv/bin/python -m infra.render_worker_units --root /opt/dsherp --user dsherp --group dsherp --target /opt/dsherp/.runtime/dsherp-agent-worker.service'   # 打印 7 个 unit 路径：worker、firewall，以及 §12 备份用的 backup/backup.timer/backup-drill/backup-drill.timer/backup-failure@
sudo install -m 755 /opt/dsherp/infra/systemd/dsherp-agent-firewall.sh /usr/local/sbin/dsherp-agent-firewall
sudo install -m 644 /opt/dsherp/.runtime/dsherp-agent-firewall.service /etc/systemd/system/dsherp-agent-firewall.service
sudo install -m 644 /opt/dsherp/.runtime/dsherp-agent-worker.service /etc/systemd/system/dsherp-agent-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now dsherp-agent-firewall
sudo /usr/local/sbin/dsherp-agent-firewall check "${DSHERP_PROJECT}_agent"   # ok on br-…
sudo systemctl enable --now dsherp-agent-worker
systemctl status dsherp-agent-worker      # active (running)
systemctl show dsherp-agent-worker -p WatchdogUSec -p WatchdogTimestamp   # WatchdogUSec=1min 且时间戳在刷新，才是看门狗真生效
sudo journalctl -u dsherp-agent-worker -n 20   # 不应有 "Unknown lvalue"/"Failed to parse"：有就是 systemd 太老，沙箱没生效
```

**新装主机的 worker journal 里必然有告警，不是装坏了**：起来 100 毫秒左右就写两条 **critical**——`backup_status_missing`（备份状态文件缺失）与 `backup_stale`（site=<租户站>，备份过期），有时还有一条 `ops_snapshot_stale`（warning）。都是「§12 的备份还没做」的必然结果——备份状态文件不存在、运维快照没人写过。跑完 §12 的 `backup-init` 与第一次备份后它们自行消失；在那之前不要按告警去改配置。（2026-09-14 第二轮看到三条、2026-09-15 第三轮只看到两条 critical，差别在 `ops_snapshot_stale` 出不出现，与判据无关。）

unit 由 dsherp 渲染到自己的目录，再由 root 安装——渲染器写不了 `/etc/systemd/system`。防火墙脚本同样由 root 从仓库复制到 `/usr/local/sbin`：它以 root 运行，不能直接执行服务账号可写的文件。

worker 在生产启动时**先核验宿主隔离再服务**：`/run/dsherp-agent-firewall/<agent 网络名>` 必须存在且记录的网桥等于当前网络的网桥，然后用发布镜像在 agent 网络里起一个探针容器同时连网关的 :22 与 :9——连通、被拒绝（RST）、被重置都说明宿主没挡，路由错误等其它异常算"无法证明"，只有超时才算隔离；三者任一不成立 worker 直接退出并把原因写进 journal（`Restart=always` 会每 10 秒重试，直到防火墙单元就位）。运行中每个 tick 重查记录与网桥，网络 id 变了就重新探针，健康时也每 30 秒重探一次（同一网桥上规则被清掉在 30 秒内被发现），失败期间每个 tick 都探；隔离失效期间心跳照发（用户看到的是排队而不是 503）但**不领取新运行**，`/metrics` 的 `dsherp_host_isolation_ok` 为 0，告警 `host_isolation_failed`（critical）。没有 systemd 的本机演练因此起不了生产形态的 worker，这是有意为之：隔离只在真实 Linux 主机上成立。

unit 为 `Type=notify` + `WatchdogSec=60s`：worker 每轮 tick 回喂看门狗，60 秒没回喂就被 SIGABRT 杀掉、`RestartSec=10` 后拉起，实测从被杀到重新 `active` 约 15 秒，期间隔离核验自动重跑、不需要人工介入；`ProtectSystem=strict` 下它只能写 `.runtime` 与 `work` 两个目录。

**看门狗重启不等于 worker 有问题**：它只说明「60 秒没人回喂」，而整台机器停摆时同样没人回喂。2026-09-14 G1 第二轮在一台 VirtualBox 客体上 2 小时内触发 6 次，其中 4 次能证明是**整机停摆**——`systemd-journald`/`resolved`/`networkd` 这些自带 3 分钟看门狗的核心单元在同一秒一起被杀、内核报 `rcu_sched kthread starved`、`clocksource: Long readout interval … cs_nsec: 267782607776`（时钟源 267.8 秒没被读到）。分辨方法：

```sh
sudo journalctl -u dsherp-agent-worker --no-pager | grep "Watchdog timeout"     # worker 被杀的时刻
sudo journalctl --no-pager --since <那个时刻前 1 分钟> | grep "Watchdog timeout"  # 同一秒还有谁被杀
sudo journalctl -k --no-pager | grep -E "clocksource: Long readout|rcu.*starved" # 内核有没有量到停摆
```

只有 worker 一个被杀、内核没有对应消息，才该去查 worker 自己那一轮 tick；一群系统单元陪葬就是宿主的问题，先修宿主。**G10 的 24 小时浸泡判据是「无卡 Running」，在一台会分钟级停摆的宿主上跑，读数分不清是系统的问题还是宿主的**——开浸泡之前先确认停摆不再发生。

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
| 全部 healthcheck 绿 | `wait_healthy 13 && compose ps` | **13 个**长驻服务全部 `healthy`（服务名见第 6 步） |
| 站点可达且走 TLS | `curl -sI https://$SLUG.$DSHERP_BASE_DOMAIN/login` | 200，含 `Content-Security-Policy`，`connect-src 'self'`。**内置 CA 的环境下这一条与下面两条都要带 `--cacert`**，见「没有公网域名时的入口证书」 |
| 密码登录已关 | `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$SLUG.$DSHERP_BASE_DOMAIN/api/method/login -d 'usr=x&pwd=y'` | `401`；不加 `-o /dev/null` 时正文是 `{"exc_type":"AuthenticationError",…"Login with username and password is not allowed."}` |
| 登录页不显示密码表单 | `curl -s https://$SLUG.$DSHERP_BASE_DOMAIN/login \| grep -c 'type="password"'` | `0`（页面上只有一个 `login_token` 验证码输入框）。这是 HTML 层的证据，不等于浏览器渲染后一定看不到 |
| 容器不出网、非 root、到不了宿主 | `sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod PYTHONPATH=. .venv/bin/python infra/probe_agent_boundary.py'` | `failures: {}`（用真实运行容器参数在 agent 网络里探测公网、代理、业务站、平台、库、**宿主网关 :22**、控制面文件、能力集） |
| 宿主防火墙已应用且对应当前网桥 | `systemctl is-active dsherp-agent-firewall && sudo /usr/local/sbin/dsherp-agent-firewall check "${DSHERP_PROJECT}_agent"` | `active`，`ok on br-…`；`probe_agent_boundary.py` 的 `host_gateway_ssh`/`host_gateway_loopback_port` 均为 `False` |
| worker 自己核验过隔离 | `curl -s 127.0.0.1:9109/metrics \| grep dsherp_host_isolation_ok` | `1`；journal 里没有 `host_isolation_failed` |
| worker 存活 | `systemctl is-active dsherp-agent-worker` | `active` |
| **每个租户站都在被看** | `curl -s 127.0.0.1:9109/metrics \| grep dsherp_queue_depth` | 每个租户站各一行 `dsherp_queue_depth{site="…"}`；少一行就是那个站没进 worker profile（第 7 步），它排队没人领也不会告警 |
| 发布可核验 | `cat "$DSHERP_RUNTIME_DIR/manifests/$DSHERP_IMAGE_TAG.json"` | tag、提交、基底 digest、架构、两个镜像的 `id` 与 `diff_ids` 齐全；两个 bench 容器跑的就是清单里那个 frappe 镜像——`sudo docker inspect --format '{{.Name}} {{.Image}}' dsherp-backend-1 dsherp-platform-backend-1`
的两个值都应等于清单 `images` 里 frappe 那一项的 `id`（容器名是 `<DSHERP_PROJECT>-<服务>-1`）。**不要用 `infra/releases/$TAG.json`**：清单只能在 tag 之后提交，按 tag 取源码的目标主机上没有这个文件（第 2 步「清单必须随镜像一起交付」一段已说明） |

### 第一条真实运行（冒烟）

§9 的表只证明「都起来了」，没有一条运行走过 **成员登录 → 提交 → worker 领取 → 运行容器 → 模型 → 工具读取 → 结算**。
这条链只有在真机上跑一次才知道通不通——2026-09-15 第一次跑就抄出两个只在真机暴露的缺陷（平台站没时区、活跑的容器被当孤儿），
所以把它写成验收的一部分。**生产形态下没有任何无头的提交入口**：业务 API key 只借给平台、grant 只存在于浏览器会话，
下面这条路就是成员真实走的那条，用 curl 走一遍。

**前置：一个能登进业务站的成员。** 这一段暴露了「接真实租户」第一天就会撞上的四件事，按顺序：

1. **业务站上的业务用户**——本仓库**没有**建它的 CLI（`provision-tenant` 只建运行身份 `runtime@<站>`）。目前只能经 `admin.Bench` 跑一段脚本
   （口令与密钥都只走 stdin）：
   ```sh
   sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod .venv/bin/python -' <<'PY'
   from dsherp import admin, deploy_env
   r=deploy_env.settings(); t=admin.Bench(r,'tenant')
   print(t.python('acme.localhost',"""
   user='smoke@acme.localhost'
   if not frappe.db.exists('User',user):
       frappe.get_doc({'doctype':'User','email':user,'first_name':'冒烟业务用户','enabled':1,'user_type':'System User',
           'send_welcome_email':0,'language':'zh','time_zone':'Asia/Shanghai',
           'roles':[{'role':'Sales User'}]}).insert()   # 要读什么 DocType 就给对应的 ERPNext 角色；System Manager 并不自带 Customer 读权限
   frappe.db.commit();print('ok')
   """))
   PY
   ```
2. **平台成员**（平台站允许口令登录）：角色必须是 `DSHERP Member`（租户 OAuth Client 的 `allowed_roles`）。同样经 `admin.Bench` 建，
   口令随机生成、落 0600 文件、用 `doc.new_password=` 写入。
3. **成员绑定 DS Membership**（第 15 节说的人工步骤，在平台站 Desk 建，或经 `admin.Bench`）：`enterprise=<slug>`、`platform_user=<成员>`、
   `erp_user=<业务用户>`、`enabled=1`。**`api_key`/`api_secret` 是必填但此时没有值，先填占位串**；然后：
   ```sh
   admin credentials acme.localhost --issue smoke@acme.localhost     # 在业务站签发首个 12 小时窗口并交给平台（报告里 api_key 脱敏）
   ```
   **不做这一步，第一次 SSO 必败**：平台校验成员身份时先用手里的凭据去业务站证明一次，占位串证明不了，退而要求「上次证明过的
   业务用户」，而新绑定从没证明过——`PermissionError`。
4. **业务站的 DocType 策略**：新开通的租户站 `DS Doctype Policy` 是**空的**，agent 什么都读不了（会得到 `error_class=permission` 并如实
   报告「无法列出」）。至少给要读的 DocType 一行，且 **`change_reason` 必填、每次修改都要新的原因**（裁决 #3）：
   ```sh
   sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod .venv/bin/python -' <<'PY'
   from dsherp import admin, deploy_env
   r=deploy_env.settings(); t=admin.Bench(r,'tenant')
   print(t.python('acme.localhost',"""
   frappe.get_doc({'doctype':'DS Doctype Policy','target_doctype':'Customer','enabled':1,'allow_read':1,
       'allow_create':0,'allow_update':0,'allow_submit':0,'allow_cancel':0,'allow_fill':0,
       'change_reason':'冒烟：只读 Customer'}).insert(ignore_permissions=True)
   frappe.db.commit();print('ok')
   """))
   PY
   ```
   策略放行之后**原生 ERP 权限仍是最终裁决**——业务用户没有 `Sales User` 之类角色时，agent 会得到 `does not have doctype access via role permission`。

**走 SSO 并提交**（以运维账号在主机上，口令/CSRF 都经 `@file`，cookie 分主机存）：

```sh
CA=/tmp/caddy-root.crt; PJ=platform.cookies; AJ=acme.cookies
curl -s --cacert $CA -c $PJ -b $PJ -X POST https://platform.localhost/api/method/login \
     --data-urlencode usr=<成员> --data-urlencode "pwd@member.pw"                        # {"message":"Logged In"}
LOC=$(curl -s --cacert $CA -c $AJ -b $AJ -o /dev/null -w '%{redirect_url}' https://acme.localhost/api/method/dsherp_bridge.sso.start)
curl -s --cacert $CA -c $PJ -b $PJ -o auth.html "$LOC"                                    # OAuth 同意页（skip_authorization=0）
# 从 auth.html 取 <form method="POST" action="…approve?…"> 的 action（要 html.unescape）与 hidden csrf_token，各存一个文件
CB=$(curl -s --cacert $CA -c $PJ -b $PJ -o /dev/null -w '%{redirect_url}' -X POST "https://platform.localhost$(cat approve.url)" --data-urlencode "csrf_token@approve.csrf")
curl -s --cacert $CA -c $AJ -b $AJ -o /dev/null -w 'callback http=%{http_code} → %{redirect_url}\n' "$CB"   # 302 → /desk/dsherp-agent
curl -s --cacert $CA -b $AJ -X POST https://acme.localhost/api/method/frappe.auth.get_logged_user           # {"message":"smoke@acme.localhost"}
curl -s --cacert $CA -b $AJ -c $AJ https://acme.localhost/desk/dsherp-agent -o desk.html                   # 取 frappe.csrf_token 存 csrf.txt
printf 'X-Frappe-CSRF-Token: %s\n' "$(cat csrf.txt)" > hdr.txt
curl -s --cacert $CA -b $AJ -c $AJ -H @hdr.txt -X POST https://acme.localhost/api/method/dsherp_bridge.context_api.send_message \
     --data-urlencode 'question=系统里现在有哪些客户？请列出客户名称。' \
     --data-urlencode 'context={"schema_version":1,"page_type":"unknown","route":[]}' \
     --data-urlencode "request_id=smoke-$(date +%s)" --data-urlencode 'domain=query'   # 返回会话，message.id 是会话 id，active_run 是 run id
# 轮询 …context_api.get_session?session_id=<id> 直到 active_run 为空；最后一条 message 的 status/answer/sources 就是结果
```

**期望**：最后一条 `status: Succeeded` 且 `sources` 非空（读到了记录）；worker journal 依次 `claimed → container_finished (Succeeded) → finish`；
`curl -s 127.0.0.1:9109/metrics` 里 `dsherp_provider_call_failures_total 0`、`dsherp_orphan_containers 0`。
**问题必须让 agent 去读记录**：「只回答一个词」这种问题模型会不调工具直接答，服务端按设计拒绝这种成功
（`成功结果必须包含实际读取或服务端记录的工具失败`，HTTP 417），运行落 `Failed`——那不是管线坏了。
2026-09-15 实测：四条运行共 12 次模型调用（单条最多 5，预算 11），第四条 41 秒 `Succeeded`，答案列出了那一个客户并附 `sources`。

## 10. 升级与回滚（G2）

**从这一节起，凡是写作 `./bin/dsherp-admin …` 的裸命令，都按第 4 步的 `admin()` 包装执行**
（`. ~/dsherpenv.sh` 之后用 `admin <子命令>`）：`prod.env` 是 0600 归 dsherp，运维账号直接跑读不到它；
加 `sudo` 跑又会把 `.runtime/` 下的产物写成 root 所有——正是「账号模型」一节点名的那类静默错误。
下文为了让每条命令自己读得懂，仍然把子命令写全。本节另外要自己赋两个变量：

```sh
NEW_TAG=v0.4.1 ; OLD_TAG=v0.4.0        # 取值见发布记录 <runtime>/releases/current.json
```

发布前提：没有运行在飞（`release` 会检查每站的 Queued/Running/Cancelling 计数，非零即拒绝），所以先停 worker：`sudo systemctl stop dsherp-agent-worker`。发布期间每个站被置为维护模式并暂停调度（`maintenance_mode`/`pause_scheduler` 写进 site_config，结束时恢复原值），用户在此期间看到 503。

升级前先把主机上的源码树换到 `$NEW_TAG`（按第 1 步的取源方式重新导出或 checkout，并重跑第 3 步的 `.venv` 同步）：`bin/dsherp-admin`、compose 文件和下面的预检都来自这棵树，不换就是在用旧 tag 的工具发布新 tag。再按第 2 步把新 tag 的清单交付到主机；**第一次发布**还要把 `--from` 那个旧 tag 的清单一并交付——首次发布没有 `current.json`，它的回滚只能以旧 tag 清单为锚，`release` 会在改动任何站点之前核对旧清单可用，拿不到就拒绝发布。

```sh
sudo -u dsherp "${EDITOR:-nano}" infra/env/prod.env   # DSHERP_IMAGE_TAG 改为新 tag；该文件 0600 归 dsherp，运维账号直接改不动
compose pull && compose up -d       # 两个 bench 都换到新镜像
# 用 --bundle 交付（DSHERP_IMAGE_REGISTRY=local）时没有 registry 可 pull：改成先 `sudo docker load -i dsherp-$NEW_TAG.tar` 再 `compose up -d`
DSHERP_ENV=prod ./bin/dsherp-admin release "$NEW_TAG" --from "$OLD_TAG"   # 第一次发布必须给 --from；之后从 current.json 取
sudo systemctl start dsherp-agent-worker
```

2026-09-15 第一次真机发布（rc4→rc5）两站各 36 秒 / 19 秒走完全部六步；平台站被判「1 条未声明差异」保持维护：`System Settings.setup_complete`
0→1——那是 `migrate` 最后一步「Updating installed applications」按「站上有没有非管理员用户」派生的（平台站在上一次发布之后才有了
第一个成员），Frappe 自己的行为、不属于任何 patch。rc6 起把这一个字段登记为 migrate 自带的声明；其它任何字段仍算未声明。

退出码：0 = 各站数据与升级前一致（或差异都被本次执行的 patch 声明），站点已重新开放；1 = 有未声明差异，**有差异的站保持维护模式**，人核对报告后要么 `rollback`，要么确认接受再 `resume-site <站>`；2 = 中途失败，失败的站保持维护模式并有带 `failed` 的部分报告。

**接受未声明差异后（`resume-site`）`current.json` 不会被写**：发布记录只在 clean 时落 `current.json`，所以下一次 `release` 仍要给
`--from <这次的 tag>`。另外 rc5 及之前 `resume-site` 解除维护后站点可能继续回 503：Frappe 把维护期间被请求过的访客页
（`/login` 的「Updating」）缓存了，解除标志不会驱逐它；rc6 起解除维护时顺带 `clear-cache`，之前的版本手工
`bench --site <站> clear-cache` 即可。

`release` 先做预检：两个异地仓库已配置（生产未配置即拒绝，见第 12 节）、`prod.env` 的 tag 就是要发布的 tag、两个 bench 服务各恰好一个运行容器（多于一个拒绝，不会只查第一个）且**运行容器**镜像全名就是该 tag 的发布镜像（读容器而不是读环境文件）、该 tag 的发布清单可用且两个容器的镜像 id 与清单记录一致（清单缺失、读不出、tag 不符、没有该镜像的记录或 id 不是 `sha256:` 开头的非空串，都在改动任何站点之前拒绝——不会退化成"没有预期 id 就不核对"；清单查找顺序见第 2 步，`--manifest FILE` 可直接指定）、`--from` 与 `current.json` 的记录一致（有记录时给出不同的 `--from` 会被拒绝：记录对就不要给，记录错就先改对或删掉）、`current.json` 里的镜像记录完整（否则将来的回滚用不了，现在就拒绝）或——没有 `current.json` 时——旧 tag 的清单可用、每站没有在飞运行、这个 tag 还没有升级前基线（基线只写一次，重来要换 tag 或先 `forget-release`，后者只删发布记录目录下的合法子目录）。然后写发布记录（新旧 tag、两个容器的镜像与镜像 id），再对每个站（租户站与平台站）按序：静默 → `bench backup --with-files` → 把四件套备份集复制到 `/home/frappe/frappe-bench/archived/releases/<tag>/<站>/`（`tenant-archive`/`platform-archive` 卷；Frappe 自己会在 23 小时后清掉 `private/backups`）→ 升级前快照 → `bench migrate` → 升级后快照（按升级前的列集求哈希）→ 从 Patch Log 算出本次实际执行的 patch，只采纳它们声明的预期变化 → 比对 → 只有干净才恢复站点标志。全部干净后把 `current.json` 记为新 tag。报告在 `.runtime/releases/release-<tag>-<时间戳>.json`（`release-<tag>.json` 是最新一份），快照与备份记录在 `.runtime/releases/<tag>/{release.json,<站>/before.json,after.json,backup.json}`。

比对口径：站上每个 DocType 按元数据归入且只归入一桶——**严格**（erpnext 与两个 dsherp App 的全部 DocType、自定义 DocType、联系人与身份表、租户写的权限与流程：Role、Custom DocPerm、Workflow 族、非标准的 Notification/Report/Print Format/Web Form、Client/Server Script 等：逐行逐字段）、**日志**（Comment/Version/Deleted Document/Communication/Activity Log：只比哈希，允许新增）、**排除**（Frappe 自己的元数据、缓存与技术日志，每次 migrate 都会改写）；租户写过的元数据按行分区（`custom=1` 的 DocType 及其字段、`is_system_generated=0` 的 Custom Field/Property Setter、`is_standard` 为否的报表/通知/打印格式、User 与 Role Profile 下的 Has Role）。新出现的表和列是 schema 变化，报告为信息不算差异；消失的表和列、行的增删改、单值文档任何设置值的变化（含首次落库）都是差异，除非本次执行的某个 patch 在自己的模块里用 `EXPECTED_CHANGES = [{'doctype': ..., 'fields': [...] 或 ['*'], 'rows': 'existing'|'inserted'|'deleted'|'any'}]` 声明过——只留哈希的大表只能被 `['*']` 整行声明放行。任何一张表读不出来就中止，不会带着"部分快照"下结论。原生 SQL 分页读取，密码列与密钥类单值只存摘要；快照行数超过上限（默认 100 万）也中止。

任何阶段失败（备份、快照、migrate、声明读取、比对）都让该站保持维护模式，报告带 `failed`，命令退出码 2；此时按下面回滚。

```sh
# 回滚：改回旧 tag、起旧镜像，再撤销那次发布——它会恢复 release 归档的升级前备份并与升级前快照比对
sudo -u dsherp "${EDITOR:-nano}" infra/env/prod.env   # DSHERP_IMAGE_TAG 改回发布记录里的 previous_tag
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
# 口令的命令替换必须发生在 dsherp 的 shell 里，所以整条经 sudo -iu dsherp 执行：
sudo -iu dsherp bash -c 'cd /opt/dsherp && docker compose --env-file infra/env/prod.env \
  -f infra/compose.prod.yml exec -T backend bench --site "'"$SLUG.$DSHERP_BASE_DOMAIN"'" restore \
  "<archive>/private/backups/<时间戳>-…-database.sql.gz" \
  --with-public-files "<archive>/private/backups/<时间戳>-…-files.tar" \
  --with-private-files "<archive>/private/backups/<时间戳>-…-private-files.tar" \
  --db-root-username root \
  --db-root-password "$(cat '"$DSHERP_SECRETS_DIR"'/db_root_password)" --force'
admin provision-tenant "$SLUG"      # 幂等重跑：核对运行身份、站点配置与 healthcheck
```

**为什么要套 `sudo -iu dsherp`**：`$(cat …/db_root_password)` 在**调用它的那个 shell** 里展开，而这个文件 0600 归 dsherp。
以运维账号跑，命令替换会静默得到空串，`bench restore` 拿着空口令连库报认证失败，看起来像库坏了。
**并且这条命令会把库 root 口令放进容器的 argv**——本文其它地方的口令一律只走 stdin，这是唯一的例外，
因为 `bench restore` 没有别的入口。`release`/`rollback`/`restore-site` 走的是 `frappe.commands.site._restore` 的
stdin 路径，不经过 argv：**能用第 12 节的 `restore-site` 就别用这一条。**

归档目录里还有整站的 `site_config.json`（含库口令与加密密钥），它与库转储同目录同权限——这是计划 4 的异地备份要分开存放的对象，归档不能原样同步出主机。

## 12. 备份与容灾

两个 restic 服务带 `profiles: [ops]`，不随 `compose up` 常驻，只由 `dsherp-admin backup-sync`/`restore-drill` 以 `compose run --rm` 拉起，所以第 6 步的服务清单与健康检查不包含它们。

**判据**：任一站点（平台站与全部租户站）在任一时刻都有一份 24 小时内的异地备份可恢复；RTO 8 小时。年龄按备份**数据本身的时点**算，不是上传完成时刻。

**验收行**（第 9 节的表之外，这一节自己的）。**跑之前本节要先做完三件事**：装好两个定时单元、`backup-init`、
以及**至少成功跑过一次备份**——`dsherp_backup_*` 那三个指标读的是 `<runtime>/backups/status.json`，没备份过就没有这个文件，
三条全红是必然的（第 7 步那几条 `backup_status_missing`/`backup_stale` 告警也是同一个原因）。手动跑第一次：

```sh
DSHERP_ENV=prod ./bin/dsherp-admin backup --sync      # 不等定时器，立刻产一个集并同步到异地
```


```sh
systemctl list-timers 'dsherp-backup*'          # 两个定时器都在，下一次触发时间合理
DSHERP_ENV=prod ./bin/dsherp-admin doctor       # 五份备份密钥材料齐全且 0600
curl -s 127.0.0.1:9109/metrics | grep dsherp_backup_
```

`dsherp_backup_sites_rpo_ok` 应等于 `dsherp_backup_sites_expected`，`dsherp_backup_offsite_oldest_hours` 小于 24 且不为 -1，`dsherp_backup_last_run_ok` 为 1。

**周期**：`dsherp-backup.timer` 每天 02:00 与 14:00 跑 `dsherp-admin backup --sync`；`dsherp-backup-drill.timer` 每周日 04:00 跑 `dsherp-admin restore-drill`。两个 `OnCalendar` 都把 `Asia/Shanghai` 写死在 unit 里（`infra/render_worker_units.py`），**与主机时区无关**——`systemctl list-timers` 显示的是主机本地时间，对不上不是配错了。两者失败时 systemd 触发 `dsherp-backup-failure@.service`，它直接写 journal 并投递 profile 里的 `alert_webhook`——worker 停止时这条路仍在。装单元：

第 7 步那一次渲染已经把这五个备份单元写进 `.runtime/` 了（渲染器一次出 7 个文件），所以这条重渲只是
让本节自成一步；两种参数写法对备份单元的产物相同，`--target/--group` 只影响 worker 那一个文件。

```sh
sudo -iu dsherp bash -c 'cd /opt/dsherp && DSHERP_ENV=prod .venv/bin/python -m infra.render_worker_units --root /opt/dsherp --user dsherp'
sudo install -m 644 /opt/dsherp/.runtime/dsherp-backup*.service /opt/dsherp/.runtime/dsherp-backup*.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dsherp-backup.timer dsherp-backup-drill.timer
```

**一次性初始化**：`DSHERP_ENV=prod ./bin/dsherp-admin backup-init`（幂等，两个仓库都建好后报 `kept`）。报「仓库已存在，但本机的
`backup_repository_password` 打不开它」时，远端是**别的口令**建的：恢复主机要先带入原主机的那份口令（见 G3 那段的顺序）；
如果远端只是以前试手留下的空仓库（只有 `config` 与 `keys/`、没有快照），清掉再 `backup-init`——2026-09-15 第一次就是这种情形。

同步容器要读两种属主不同的文件：备份集是 bench 写的（uid 1000，0700），仓库口令是 `secrets init` 以服务账号写的（0600，第一台
生产主机上是 997），而 compose 把 secret 按宿主属主原样 bind 进容器、不认 uid/gid/mode（v5.5.1 实测）。**没有一个非 root uid
能同时读到两者**：`v0.4.0-rc4` 以 agent uid 跑读不到集，rc5 改成 1000 又读不到口令。rc6 起以 root + 仅 `DAC_READ_SEARCH`
（只读越权）跑，其余能力全丢、根文件系统只读、缓存与临时目录在 tmpfs——它是前置表「容器不以 root 运行」的第二个例外，与 caddy 一样
点名记在这里。

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

**异机恢复（G3）**：新主机上的步骤与第 1–9 步**不一样**，两处差别都会让人做错，写清楚：

1. **只做第 1–4 步，跳过第 5 步的开站**。`restore-site` 的契约是目标站在本机必须不存在，而且它**自己会按正常路径开站**
   （开站 → 恢复 → 还回加密密钥 → 与备份窗口内的快照比对 → 再开一次以派生本机配置）。先跑 `provision-platform` /
   `provision-tenant` 会让每一个要恢复的站都被拒。第 6 步的入口与第 7 步的 worker 放到恢复完成之后再做。
2. **五份密钥材料要在第 3 步 `secrets init` 之前就位**。`secrets init` 只生成缺失的那几份、**永不覆盖已有文件**，
   所以先跑它就会给你两把全新的仓库口令，而远端仓库认的是旧的那两把——之后再把材料放进去也不会被采纳，
   `backup-init` 报不出 `kept`，要到这一步才发现。正确顺序是：建好 `prod.env` → 把五份材料 `install` 进
   `$DSHERP_SECRETS_DIR`（0600 归 dsherp）→ 才 `secrets init`（它会把五份都报成 `kept`）→ `doctor`。
3. 恢复前 `prod.env` 的 `DSHERP_IMAGE_TAG` 必须是**备份集记录的那个 tag**：`restore-site` 会比对本机 bench 容器的镜像 id
   与集里记的 `image_id`，不一致直接拒绝并告诉你该用哪个 tag。
4. 本机记录里没有异地已有的集时先 `backup-sync`，它会从已核验的远端清单收养并补齐镜像身份；没有镜像身份的集一律拒绝恢复。

然后逐站：

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
（只能走登记的受控脱敏迁移）、已生成的异地备份按保留策略到期淘汰。平台授权令牌不在任何行里：运行期间
存于站点缓存、运行结束即删；配置交接期间也只存于缓存、随交接窗口（默认 2 小时，
`configuration_transfer.TRANSFER_WINDOW_SECONDS`）到期即失效。两者删除时都无需处理。

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

（本节与第 12、13 节的示例站名写作 `acme.tenant.example.com` 只是**举例**；本文这台机器上是
`$SLUG.$DSHERP_BASE_DOMAIN`，没有公网域名时就是 `acme.localhost`。）

三类长期凭据用 `rotate` 轮换，账簿在 `<runtime>/rotations.json`（只记类别、目标、第几次、生效时间与
值的指纹，不记值）。第 7 步的 `provision-tenant <slug> --rotate-runtime-key` 换的是同一把 runtime 密钥，
它也记这一本账（所以装完之后 `doctor` 不会再对那个站报「从未登记过轮换」）：

```sh
DSHERP_ENV=prod ./bin/dsherp-admin rotate provider --file /opt/dsherp/.env < new-key.txt
DSHERP_ENV=prod ./bin/dsherp-admin rotate runtime "$SLUG.$DSHERP_BASE_DOMAIN" \
    --profile /opt/dsherp/.runtime/context-worker-sites.json
DSHERP_ENV=prod ./bin/dsherp-admin rotate oauth-client "$SLUG"
```

- `provider`：新 key 只从标准输入读，只改 worker 单元读的那个 `.env` 里的那一行；**改完重启 worker 单元**。
- `runtime`：**必须给站点名**（没有默认值：平台站从来没有运行身份，拿它当默认只会报一句让人以为站没开通的错）；
  按该站 `site_config` 声明的运行身份轮换；必须给交付目的地——`--profile`（可多次，所有
  文件先校验后签发）或 `--print-secret`（打印一次并留一份 0600 副本文件，用后删除）；签发后的密钥先写
  到 `<runtime>/rotations/runtime-<站点>-<时间>.json` 再写 profile，写入失败时错误信息指明该文件，
  全部写成功后自动删除。改完重启 worker 单元。
- `oauth-client`：平台与业务站两侧一起换；进行中的登录会失败一次，重新登录即可，已建立的会话不受影响。

`doctor` 会把从未登记轮换和超过窗口的目标列出来（provider/runtime 90 天、oauth-client 180 天）。
**runtime 只列租户站**：平台站没有运行身份，列它就是一条任何命令都清不掉的 finding。轮换
不会作废原生会话：`runtime_revision` 不计入 provider key。

## 15. 质量门禁与 CI

### 什么在哪跑

| 工作流 | 触发 | 跑什么 | 计入 G9 |
|---|---|---|---|
| `ci.yml` | 每次 PR 与合入 main | ruff（F、E9）、非集成 pytest（含迁移守卫的真实调用）、vitest、`dist` 一致性、Node runtime | 是 |
| `nightly.yml` | 02:00 Asia/Shanghai，可手动 | `dev_stack.py up --provision` 从零拉起四站 → 全部集成 → Frappe 原生测试 → 工件 → 总是拆栈 | 是 |
| `supply-chain.yml` | 每周一 03:00，可手动 | 构建两个发布镜像、syft SBOM、grype high 门 | **否**（允许红） |

**任何工作流都不配置重试**（`retries`、`--reruns`、失败后自动 re-run 都不允许）。唯一的退避
循环是 Docker Hub 拉镜像——那是取件，不是测试。

### 怎么读一个红的 nightly

先分三类，不要先改超时：

1. **环境**。看 `work/dev-stack-status.json` 与 `work/compose.log`。容器退出码 137 是被内存
   上限杀掉的，上限就是 `infra/compose.validation.yml` 里该服务的 `mem_limit`；数据库是
   `restart: "no"`，被杀之后不会自愈，后面所有用例都会错在夹具上。
2. **夹具**。看 `work/junit-integration.xml` 里错在 setup/teardown 的条目，以及台账
   `work/dev-stack-ledger.json`。集成套件的残留登记见
   [计划 5 证据](quality-gates-evidence.md)：清不掉的条目会留在
   `.runtime/integration-residue.json` 里，下一次会话开头再清；清不掉就挡住运行，等人处理。
3. **真实回归**。以上都排除之后才是。

红了当天修或删那条用例（记提交号与 run 链接），不许重试到绿。不能复现的失败按复盘 Q3 处理：
记录 run 链接、日志与现场数据，开缺陷项并留在证据文档的"未闭合"里，不得"未复现即结案"。

### 本地重跑一次 nightly 做的事

```sh
.venv/bin/python infra/dev_stack.py up --provision      # 从零；本机含 agent 运行时卷，约 15–25 分钟
.venv/bin/python -m pytest tests/integration -m integration -q --timeout 600
.venv/bin/python infra/dev_stack.py native-tests --out work/native
.venv/bin/python infra/dev_stack.py down --volumes      # 会销毁合成数据；控制面口令保留
```

开发栈已经在跑时，四条本机门：

```sh
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
(cd frontend && npm test)
node --test runtime/*.test.cjs
```

跑进程内 `worker.run_once` 的集成测试前要停常驻 worker
（`launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16`），`scheduled` profile 也不能在跑。

### 原生测试

两个一次性测试站 `dsherp-test.localhost`（backend）与 `dsherp-platform-test.localhost`
（platform-backend）只给 `frappe_app/*/tests` 用，nginx 不暴露（对两站都回 421）。
`up --provision` 的最后一步会建它们。

成败不由 `bench` 的退出码单独判定：站点没开 `allow_tests` 时它打印
"Testing is disabled for the site!" 并返回 0，什么也没跑。判据是退出码非零即失败，
**且**必须出现 runner 自己的 `Running N <category> tests for <app>` 且 N>0
（`dsherp/native_tests.py`）。镜像里没有 `xmlrunner`，`--junit-xml-output` 不写文件。

### 改了 DocType JSON 或 patch 之后

六站都要 migrate，然后重启三个后端（gunicorn 缓存模块；`bench run-tests` 是另一个进程，不缓存）：

```sh
for s in backend:dsherp-validation.localhost backend:dsherp-daily.localhost backend:dsherp-test.localhost \
         beta-backend:dsherp-beta.localhost platform-backend:dsherp-platform.localhost \
         platform-backend:dsherp-platform-test.localhost; do
  docker compose -f infra/compose.validation.yml exec -T ${s%%:*} bench --site ${s##*:} migrate; done
docker restart dsherp-validation-backend-1 dsherp-validation-beta-backend-1 dsherp-validation-platform-backend-1
```

改 DocType JSON 的提交必须在该 App 的 `patches.txt` 里加一行，或在提交信息里写
`no-patch: <原因>`（`infra/check_doctype_patches.py` 检查）。

### G9 的计算口径

只对 `ci.yml` 与 `nightly.yml` 计算连续 30 天绿，从第一个含原生测试步的绿色 nightly 起算
（日期与 run 链接记在[计划 5 证据](quality-gates-evidence.md)）。周报型的
`supply-chain.yml` 不代表代码状态，不计入。

## 没有公网域名时的入口证书（内置 CA）

ACME HTTP-01 要求 Let's Encrypt 能从公网打到本机 80 端口。**局域网或无公网域名的主机签不出证书**，
此时走 Caddy 的内置 CA：把 `DSHERP_BASE_DOMAIN` 设为 `localhost`，站点即 `platform.localhost` 与
`<slug>.localhost`。Caddyfile 模板对 `.localhost` 主机名自动回落到内置 CA（无需另加开关），
签发者为 `Caddy Local Authority - ECC Intermediate`，叶证书有效期 12 小时、自动续。

后果与核验方式：系统信任库里没有这个根，所以 §9 验收表里三条 `curl` 原样执行都会
**rc=60**（`unable to get local issuer certificate`）——第 2 条（`-sI`）、第 3 条（POST login）、
第 4 条（登录页 HTML）都要带 `--cacert`。先取根证书：

```sh
sudo docker cp dsherp-caddy-1:/data/caddy/pki/authorities/local/root.crt /tmp/caddy-root.crt
sudo chmod a+r /tmp/caddy-root.crt     # 容器内它是 root:0600，docker cp 保留该模式；不加这一行下面的 curl 是 rc=77
```

`chmod` 这一行不是可有可无：容器里的 `root.crt` 是 `-rw------- root root`，`sudo docker cp` 出来在宿主上
还是 `root:0600`，紧接着以运维账号执行的 `curl --cacert` 读不到它，报 **rc=77**（`CURLE_SSL_CACERT_BADFILE`）
且 `-s` 下什么也不打印——很容易被当成又一次 TLS 失败。

```sh
curl -sI --cacert /tmp/caddy-root.crt https://$SLUG.localhost/login        # 200 + CSP
curl -s -o /dev/null -w '%{http_code}\n' --cacert /tmp/caddy-root.crt \
     -X POST https://$SLUG.localhost/api/method/login -d 'usr=x&pwd=y'     # 401
curl -s --cacert /tmp/caddy-root.crt https://$SLUG.localhost/login | grep -c 'type="password"'   # 0
```

这条路径**不满足 G4「公网入口走 TLS」里 ACME 签发那一条**，只用于没有公网域名的验收环境；
正式环境仍走 ACME。

（**2026-09-14 在 G1 第二轮那台主机上以运维账号逐条执行**：`acme.localhost` 与 `platform.localhost`
的 `-sI` 均 `HTTP/2 200` 且带完整 CSP，POST 为 `401` + `Login with username and password is not allowed.`，
密码框计数 `0`。此前本节写着「按此执行两个站均 200」，但当时给的命令序列少了 `chmod` 那一行、
原样跑出来是 rc=77——那句话是照抄上一轮报告的结论、没有自己跑过，第二轮审计当场证伪。）

## 与其他服务共用的主机

`DSHERP_HTTP_PORT` / `DSHERP_HTTPS_PORT` 可以把 Caddy 挪开 80/443（例如 18080/18443），用于一台已经在跑别的服务的演练机；这样做 ACME HTTP-01 无法签发，Caddy 对 `.localhost` 主机名回落到内置 CA。生产主机不应这样用。

## 本地 Docker 上的 G1 演练（非 Linux 主机时）

同一份 runbook 可以在一台已装 Docker Desktop 的开发机上以生产形态跑通，作为拿不到干净 Linux 主机时的替代演练。差别只有四点，全部由 `infra/env/prod.env` 表达：`DSHERP_BASE_DOMAIN=localhost`（`*.localhost` 解析到回环，Caddy 对这类主机名自动用内置 CA 签发，用 `curl -k` 或导入其根证书验证 TLS）；`DSHERP_IMAGE_REGISTRY=local` 且镜像用 `--platform linux/arm64` 在本机构建（不是 x86_64）；数据面用 `DSHERP_DB_BUFFER_POOL=256M`、两个 redis `64mb`、gunicorn 1×2 的笔记本规格；worker 没有 systemd，改为前台启动一次核对 `prepare_host`、心跳与 `/metrics`。dev 栈与它并存：项目名 `dsherp` 对 `dsherp-validation`，网络、卷、容器名全部不同，端口只共用宿主 80/443（dev 不占）。

## 已知边界

- **通配证书**：目标拓扑写的是 `*.base_domain` 通配证书；通配必须走 DNS-01，需要带 DNS 提供商插件的 Caddy 构建与 API 凭证。按已裁决 #2 的 ≤3 租户试点规模，本文改为逐站 HTTP-01：不需要插件、不需要 DNS 凭证，代价是每次增删租户要重跑 `render-ingress`。
- **G1 的执行环境**：本文先在本机 Docker Desktop 与一台 x86_64 CentOS 7 共用服务器上各以生产形态完整执行过一次（见[证据](deployment-security-evidence.md)），共修掉 22 个断点；那两台都不满足 G1 判据字面。2026-09-14 由**两个不给上下文的独立审计会话**在一台干净的 Ubuntu 22.04.5 VM 上各跑了一轮（`v0.4.0-rc2` 22 分 11 秒、`v0.4.0-rc3` 9 分 34 秒，均 13/13 healthy），读数满足 60 分钟判据；两轮撞到的文档缺口都已修进本文，读数与未闭合项记在[终验台账](final-acceptance-review.md)。**ACME 签发仍未验证**：那台主机在局域网内、没有公网域名，入口走的是本文末尾的内置 CA。
- **登录页 cookie 的属性**：Frappe 在 `/login` 上下发的 `sid=Guest` 带 `HttpOnly`/`SameSite=Lax` 但**没有 `Secure`**，
  另外四个（`system_user`/`full_name`/`user_id`/`user_lang`）两者都没有（2026-09-14 G1 第二轮实测）。入口只发布
  HTTPS 且带 `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`，浏览器不会再走明文，
  所以实际暴露面很窄；但这是上游 Frappe 的下发行为，本项目没有改它，记在这里而不是当作已解决。
- **成员绑定**：`provision-tenant` 已覆盖建站、装 App、运行服务身份、站点配置、DS Enterprise、OAuth Client、Social Login Key、平台端点表、入口渲染与 healthcheck。**把某个平台用户加入某个企业（DS Membership）仍是人工步骤**：按已裁决 #4，成员的业务站短期密钥由 SSO 回调签发属于计划 4，本计划不改这条链路，因此成员绑定沿用平台站 Desk 上的手工创建。
