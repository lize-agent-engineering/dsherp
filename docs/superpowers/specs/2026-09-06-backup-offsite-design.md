# 计划 4 切片 2：备份——定时生成 → 异地同步 → 失败可见 → 异机恢复验证（设计，第 2.1 版）

日期：2026-09-06。第 1 版（`faa4d38`）经 [备份设计审阅](../../engineering/backup-design-review-2026-09-06.md) 修订为第 2 版（`7602585`）；第 2 版与实施计划再经 [设计 v2 与实施计划审阅](../../engineering/backup-v2-plan-review-2026-09-06.md) 修订为本版（2.1）：架构与默认取值已放行，六组契约按审阅收紧（§4.0、§4.1、§4.2、§4.3、§4.4、§4.5、§4.6 标注"2.1"的段落）。上位文档：[生产加固总体设计](2026-09-03-production-hardening-design.md) 工作流 E、门 G3/G6/G10、已裁决 #2（RTO 8h）、#8（跨主机 TLS）、#10（删除边界）。切片 1（G2）已合入 main `30cf6fb`。

## 1. 判据

- **不变量**（总体设计 :144）：任一站点（平台站 + 全部租户站）在任一时刻都有一份 24h 内的异地备份可恢复。
- **年龄按数据时点算**：一套备份的年龄 = 现在 − 该套备份的稳定窗口时刻（不是上传完成时刻）。任一站点最新的**完整配对**异地备份年龄 ≥ 24h，或该站从未有过完整配对（新站首份未完成），即"目标未满足"（critical）；≥ 20h 预警（warning）。为此每 **12h** 生成并同步一次，给失败一次留补救余量。
- **RTO 8h**（G3）；保留 7 日 + 4 周 + 3 月，按站点维度淘汰（§4.7 如实说明其语义）。
- **密钥分离**：`site_config_backup.json`（含 `db_password`、`encryption_key`、`backup_encryption_key` 与部署键）与库转储分仓库、分访问身份、分挂载；数据仓库的访问者拿不到密钥仓库的解密材料。Frappe 原目录里的那一份也在本设计的处置范围内。
- **一致性**：四件套与核验快照对应同一份业务状态；生成过程与 release/rollback/下线互斥。
- **失败可见（G6 口径）**：备份未生成、同步失败/半成功、异地陈旧、恢复未验证、定时任务失联，都在 5 分钟内成为告警；worker 停止时失败通知仍然存在。
- **恢复隔离**：恢复副本从创建起就不接入业务入口、生产 worker、邮件与 Webhook；按备份记录的制品版本恢复并核验，升级另走 G2。
- **异机恢复**：只凭异地副本 + 带出的密钥在另一台主机恢复全部站点，数据与备份时一致、密码字段可解密。本切片在本机第二个 compose 项目上做工程自证；G3 的正式验收（物理异机、真实异地、正式 RTO）由审计方另行执行。

## 2. 现状（2026-09-06 勘察，7 个只读代理；审阅未推翻）

