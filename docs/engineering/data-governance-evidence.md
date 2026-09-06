# 计划 4：数据治理与容灾 证据

日期：2026-09-06 起。执行方式：按用户指示由 Claude 直接实施并自行入档（同计划 3），本文同时承担计划说明与证据两个角色。覆盖范围按[生产化总体设计](../superpowers/specs/2026-09-03-production-hardening-design.md)实施顺序表第 4 行：工作流 E 全部 + 工作流 B 的凭证托管与轮换；放行门 G2、G3、G7。切片顺序（[项目状态审查](project-state-review-2026-09-05.md)与用户裁决）：**G2 升级与恢复的可靠核验（本节）** → 备份「定时生成 → 异地同步 → 失败可见 → 异机恢复验证」→ 审计保留与数据生命周期 → 短期凭据与轮换。

**总判定**：本文不宣称任何门通过；每个切片只记录实测结果与未闭合项。按 spec 第 29 行，执行方自报的演练不是审计方的 G2 放行。

## 切片 1：升级与恢复结果的可靠核验（G2）

### 起点

[项目状态审查](project-state-review-2026-09-05.md) D 项与计划 3 收口的更正已确认：原 `release`/`rollback` 的 `SNAPSHOT` 只比对父表整表摘要，排除子表与单值文档，读取异常静默跳过，把 Patch Log、DocType、Custom Field 等随 migrate 合法变动的元数据一并比对，从未对真实站点跑过，`rollback` 恢复后不比对。勘察时一名只读侦察代理对 dev 站执行了这段脚本，`SELECT *` 全表读取 19,787 行 Comment 与 19,077 行 Deleted Document（LONGTEXT）把 1 GiB 限额的 dev MariaDB OOM 打挂（exit 137，重启策略 no，已由本人重启），同一次运行只报了 31 个 DocType 就"成功"——两个缺陷在真实站上同时坐实。

### 设计

- **范围按元数据在运行时决定，每个 DocType 必入一桶**（`dsherp/release_snapshot.py`）：**严格**——erpnext 与两个 dsherp App 的全部 DocType（含子表）、`custom=1` 的自定义 DocType、Contacts 模块、身份与配置表（User、Has Role、User Permission、File、OAuth Client、Social Login Key 等），逐行逐字段；**日志**——Comment/Version/Deleted Document/Communication/Activity Log，只比哈希、允许新增；**排除**——Frappe 自身的元数据、设置机器与技术日志。租户写过的元数据按行分区：`tabDocType` 只取 `custom=1`，其 DocField/DocPerm 只取自定义 DocType 的，Custom Field/Property Setter 只取 `is_system_generated=0`。单值文档取 erpnext/dsherp 模块的 Settings 与 System Settings；`__Auth` 按行摘要。
- **读取**：原生 SQL 按 name 键集分页（2,000 行/页），每行求哈希；严格桶且 ≤5,000 行的表保留字段值以便报出逐字段差异，更大的表与日志桶只留哈希；列名匹配 `secret|password|token` 的值只存摘要。任何一张表读取异常即中止并点名，不会带着"部分快照"下结论。
- **判定**（`dsherp/release_compare.py`，纯 Python）：升级后新出现的表和列是 schema 变化，报为信息；消失的表和列、行的增删改都是差异；差异只有被某个 patch 声明过才算"预期"——patch 模块定义 `EXPECTED_CHANGES = [{'doctype', 'fields', 'rows': existing|inserted|deleted|any, 'columns_removed'}]`，`release` 从新镜像里两个 App 的 patches.txt 逐条导入收集。`clean` 只在没有未声明差异时为真；明细上限 500 条，计数不封顶。
- **`release <tag> [--from 旧 tag]`**（`dsherp/admin.py`）：预检——`prod.env` 的 tag、两个 bench **运行容器**的镜像 tag（读容器）、每站无在飞运行、该 tag 尚无升级前基线（只写一次）；写发布记录（新旧 tag、镜像与镜像 id）→ 对每个站（租户站与平台站）：写 `maintenance_mode`/`pause_scheduler` 静默 → `bench backup --with-files` → 四件套复制到 `archived/releases/<tag>/<站>/`（backend 的 `tenant-archive` 卷与新增的 platform-backend `platform-archive` 卷，躲开 Frappe 23 小时的自动清理）→ 升级前快照落 `.runtime/releases/<tag>/<站>/before.json` → `bench migrate` → 升级后快照（按升级前列集求哈希）→ 从 Patch Log 差集得到本次执行的 patch，只采纳它们的声明 → 比对 → **只有干净才恢复站点标志**。任何阶段失败：站点保持维护模式，写带 `failed`（站、阶段、原因）的部分报告，退出码 2；不干净：该站保持维护，退出码 1。全部干净后 `current.json` 记为新 tag。报告文件带时间戳不覆盖。
- **`rollback <tag>`**：参数是要撤销的发布；先核对 `prod.env` 与两个运行容器都是发布记录里的 `previous_tag`（否则拒绝，数据不动）；从记录找到归档备份集（`--backup SITE=FILE` 可覆盖），静默后 `bench restore <db> --with-public-files … --with-private-files … --force`（root 口令经脱敏），恢复后快照并与 `before.json` 比对，唯一容忍的是 restore 自己回写的 `System Settings.enable_scheduler`（单值文档的 `modified`/`modified_by` 一律不比）；干净的站才重新开放，全部干净后 `current.json` 记回旧 tag；恢复失败退出码 2、有差异退出码 1，站点保持维护。
- 新增 `snapshot <站> --out FILE`、`compare BEFORE AFTER`（演练与排查）、`resume-site <站>`（人确认后解除维护的唯一途径）、`forget-release <tag>`（删除宿主侧记录）。

