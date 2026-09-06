# 计划 4 切片 2：备份——定时生成 → 异地同步 → 失败可见 → 异机恢复验证（设计）

日期：2026-09-06。上位文档：[生产加固总体设计](2026-09-03-production-hardening-design.md) 工作流 E、门 G3/G10、已裁决 #2（RTO 8h）、#8（跨主机 TLS）、#10（删除边界）。切片 1（G2）已合入 main `30cf6fb`。

## 1. 判据

- **不变量**（总体设计 :144）：任一站点在任一时刻都有一份 24h 内的异地备份可恢复。站点 = 平台站 + 全部租户站。
- **RPO 24h、RTO 8h**（G3，裁决 #2）；保留 7 日 + 4 周 + 3 月。
- `site_config_backup.json`（含 `db_password`、`encryption_key`、`backup_encryption_key` 与部署键）单独进密钥库，**永不与库转储同目录同权限**（总体设计 :135、runbook §11 :260）。
- 失败可见：备份未生成、同步失败、异地副本陈旧、恢复验证失败，都在 5 分钟内成为告警（G6 口径），不靠人翻日志。
- 异机恢复：只凭异地副本 + 带出的密钥，在另一台主机恢复全部站点并证明数据与备份时一致、密码字段可解密。G3 的正式验收由审计方执行；本切片只能自证并如实标注。

## 2. 现状（2026-09-06 勘察，7 个只读代理）

1. **没有任何定时备份**。Frappe 16.31 核心 `hooks.py` 的 scheduler_events 不含站点备份；`dsherp_bridge` 只调度 ops 快照与提案过期，`dsherp_platform` 没有 scheduler_events；宿主没有 timer/cron。dev 两站最新备份已 60h+。
2. **Frappe 会清掉本地备份**：每次 `bench backup`（以及 drop-site、uninstall-app）开始时按 ctime 删除 `private/backups` 下所有超过 23h（`keep_backups_for_hours`）的文件，不分文件名。所以"本地留一份"必须放在 `private/backups` 之外。
3. **预留卷空置**：`tenant-backups`/`platform-backups` 挂在四个 scheduler/queue 服务的 `/home/frappe/backups`，没有代码写它；backend 不挂它们，但挂归档卷。`Bench.run` 只能进 backend/platform-backend。
4. **restore 不恢复 site_config**：`bench restore` 复用已存在站点的 `site_config.json`（db_name/db_password）并重建库；从不读 `site_config_backup.json`。另一台主机上必须 `new-site` 后 `restore`，再自己注入 `encryption_key`，否则 `__Auth` 里的密码字段全部不可解密（v16 迁移证据 :267）。
5. **Frappe 自带的备份加密不能用**：口令写在 `site_config` 里、随 `site_config_backup.json` 一起被备份，gpg 失败时静默存明文，且从不加密 config 那一件。
6. **可见性只覆盖 `sites[0]`**：`backup_stale` 来自 ops 快照的 `backup_age_hours`（只看 `private/backups` 里 `*-database.sql*` 的 mtime），worker 只轮询 profile 第一个站；平台站没有 ops 快照；gauge 无标签；告警无站点维度。
7. **工具全无**：宿主 venv、frappe 镜像、worker 镜像都没有 restic/rclone/age；新增 pip 依赖会随锁文件进入运行容器镜像；compose 只有 `provider`/`worker` 两个能出网的网络，`worker` 按契约只给 backend。
8. **契约测试**：生产 compose 13 个服务且每个有 healthcheck、只允许具名卷、第三方镜像按 digest；宿主 unit 由 `render_worker_units.py` 渲染、`ProtectSystem=strict`、只写 `.runtime` 与 `work`；`secrets init` 只会**生成**随机密钥，外部签发的凭据不能直接加进那个元组。
9. **另一台主机**：唯一的 x86 服务器（CentOS 7，glibc 2.17，systemd 219）不满足 runbook 前提；G1/ACME 未过。本切片的异机演练只能在本机以第二个 compose 项目模拟，如实标注。
10. G2 留给本切片的两项：bench.log 历史上有明文 root 密码行；`bench restore` 在异机上重建的库账号 host 范围未验证。

## 3. 方案比较