1. 没有任何定时备份：Frappe 16.31 核心与两个 App 的 scheduler_events 都不含站点备份；宿主无 timer/cron；dev 两站最新备份已 60h+。
2. Frappe 每次 `bench backup`（及 drop-site、uninstall-app）开始时按 ctime 删除 `private/backups` 下超过 23h 的所有文件，不分文件名；`--compress` 只影响两个 tar，库转储永远是 `.sql.gz`。
3. 备份实现顺序：`take_dump` → `copy_site_config` → 打包 public files → 打包 private files；四件不在一个事务里，更不包含之后的任何站点快照。
4. `tenant-backups`/`platform-backups` 卷挂在四个 scheduler/queue 服务的 `/home/frappe/backups`，无代码使用；backend 不挂它们；`Bench.run` 只能进 backend/platform-backend。
5. `bench restore` 复用已存在站点的 `site_config.json` 并重建库，从不读 `site_config_backup.json`；异机上必须 `new-site` 后 `restore`，再注入 `encryption_key`，否则 `__Auth` 里的密码字段全部不可解密。`bench drop-site --no-backup` 把整个站目录**移入** `archived/sites/`（本项目为它挂了持久卷），不是删除。
6. Frappe 自带的 `encrypt_backup` 不可用：口令写在 `site_config` 里随 config 件被备份，gpg 失败时静默存明文，且从不加密 config 件。
7. 可见性只覆盖 worker profile 的 `sites[0]`；平台站无 ops 快照；gauge 无标签；告警无站点维度；worker 是宿主上唯一会发告警的进程。
8. `bench` 命令行包装器把完整 argv 写进 `logs/bench.log`（含 `--db-root-password`）；Frappe 自己在没有 argv 口令时会读 `frappe.conf.root_password`，再回退到 `getpass`（可经 stdin 喂入）。
9. 工具全无：宿主 venv、frappe 镜像、worker 镜像都没有 restic/rclone/age；新增 pip 依赖会随锁文件进入运行容器镜像；compose 只有 `provider` 能出网且不违反 `worker` 网络契约。
10. 契约测试目前锁"13 个服务、每个有 healthcheck"与 unit 逐行文本；审阅要求改为按行为验证，不设新的数量门禁。
11. 唯一的 x86 服务器不满足 runbook 前提；G1/ACME 未过；"隔离预览"经验（beta 独立 Site/DB、无外网网络、邮件/异步关闭）可复用于恢复环境。
12. G2 留项：bench.log 历史明文 root 口令；`bench restore` 在异机上重建的库账号 host 范围未验证。

## 3. 方案

方案 A（宿主 timer 驱动 `dsherp-admin`，restic 作为按 digest 固定的一次性 compose 服务）已采纳；B（Frappe scheduler 钩子生成）与 C（长驻备份容器）的比较见第 1 版与审阅文件，不再重复。本版按审阅修订行为契约。

## 4. 设计

### 4.0 备份集协议

- **backup_set_id**：`<stamp>-<site_slug>-<6 位随机>`，例 `20260906_020007-acme_tenant_example_com-k3f9qx`；stamp 为稳定窗口开始时刻（UTC）。（2.1）slug 保留站名里的连字符（`dsherp-validation.localhost` → `dsherp-validation_localhost`），解析按位置：前 15 位是 stamp、后 6 位是 token、中间是 slug，而不是靠正则字符类；项目现有真实站名都必须能通过。同一个 id 出现在：暂存目录名、两个 restic 快照的 `set=<id>` 标签、`set.json`、`pair.json` 与状态文件。
- **数据侧** `sets/<site>/<set_id>/`：`database.sql.gz`、`files.tar`、`private-files.tar`、`snapshot.json`（G2 口径的站点数据快照）、`set.json`：
  `{"format":1,"set_id","site","kind":"scheduled|release|retire","stamp","window":{"started","finished"},"image_tag","image_id","frappe_version","pieces":{name:{"sha256","bytes"}},"snapshot_sha256"}`。
- **密钥侧** `secrets/<site>/<set_id>/`：`site_config_backup.json` + `pair.json`（`set_id`、站名、`format`、数据侧各件 sha256 的副本、`set.json` 的 sha256、以及（2.1）`config_sha256` = `site_config_backup.json` 文件字节的 sha256）。两侧互相能证明属于同一集。（2.1）**摘要定义**：文件类摘要都是文件原始字节的 sha256（`sha256sum`）；`snapshot_sha256` 是写入 `snapshot.json` 的那串 JSON 文本（`sort_keys=True, ensure_ascii=False, default=str`，无缩进）的 sha256，恢复时对取回的 `snapshot.json` 原样重算；`set_sha256` 是 `set.json` 规范化 JSON（`sort_keys=True, separators=(",", ":")`）的 sha256。任何摘要不符都在报告里给出"期望/实际/来源文件"。
- **状态机**（记录在状态文件 `sets[set_id].state`）：`staged` → `data_uploaded` / `secrets_uploaded`（两者之一，pending）→ `complete` → `verified`（恢复演练成功）。（2.1）**complete 的判定**：不是"两侧各有一个快照 id"，而是：用**完整**快照 id 从数据仓库 `restic dump <id> …/set.json`、从密钥仓库 `restic dump <id> …/pair.json` 读回两份清单，核对 `set_id`、`site`、`format`、各件 sha256、`set_sha256`、`config_sha256` 全部一致，才标 `complete` 并记录两个完整快照 id。状态文件里已记录的快照 id 每次同步都要重新 `dump` 核对（远端副本可能已被人删除或损坏），核对不过就退回 pending 并告警；pending/失败的集不能作为 RPO 满足或下线前置。**同步成功时间 = 变为 `complete` 的时刻**；恢复只选最新的 `complete` 集，绝不各取两个仓库的 latest。pending 集由下一次运行先补齐缺失的一侧。
- `kind=release`（发布前备份）与 `kind=retire`（下线最终备份）用同一协议，由事件触发生成与上传（§4.1）。