### 测试

| 文件 | 内容 |
|---|---|
| `tests/test_release_compare.py`（10 条） | 相同快照干净；字段变化带前后值；行增删、单值与 `__Auth` 变化；新增列/表为信息、删除列/表为差异；patch 声明只放行它声明的字段与行种类；声明格式校验；只有哈希的行也能判出变化；明细封顶计数不封顶；哈希与键序无关、对易变列不敏感 |
| `tests/test_release_snapshot.py`（7 条） | 用假 `frappe` 执行真实读取模块：分桶规则覆盖每个 DocType、单值与虚拟 DocType；键集分页三次读完 4,500 行并保留值；6,000 行与日志桶只留哈希；读失败点名中止；密钥列摘要化、单值与 `__Auth` 采集；Custom Field 按 `is_system_generated` 分区；容器脚本无三引号且带标记 |
| `tests/test_admin_cli.py`（8 条 G2 用例） | release 各站步骤顺序（静默→备份→归档→快照→migrate→快照→恢复标志）、报告与记录文件；未声明漂移与 patch 声明；tag 不符/有在飞运行/备份集不全的拒绝；migrate 失败保持维护模式并写部分报告；rollback 恢复归档集（带 files、`--force`）并与升级前快照比对；不一致时不干净、显式备份覆盖发现；无记录或备份文件缺失时拒绝；snapshot/compare 命令 |
| `tests/test_deployment_contract.py` | platform-backend 挂 `platform-archive` 卷 |

### 本机生产形态演练（2026-09-06）

（演练结果见下表；材料：项目名 `dsherp`，镜像 `local/dsherp-frappe:v0.3.1-rc2`，租户 `g1.localhost` 先由 `provision-tenant` 建为当前 schema，再用 dev alpha 站 2026-09-03 的备份覆盖成**旧 schema 的真实形状数据**——该备份早于 DS Run Event / DS Ops Snapshot 两张表与 DS Model Run/Proposal 六列的引入，因此 `bench migrate` 是真实的 DDL 同步而非空转。）