| | A（推荐）宿主 timer 驱动 `dsherp-admin`，restic 作为按 digest 固定的一次性 compose 服务 | B Frappe scheduler 钩子生成，宿主只做同步 | C 单独的长驻备份容器（自带 cron + 客户端） |
|---|---|---|---|
| 生成 | `dsherp-admin backup` 经 `Bench.run` 对每个目标站执行原生 `bench backup --with-files`（即 `scheduled_backup(force=True)`），平台站与租户站同一路径 | 两个 App 各加一条 scheduler_events 调 `scheduled_backup`；平台站要改 `dsherp_platform` | 需要 bench 与 docker socket，破坏"任何环境只来自镜像 tag"的不变量 |
| 失败可见 | 命令写宿主状态文件，worker 每 tick 读（防火墙状态文件的既有模式），覆盖全部站含平台站 | 失败只落在 Scheduled Job Log；平台站无人观测；release/rollback 期间 `pause_scheduler` 让它静默停跑 | 要再造一套上报 |
| 与发布的关系 | 站点处于维护模式时该站记为"推迟"，状态可见 | 静默跳过 | — |
| 同步 | `docker compose run --rm backup-sync`：restic 镜像按 digest 固定、`cap_drop ALL`、只读挂两个备份卷、走 `provider` 网络出 TLS；两个仓库（数据/密钥）各自口令 | 同 A | 同 A 但常驻 |
| 契约测试 | 需要允许"带 profiles 的一次性服务"不受 healthcheck/restart 约束（显式改测试） | 不改 compose | 加常驻服务，改 13→14 |
| 与总体设计 :135 的字面差异 | "scheduler 每日…原生 scheduled_backup"改为宿主 timer 调原生 `bench backup`（同一个函数）；`backup-sync` 名称与语义一致 | 字面一致 | 不一致 |

**推荐 A**。理由：平台站与租户站一条路径；失败可见性走已有的"宿主状态文件 → worker gauge → 规则告警"链路，不依赖 Frappe 调度器是否在跑；不给运行容器镜像增加 pip 依赖；restic 自带客户端加密、去重、保留策略与完整性检查，不必再拼 rclone + age；B 无论如何也需要宿主 timer 做同步，等于两套调度。

## 4. 设计（方案 A）

### 4.1 生成：`dsherp-admin backup`

对 `_targets`（平台站 + 全部租户站）逐站：

1. 读站点标志；`maintenance_mode`/`pause_scheduler` 任一为 1（发布/回滚进行中）→ 该站记 `deferred`，不备份，继续下一站。
2. `bench --site S backup --with-files`（不 `--compress`：tar 必须是 `.tar`，库转储本来就是 `.sql.gz`），按 `BACKUP_PIECES` 前缀发现四件套，缺件即该站失败。
3. 站点数据快照：复用 `release_snapshot.container_script`（与 G2 同一口径：分桶、分页、密钥列摘要、`hash_columns`）写成 `snapshot.json`——这是异机恢复时的比对基准，因为那时源站不在。
4. 暂存到备份卷（backend/platform-backend 新增挂载 `tenant-backups`/`platform-backups` 于 `/home/frappe/backups`）：
   - 数据目录 `/home/frappe/backups/sets/<site>/<stamp>/`：`database.sql.gz`、`files.tar`、`private-files.tar`、`snapshot.json`、`set.json`（每件 sha256 与大小、站名、stamp、发布 tag、frappe 版本、格式号）；
   - 密钥目录 `/home/frappe/backups/secrets/<site>/<stamp>/site_config_backup.json`，目录 0700、文件 0600，与数据目录不同父目录、不同权限；
   - 复制后回读 sha256 核对；`private/backups` 里的原件留给 Frappe 自己清理（Desk 下载页与 ops 的 `backup_age_hours` 继续看那里）。
5. 本地保留：每站只留最近 3 套（数据与密钥目录同步删），有界且失败一次同步不会丢掉唯一副本。
6. 运行互斥：`<runtime>/backups/lock`（flock），timer 与人工重叠时后者立即以 Fault 退出（并发路径测试）。
7. 写状态文件（§4.4）。任一站失败退出码 1，但不中断其他站。

### 4.2 异地：`dsherp-admin backup-sync`（`backup --sync` 顺带执行）