### 4.1 稳定窗口与生成：`dsherp-admin backup`

1. **互斥**：宿主级操作锁 `<runtime>/operations.lock`（flock，非阻塞）。`backup`、`release`、`rollback`、`retire-tenant` 都持有它；拿不到即 Fault（说明是谁在跑），不排队。
2. **站点保持（2.1：停止领取 ≠ 等待执行者退出）**：对每个目标站，(a) 写 `<runtime>/holds/<site>`——worker 每 tick 读取，对被保持的站不再领取（Coordinator 新增 `holds` 门，心跳照旧）；(b) `set-config dsherp_hold 1`——服务端 `claim_run` 在该标志下拒绝领取，关闭"worker 已过保持检查、claim 尚未落地"的窗口；(c) 等待**实际在途执行者**归零：只数 `Running`/`Cancelling`（`Queued` 与 `NeedsInput` 被保持冻结，不计入，也不会让备份永久推迟），上限 10 分钟；超时则该站记 `deferred(busy)`，撤 (b)(a)，继续下一站。
3. **窗口（2.1：明确排空写入者）**：`maintenance_mode=1`（新请求立即 503）、`pause_scheduler=1`（不再入队新作业）→ 排空已在途的写入者：轮询直到该站 RQ 队列里没有本站的排队/执行中作业（`frappe.utils.background_jobs.get_jobs(site)` 与 started registry）且 MariaDB 里本站数据库用户的活动连接为 0（`information_schema.processlist` 中 `command <> 'Sleep'` 且非本连接；每站独立库用户，因此只见本站线程），连续两次为 0 才算排空，上限 2 分钟，超时该站 `deferred(draining)` → 记 `window.started` → `bench --site S backup --with-files`（不 `--compress`）→ 站点数据快照（`release_snapshot.container_script`，与 G2 同口径）→ 记 `window.finished` → 逆序撤销：恢复原标志、清 `dsherp_hold`、撤保持文件。窗口内没有任何业务写入，四件套与快照对应同一状态。任一步失败（含标志只设了一半）都按已完成的动作逆序清理，该站记失败，`last_success` 不动。
4. **暂存**（backend/platform-backend 新增挂载各自的 `*-backups` 卷于 `/home/frappe/backups`）（2.1：`set.json` 的 `image_tag`/`image_id` 取自**实际运行**该站的 bench 容器——沿用 G2 的 `_running_images` 读 `docker inspect`，dev 环境记 `image_tag=dev` 与真实 id；空 id 视为生成失败）：按 `BACKUP_PIECES` 前缀发现四件；数据三件**复制**到 `sets/<site>/<set_id>/`，config 件**移动**到 `secrets/<site>/<set_id>/`（`private/backups` 里不再留 config 件；目录 0700、文件 0600）；写 `snapshot.json`、`set.json`、`pair.json`；逐件回读 sha256 核对。`private/backups` 里的三件数据留给 Frappe 自己清理（Desk 下载页与 ops 的 `backup_age_hours` 继续看那里）。
5. **本地保留**：每站最多 3 套；永不删除"最新 `complete` 集"与"最新 `verified` 集"（两者可能相同）；数据侧与密钥侧同删。
6. **状态**：每阶段开始/成功/失败都更新状态文件（§4.4），保留 `last_success` 与 `last_attempt`，失败不抹掉上次成功。
7. 任一站失败退出码 1，不中断其他站；`--sync` 时接着做 §4.2。
8. **事件式备份集（2.1）**：`release` 的升级前备份与 `retire-tenant` 的最终备份改用同一暂存函数生成 `kind=release|retire` 的集（`release` 在既有静默窗口内，`retire` 在 drop-site 之前），并**立即上传配对**。`DSHERP_ENV=prod` 下 `retire-tenant` 与 `release` 都以"两个仓库已配置且可达"为前置，未配置即在任何破坏性动作前 Fault，不存在"缺配置就跳过上传"的隐式路径；dev 环境可用显式 `--local-only` 跳过（只影响本机演练）。`retire` 的最终集必须 `complete` 才 drop、才移出清单；`release` 的上传失败记入状态并告警（`backup_run_failed`），发布本身继续（本地与归档卷仍有该集）。`_archive_backup` 与下线归档也改为数据/密钥分目录分权限，不再把 config 件与转储放在一起。