| 步骤 | 实测（第二轮，口径校准后） |
|---|---|
| 起栈 + `provision-platform` + `provision-tenant g1` | 数据面与两 bench 起来后 39 s 两站可用（第二轮为幂等重跑 `kept`） |
| 用 2026-09-03 alpha 备份覆盖 g1 | `bench restore --force` 8 s；恢复后 752 张表、无 `tabDS Run Event`、DS Model Run 30 列、在飞运行 0、Sales Order Item 1 行 |
| `release v0.3.1-rc2` | **退出码 0，33 s**。g1：531 张表 14,498 行、31 个单值、7 条 `__Auth`；差异 4 条全部为 Comment/Deleted Document/Version 的新增（日志桶允许），未声明 0；新增表 `tabDS Ops Snapshot`、`tabDS Run Event`，新增列 DS Model Run 4 列、DS Operation Proposal 2 列——即真实 DDL 同步被如实报为 schema 变化而非漂移；migrate 尾行 `Queued rebuilding of search index`。平台站：31 张表 34 行，差异 2 条（日志新增），未声明 0。备份集四件套已复制到 `archived/releases/v0.3.1-rc2/<站>/`，站点标志已恢复 |
| 升级后注入一处业务字段改动（`Sales Order Item.qty` 2 → 3） | `snapshot` + `compare after.json`：退出码 1，差异恰为 `tabSales Order Item / 9flrmaeb49 / qty / 2.0 → 3.0 / 未声明` |
| `rollback v0.3.1-rc2` | **退出码 0，19 s**。两站与升级前快照差异 0（g1 10.4 s、平台 5.3 s）；恢复后 `tabDS Run Event` 不存在（旧 schema 回来了）、注入的改动被抹掉、`maintenance_mode`/`pause_scheduler` 均为 0 |
| 恢复后再删一条 Item，与升级前快照比对 | 退出码 1，差异 `tabItem / DSHERP-HITL-ITEM / deleted` |

第一轮（口径校准前）的两处未声明差异及处置：`tabHas Role` 新增一行——它挂在 migrate 重新导入的标准 Report `DS Agent Audit` 下，不是身份数据，`Has Role` 改为只取 `parenttype in ('User','Role Profile')` 的行；`System Settings.modified` 在 migrate 中被重新保存而值未变——单值文档的 `modified`/`modified_by` 不再比对（其余字段照比）。两条各有单元测试锁定。restore 自己回写的 `System Settings.enable_scheduler` 作为 rollback 的唯一声明容忍。

快照大小：g1 的 `before.json` 3.6 MB（严格桶保留字段值），读取耗时最长的表 Deleted Document 0.22 s、Comment 0.12 s（分页哈希）。演练后 `compose down -v` 拆栈、`.runtime/prod-local/` 清理；`local/dsherp-*:v0.3.1-rc2` 镜像与 `infra/env/prod.env` 保留给后续切片。

### 门禁

| 门 | 结果 |
|---|---|
| 非集成 `pytest tests --ignore=tests/integration` | `441 passed`（main 合并后 402；本切片新增 39 条） |
| Node / 前端 | Node `10/10`；前端未改动 |
| 集成 | 本切片不改 Frappe App 代码，未重跑集成门 |

### 未闭合

- G2 按 spec 第 29 行仍待审计方在合规主机上执行；本节是执行方自报的本机演练（含跨版本）。
- 材料是 DDL 同步而非带数据 patch 的升级：仓库两个 App 的 patches.txt 至今为空，`EXPECTED_CHANGES` 只在单元测试里被验证；第一个带 patch 的发布（例如后续切片给 DS Model Run 加用量字段并回填）会是它的第一次真实使用。
- 平台站的 `OAuth Client.client_secret` 是 Data 字段，快照里按列名规则只存摘要；`bench.log` 里历史遗留的明文 root 口令行不属本切片，留待备份切片一并处理。
- `bench restore` 在 MariaDB 里以连接来源主机建库账号（勘察未核实），与 `provision-tenant` 的 `%` 作用域是否冲突，待备份切片的异机恢复演练核实。


### PR #7 审查（GPT，2026-09-06）的六组问题与处置

