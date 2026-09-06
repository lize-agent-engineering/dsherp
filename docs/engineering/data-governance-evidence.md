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
| 非集成 `pytest tests --ignore=tests/integration` | `435 passed`（main 合并后 402；本切片新增 33 条：比对 14、读取 12、CLI 12 替换原 5 条、契约 1 处扩展） |
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