### 4.2 异地同步与保留：`dsherp-admin backup-sync`

- **两个一次性 compose 服务**（`profiles: [ops]`，不常驻）：`backup-sync-data` 只挂 `tenant-backups`/`platform-backups` 的 `sets/` 子树（只读）、只带数据仓库口令与数据存储凭据；`backup-sync-secrets` 只挂 `secrets/` 子树（只读）、只带密钥仓库口令与密钥存储凭据。两者：`restic/restic:<版本>@sha256:…`（实施时固定，linux/amd64 + arm64），`cap_drop: [ALL]`、`no-new-privileges`、`read_only: true`、非 root 用户、`backup-cache` 卷、网络 `provider`、`RESTIC_CACERT` 可选。
- **上传（2.1）**：对每个非 `complete`/`verified` 的集，缺哪一侧传哪一侧：`restic backup --host <compose 项目名> --tag site=<site> --tag set=<set_id> --tag kind=<kind> <目录>`；然后按 §4.0 的判定读回核对（`snapshots --json` 取完整 id → `dump` 两份清单 → 逐项核对）才标 `complete`。已记录 id 的一侧同样重新 `dump` 核对，不凭本地记录跳过。半成功或核对不过保留为 pending，状态与告警如实显示。远端存在但状态文件不认识的集（如状态文件重建后）先按 pending 处理并读回核对，通过后才进入 `complete`；保留淘汰只针对核对过的 `complete`/`verified` 集。
- **保留**：不用 restic 的分组规则。宿主侧纯函数 `retention.select(sets, now)` 以**站点**为维度、以 `set.json` 的 stamp 为时间，按 7 日 + 4 周 + 3 月选出保留集；额外保护：每站最新 `complete` 集、最新 `verified` 集；`kind=retire` 集不自动淘汰（等审计保留切片裁决）；`kind=release` 集与日常集同策略。选出后对两个仓库分别 `restic forget <快照 id…>`（只删两侧都在淘汰名单里的集）再 `restic prune`。淘汰只在任务运行时发生；本函数有真实日期的测试。
- **完整性检查**（每次运行）：两个仓库 `restic check`（结构）+ `--read-data-subset=<n>/7`（按天轮换，一周读完全部数据）；任一失败记入状态并告警。结构检查不替代恢复演练（§4.5）。
- `dsherp-admin backup-init`：一次性 `restic init` 两个仓库，幂等（先 `cat config`）。
- 传输：`s3:https://…`（裁决 #8）；`DSHERP_ENV=prod` 下拒绝 `http://`；私有 CA 经 `backup_storage_ca.pem` 由 restic 实际校验。

### 4.3 定时：systemd

- `dsherp-backup.timer`/`.service`：`OnCalendar=*-*-* 02,14:00:00 Asia/Shanghai`（2.1：systemd 的小时列表语法，时区显式为 Asia/Shanghai；备份集 stamp 与年龄计算仍是 UTC；Linux 上用 `systemd-analyze calendar` 核对下一次触发时刻，本机无 systemd 时在容器里核对）、`Persistent=true`、`RandomizedDelaySec=5min`；`Type=oneshot`、`ExecStart={root}/bin/dsherp-admin backup --sync`、`TimeoutStartSec=3h`、`User/Group`、`SupplementaryGroups=docker`、`Environment=DSHERP_ENV=prod`、沙箱同 worker unit（`ReadWritePaths={root}/.runtime`）。
- `dsherp-backup-drill.timer`/`.service`：每周 `Sun *-*-* 04:00:00`，`ExecStart={root}/bin/dsherp-admin restore-drill --all`，`TimeoutStartSec=6h`。
- 两个 service 都带 `OnFailure=dsherp-backup-failure@%n.service`：模板 unit 运行 `{root}/bin/dsherp-admin notify-failure %i`，向 journal 写一行结构化失败记录，并在 profile 配置了 `alert_webhook` 时直接投递——不经过 worker，worker 停止时失败通知仍然存在（裁决 6）。（2.1）**只有确认投递成功才报 `posted`**：直接 `httpx.post` 并 `raise_for_status`，失败返回 `{"webhook": "failed", "error": …}` 且退出码 1，journal 行始终写；不用会吞异常的 `Notifier`。失败演练资料保留 14 日是上限，失败当时就通知并处理。
- `render_worker_units.py` 新增 `render_backup_units`；测试验证行为指令（`Type`、`OnCalendar`、`Persistent`、`ExecStart`、`User`、`OnFailure`、沙箱行），不锁整段文本。本机 dev 不装定时器。