| # | 问题 | 处置 | 测试 |
|---|---|---|---|
| R1 | 迁移后快照失败、restore 失败或比对不干净仍解除维护 | 每站每阶段记录 `step`；任何异常都写带 `failed` 的部分报告并保持维护（退出码 2）；比对不干净的站保持维护（退出码 1），报告 `maintenance: kept`；只有干净才恢复标志；新增 `resume-site` 作为人确认后的唯一解除途径 | `test_failure_after_migrate_or_an_unclean_result_keeps_the_site_in_maintenance_until_resumed_deliberately`、`test_a_rollback_requires_the_previous_images_running_and_keeps_maintenance_on_failure_or_drift` |
| R2 | 同 tag 重跑覆盖回滚基线 | 任一站已有 `before.json` 即拒绝，基线只写一次；重来要换 tag 或显式 `forget-release`；预检失败（在飞运行、镜像不符）发生在写基线之前，不锁死重试 | `test_a_release_binds_the_running_images_records_the_previous_tag_and_refuses_a_second_run_of_the_same_tag` |
| R3 | 大表字段声明放行任意变化；大表加列后整行哈希误报 | 只留哈希的行只能被 `fields: ['*']` 的整行声明放行，字段声明不再匹配；升级后快照按升级前的列集求哈希（`hash_columns`），加列不改变未变行的哈希 | `test_a_hash_only_change_is_declared_only_by_a_whole_row_declaration_never_by_a_field_list`、`test_the_after_snapshot_hashes_over_the_columns_the_before_snapshot_had…`、`test_only_patches_this_migrate_executed…` |
| R4 | 租户权限与工作流被按模块排除 | Role、Custom DocPerm、Workflow 族（含子表）、Notification/Report/Print Format/Web Form（按 `is_standard` 分区）、Client/Server Script、Letter Head、Assignment Rule、Auto Repeat、Email Account、Webhook 进严格桶 | `test_tenant_permissions_and_workflows_are_strict_and_standard_definitions_are_partitioned_out` |
| R5 | 历史 patch 声明持续生效 | 快照记录 Patch Log；本次执行的 patch = 升级后减升级前；只有它们的声明被采纳，其余记入 `expectations_ignored` | `test_only_patches_this_migrate_executed_may_declare_changes…` |
| R6 | 未绑定升级前镜像与回滚目标 | `release --from <旧 tag>`（有 `current.json` 时可省略）记录 `previous_tag` 与两个运行容器的镜像/镜像 id，并核对运行容器就是新 tag；`rollback` 要求 `prod.env` 与运行容器都是 `previous_tag`；干净后 `current.json` 切换 | 同 R2/R1 的两条；本机跨版本演练（下表） |

同批快照边界：Single 首次落库的值算变化（不再当作新 schema）；Single 值也走密钥摘要；行哈希 helper 统一为 `release_snapshot.row_hash`，快照带 `format` 版本且不同版本拒绝比对；行数上限 `max_rows`（默认 100 万）超过即中止——分页降低的是数据库单次读取压力，快照本身仍是一个文档，这里不宣称内存无风险。

### 本机跨版本演练（2026-09-06，审查 R6 要求）

材料：从 `v0.3.1-rc1` 的干净 worktree 按 tag 构建 rc1 镜像（revision `1cd8927`）；rc2 镜像即已推送的候选。栈以 **rc1** 起，`provision-platform`/`provision-tenant g1` 后用 2026-09-03 旧 schema 备份覆盖 g1；然后改 `prod.env` 到 rc2 并 `compose up -d`（两 bench 换成 rc2 容器）。

| 步骤 | 实测 |
|---|---|
| `release v0.3.1-rc2 --from v0.3.1-rc1`（运行容器 rc2） | **退出码 0**。记录 `previous_tag=v0.3.1-rc1`，两个容器镜像 `local/dsherp-frappe:v0.3.1-rc2` 与镜像 id 入档；g1 550 张表 14,579 行，差异 4 条全为日志新增，未声明 0，`patches_executed=[]`，新增表 `tabDS Ops Snapshot`/`tabDS Run Event`、新增列 DS Model Run 4、DS Operation Proposal 2；平台站 50 张表 70 行，差异 2 条日志新增；两站 `maintenance: released`；`current.json` 记为 rc2。g1 19.7 s，平台 10.9 s |
| 同 tag 再发布 | 退出码 2：「已有升级前基线……基线只写一次」，基线未被覆盖 |
| rc2 上注入 `Sales Order Item.qty` 2 → 3 | `compare` 退出码 1，差异恰为该字段 |
| rc2 仍在运行时 `rollback v0.3.1-rc2` | 退出码 2：「要在升级前的版本 v0.3.1-rc1 上进行……先改回并 compose up -d」，数据未动 |
| 改回 rc1 并 `compose up -d` 后 `rollback v0.3.1-rc2`（运行容器 rc1） | **退出码 0**。报告记录两个容器镜像 `local/dsherp-frappe:v0.3.1-rc1`；两站与升级前快照差异 0（g1 11.1 s，平台 5.5 s），`maintenance: released`，`current.json` 记回 rc1（含镜像 id） |
| 恢复后删一条 Item，与基线比对 | 退出码 1，差异 `tabItem / DSHERP-HITL-ITEM / deleted` |

演练后 `compose down -v` 拆栈、`.runtime/prod-local/` 清理；rc1/rc2 镜像与本地 `prod.env`（tag 复位为 rc2）保留。失败边界（migrate 后快照失败、restore 失败、比对不干净）在本机无法不破坏容器地制造，由带假 bench 的单元测试覆盖（R1 一行）。