- compose 新增服务 `backup-sync`（`profiles: [ops]`，不常驻）：`restic/restic@sha256:…`（linux/amd64 + arm64 多架构清单，记入 runtime-baseline），`cap_drop: [ALL]`、`security_opt: [no-new-privileges:true]`、`read_only: true`、`user` 为 frappe uid；挂 `tenant-backups:/backups/tenant:ro`、`platform-backups:/backups/platform:ro`、`backup-cache:/cache`；网络 `provider`（唯一允许出网且不违反 `worker` 网络契约的网络）；环境 `RESTIC_PASSWORD_FILE=/run/secrets/…`、`RESTIC_CACERT`（可选）；存储凭据 `env_file: ${DSHERP_SECRETS_DIR}/backup_storage_credentials`。
- 两个仓库：`DSHERP_BACKUP_REPOSITORY`（数据）与 `DSHERP_BACKUP_SECRETS_REPOSITORY`（密钥），口令分别为 `backup_repository_password`、`backup_secrets_repository_password`（`secrets init` 生成，0600，永不打印）。两仓库可在同一对象存储的不同桶，也可不同存储；凭据文件同一份或两份由运维决定（默认一份）。
- 每次运行：对每个 `sets/<site>/<stamp>` 与 `secrets/<site>/<stamp>` 分别 `restic backup --tag site=<site> --tag stamp=<stamp>`（`--host` 固定为 compose 项目名，避免容器 id 漂移）；然后 `restic forget --group-by tags --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune`；然后 `restic check`（结构检查，每次）；`restic snapshots --json --latest 1 --tag site=<site>` 记录快照 id 到状态。每步有超时；非零即该步失败，状态记 `sync.ok=false` 与脱敏后的错误尾部；不在本次运行内重试（timer 次日再跑，人工可随时重跑）。
- `dsherp-admin backup-init`：一次性 `restic init` 两个仓库，幂等（先 `cat config`）。
- 传输：`s3:https://…`（裁决 #8：跨主机必须 TLS）；`DSHERP_ENV=prod` 下拒绝 `http://`。私有 CA 用 `backup_storage_ca.pem`（可选密钥文件）经 `RESTIC_CACERT` 传入。

### 4.3 定时：systemd `dsherp-backup.timer` / `.service`

- `render_worker_units.py` 新增 `render_backup_units(root, *, user, group, target)`：timer `OnCalendar=*-*-* 02:30:00`、`Persistent=true`、`RandomizedDelaySec=15min`；service `Type=oneshot`、`ExecStart={root}/bin/dsherp-admin backup --sync --verify`、`User/Group`、`SupplementaryGroups=docker`、`Environment=DSHERP_ENV=prod`、`TimeoutStartSec=6h`，沙箱与 worker unit 相同（`ProtectSystem=strict`、`ReadWritePaths={root}/.runtime`）。契约测试逐行钉住。
- 安装步骤进 runbook §7；`--verify` 见 §4.5。
- 本机 dev 不装定时器，演练手工触发。

### 4.4 可见性：状态文件 → worker → 告警

- `<runtime>/backups/status.json`（`_write_json`，0600，原子）：

```json
{"format": 1, "started": "...", "finished": "...", "ok": false,
 "sites": {"<site>": {
   "backup":   {"at": "...", "stamp": "20260906_023001", "ok": true,  "error": null, "deferred": false},
   "sync":     {"at": "...", "ok": true,  "snapshot": "1a2b…", "error": null},
   "verified": {"at": "...", "ok": true,  "error": null}}}}
```