### 4.4 可见性

- 状态文件 `<runtime>/backups/status.json`（`_write_json` 原子、0600）：

```json
{"format": 1,
 "runs": {"backup": {"last_attempt": {"at","ok","error"}, "last_success": {"at"}},
          "sync":   {"last_attempt": {...}, "last_success": {...}},
          "check":  {"last_attempt": {...}, "last_success": {...}},
          "drill":  {"last_attempt": {...}, "last_success": {...}}},
 "sites": {"<site>": {
    "backup":   {"last_attempt": {"at","ok","error","deferred"}, "last_success": {"at","set_id","stamp"}},
    "offsite":  {"last_attempt": {"at","ok","error"}, "last_complete": {"at","set_id","stamp"}},
    "verified": {"last_attempt": {"at","ok","error"}, "last_success": {"at","set_id","image_tag"}}}},
 "sets": {"<set_id>": {"site","kind","stamp","state","data_snapshot","secrets_snapshot","updated"}}}
```

- **监控范围来自清单（2.1）**：worker 每 tick（mtime 变化才解析）读状态文件，期望站点 = `resolved['platform_site']` + `tenants.json`，不是状态文件里出现过的站，也不是 worker profile。清单读不出来时**不缩小范围计算**：只发 `backup_scope_unknown`（critical）并把 `dsherp_backup_sites_expected` 置 -1。状态文件存在但损坏与缺失分开对待：worker 两者都发 `backup_status_missing`；操作命令遇到损坏的状态文件一律拒绝执行保留/本地修剪等删除动作（Fault 提示先修复或显式重建），生成与上传可以继续但报告里标注"状态损坏，未修剪"。纯函数 `backup_status.evaluate(status, expected_sites, now)` 产出 gauge 与告警。
- gauge（无标签，聚合）：`dsherp_backup_sites_expected`、`dsherp_backup_sites_rpo_ok`、`dsherp_backup_offsite_oldest_hours`（各站最新 complete 集数据时点年龄的最大值；有站从未完整则为 -1）、`dsherp_backup_local_oldest_hours`、`dsherp_backup_status_age_seconds`、`dsherp_backup_last_run_ok`、`dsherp_backup_unverified_days_max`。
- 告警键（`alerts.evaluate` 纯函数、常量阈值、边界测试）：`backup_rpo_warning`（warning：某站最新 complete 集 ≥ 20h）、`backup_rpo_unmet`（critical：≥ 24h 或从未完整；消息列站名）、`backup_run_failed`（warning：任一阶段的 `last_attempt` 失败且晚于 `last_success`）、`backup_status_missing`（critical：状态文件缺失/损坏，或 `runs.backup.last_attempt` 距今 > 13h——定时器失联）、`restore_unverified`（warning：某站 `verified.last_success` > 8 日或从未，新站宽限 8 日）。既有 `backup_stale`（ops 快照口径）保留。
- `doctor`：配置了仓库 URL 时四个密钥文件存在且 0600；prod 下仓库 URL 必须 `https`；本机存在 `dsherp-backup.timer` 时不做检查（systemd 状态由 runbook 验收行覆盖）。
- 故障注入（G6）：对象存储不可达 → `backup_run_failed`；删掉/损坏状态文件 → `backup_status_missing`；把 complete 集的 stamp 改旧 → `backup_rpo_warning`/`backup_rpo_unmet`；停掉 worker 后让 `backup --sync` 失败 → journal 里的 `OnFailure` 记录与 webhook 投递。证据记录 worker 日志原文、`/metrics` 原行与时延。