### PR #7 第二轮审查（GPT，2026-09-06）的四处与处置

| 优先级 | 问题 | 处置 | 测试 |
|---|---|---|---|
| P1 | `forget-release` 未校验 tag，`../` 可删到发布记录目录之外 | tag 必须匹配 `deploy_env.TAG`；目标解析后必须是发布记录根目录下的普通子目录（拒绝符号链接与越界路径） | `test_forget_release_validates_the_tag_and_never_leaves_the_release_records_root` |
| P1 | 镜像身份只比 tag 后缀，记录的镜像 id 不参与校验 | `release` 要求两个 bench 运行的镜像**全名**等于 `prod.env` 解析出的发布镜像，且当 `infra/releases/<tag>.json` 清单存在时镜像 id 等于清单记录的 id；发布记录同时保存 `previous_images`（来自 `current.json`，即升级前实际运行的镜像与 id）；`rollback` 要求运行的镜像全名等于旧 tag 的镜像，id 等于 `previous_images`（或旧 tag 清单）记录的 id | `test_image_identity_is_checked_by_full_name_and_by_manifest_id_not_by_tag_suffix` |
| P2 | 行数上限在整表读完后才检查 | 先用 count 在读取前拒绝明显超限，再在分页累计时按剩余预算检查（count 可能陈旧） | `test_the_row_ceiling_is_enforced_before_reading_and_while_paging_not_after` |
| P2 | 快照没保存准确的哈希列集合，普通快照与 release 快照口径不一致 | 每张表保存 `hash_columns`（求哈希时实际覆盖的列）；只留哈希的表在两份快照列集不同时拒绝比对并提示用 `snapshot --like <快照>` 对齐；`release`/`rollback` 的第二份快照都按第一份的 `hash_columns` 求哈希 | `test_every_snapshot_records_the_exact_columns_each_hash_covers`、`test_hash_only_rows_are_compared_only_when_both_snapshots_hashed_the_same_columns`、`test_snapshot_command_can_align_its_hash_columns_with_an_existing_snapshot` |

**第二轮复审修法后的跨版本重跑（2026-09-06 16:42–16:45）**：`release v0.3.1-rc2 --from v0.3.1-rc1` 在 rc2 容器上退出码 0，两个容器镜像 id `sha256:c9147d67…` 与 `infra/releases/v0.3.1-rc2.json` 清单一致（清单 id 校验第一次真实生效）；同 tag 再发布退出码 2；注入的 qty 改动用 `snapshot --like after.json` 对齐后被点名；rc2 仍在运行时回滚退出码 2。切回 rc1 后回滚**先被拒绝**：运行的 rc1 镜像 id `sha256:9f7ec33e…` 与仓库里 rc1 清单记录的上一次本机构建 `sha256:2de0a39f…` 不同——同一提交本机两次构建的镜像 id 不同，id 校验如实判定"不是同一个制品"。rc1 从未推送，清单只是本机演练记录，因此按当前 rc1 构建重生成清单（`infra/releases/v0.3.1-rc1.json`，id `9f7ec33e…`）后再回滚：退出码 0，两站差异 0（g1 10.2 s、平台 5.3 s），`current.json` 记回 rc1 并带镜像 id。这条边界在 runbook 里的含义：目标主机必须运行清单记录的那一次构建（从 registry 拉取或 `docker load` 构建机导出的镜像），本地重建会被拒绝。演练后拆栈清理。

### PR #7 第三轮审查（GPT，2026-09-06）的一处阻断与处置

审查独立重跑 441 passed，关闭了删除范围、分页预算、哈希列集三项，只剩一处 P1：`_manifest_image_id` 在清单缺失、JSON 损坏、没有对应镜像、id 为空四种情况下都返回 `None`，`_require_running` 随即把"没有预期 id"当作"不核对"，错误的镜像 id 被接受。审查还指出这不是假设：`v0.3.1-rc2` 清单在 `d7e115b` 入库，而 tag 指向 `690136a`，按 tag 取源码的主机确实没有它。处置：