- worker 每 tick 读（mtime 变化才解析；读失败等同缺失），新增 gauge：`dsherp_backup_status_age_seconds`、`dsherp_backup_sites`、`dsherp_backup_sites_stale`（最近一次成功本地备份 > 26h 或从未）、`dsherp_backup_sites_unsynced`（最近一次成功同步 > 26h 或从未）、`dsherp_backup_sites_unverified`（最近一次成功恢复验证 > 8 日或从未）、`dsherp_backup_last_run_ok`。
- 新告警键（`alerts.evaluate` 纯函数，阈值常量，边界测试）：`backup_status_missing`（critical：无状态文件或 > 26h——定时器没在跑）、`backup_run_failed`（warning：最近一次运行有站失败/同步失败，消息列站名）、`backup_sites_stale`（critical）、`offsite_backup_stale`（critical）、`restore_unverified`（warning）。既有 `backup_stale`（ops 快照口径）保留。
- `doctor` 新增：配置了仓库 URL 时三个密钥文件必须存在且 0600；`DSHERP_ENV=prod` 下仓库 URL 必须 `https`。
- 注入故障演练（G6 口径）：停掉对象存储 → 下一次 `backup --sync` 后 5 分钟内 `backup_run_failed`；删掉状态文件 → `backup_status_missing`；把状态文件时间改旧 → `backup_sites_stale`/`offsite_backup_stale`；证据记录 worker 日志原文与 `/metrics` 原行。

### 4.5 恢复验证

**同栈演练 `dsherp-admin restore-drill <site>`（`backup --verify` 对每个站执行）**：

1. `docker compose run --rm backup-sync restic restore latest --tag site=<site> --target /restore`（数据仓库）与密钥仓库同法，落到新增可写卷 `backup-restore`（backend/platform-backend 也挂，路径 `/home/frappe/restore`）；按 `set.json` 核对 sha256。
2. 一次性站名 `<slug>-drill.<base_domain>`（平台站 `platform-drill…`）；已存在即拒绝（沿用 verify_daily_backup 的审计行为），不覆盖。
3. `bench new-site` → `bench --site <drill> restore <db> --with-public-files … --with-private-files … --force` → 从密钥副本注入 `encryption_key`（只这一项；`db_password`、`host_name`、`dsherp_agent_sources`、`dsherp_platform_oauth` 等部署键不带）→ `set.json` 的 tag 与运行 tag 不同时 `bench migrate`。
4. 判定：`release_snapshot` 快照 drill 站，与 `snapshot.json` 用 `release_compare` 比对（容忍项只有 `RESTORE_EXPECTATIONS`，migrate 过则加本次执行 patch 的声明）；再跑容器脚本对 `__Auth` 抽样解密（`get_decrypted_password`），任一失败即验证失败。
5. 无论结果都 `bench drop-site <drill> --no-backup --force`；失败时保留 `/home/frappe/restore/<site>` 供排查（下一次运行前清理）。写 `verified` 到状态。

**异机恢复 `dsherp-admin restore-site <site> [--stamp S]`（G3 路径，runbook 新 §12）**：在按 runbook §1–§6 拉起的新栈上：`provision-platform`/`provision-tenant` 幂等建站 → 从异地取回该站指定/最新一套 → `bench restore --force` → 注入 `encryption_key` → 按需 `bench migrate` → 快照比对 → 再跑一次 provision 让主机相关配置按新主机重算（runbook §11 既有做法）→ 报告与 RTO 计时。本切片在本机用第二个 compose 项目（`dsherp-drill`，独立端口/卷/runtime 目录）演练，对象存储用本机 MinIO（按 digest 固定、自签 TLS，属裁决 #8 的单主机内部通信），如实标注为自证。库账号 host 范围（G2 留项）在此演练中核实。

### 4.6 配置、密钥与保管

- prod.env 新键：`DSHERP_BACKUP_REPOSITORY`、`DSHERP_BACKUP_SECRETS_REPOSITORY`（`deploy_env.DEFAULTS` + 正则校验 + `prod.env.example`）；留空表示未配置，`backup --sync` 拒绝、`doctor` 报告。
- 密钥目录新文件：`backup_repository_password`、`backup_secrets_repository_password`（生成）；`backup_storage_credentials`（运维提供，`AWS_ACCESS_KEY_ID=…`/`AWS_SECRET_ACCESS_KEY=…` 两行）；`backup_storage_ca.pem`（可选）。
- 保管：三份密钥 + 凭据必须另存于对象存储之外（运维的密码库/离线介质），runbook 写明"丢失即等于丢失全部异地备份"；恢复主机靠人工带入。

### 4.7 保留与裁决 #10

