# ERPNext / Frappe v16 迁移探针证据

日期：2026-09-01。范围：仅 `dsherp` 本地合成环境与独立 throwaway probe；不代表生产部署。

## T0 目标基线

### 官方发布与镜像

| 项目 | 执行时核验结果 | 依据 | 判定 |
| --- | --- | --- | --- |
| ERPNext 最新 v16 release | `v16.33.0`，发布于 `2026-08-25T17:04:34Z` | `https://api.github.com/repos/frappe/erpnext/releases` 与 `https://github.com/frappe/erpnext/releases/tag/v16.33.0` | 目标 ERPNext 版本 |
| Frappe 最新 v16 release | `v16.32.0`，发布于 `2026-08-26T05:43:47Z` | `https://api.github.com/repos/frappe/frappe/releases` 与 `https://github.com/frappe/frappe/releases/tag/v16.32.0` | 仅作为发布动态；实际配套版本以镜像内结果为准 |
| ERPNext 镜像 | `frappe/erpnext:v16.33.0`，manifest / RepoDigest `sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd` | Docker Hub tag API、`docker pull`、`docker image inspect` 三方一致 | 固定此 RepoDigest |
| 本机镜像架构 | `linux/arm64`；本地 image ID `sha256:a4fd94c1de264ebaa83713ac663ef1ab7252809f5f8dee84b41065067c53f1e9` | `docker image inspect` | 通过 |
| 镜像内实际应用 | ERPNext `16.33.0`、Frappe `16.31.0` | `docker run --rm --entrypoint bench ... version` | 后续源码与运行契约以这一实际配套为准，不用 Frappe 最新 release 代替 |
| 镜像内运行时 | Python `3.14.7`、Node `v24.19.0` | 容器内 `python3 -V` / `node -v` | 满足 v16 的 Python 3.14 / Node 24 要求 |

`docker pull frappe/erpnext:v16.33.0` fresh 输出的 digest 与 Docker Hub manifest digest 均为 `sha256:493cec…f1bd`；不是用标签推断实际内容。

### 数据库与 Redis

Frappe v16 当前官方安装页要求 MariaDB `11.8`、Redis / Valkey `6+`；官方 `frappe_docker` 的 `overrides/compose.mariadb.yaml` 同样使用 `mariadb:11.8`。因此现有 MariaDB 10.6 不作为 v16 新站基线，T2.1 必须同时翻转数据库 digest。

本机已拉取并核验：

- MariaDB `11.8.9` arm64，RepoDigest `mariadb@sha256:2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3`。
- 现有 Redis `6.2.24` arm64，RepoDigest `redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015`；满足 `6+`，本迁移不升级 Redis。

### v15 历史运行基线

切换前实际容器重新核验：ERPNext `15.119.3`、Frappe `15.118.0`、Python `3.11.6`。v15 compose 仍在 `dsherp-validation` 项目运行，C1 使用不同 project、端口和卷，不触碰现有 v15 站点。

## 可重跑命令

```sh
curl -fsSL 'https://api.github.com/repos/frappe/erpnext/releases?per_page=100' | jq '[.[] | select(.tag_name | startswith("v16."))][0] | {tag_name,published_at,html_url}'
curl -fsSL 'https://api.github.com/repos/frappe/frappe/releases?per_page=100' | jq '[.[] | select(.tag_name | startswith("v16."))][0] | {tag_name,published_at,html_url}'
docker pull frappe/erpnext:v16.33.0
docker image inspect frappe/erpnext:v16.33.0 --format '{{json .RepoDigests}} {{.Architecture}} {{.Id}}'
docker run --rm --entrypoint bench frappe/erpnext:v16.33.0 version
docker run --rm --entrypoint python3 frappe/erpnext:v16.33.0 -V
docker run --rm --entrypoint node frappe/erpnext:v16.33.0 -v
docker pull mariadb:11.8
docker image inspect mariadb:11.8 --format '{{json .RepoDigests}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}}'
docker run --rm redis@sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015 redis-server --version
docker compose -p dsherp-validation -f infra/compose.validation.yml exec -T backend bench version
docker compose -p dsherp-validation -f infra/compose.validation.yml exec -T backend python3 -V
```

## C1 探针结论表

| 行 ID | 风险 | 脚本 / 命令 | 预期 | 实测 | 判定 | 阶段 2 关联任务 |
| --- | --- | --- | --- | --- | --- | --- |
| C1-R13 | bind-mount 装 app | 待 T1.1 | 两 app 可安装，hooks 与 boot 生效 | 待执行 | 待判定 | T2.1 / 部署重设计裁决 |
| C1-R14 | agent-runtime venv | 待 T1.2 | Python 3.14 下锁文件可安装 | 待执行 | 待判定 | T2.9 |
| C1-R1 | commit 语义 | 待 T1.3 | hook 内 no-op 可观测；业务链无 warning | 待执行 | 待判定 | 按红项追加 |
| C1-R2-R3 | Desk / SSO | 待 T1.4 | `/desk` 与 OAuth 全链成立 | 待执行 | 待判定 | T2.2-T2.5 |
| C1-R6 | mapper / 委外 | 待 T1.5 | 七条 route 与委外内部方法可用 | 待执行 | 待判定 | 按红项追加 |
| C1-R4 | Page / sidebar | 待 T1.6 | Page、全局资源、Workspace 可加载 | 待执行 | 待判定 | T2.4 |
| C1-R15 | 镜像入口 | 待 T1.7 | backend/worker/scheduler/websocket 路径有效 | 待执行 | 待判定 | T2.1 |
| C1-R9-R11 | provision / boot | 待 T1.8 | 内部导入、参数与 boot 语义有效 | 待执行 | 待判定 | T2.8 |
| C1-R5-R7-R10-R12-R17 | 其余框架边界 | 待 T1.9 | 排序、翻译、锁、CSRF、Python import 可用 | 待执行 | 待判定 | T2.6-T2.8 |
| C1-API | API 面 | 待 T1.10 | v1、resource 与 v2 权限事实明确 | 待执行 | 待判定 | runtime-baseline |

## C1 资源清理

未开始。C1 完成后必须同时满足：`docker compose -p dsherp-v16probe ps` 无容器，且 `docker volume ls` 无 `dsherp-v16probe` 卷。