| 规则 | 实现 | 测试 |
|---|---|---|
| release 必须取得**有效**的目标清单，否则在改动站点前失败 | `_manifest_image_id` 不再返回 `None`：文件不存在、读不出或不是 JSON、`tag` 字段不等于目标 tag、`images` 里没有该镜像、`id` 不是 `sha256:` 开头的非空字符串，各自抛出说明原因的 `Fault`；`_require_image_ids` 对没有预期 id 的服务直接拒绝，不再跳过；镜像全名检查、清单校验、id 校验都在 `_targets`、写记录与静默之前 | `test_release_refuses_unless_a_valid_manifest_vouches_for_the_running_image_id`：缺失、损坏、无该镜像、`""`、`null`、他 tag 的清单、有效清单但运行 id 不同，七种都拒绝且 bench 无任何动作、不写 `release.json`；`--manifest` 与源码树内清单两种来源都能放行 |
| rollback 用**完整有效**的 `previous_images`；没有时必须取得有效旧清单；两者都拿不到 id 就拒绝恢复 | `_previous_ids`：记录里没有 `previous_images` 才返回 `None`（只有第一次发布如此）；存在但缺服务、镜像名不符、id 为空或非字符串，一律 `Fault`，不再"有多少用多少"；`None` 时才用旧 tag 清单，清单同样严格校验 | `test_rollback_needs_complete_previous_images_or_a_valid_old_manifest_and_never_restores_blind`：无记录且无清单、清单是 `[]`、id 为空、清单 id 与运行不符，四种都在静默与 restore 之前拒绝；`--manifest` 指定有效旧清单后干净；记录里的 `previous_images` 缺一个服务 / id 为空 / 镜像名不同，即使旧清单有效也拒绝 |
| 清单随发布制品交付，不依赖 tag 之后的仓库提交 | `release_images.py --bundle DIR`：`docker save` 两个镜像到 `DIR/dsherp-<tag>.tar` 并把清单复制为 `DIR/<tag>.json`，save 失败不留半份；`release`/`rollback` 查找顺序 `--manifest FILE` → `infra/releases/<tag>.json` → `<runtime>/manifests/<tag>.json`（放在 `releases/` 之外，`forget-release` 的包含性检查碰不到它）；runbook 第 2 步与第 10 步改写 | `test_a_bundle_ships_the_images_and_the_manifest_together`；`test_the_cli_passes_an_explicit_manifest_to_release_and_rollback`；测试夹具改为"构建机交付两份清单到运行目录"、假 docker 按镜像而不是按服务派生 id |

门禁：非集成 445 passed（新增 4 个测试）。`--bundle` 用本机 rc2 镜像真实跑过一次（2026-09-06 17:20）：`docker save` 52 s 产出 3.48 GB 的 `dsherp-v0.3.1-rc2.tar`，tar 内 docker manifest 的两个 config 摘要 `sha256:c9147d67…`（frappe）与 `sha256:ec323b54…`（worker）就是 `infra/releases/v0.3.1-rc2.json` 记录的镜像 id，旁边的 `v0.3.1-rc2.json` 与仓库清单逐字节相同；产物已删除。对抗核验后的加固版又真实跑了一次（17:45，46 s）：`_saved_digests` 从真正的 save tar 里读出的两个 config 摘要与清单 id 一致才放清单，重复 bundle 到同一目录被拒绝，`--bundle dist`（源码树内）被拒绝且没有创建目录；产物已删除。

**对第三轮修法的对抗核验（2026-09-06）**：修法完成后派 5 个只读代理分别从"release 绕过""rollback 绕过""测试诚实性""操作链与交付""既有行为回归"五个视角攻击工作树差异，25 条候选每条再由 2 名反驳者独立复核（共 55 个代理，不碰容器与数据库）。处置如下：