- 异地：restic `forget` 7/4/3，按站分组；本地：3 套。归档卷上的 `archived/releases/<tag>/<site>/`（升级前备份）与 `archived/sites/<site>`（下线归档）**不同步异地**——它们是主机本地的操作记录，日常四件套已覆盖数据；runbook 注明。
- 备份是个人数据的副本：按裁决 #10，删除只作用于在线数据；已生成的备份集不改写，随保留策略到期消失（最长约 3 个月 + 归档）。未来 `delete-user-data` 只影响之后的备份集。写入 runbook 与证据。

### 4.8 错误处理

- 逐站隔离：一个站的失败不影响其他站；缺件、sha256 不符、快照失败都算该站失败。
- restic：每步超时（backup 3600s、forget/prune 1800s、check 1800s）；失败记录脱敏尾部；不重试。
- 互斥锁；站点维护中推迟；状态文件缺失本身就是告警。
- 一切拒绝都是 Fault（说明查什么，不说怎么强行通过），退出码 2；有站失败退出码 1。

### 4.9 测试

- 单元（假 Bench/假 runner）：`backup` 的 verbs 与顺序（flags → backup → 发现四件 → 快照 → 复制到两目录并 chmod → sha256 回读 → 本地修剪）；缺件/校验不符/维护中推迟/锁冲突；`backup-sync` 的 `docker compose run` argv（镜像不在 argv，由 compose 决定）、两仓库两口令、forget 策略、check、状态写入、失败尾部脱敏；`backup-init` 幂等；`restore-drill` 流程含拒绝覆盖既有 drill 站、注入 `encryption_key`、migrate 条件、比对与解密抽样判定、总是 drop；`restore-site` 流程；状态文件解析 → gauge → 五条告警规则的边界（26h/27h、8 日）；`render_backup_units` 逐行；compose 契约（带 profiles 的一次性服务只需 digest/具名卷/无端口/`cap_drop`，服务计数改为"常驻 13 + 一次性 1"）；`deploy_env` 新键校验（prod 必须 https）；`doctor` 发现项；CLI 接线（`backup --sync --verify`、`backup-init`、`backup-sync`、`restore-drill`、`restore-site`）。
- 集成（dev 栈）：`backup` 真实生成并暂存，容器内核对两目录权限与 sha256；`restore-drill` 对 dev 站真实走一遍（对象存储用本机 MinIO）。
- 演练（本机生产形态 + MinIO）：完整周期 backup → sync → 注入三种故障并记录告警时延 → restore-drill → 第二项目 restore-site 全站恢复并比对，记录 RTO 用时。

### 4.10 文档

runbook：§3 密钥（新文件与保管）、§6 compose 服务（`backup-sync` 一次性）、§7 unit（timer 安装）、§9 验收表（`/metrics` 的 `dsherp_backup_*` 行）、新 §12「备份与容灾」（RPO/RTO、配置、状态文件、告警键、restore-site 步骤、密钥保管、保留与裁决 #10）；`data-governance-evidence.md` 新增「切片 2」；总体设计计划 4 行更新；runtime-baseline 加 restic/MinIO digest 行。

## 5. 不做的事

- 不改 Frappe 核心，不加 App 钩子，不用 Frappe 的 `encrypt_backup`。
- 不同步归档卷；不把 `.runtime/business-sessions` 纳入（T4 另行处理）。
- 不在 x86 服务器上演练（前提不满足）；G3 正式验收仍由审计方执行。
- bench.log 明文 root 密码：本切片不新增暴露（`new-site`/`restore` 沿用既有的 argv 传参），仅在证据中标注为未闭合，待短期凭据切片处理。

## 6. 需要裁决的点

1. 生成用宿主 timer 驱动 `dsherp-admin backup`（方案 A），而不是总体设计字面上的 Frappe scheduler 钩子。
2. restic 作为带 `profiles` 的一次性 compose 服务（契约测试为此显式放行一次性服务），而不是宿主二进制。
3. 每日 `--verify` 对全部站做同栈恢复演练（试点 ≤3 租户可承受；超过后改轮换），验证时效阈值 8 日。
4. 异机恢复在本机第二个 compose 项目 + 自签 TLS 的 MinIO 上自证，如实标注非正式验收。
5. 本地暂存保留 3 套；归档卷不同步异地。
6. 可见性统一走"状态文件 → worker gauge → 规则告警"，不扩展 ops 快照的键集。