### 4.5 恢复验证

**隔离恢复栈**（复用"隔离预览"经验）：`infra/compose.restore.yml`，compose 项目 `dsherp-restore`：`db`、`redis-cache`、`redis-queue`、`backend`（本项目镜像）、`restore-fetch-data`、`restore-fetch-secrets`（restic 一次性，各带自己那一侧的口令与凭据，（2.1）各挂**各自**的取回卷 `restore-fetched-data`/`restore-fetched-secrets`，互相看不见；只有隔离栈的 backend 同时挂两个取回卷）；网络 `internal: true`，没有端口、caddy、worker、scheduler、queue、egress；自己的具名卷；站点配置 `mute_emails=1`、`pause_scheduler=1`、`maintenance_mode=0`（无入口可达）。（2.1）**按备份的版本启动**：先在生产栈用 `backup-sync-data` 的 `restic dump` 读回各站最新 `complete` 集的 `set.json`，按 `(image_tag, image_id)` 分组，每组用该 tag 拉起隔离栈，并用 `docker inspect` 核对隔离栈 backend 的运行镜像 id 等于集记录的 `image_id`（清单存在时也等于清单 id）；不是"先用当前 tag 起栈再拒绝不同的集"。该 tag 的镜像不在本机即该组失败并说明。（2.1）**凭据与接线**：`Bench` 支持指定 compose 项目、compose 文件与进程环境（镜像 tag、密钥目录），隔离栈的 bench 与拉栈用同一份环境；root/admin 口令与 `encryption_key` 只出现在 `Bench.script`（`env/bin/python -` 读 stdin 的进程内脚本，直接调用 `frappe.installer._new_site` 与 `frappe.commands.site` 的恢复函数）里，不上 argv、不写入任何共享卷；隔离栈自身的 `common_site_config.json` 只含 db/redis 地址。冷启动的 bench 初始化、凭据、私有 CA、镜像核验都走真实接线在阶段 3 末验证，假 Bench 只覆盖分支。

**`dsherp-admin restore-drill [--all | <site>…]`（每周，加：首次 `backup-init` 后、集的 `image_tag` 与上次验证不同时、`set.json` 格式号变化时）**：

1. 拒绝：上一次失败演练的卷仍在（除非已过 14 日保留期或显式 `--discard-failed`）；持有操作锁。
2. 读回元数据并按版本分组（见上）→ 起该版本的隔离栈并核对镜像 id → 两个 fetch 服务把每站的集取到各自的取回卷 → 按 `set.json`/`pair.json` 核对：三件数据 sha256、`config_sha256`、`snapshot.json` 重算的 `snapshot_sha256`、`set_sha256`、配对一致。
3. 逐站：进程内 `_new_site`（同名，栈隔离所以不冲突）→ 进程内恢复（转储 + 两个 files tar，`force`）→ 注入 `encryption_key`（只这一项）→ 不 migrate（版本相同）→ 快照 → 与 `snapshot.json` 用 `release_compare` 比对（容忍项只有 `RESTORE_EXPECTATIONS`）→ 容器脚本对 `__Auth` 抽样 `get_decrypted_password`，任一失败即该站验证失败。
4. 清理：成功 → `compose -p dsherp-restore down -v`（只删本项目自己的容器与卷，这是本次演练确切拥有的全部）；失败 → `down`（保留卷）+ 诊断包 `<runtime>/backups/drills/<drill_id>/`（报告、脱敏日志尾部，保留 14 日），状态记失败并告警。
5. 状态写 `verified`（含 `set_id`、`image_tag`）；`--all` 覆盖平台站与全部租户；退出码 0/1/2 同其他命令。