| 类别 | 发现 | 处置 |
|---|---|---|
| 修代码 | `_running_images` 只查 `compose ps -q` 的第一个容器，同服务第二个容器不核对（预存问题） | 多于一个容器直接拒绝 |
| 修代码 | 仓库清单遮蔽交付清单、`--manifest ""` 被当作未给、拒绝信息不说用的是哪份 | 显式路径只看它且不能为空；缺省两处都有时必须一致，否则列出两份拒绝；来源与路径进入拒绝信息 |
| 修代码 | `release --from` 与 `current.json` 矛盾时静默丢弃 `previous_images`，回滚以人打的 tag 为锚 | 有记录而 `--from` 不同即拒绝 |
| 修代码 | 首次发布后回滚才发现旧 tag 清单不在主机上，正是需要它时被挡住 | 没有 `current.json` 时 `release` 先核对旧 tag 清单可用；有 `current.json` 时先核对其镜像记录完整 |
| 修代码 | `release.json` 缺 `previous_tag` 抛 `KeyError`；`_previous_ids` 不校验 id 形状；记录有 `previous_images` 时 `--manifest` 被静默忽略（连不存在的路径也不报） | 各自 `Fault`；id 形状与清单同一口径；`--manifest` 总会被读取并须与记录一致 |
| 修代码 | `bundle()` 静默覆盖旧包，tar 与旁边的清单没有绑定；runbook 示例把 3.5 GB 的 tar 写进构建上下文（`.dockerignore` 有意不排除 `dist`） | 拒绝覆盖；save 后读 tar 的 `manifest.json`，config 摘要必须等于清单 id 才放清单；目录必须在源码树外；runbook 改为 `../dsherp-dist/` |
| 修测试 | 缺失/损坏/空 id 的断言只匹配"清单"或"镜像 id"，回退到"无 id 不核对"时会被后一道检查的措辞顶过去；rollback 兜底没测损坏 JSON 与无该镜像；`previous_images` 不完整的断言也会被部分集合顶过去；`_require_image_ids` 的"无预期 id"分支无隔离测试；来源优先级未测；测试隐含依赖真实仓库没有 `v0.3.0/v0.4.0` 清单；`--bundle` 的 CLI 接线未测 | 每种拒绝钉住自己的措辞；补齐上述场景；夹具显式断言前置条件；补 CLI 接线测试 |
| 改文档 | runbook 第 10 步从不更新主机源码树，升级跑的是旧 tag 的 `dsherp-admin`；构建机与主机的 Docker 镜像存储类型不同会让同一制品 id 不同（经典 overlay2 记 config 摘要，containerd 存储记 manifest 摘要） | 第 10 步加"先换源码树"；第 2 步注明必须同种镜像存储 |
| 不是缺陷 | 同 tag 重做（`forget-release` 后再 `release` 同一 tag，或 `--from` 等于目标 tag）让 `previous_tag == tag`，回滚在"新"镜像上恢复 | 有意允许：重做前的备份就是在这个镜像上做的，恢复它到同一镜像是一致的，既有测试即如此断言 |
| 不适用 | 早于本修法开通的主机没有旧 tag 清单 | 目前没有真实部署的主机（G1 未过），无存量 |

门禁：非集成 452 passed（较上一轮再加 7 个测试）。

## 切片 2：备份——定时生成 → 异地同步 → 失败可见 → 异机恢复验证

设计 [2.1 版](../superpowers/specs/2026-09-06-backup-offsite-design.md)（第 1 版经[备份设计审阅](backup-design-review-2026-09-06.md)、第 2 版经[设计 v2 与实施计划审阅](backup-v2-plan-review-2026-09-06.md)两轮修订）；实施计划 [2026-09-06-backup-offsite](../superpowers/plans/2026-09-06-backup-offsite.md)。分支 `plan4/backup`。

### 设计要点与它们各自防的事

| 机制 | 防的是 |
|---|---|
| 备份集协议：一个 id 贯穿两侧目录、两个仓库标签、`set.json`/`pair.json`，每件（含快照与 site_config）都有 sha256 | 数据与密钥配错批次；远端内容与本机不符而无人察觉 |
| 稳定窗口：保持文件 + 服务端 `dsherp_hold` 闸门 + 等在途执行者 + 维护标志 + 排空 RQ 与数据库连接，逆序撤销 | 备份文件与核验快照描述不同状态；排队的运行让备份永远推迟；worker 已过检查而 claim 未落地的缝 |
| `complete` 必须读回两份清单逐项核对，且每次运行重新向仓库确认 | "两个快照 id"被当成配对成功；远端副本被删或被改后仍算数 |
| 保留在宿主侧按站点算，两侧都判淘汰才 forget 该集全部快照 | restic 按唯一标签分组导致永不淘汰；一侧删干净另一侧留下孤儿 |
| 隔离恢复栈：internal 网络、无入口无 worker、按集记录的构建起栈并核对镜像 id、凭据只走 stdin | 恢复副本对外产生副作用；在错误的版本上恢复；新链路继续往 bench.log 写明文口令 |
| 生产未配置仓库即拒绝发布与下线；下线的最终集必须完整到达异地才 drop | 最后一份状态只留在本机 |
| 状态文件 → worker gauge/告警 + systemd `OnFailure` 兜底 | 监控随 worker 一起消失；范围被静默缩小 |