**`dsherp-admin restore-site <site> [--set <set_id>]`（G3 路径，runbook 新 §12；2.1 冷启动契约）**：在按 runbook §1–§6 拉起的新栈上，带入四个密钥文件（与 CA），`backup-init` 报 `kept`。契约：① 目标站在新栈上**必须不存在**，存在即拒绝（不覆盖，不自动清理）；② 先读回并核对元数据与制品：两侧 `dump` 清单配对、各件与快照摘要，再核对新栈运行镜像的 tag/id 等于集记录（不符即拒绝，提示先把 `prod.env` 切到该 tag）；③ 取回到本栈备份卷的临时目录（`compose run` 覆盖挂载为可写），凭据只在进程内脚本里使用；④ `provision-platform`/`provision-tenant` 幂等建站（新 db 口令、本机部署键）→ 进程内恢复 → 注入 `encryption_key` → 快照与集内快照比对 → 再跑一次 provision 让主机相关配置按新主机重算 → 解密抽样；⑤ 全部通过才解除维护并写 `current.json`（tag = 集的 tag）；任一步失败站点保持维护模式、报告说明、不自动重试。升级到更新的 tag 是随后显式的 `release`（G2）。本切片以本机第二个 compose 项目（`dsherp-drill`：独立数据库、卷、网络、runtime 目录）+ 本机 MinIO（按 digest 固定、私有 CA、restic 实际校验证书）做工程自证，并核实库账号 host 范围（G2 留项）；证据明确写"证明工具链，不证明物理异机容灾、真实异地副本与正式 RTO"。

### 4.6 配置、密钥与保管

- prod.env：`DSHERP_BACKUP_REPOSITORY`、`DSHERP_BACKUP_SECRETS_REPOSITORY`（`deploy_env.DEFAULTS` + 正则 + `prod.env.example`）；留空 = 未配置：`backup --sync`/`backup-init`/`restore-*` 拒绝，`doctor` 报告；（2.1）`DSHERP_ENV=prod` 下 `retire-tenant` 与 `release` 也拒绝（见 §4.1 第 8 条），dev 只能用显式 `--local-only`。
- 密钥目录：`backup_repository_password`、`backup_secrets_repository_password`（`secrets init` 生成）；`backup_storage_credentials`、`backup_secrets_storage_credentials`（运维提供，`AWS_ACCESS_KEY_ID=…`/`AWS_SECRET_ACCESS_KEY=…`；两个仓库用**不同的访问身份**，runbook 写明 IAM/桶策略：数据身份只能读写数据桶）；`backup_storage_ca.pem`（可选）。`ensure_secrets` 只生成前两者；后两者由 `doctor` 检查存在与 0600。
- 保管：两个口令 + 两份凭据 + CA 必须另存于对象存储之外（运维密码库/离线介质）；runbook 写明"丢失即等于丢失全部异地备份"；恢复主机靠授权的人带入，数据-only 身份无法解密密钥仓库。

### 4.7 保留与裁决 #10（如实口径）

- 异地：按站点 7 日 + 4 周 + 3 月，**按有快照的周期计算**：某周期内没有备份则不占名额；淘汰只在 `backup-sync` 运行时发生，任务不跑则不会自动到期；保护最新 complete 与最新 verified；`kind=retire` 不自动淘汰。本地：每站 3 套 + 同样的保护。
- 归档卷上的 `archived/releases/<tag>/<site>/` 与 `archived/sites/<site>` 不做目录级同步；它们所依赖的备份已按 §4.1 第 8 条以事件方式上传。
- 备份是个人数据的副本：按裁决 #10，删除只作用于在线数据；已生成的集不改写，随上述规则淘汰；`kind=retire` 集的最终去留与审计保留切片一起裁决。未来 `delete-user-data` 只影响之后的集。

### 4.8 错误处理

- 逐站隔离；缺件、sha256 不符、快照失败、窗口内标志设置失败都算该站失败，标志总是恢复、保持总是撤除。
- restic 每步超时（backup 3600s、snapshots 300s、forget/prune 1800s、check 3600s）；失败记脱敏尾部；本次运行不重试；pending 集下次先补。
- 操作锁冲突、状态文件写失败、仓库未配置：Fault（退出码 2）；有站失败：退出码 1。
- 演练失败：隔离栈保留供排查（14 日），下一次拒绝启动直到清理；恢复副本任何时候都没有入口与出网。

### 4.9 测试（按行为，不设数量门禁）

1. **协议与生成**（假 Bench/假 runner）：verbs 顺序（锁 → 保持 → 等待归零 → 标志 → backup → 快照 → 暂存 → 回读 → 恢复标志 → 撤保持）；`set.json`/`pair.json` 内容与 sha256；config 件从 `private/backups` 移出；本地保留与保护规则；在途运行超时推迟；锁冲突（并发路径：两个 `backup`、`backup` 对 `release`）；worker 的保持门（Coordinator 不领取被保持站）；`release`/`retire` 产出事件集与上传前置（`retire` 上传失败不 drop）。
2. **同步与保留**：两个服务各自的 argv/挂载/凭据（compose 契约按行为：digest、`cap_drop`、只读挂载、无端口、`provider` 网络、profiles）；单侧成功保持 pending 且下次只补另一侧；存储不可达；`retention.select` 用真实日期序列验证 7/4/3 与保护项与 `retire` 豁免；`forget` 只删两侧都淘汰的；`check --read-data-subset` 轮换；`backup-init` 幂等；状态文件更新与 `last_success` 不被失败覆盖。
3. **恢复**：`restore-drill` 流程（拒绝残留失败演练、取集配对核对、new-site 口令走 stdin、restore、注入 `encryption_key`、比对判定、解密抽样判定、成功 `down -v`/失败保留 + 诊断包）；`restore-site` 流程；隔离栈 compose 文件的行为契约（`internal` 网络、无端口、只含列出的服务）。
4. **定时与可见**：`render_backup_units`（两对 timer/service + `OnFailure` 模板）；`notify-failure`（journal 行 + webhook）；`backup_status.evaluate` 的边界（20h/24h、13h、8 日、新站宽限、期望站来自清单、状态缺失/损坏）；gauge 与 worker 读取；`deploy_env` 新键（prod 拒绝 http）；`doctor`。
5. **集成（dev 栈）**：真实 `backup` 一站，容器内核对两目录权限、config 件已移出、sha256 一致；`restore-drill` 对 dev 站真实跑一遍（本机 MinIO）。
6. **演练（本机生产形态）**：完整回路 backup → sync → 三种故障注入与告警时延 → 周演练 → 第二项目 `restore-site` 全站恢复比对与用时 → 保留淘汰用真实历史集验证。按审阅顺序：先 1，再 2，再 3，最后 4。

### 4.10 文档

runbook：§3 密钥（四个文件、两个身份、保管）、§6（两个一次性服务）、§7（两对 timer、`OnFailure`）、§9 验收表（`dsherp_backup_*` 行、`systemctl list-timers`）、§11（归档分目录）、新 §12「备份与容灾」（RPO/RTO 口径、12h 周期、状态文件、告警键、`restore-site` 步骤、密钥保管、保留语义与裁决 #10）；`data-governance-evidence.md` 新增「切片 2」；总体设计工作流 E 与计划 4 行同步（生成改为宿主 timer 调原生 `bench backup`）；runtime-baseline 加 restic/MinIO digest 行。

## 5. 不做的事

- 不改 Frappe 核心，不加 App 钩子，不用 Frappe 的 `encrypt_backup`，不新建指标平台。
- 不做目录级归档同步；不把 `.runtime/business-sessions` 纳入（T4 另行处理）。
- 不在 x86 服务器上演练；G3 正式验收仍由审计方执行。
- 历史 bench.log 的清理另排（本切片新增的调用路径不再写入明文口令）。
- 恢复时不 migrate（升级走 G2）。

## 6. 裁决记录与待确认

已采纳（第二次审阅 2026-09-06）：架构与默认取值放行；02:00/14:00 为 Asia/Shanghai；执行节奏为同会话顺序 TDD、每任务跑相关测试、阶段末全量回归 + 该阶段真实链路、四阶段末各一次检查点。已采纳（第一次审阅 2026-09-06）：① 宿主 timer，每 12h；② 一次性 restic 服务，按行为验证；③ 每周完整恢复 + 事件触发，日常做完整性与同步检查；④ 本机第二项目自证；⑤ 本地三套并保护最后一套已验证副本，下线/发布备份事件式异地保存；⑥ 状态文件 → worker，加 systemd `OnFailure` 通知。

已确认的取值：定时 02:00/14:00 Asia/Shanghai；RPO 预警 20h；定时失联判据 13h；恢复验证阈值 8 日；失败演练保留上限 14 日（失败当时即通知处理）；`kind=retire` 集不自动淘汰；两个仓库两份不同的存储身份。