### 真实链路验证（本机，2026-09-06）

演练环境：dev 栈（两个 bench 挂上备份卷与备份密钥卷）+ `infra/drills/minio.yml` 起的 MinIO（按 index digest 固定、自签 CA、两个桶、两个只能读写各自桶的身份）。它证明的是工具链与契约，**不证明**物理异机容灾、真实异地副本与正式 RTO。

| 环节 | 实测 |
|---|---|
| 生成 | `backup` 对 `dsherp-platform.localhost` 真实开窗：`dsherp_hold` → 等在途执行者 → 维护标志 → 排空写入者 → `bench backup --with-files` → 快照 → 逆序撤销。集内五个文件与两个密钥侧文件齐全，密钥目录 0700、文件 0600，`private/backups` 里已无 `site_config_backup.json`，逐件 sha256 与 `set.json` 一致（集成测试 `tests/integration/test_backup_sets_real.py`） |
| 仓库初始化 | `backup-init` 经私有 CA 的 TLS 建两个仓库，重跑报 `kept` |
| 上传与配对 | 上传后从两个仓库 `dump` 读回 `set.json` 与 `pair.json` 逐项核对才标 `complete`；**伪造的历史集（复制目录未改 set.json）被如实拒绝** |
| 单边丢失 | 在密钥仓库 `forget` 掉一侧后再同步：如实降级并在同一次运行补齐，`errors` 里点名 |
| 内容不符 | 远端快照内容与本机暂存不一致时按本机重传一次，再核对通过才算完整 |
| 保留淘汰 | 造 20 个跨 20 个月的历史集：一次同步后两侧各剩 7 个（7 日槽位吃掉了 7 个不同日期），记录同步收敛，第二次运行无操作 |
| 完整性 | 每次同步两侧 `check --read-data-subset=<n>/7`，按天轮换 |
| 隔离恢复 | `restore-drill` 在 `dsherp-restore` 项目里按集记录的构建起栈、核对镜像 id、取回两侧、核对配对与全部摘要、进程内恢复、注入 `encryption_key`、比对与抽样解密：**50 张表 1311 行 0 处未声明差异，6 个加密字段全部解密通过，8.1 秒**；成功后 `down -v` 只删本项目的容器与卷，第二次演练无需 `--discard-failed` 即可开始 |
| 故障注入 | 见下表 |
| 调度表达式 | `*-*-* 02,14:00:00 Asia/Shanghai` 与 `Sun *-*-* 04:00:00 Asia/Shanghai` 由真实 `systemd-analyze calendar`（容器内）确认可解析并给出下次触发 |

### 门禁

| 门 | 结果 |
|---|---|
| 非集成 pytest | `533 passed`（切片前 504） |
| 集成（dev 栈） | 备份生成两项：`tests/integration/test_backup_sets_real.py`；保持闸门一项：`tests/integration/test_backup_hold_gate.py` |
| compose 与单元契约 | 生产/开发/恢复三个 compose 与五个 systemd 单元按行为验证（不锁服务数量与文本排版），日历表达式经真实 systemd 解析 |

### 未闭合

- **G3 正式验收**：本轮全部演练在一台机器上完成，用自建对象存储与自签 CA。它证明工具链与契约，不证明物理异机容灾、真实异地副本与正式 RTO。真正的 G3 仍待审计方在另一台主机上执行。
- **`restore-site` 未跑过真机全流程**：冷启动路径的每个拒绝点与接线都有测试，但"新主机上从零恢复整套站点"尚未真实执行（本机第二个生产形态栈的演练排在 G1/ACME 主机就绪之后）。
- **定时器未在真机运行过**：单元与日历表达式已验证，但 12 小时周期与每周演练的实际触发要在有 systemd 的主机上观察。
- **`kind=retire` 集的最终去留**：不自动淘汰，与审计保留切片一起裁决。
- **bench.log 历史明文口令**：本切片新增的调用路径不再写入（凭据只走解释器 stdin），历史清理另排。
- **单机演练的排障步骤**：对象存储与恢复栈分属不同 compose 项目，演练时需要把它接进恢复栈的出网网络；真实部署里 `provider` 网络本身有出口，不需要这一步。
