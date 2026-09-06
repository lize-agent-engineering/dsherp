# 备份切片（定时生成 → 异地同步 → 失败可见 → 异机恢复验证）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个站点（平台站 + 全部租户站）每 12h 在稳定窗口里生成四件套 + G2 快照，绑定为一个备份集，分数据/密钥两个 restic 仓库异地保存并配对确认，按站点维度保留 7/4/3，任何失败 5 分钟内可见，每周在隔离栈里从异地副本恢复并核验。

**Architecture:** 宿主 systemd timer 驱动 `dsherp-admin backup --sync`（`Bench.run` 进 backend/platform-backend 执行原生 `bench backup --with-files` + `release_snapshot`），备份集暂存在两个新卷（数据 `*-backups`、密钥 `*-backup-secrets`）；两个按 digest 固定的一次性 restic compose 服务各自只挂自己那一侧的卷、只带自己那一侧的口令与凭据；宿主状态文件记录每站每阶段的 last_attempt/last_success，worker 每 tick 读它产出 gauge 与告警，systemd `OnFailure` 在 worker 停止时兜底；每周 `restore-drill` 在独立 compose 项目 `dsherp-restore`（internal 网络、无入口）里按备份记录的镜像 tag 恢复、比对、抽样解密、精确清理。

**Tech Stack:** Python 3.12（仓库 `.venv`）、pytest、Docker Compose v2、restic（容器镜像，实施时固定版本与 digest）、MariaDB/Frappe 16.31（现有镜像）、systemd。

## Global Constraints

- 设计：`docs/superpowers/specs/2026-09-06-backup-offsite-design.md`（第 2 版）；审阅：`docs/engineering/backup-design-review-2026-09-06.md`。以下取值来自设计 §6：定时 `02:00,14:00`；RPO 预警 20h、未满足 24h；定时失联 13h；恢复验证阈值 8 日；失败演练保留 14 日；`kind=retire` 集不自动淘汰；两仓库两份不同存储凭据。
- TDD：每个行为先写失败测试再实现；测试按行为，不锁服务数量、不锁 unit 整段文本（审阅裁决 2）。
- 容器命令只经 `admin.Bench.run/python`（`docker compose exec -T`），`Bench.python` 的 body 不能含三引号字符串；含密钥的 argv 必须传 `secrets=`；**本切片新增的 `new-site`/`restore`/`set-config` 路径不得把 root 口令、admin 口令或 `encryption_key` 放上 argv**（Frappe 会从 `common_site_config.json` 的 `root_password`/`admin_password` 读取；`Bench.python` 走 stdin）。
- 生产 compose：第三方镜像 `@sha256:` 固定、只用 `[a-z0-9-]+` 具名卷、不发布端口、只有 `provider` 网络可出网；新服务 `cap_drop: [ALL]`、`security_opt: [no-new-privileges:true]`。
- 宿主侧文件用 `admin._write_json`（0600、原子）写在 `admin.runtime_dir(resolved, root)` 下；密钥文件 0600 且不打印。
- 所有拒绝都是 `admin.Fault`（中文，说明查什么，不说怎么强行通过）→ 退出码 2；有站失败退出码 1。
- 备份集四件：`database.sql.gz`、`files.tar`、`private-files.tar`（数据侧）与 `site_config_backup.json`（密钥侧）；不用 `--compress`。
- 提交信息用中文 `feat:/fix:/test:/docs:` 前缀，分支 `plan4/backup`。**执行节奏（第二次审阅）**：每个任务跑该任务相关的行为测试；每个阶段末跑全量非集成回归 + 该阶段的真实链路（阶段 1：dev 栈真实生成一套并核对；阶段 2：本机 MinIO 双仓库真实上传/单边失败/错配拒绝/淘汰；阶段 3：隔离栈真实恢复与第二栈冷启动恢复；阶段 4：调度表达式与失败通知真实验证），然后停下给检查点。
- **计划中的代码块是候选方案**（第二次审阅）：不锁死等价实现、函数名或源码布局；与设计 2.1 或下列"契约修订"冲突时以契约为准。测试按行为验证，不固定服务数量、DocType 数或文本排版。

## 文件结构

| 文件 | 职责 |
|---|---|
| `dsherp/backup_sets.py`（新） | 备份集协议的纯函数：set_id、`set.json`/`pair.json` 生成与校验、本地修剪计划 |
| `dsherp/backup_retention.py`（新） | 异地保留选择的纯函数（按站 7/4/3 + 保护项） |
| `dsherp/backup_status.py`（新） | 状态文件读写、`record_*`、`evaluate()`（gauge + 告警） |
| `dsherp/site_holds.py`（新） | 站点保持文件（CLI 写，worker 读） |
| `dsherp/backup.py`（新） | `backup`、`backup-sync`、`backup-init`、`notify-failure` 命令；操作锁；`stage_set`；restic 包装 |
| `dsherp/restore_drill.py`（新） | 隔离恢复栈、`restore-drill`、`restore-site` |
| `infra/compose.restore.yml`（新） | 隔离恢复栈 |
| `infra/compose.prod.yml` | 新卷、backend 挂载、两个一次性 restic 服务、secrets |
| `infra/docker/frappe/Dockerfile` | 预建 `/home/frappe/backups`、`/home/frappe/backup-secrets` |
| `infra/render_worker_units.py` | `render_backup_units` |
| `dsherp/admin.py` | CLI 接线；`_archive_backup` 分目录；`release`/`retire_tenant` 生成事件集；共享操作锁 |
| `dsherp/context_worker.py` | 保持门；读状态文件 → gauge/告警 |
| `dsherp/deploy_env.py` | `DSHERP_BACKUP_REPOSITORY`、`DSHERP_BACKUP_SECRETS_REPOSITORY` |
| `tests/test_backup_sets.py`、`tests/test_backup_retention.py`、`tests/test_backup_status.py`、`tests/test_site_holds.py`、`tests/test_backup_cli.py`、`tests/test_restore_drill.py`（新）；`tests/test_admin_cli.py`、`tests/test_context_worker.py`、`tests/test_deployment_contract.py`、`tests/test_deploy_env.py`、`tests/test_alerts.py`（改） | |
| `docs/engineering/deployment-runbook.md`、`docs/engineering/data-governance-evidence.md`、`docs/engineering/runtime-baseline.md`、`infra/env/prod.env.example` | 文档 |

---

## 阶段 1：备份集协议、稳定窗口、生成与状态记录

### Task 1: 备份集协议（`dsherp/backup_sets.py`）

> **契约修订（第二次审阅）**：slug 保留连字符（`dsherp-validation.localhost` → `dsherp-validation_localhost`），`parse_set_id` 按位置解析（前 15 位 stamp、第 16 位 `-`、末 6 位 token、倒数第 7 位 `-`，中间为 slug，slug 只含 `[a-z0-9_-]`），测试必须覆盖 `dsherp-validation.localhost`、`dsherp-daily.localhost`、`acme.tenant.example.com`；`pair_manifest` 增加 `config_sha256`（`site_config_backup.json` 字节的 sha256，由调用方算好传入）与 `format`；`pair_matches` 一并核对。摘要定义见设计 §4.0（2.1）。

**Files:**
- Create: `dsherp/backup_sets.py`
- Test: `tests/test_backup_sets.py`

**Interfaces:**
- Produces:
  - `DATA_PIECES = ('database.sql.gz', 'files.tar', 'private-files.tar')`, `CONFIG_PIECE = 'site_config_backup.json'`, `FORMAT = 1`
  - `slug(site: str) -> str`（`site.replace('.', '_')`，与 Frappe 一致）
  - `new_set_id(site, stamp, token) -> str`，`parse_set_id(set_id) -> dict | None`（键 `stamp`、`slug`、`token`）
  - `set_manifest(*, set_id, site, kind, stamp, window, image_tag, image_id, frappe_version, pieces, snapshot_sha256) -> dict`
  - `pair_manifest(set_doc) -> dict`；`set_sha256(set_doc) -> str`；`pair_matches(set_doc, pair_doc) -> bool`
  - `local_prune(set_ids, keep=3, protect=()) -> list[str]`（要删除的 set_id，按 stamp 从旧到新）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backup_sets.py
"""A backup set is one stable state of one Site: three data pieces and one secret piece,
bound by one id that appears in both halves and in both repositories."""
import json
import re

import pytest

from dsherp import backup_sets


def test_set_ids_carry_the_window_stamp_the_site_slug_and_a_token_and_parse_back():
    set_id = backup_sets.new_set_id("acme.tenant.example.com", "20260906_020007", "k3f9qx")
    assert set_id == "20260906_020007-acme_tenant_example_com-k3f9qx"
    assert backup_sets.parse_set_id(set_id) == {"stamp": "20260906_020007", "slug": "acme_tenant_example_com", "token": "k3f9qx"}
    assert backup_sets.parse_set_id("../evil") is None and backup_sets.parse_set_id("") is None
    with pytest.raises(ValueError):
        backup_sets.new_set_id("acme.tenant.example.com", "2026-09-06", "k3f9qx")   # not a stamp
    with pytest.raises(ValueError):
        backup_sets.new_set_id("acme.tenant.example.com", "20260906_020007", "K3F9QX")  # token is 6 lowercase alnum


def _set_doc(**over):
    base = dict(set_id="20260906_020007-acme_tenant_example_com-k3f9qx", site="acme.tenant.example.com", kind="scheduled",
                stamp="20260906_020007", window={"started": "2026-09-06T02:00:07Z", "finished": "2026-09-06T02:00:41Z"},
                image_tag="v0.3.1-rc2", image_id="sha256:c914", frappe_version="16.31.0",
                pieces={"database.sql.gz": {"sha256": "a" * 64, "bytes": 10}, "files.tar": {"sha256": "b" * 64, "bytes": 20},
                        "private-files.tar": {"sha256": "c" * 64, "bytes": 30}},
                snapshot_sha256="d" * 64)
    return backup_sets.set_manifest(**{**base, **over})


def test_the_set_manifest_records_every_data_piece_and_refuses_a_missing_or_extra_one():
    doc = _set_doc()
    assert doc["format"] == 1 and doc["kind"] == "scheduled" and set(doc["pieces"]) == set(backup_sets.DATA_PIECES)
    with pytest.raises(ValueError):
        _set_doc(pieces={"database.sql.gz": {"sha256": "a" * 64, "bytes": 1}})
    with pytest.raises(ValueError):
        _set_doc(kind="weekly")
    with pytest.raises(ValueError):
        _set_doc(pieces={**_set_doc()["pieces"], "site_config_backup.json": {"sha256": "e" * 64, "bytes": 1}})


def test_the_pair_manifest_binds_the_secret_half_to_the_data_half_by_id_and_digests():
    doc = _set_doc()
    pair = backup_sets.pair_manifest(doc)
    assert pair["set_id"] == doc["set_id"] and pair["site"] == doc["site"]
    assert pair["pieces"] == {name: row["sha256"] for name, row in doc["pieces"].items()}
    assert pair["set_sha256"] == backup_sets.set_sha256(doc) and re.fullmatch("[0-9a-f]{64}", pair["set_sha256"])
    assert backup_sets.pair_matches(doc, pair)
    assert not backup_sets.pair_matches(_set_doc(snapshot_sha256="f" * 64), pair)  # a different set with the same id
    assert not backup_sets.pair_matches(doc, {**pair, "set_id": "20260906_020007-acme_tenant_example_com-other0"})
    assert json.dumps(doc, sort_keys=True) == json.dumps(json.loads(json.dumps(doc)), sort_keys=True)  # JSON round trip


def test_local_pruning_keeps_the_newest_three_and_never_the_protected_ones():
    ids = [f"2026090{d}_020000-acme_tenant_example_com-aaaaaa" for d in range(1, 7)]  # 6 sets, day 1..6
    assert backup_sets.local_prune(ids, keep=3) == ids[:3]
    assert backup_sets.local_prune(ids, keep=3, protect=(ids[0],)) == ids[1:3]
    assert backup_sets.local_prune(ids[:2], keep=3) == []
    assert backup_sets.local_prune(list(reversed(ids)), keep=3) == ids[:3]  # order of input does not matter
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_sets.py -q`
Expected: `ModuleNotFoundError: No module named 'dsherp.backup_sets'`

- [ ] **Step 3: 实现**

```python
# dsherp/backup_sets.py
"""The backup-set protocol: pure functions shared by the host CLI, the sync step and the drill.

A set is one stable state of one Site. Its data half (three pieces + the G2 snapshot +
set.json) and its secret half (site_config_backup.json + pair.json) live in different
volumes and different repositories, bound by one id and by the digests pair.json copies
from set.json. Restore never takes 'latest' from each side: it takes the newest set whose
two halves were both read back."""
import hashlib
import json
import re

FORMAT = 1
DATA_PIECES = ('database.sql.gz', 'files.tar', 'private-files.tar')
CONFIG_PIECE = 'site_config_backup.json'
KINDS = ('scheduled', 'release', 'retire')
STAMP = re.compile(r'\d{8}_\d{6}')
TOKEN = re.compile(r'[a-z0-9]{6}')
SET_ID = re.compile(r'(?P<stamp>\d{8}_\d{6})-(?P<slug>[a-z0-9_]+)-(?P<token>[a-z0-9]{6})')
SHA256 = re.compile(r'[0-9a-f]{64}')


def slug(site):
    return site.replace('.', '_')


def new_set_id(site, stamp, token):
    if not STAMP.fullmatch(stamp or ''):
        raise ValueError('A set stamp is YYYYmmdd_HHMMSS (UTC): ' + repr(stamp))
    if not TOKEN.fullmatch(token or ''):
        raise ValueError('A set token is six lowercase alphanumerics: ' + repr(token))
    return f'{stamp}-{slug(site)}-{token}'


def parse_set_id(set_id):
    match = SET_ID.fullmatch(set_id or '')
    return match.groupdict() if match else None


def set_manifest(*, set_id, site, kind, stamp, window, image_tag, image_id, frappe_version, pieces, snapshot_sha256):
    if parse_set_id(set_id) is None or not set_id.startswith(stamp + '-' + slug(site) + '-'):
        raise ValueError('set_id does not belong to this Site and stamp: ' + repr(set_id))
    if kind not in KINDS:
        raise ValueError('Unknown set kind: ' + repr(kind))
    if set(pieces) != set(DATA_PIECES):
        raise ValueError('A set records exactly the data pieces ' + ', '.join(DATA_PIECES) + ': ' + repr(sorted(pieces)))
    for name, row in pieces.items():
        if not SHA256.fullmatch(str(row.get('sha256'))) or type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise ValueError('Piece needs a sha256 and a byte count: ' + name)
    if not SHA256.fullmatch(snapshot_sha256 or ''):
        raise ValueError('The snapshot digest is missing')
    return {'format': FORMAT, 'set_id': set_id, 'site': site, 'kind': kind, 'stamp': stamp,
            'window': {'started': window['started'], 'finished': window['finished']},
            'image_tag': image_tag, 'image_id': image_id, 'frappe_version': frappe_version,
            'pieces': {name: {'sha256': row['sha256'], 'bytes': row['bytes']} for name, row in pieces.items()},
            'snapshot_sha256': snapshot_sha256}


def set_sha256(set_doc):
    return hashlib.sha256(json.dumps(set_doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def pair_manifest(set_doc):
    return {'format': FORMAT, 'set_id': set_doc['set_id'], 'site': set_doc['site'],
            'pieces': {name: row['sha256'] for name, row in set_doc['pieces'].items()},
            'set_sha256': set_sha256(set_doc)}


def pair_matches(set_doc, pair_doc):
    try:
        return (pair_doc['set_id'] == set_doc['set_id'] and pair_doc['site'] == set_doc['site']
                and pair_doc['pieces'] == {name: row['sha256'] for name, row in set_doc['pieces'].items()}
                and pair_doc['set_sha256'] == set_sha256(set_doc))
    except (KeyError, TypeError, AttributeError):
        return False


def local_prune(set_ids, keep=3, protect=()):
    """The set ids to delete locally: everything but the newest `keep`, never a protected one."""
    ordered = sorted(set_ids)  # the stamp leads the id, so lexical order is time order
    return [set_id for set_id in ordered[:-keep] if set_id not in set(protect)] if len(ordered) > keep else []
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_sets.py -q`
Expected: `4 passed`

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup_sets.py tests/test_backup_sets.py
git commit -m "feat: 备份集协议——set_id、set.json/pair.json 生成与配对校验、本地修剪计划（纯函数）"
```

### Task 2: 站点保持文件与 worker 的保持门（`dsherp/site_holds.py`）

> **契约修订（第二次审阅）**：保持只负责"停止领取"。另加服务端闸门：`frappe_app/dsherp_bridge/context_execution.py` 的 `claim_run` 在 `frappe.conf.get('dsherp_hold')` 为真时拒绝领取（返回明确错误码 `site_held`，worker 侧把它当作"无可领取"而不是站点故障计数）；本任务补该端点的行为测试（`tests/test_context_execution.py` 或集成测试）。

**Files:**
- Create: `dsherp/site_holds.py`
- Modify: `dsherp/context_worker.py:404-430`（`Coordinator.__init__` 加 `holds=None`）、`:461-462`（站点允许判断）、`:770-772`（`main()` 传入 `holds`）
- Test: `tests/test_site_holds.py`、`tests/test_context_worker.py`

**Interfaces:**
- Produces: `site_holds.hold(runtime_dir, site, reason) -> Path`；`site_holds.release(runtime_dir, site) -> None`；`site_holds.held(runtime_dir) -> set[str]`；`Coordinator(..., holds=callable)`，`holds()` 返回当前被保持的站名集合，被保持的站不领取（心跳照旧）。
- 文件：`<runtime>/holds/<site>`，内容 `{"reason": "backup", "pid": 123, "at": "..."}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_site_holds.py
"""A hold is how the host CLI tells the worker to stop claiming for one Site while a
stable window is open; the worker keeps heartbeating so users see a queue, not a 503."""
import json

from dsherp import site_holds


def test_a_hold_is_one_file_per_site_and_release_removes_only_that_one(tmp_path):
    a = site_holds.hold(tmp_path, "acme.tenant.example.com", "backup")
    b = site_holds.hold(tmp_path, "beta.tenant.example.com", "release")
    assert a == tmp_path / "holds" / "acme.tenant.example.com" and json.loads(a.read_text())["reason"] == "backup"
    assert site_holds.held(tmp_path) == {"acme.tenant.example.com", "beta.tenant.example.com"}
    site_holds.release(tmp_path, "acme.tenant.example.com")
    assert site_holds.held(tmp_path) == {"beta.tenant.example.com"}
    site_holds.release(tmp_path, "acme.tenant.example.com")  # idempotent
    assert (tmp_path / "holds").stat().st_mode & 0o077 == 0
    assert site_holds.held(tmp_path / "nowhere") == set()
```

在 `tests/test_context_worker.py` 末尾追加（`_coordinator` 类夹具按该文件既有写法：找到现有构造 `Coordinator(...)` 的测试，复制其 sites/settings_loader/execute/probe 假件）：

```python
def test_a_held_site_is_not_claimed_but_still_heartbeats(tmp_path):
    """Backup opens a stable window per Site; the coordinator must not claim for that Site
    meanwhile, and must resume as soon as the hold is released."""
    from dsherp import site_holds
    claimed = []
    # 用本文件既有的假 client：其 claim 端点记录被调用的站；此处以 `claims` 计数器为准
    coordinator, clients = _coordinator_with_fake_sites(tmp_path, ["acme.tenant.example.com", "beta.tenant.example.com"], claimed,
                                                        holds=lambda: site_holds.held(tmp_path))
    site_holds.hold(tmp_path, "acme.tenant.example.com", "backup")
    coordinator.tick(0)
    assert "acme.tenant.example.com" not in claimed and "beta.tenant.example.com" in claimed
    assert clients["acme.tenant.example.com"].heartbeats >= 1
    site_holds.release(tmp_path, "acme.tenant.example.com")
    claimed.clear()
    coordinator.tick(10)
    assert "acme.tenant.example.com" in claimed
```

（`_coordinator_with_fake_sites` 是本任务新增的测试辅助：用文件里已有的 FakeClient/假 execute 构造两个站的 `Coordinator`，返回 `(coordinator, clients_by_site)`；`claimed` 由假 claim 端点 append 站名。写法参照 `tests/test_context_worker.py` 中 `test_tick_*` 的构造。）

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_site_holds.py tests/test_context_worker.py -q -k "held or hold"`
Expected: `ModuleNotFoundError` / `TypeError: __init__() got an unexpected keyword argument 'holds'`

- [ ] **Step 3: 实现**

```python
# dsherp/site_holds.py
"""Per-Site holds: the host CLI opens one while a Site's data is being backed up or judged;
the worker reads them every tick and claims nothing for a held Site."""
import json
import os
import time
from pathlib import Path


def _directory(runtime_dir):
    return Path(runtime_dir) / 'holds'


def hold(runtime_dir, site, reason):
    directory = _directory(runtime_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / site
    path.write_text(json.dumps({'reason': reason, 'pid': os.getpid(), 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}))
    return path


def release(runtime_dir, site):
    (_directory(runtime_dir) / site).unlink(missing_ok=True)


def held(runtime_dir):
    directory = _directory(runtime_dir)
    if not directory.is_dir():
        return set()
    return {entry.name for entry in directory.iterdir() if entry.is_file()}
```

`context_worker.py`：`Coordinator.__init__(..., isolation=None, holds=None)`，校验 `holds is None or callable(holds)`，保存 `self.holds=holds`；在 `:461-462` 的站点允许判断里加：

```python
    def _site_open(self,site,now):
        if self.holds is not None and site['site'] in self.holds():return False
        return self._site_skip_until.get(site['site'],0)<=now
```

（把原方法体改成上述两行，方法名沿用原名。）`main()` 里构造 `Coordinator(...)` 时传 `holds=lambda:site_holds.held(deploy_env.settings()['runtime_dir'])`——在 `main()` 开头 `resolved=deploy_env.settings()` 一次，复用其 `runtime_dir`；`holds()` 每 tick 一次目录列举，失败（`OSError`）视为空集并记 `worker_log.log('holds_unreadable',...)`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_site_holds.py tests/test_context_worker.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/site_holds.py dsherp/context_worker.py tests/test_site_holds.py tests/test_context_worker.py
git commit -m "feat: 站点保持文件——CLI 开窗时写、worker 每 tick 读，被保持的站不领取但照常心跳"
```

### Task 3: 状态文件的读写与记录（`dsherp/backup_status.py`，第一部分）

**Files:**
- Create: `dsherp/backup_status.py`
- Test: `tests/test_backup_status.py`

**Interfaces:**
- Produces: `FORMAT = 1`；`PHASES = ('backup', 'sync', 'check', 'drill')`；`SITE_PHASES = ('backup', 'offsite', 'verified')`
  - `empty() -> dict`；`load(path) -> dict | None`（缺失、非 JSON、`format` 不符都返回 `None`）；`save(path, status) -> Path`（用 `admin._write_json`）
  - `record_run(status, phase, *, at, ok, error=None) -> None`
  - `record_site(status, site, phase, *, at, ok, error=None, **facts) -> None`（成功时 `facts` 合并进 `last_success`，如 `set_id`、`stamp`、`image_tag`；失败只写 `last_attempt`，`last_success` 不动）
  - `record_set(status, set_doc, state, **snapshots) -> None`（`state` ∈ `staged|data_uploaded|secrets_uploaded|complete|verified`；`snapshots` 键 `data_snapshot`/`secrets_snapshot`）
  - `now_iso(clock=time.time) -> str`（`YYYY-mm-ddTHH:MM:SSZ`）
- 结构见设计 §4.4。`evaluate()` 在 Task 14 加入同一模块。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backup_status.py
"""The status file is the single place the host records what the backup chain last did,
per Site and per phase; a failure never erases the last success."""
import json

from dsherp import backup_status


def test_an_empty_status_has_every_phase_and_load_rejects_missing_or_broken_files(tmp_path):
    status = backup_status.empty()
    assert set(status["runs"]) == set(backup_status.PHASES) and status["sites"] == {} and status["sets"] == {}
    assert backup_status.load(tmp_path / "missing.json") is None
    (tmp_path / "broken.json").write_text("{nope")
    assert backup_status.load(tmp_path / "broken.json") is None
    (tmp_path / "old.json").write_text(json.dumps({"format": 0}))
    assert backup_status.load(tmp_path / "old.json") is None
    path = backup_status.save(tmp_path / "state" / "backups" / "status.json", status)
    assert backup_status.load(path) == status and path.stat().st_mode & 0o077 == 0


def test_a_failed_attempt_keeps_the_last_success_and_a_success_carries_its_facts():
    status = backup_status.empty()
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-06T02:00:41Z", ok=True,
                              set_id="20260906_020007-acme_tenant_example_com-k3f9qx", stamp="20260906_020007")
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-06T14:00:20Z", ok=False, error="dump failed")
    row = status["sites"]["acme.tenant.example.com"]["backup"]
    assert row["last_success"] == {"at": "2026-09-06T02:00:41Z", "first_at": "2026-09-06T02:00:41Z",
                                   "set_id": "20260906_020007-acme_tenant_example_com-k3f9qx", "stamp": "20260906_020007"}
    assert row["last_attempt"] == {"at": "2026-09-06T14:00:20Z", "ok": False, "error": "dump failed"}
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-07T02:00:30Z", ok=False, error=None, deferred="busy")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_attempt"]["deferred"] == "busy"
    backup_status.record_run(status, "backup", at="2026-09-06T14:01:00Z", ok=False, error="1 site failed")
    assert status["runs"]["backup"]["last_attempt"]["ok"] is False and status["runs"]["backup"]["last_success"] is None
    backup_status.record_run(status, "backup", at="2026-09-07T02:01:00Z", ok=True)
    assert status["runs"]["backup"]["last_success"] == {"at": "2026-09-07T02:01:00Z"}
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-07T02:00:30Z", ok=True, set_id="x", stamp="20260907_020007")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_success"]["first_at"] == "2026-09-06T02:00:41Z"  # kept from the first success


def test_sets_move_through_states_and_keep_their_snapshot_ids():
    status = backup_status.empty()
    doc = {"set_id": "20260906_020007-acme_tenant_example_com-k3f9qx", "site": "acme.tenant.example.com", "kind": "scheduled",
           "stamp": "20260906_020007"}
    backup_status.record_set(status, doc, "staged")
    backup_status.record_set(status, doc, "data_uploaded", data_snapshot="1a2b3c4d")
    backup_status.record_set(status, doc, "complete", secrets_snapshot="5e6f7a8b")
    row = status["sets"][doc["set_id"]]
    assert row["state"] == "complete" and row["data_snapshot"] == "1a2b3c4d" and row["secrets_snapshot"] == "5e6f7a8b"
    assert row["site"] == doc["site"] and row["kind"] == "scheduled" and row["stamp"] == "20260906_020007" and row["updated"]
    import pytest
    with pytest.raises(ValueError):
        backup_status.record_set(status, doc, "uploaded")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_status.py -q`
Expected: `ModuleNotFoundError: No module named 'dsherp.backup_status'`

- [ ] **Step 3: 实现**

```python
# dsherp/backup_status.py
"""The host-side record of the backup chain (design §4.4). Written by dsherp-admin at every
phase boundary, read by the worker every tick. Pure except load/save."""
import json
import time
from pathlib import Path

FORMAT = 1
PHASES = ('backup', 'sync', 'check', 'drill')
SITE_PHASES = ('backup', 'offsite', 'verified')
SET_STATES = ('staged', 'data_uploaded', 'secrets_uploaded', 'complete', 'verified')


def now_iso(clock=time.time):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))


def empty():
    return {'format': FORMAT,
            'runs': {phase: {'last_attempt': None, 'last_success': None} for phase in PHASES},
            'sites': {}, 'sets': {}}


def load(path):
    path = Path(path)
    try:
        status = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict) or status.get('format') != FORMAT:
        return None
    return status


def save(path, status):
    from dsherp.admin import _write_json
    return _write_json(Path(path), status)


def record_run(status, phase, *, at, ok, error=None):
    if phase not in PHASES:
        raise ValueError('Unknown phase: ' + repr(phase))
    row = status['runs'].setdefault(phase, {'last_attempt': None, 'last_success': None})
    row['last_attempt'] = {'at': at, 'ok': bool(ok), 'error': error}
    if ok:
        row['last_success'] = {'at': at}


def record_site(status, site, phase, *, at, ok, error=None, **facts):
    if phase not in SITE_PHASES:
        raise ValueError('Unknown site phase: ' + repr(phase))
    site_row = status['sites'].setdefault(site, {})
    row = site_row.setdefault(phase, {'last_attempt': None, 'last_success': None})
    attempt = {'at': at, 'ok': bool(ok), 'error': error}
    if not ok:
        attempt.update(facts)  # e.g. deferred='busy'
    row['last_attempt'] = attempt
    if ok:
        first = (row.get('last_success') or {}).get('first_at') or at
        row['last_success'] = {'at': at, 'first_at': first, **facts}


def record_set(status, set_doc, state, **snapshots):
    if state not in SET_STATES:
        raise ValueError('Unknown set state: ' + repr(state))
    row = status['sets'].setdefault(set_doc['set_id'], {'site': set_doc['site'], 'kind': set_doc['kind'],
                                                        'stamp': set_doc['stamp'], 'data_snapshot': None,
                                                        'secrets_snapshot': None})
    row['state'] = state
    for key in ('data_snapshot', 'secrets_snapshot'):
        if key in snapshots:
            row[key] = snapshots[key]
    row['updated'] = now_iso()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_status.py -q`
Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup_status.py tests/test_backup_status.py
git commit -m "feat: 备份状态文件——每站每阶段 last_attempt/last_success 与备份集状态机的记录"
```

### Task 4: 备份卷与密钥卷挂到 bench；镜像预建目录

**Files:**
- Modify: `infra/compose.prod.yml`（backend :64-91、platform-backend 对应块、`volumes:` :324-337）
- Modify: `infra/compose.validation.yml`（backend 与 platform-backend 同样挂载；`volumes:` 加 `v16-tenant-backup-secrets`、`v16-platform-backup-secrets`——先看该文件现有卷名前缀）
- Modify: `infra/docker/frappe/Dockerfile`（在 `install -d … /home/frappe/frappe-bench/archived` 那一行旁）
- Modify: `tests/test_deployment_contract.py:167-173`、`:227-236`（dev 卷集合）

**Interfaces:**
- Produces（容器内路径常量，Task 5 使用）：`BACKUPS = '/home/frappe/backups'`（数据集 `sets/<site>/<set_id>/`）、`BACKUP_SECRETS = '/home/frappe/backup-secrets'`（`<site>/<set_id>/`）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_deployment_contract.py`）

```python
def test_the_benches_can_stage_backup_sets_in_a_data_volume_and_a_separate_secrets_volume():
    """A backup set's data half and secret half never share a volume: the sync container
    for one half cannot even see the other."""
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert "tenant-backups:/home/frappe/backups" in _block(body, "backend")
    assert "tenant-backup-secrets:/home/frappe/backup-secrets" in _block(body, "backend")
    assert "platform-backups:/home/frappe/backups" in _block(body, "platform-backend")
    assert "platform-backup-secrets:/home/frappe/backup-secrets" in _block(body, "platform-backend")
    volumes = PROD_COMPOSE.split("\nvolumes:\n", 1)[1].split("\n\n", 1)[0]
    for name in ("tenant-backup-secrets", "platform-backup-secrets"):
        assert f"  {name}:" in volumes, name
    dockerfile = (ROOT / "infra/docker/frappe/Dockerfile").read_text()
    assert re.search(r"install -d .*-o frappe .*/home/frappe/backups /home/frappe/backup-secrets", dockerfile)
```

并把 `:227-236` 的 dev 卷集合断言改为包含新的两个 dev 卷名（按该文件现有前缀，如 `v16-tenant-backup-secrets`）。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py -q -k "stage_backup_sets or volumes"`
Expected: AssertionError on the backend block

- [ ] **Step 3: 实现**

`infra/compose.prod.yml` backend 的 `volumes:` 追加两行：

```yaml
      # Backup sets staged by dsherp-admin backup: data half and secret half in different volumes.
      - tenant-backups:/home/frappe/backups
      - tenant-backup-secrets:/home/frappe/backup-secrets
```

platform-backend 同样追加 `platform-backups:/home/frappe/backups` 与 `platform-backup-secrets:/home/frappe/backup-secrets`；顶层 `volumes:` 加 `tenant-backup-secrets:` 与 `platform-backup-secrets:`。dev compose 对应加 `v16-…` 卷。Dockerfile 那一行改为：

```dockerfile
RUN install -d -m 755 -o frappe -g frappe /home/frappe/frappe-bench/archived /home/frappe/backups /home/frappe/backup-secrets
```

（若原行形式不同，保持原形式只追加两个目录；`backup-secrets` 目录随后由 `stage_set` 收紧为 0700。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py -q`
Expected: 全部 passed（`deployment_digest` 变化是预期的；若 `tests/test_deploy_env.py` 钉了指纹值，按新指纹更新并在提交信息里说明）

- [ ] **Step 5: 提交**

```bash
git add infra/compose.prod.yml infra/compose.validation.yml infra/docker/frappe/Dockerfile tests/test_deployment_contract.py
git commit -m "feat: 两个 bench 挂载备份卷与独立的备份密钥卷，镜像预建目录"
```

### Task 5: `dsherp-admin backup`——操作锁、稳定窗口、暂存、本地修剪、状态

> **契约修订（第二次审阅）**：`backup_window` 的顺序改为——写保持文件 → `set-config dsherp_hold 1` → 等待 `Running`/`Cancelling` 归零（**不数 `Queued`/`NeedsInput`**，它们被保持冻结；上限 10 分钟，超时 `deferred(busy)`）→ `maintenance_mode=1`、`pause_scheduler=1` → 排空写入者：容器脚本轮询"本站 RQ 排队/执行中作业数 == 0 且 `information_schema.processlist` 中本站库用户非 Sleep 的活动连接（排除本连接）== 0"，连续两次为 0，上限 2 分钟，超时 `deferred(draining)` → 备份 + 快照 → **逆序撤销**每一步已完成的动作（用记录动作栈的上下文管理器，任一步失败也按栈清理）。`set.json` 的 `image_tag`/`image_id` 来自 `admin._running_images`（真实容器），dev 记 `dev` + 真实 id；取不到 id 即该站失败。状态文件**损坏**（存在但读不出）时：生成继续但跳过本地修剪并在报告与状态里标注，`backup-sync` 拒绝 forget/prune。测试至少覆盖："Queued 存在但无执行者仍能备份""正在领取时建立保持（服务端拒绝）""HTTP/后台写入未结束时等待、超时推迟""标志设了一半失败时的逆序清理""镜像 id 为空即失败"。计划里的 `backup_window` 代码块据此重写，不照抄。

**Files:**
- Create: `dsherp/backup.py`
- Modify: `dsherp/admin.py`（`main()` 解析器与分派，:1128-1250）
- Test: `tests/test_backup_cli.py`（新；复用 `tests/test_admin_cli.py` 的 `SnapshotBench`、`RELEASE`、`_tenant_row`、`host` 夹具——把它们 `from tests.test_admin_cli import …`，若 `tests/` 不是包则在 `tests/conftest.py` 里把这些夹具与类挪成共享 fixture，本任务顺手完成）

**Interfaces:**
- Consumes: `backup_sets.*`（Task 1）、`site_holds.*`（Task 2）、`backup_status.*`（Task 3）、`admin.Bench`、`admin._targets`、`admin._site_flags`、`admin._set_flag`、`admin.take_snapshot`、`admin._write_json`、`admin.runtime_dir`、`admin.Fault`、`admin.BACKUP_PIECES`、`admin.SITES`。
- Produces:
  - 常量 `BACKUPS = '/home/frappe/backups'`、`BACKUP_SECRETS = '/home/frappe/backup-secrets'`、`LOCAL_KEEP = 3`、`ACTIVE_WAIT_SECONDS = 600`
  - `operations_lock(resolved, root, who) -> contextmanager`：`<runtime>/operations.lock`，`flock(LOCK_EX|LOCK_NB)`；拿不到抛 `Fault('另一项操作正在进行（<who>，pid …）；不并行执行 backup/release/rollback/retire-tenant')`；持有期间文件内容为 `{"who","pid","at"}`。
  - `find_pieces(bench, site) -> dict[str, str]`：从 `private/backups` 按最新 `-database.sql.gz` 前缀取四件文件名（现 `_archive_backup` 的发现逻辑抽出来），缺件 `Fault`。
  - `stage_set(bench, site, pieces, *, kind, stamp, token, window, image_tag, image_id, snapshot, frappe_version) -> dict`（返回 `set.json` 内容；容器内执行复制/移动/摘要/清单写入，见实现）
  - `backup_window(resolved, bench, site, *, root, kind='scheduled', clock=time.time, sleep=time.sleep, token=None) -> dict | None`：保持 → 等待 → 标志 → 备份 → 快照 → 复原 → 暂存；返回 set_doc；在途运行超时返回 `None`（调用方记 deferred）。
  - `prune_local(bench, site, protect) -> list[str]`
  - `backup(resolved, *, root=ROOT, runner=subprocess.run, bench_factory=None, sync=False, clock=time.time, sleep=time.sleep) -> dict`（报告：`sites`、`sets`、`failed`、`deferred`、`ok`）；CLI `dsherp-admin backup [--sync]`。
  - 容器内 sha256 与文件操作统一走 `bench.run('sh', '-c', …)`；写 `set.json`/`pair.json` 用 `bench.run('sh', '-c', f'umask 077 && cat > {path}', stdin=json_text)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backup_cli.py
"""dsherp-admin backup: one stable window per Site (held, quiesced) produces the four pieces
and the G2 snapshot, stages them as one set in two volumes, and records what happened."""
import json
import fcntl

import pytest

from dsherp import admin, backup, backup_status, site_holds
from tests.test_admin_cli import RELEASE, SAME, SnapshotBench, _tenant_row


class StagingBench(SnapshotBench):
    """SnapshotBench plus the staging verbs: sha256sum, cp/mv into the two volumes, cat > manifests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.written = {}
        self.staged_verbs = []

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        if arguments[:2] == ("sh", "-c") and any(mark in arguments[2] for mark in ("/home/frappe/backups", "/home/frappe/backup-secrets", "sha256sum")):
            self.calls.append(("run",) + arguments[:3]); self.verbs.append(" ".join(arguments)); self.staged_verbs.append(arguments[2])
            if "cat >" in arguments[2]:
                self.written[arguments[2].split("cat > ", 1)[1].split()[0]] = json.loads(stdin)
                return ""
            if "sha256sum" in arguments[2]:
                names = [word for word in arguments[2].split() if word.endswith((".sql.gz", ".tar", ".json"))]
                return "".join(f"{'a' * 64}  {name}\n" for name in names)
            if "stat -c %s" in arguments[2] or "wc -c" in arguments[2]:
                return "42\n"
            if arguments[2].startswith("ls -1 ") and "/sets/" in arguments[2]:
                return "".join(name + "\n" for name in self.config.get("local_sets", []))
            return ""
        return super().run(*arguments, stdin=stdin, timeout=timeout, secrets=secrets)


def _prepare():
    admin.ensure_secrets(RELEASE)
    _tenant_row()


def test_a_backup_opens_a_held_quiesced_window_stages_one_set_in_two_volumes_and_records_it(host):
    _prepare()
    bench = StagingBench([SAME, SAME])
    report = backup.backup(RELEASE, bench_factory=lambda kind: bench, clock=lambda: 1_757_124_007.0, sleep=lambda s: None)
    assert report["ok"] is True and set(report["sets"]) == {"acme.tenant.example.com", "platform.tenant.example.com"}
    verbs = bench.verbs
    site = "acme.tenant.example.com"
    order = [verbs.index(v) for v in (f"bench --site {site} set-config --parse maintenance_mode 1",
                                      f"bench --site {site} set-config --parse pause_scheduler 1",
                                      f"bench --site {site} backup --with-files", f"snapshot {site}",
                                      f"bench --site {site} set-config --parse maintenance_mode 0")]
    assert order == sorted(order), "flags before backup, snapshot inside the window, flags restored after"
    staged = [v for v in bench.staged_verbs if "acme" in v]
    assert any("cp " in v and "/home/frappe/backups/sets/acme.tenant.example.com/" in v and "database.sql.gz" in v for v in staged)
    assert any("mv " in v and "site_config_backup.json" in v and "/home/frappe/backup-secrets/acme.tenant.example.com/" in v for v in staged)
    assert any("chmod 700" in v and "backup-secrets" in v for v in staged) and any("chmod 600" in v and "site_config_backup.json" in v for v in staged)
    assert not any("cp " in v and "site_config_backup.json" in v for v in staged), "the secret piece is moved, never copied"
    set_doc = report["sets"][site]
    assert set_doc["stamp"] == "20260906_020007" and set_doc["kind"] == "scheduled" and set_doc["image_tag"] == "v0.4.0"
    written = bench.written
    assert written[f"/home/frappe/backups/sets/{site}/{set_doc['set_id']}/set.json"] == set_doc
    assert written[f"/home/frappe/backup-secrets/{site}/{set_doc['set_id']}/pair.json"]["set_id"] == set_doc["set_id"]
    assert f"/home/frappe/backups/sets/{site}/{set_doc['set_id']}/snapshot.json" in written
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"][site]["backup"]["last_success"]["set_id"] == set_doc["set_id"]
    assert status["sets"][set_doc["set_id"]]["state"] == "staged" and status["runs"]["backup"]["last_success"]
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()


def test_a_site_with_runs_in_flight_is_deferred_after_the_wait_and_the_others_still_back_up(host):
    _prepare()
    bench = StagingBench([SAME], active_runs=0)
    calls = {"n": 0}
    original = bench.python

    def python(site, body, timeout=900):
        if "DS Model Run" in body and "active" in body and site == "acme.tenant.example.com":
            return json.dumps({"active": 2, "maintenance_mode": 0, "pause_scheduler": 0}) + "\n"
        return original(site, body, timeout)
    bench.python = python
    clock = {"t": 0.0}
    report = backup.backup(RELEASE, bench_factory=lambda kind: bench, clock=lambda: clock["t"],
                           sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert report["ok"] is False and report["deferred"] == {"acme.tenant.example.com": "busy"}
    assert "platform.tenant.example.com" in report["sets"]
    assert not any("acme" in v and "backup --with-files" in v for v in bench.verbs)
    assert not any("acme" in v and "maintenance_mode 1" in v for v in bench.verbs)
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_attempt"]["deferred"] == "busy"
    assert site_holds.held(admin.runtime_dir(RELEASE)) == set()


def test_a_failed_piece_check_fails_that_site_restores_its_flags_and_keeps_the_last_success(host):
    _prepare()
    bench = StagingBench([SAME, SAME], backup_pieces=3)  # config piece missing
    report = backup.backup(RELEASE, bench_factory=lambda kind: bench, clock=lambda: 1_757_124_007.0, sleep=lambda s: None)
    assert report["ok"] is False and "acme.tenant.example.com" in report["failed"]
    assert bench.site_config[("acme.tenant.example.com", "maintenance_mode")] == "0"
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_attempt"]["ok"] is False
    assert status["sites"]["acme.tenant.example.com"]["backup"]["last_success"] is None


def test_two_backups_cannot_overlap_and_release_shares_the_same_lock(host):
    _prepare()
    with backup.operations_lock(RELEASE, admin.ROOT, "backup"):
        with pytest.raises(admin.Fault, match="backup"):
            backup.backup(RELEASE, bench_factory=lambda kind: StagingBench([SAME, SAME]))
        with pytest.raises(admin.Fault, match="backup"):
            admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: StagingBench([SAME] * 4), from_tag="v0.3.0")
    lock = admin.runtime_dir(RELEASE) / "operations.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released after the with-block


def test_local_pruning_keeps_three_sets_and_the_protected_ones(host):
    _prepare()
    bench = StagingBench([SAME, SAME])
    bench.config["local_sets"] = [f"2026090{d}_020000-acme_tenant_example_com-aaaaaa" for d in range(1, 5)]
    report = backup.backup(RELEASE, bench_factory=lambda kind: bench, clock=lambda: 1_757_124_007.0, sleep=lambda s: None)
    removed = [v for v in bench.staged_verbs if v.startswith("rm -rf ")]
    assert len(removed) == 2 and all("20260901_020000" in v for v in removed)
    assert any("/home/frappe/backups/sets/acme.tenant.example.com/20260901_020000" in v for v in removed)
    assert any("/home/frappe/backup-secrets/acme.tenant.example.com/20260901_020000" in v for v in removed)


def test_the_cli_wires_backup_with_and_without_sync(monkeypatch):
    seen = {}
    monkeypatch.setattr(backup, "backup", lambda resolved, **kw: seen.setdefault("kw", kw) or {"ok": True, "sites": {}, "sets": {}, "failed": {}, "deferred": {}})
    from dsherp import deploy_env
    monkeypatch.setattr(deploy_env, "settings", lambda *a, **k: RELEASE)
    assert admin.main(["backup"]) == 0 and seen["kw"]["sync"] is False
    seen.clear()
    assert admin.main(["backup", "--sync"]) == 0 and seen["kw"]["sync"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_cli.py -q`
Expected: `ModuleNotFoundError: No module named 'dsherp.backup'`

- [ ] **Step 3: 实现 `dsherp/backup.py`（生成部分）**

```python
# dsherp/backup.py
"""dsherp-admin backup / backup-sync / backup-init / notify-failure (design §4.1-4.2).

Generation: every Site gets one stable window (held for the worker, maintenance on,
scheduler paused) in which the native `bench backup --with-files` and the G2 snapshot
read the same state; the four pieces are then staged as one backup set, data half and
secret half in different volumes. Any failure fails that Site only, and flags always
come back."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import secrets as secret_tokens
import subprocess
import time
from pathlib import Path

from dsherp import admin, backup_sets, backup_status, deploy_env, site_holds
from dsherp.admin import Fault, ROOT, SITES

BACKUPS = '/home/frappe/backups'
BACKUP_SECRETS = '/home/frappe/backup-secrets'
LOCAL_KEEP = 3
ACTIVE_WAIT_SECONDS = 600
ACTIVE_POLL_SECONDS = 10


@contextmanager
def operations_lock(resolved, root, who):
    """One operator action at a time on this host: backup, release, rollback, retire."""
    path = admin.runtime_dir(resolved, root) / 'operations.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path, 'a+')
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip() or '未知'
            raise Fault(f'另一项操作正在进行（{holder}）；不并行执行 backup/release/rollback/retire-tenant，等它结束再来')
        handle.seek(0); handle.truncate()
        handle.write(json.dumps({'who': who, 'pid': os.getpid(), 'at': backup_status.now_iso()})); handle.flush()
        yield path
    finally:
        try:
            handle.seek(0); handle.truncate(); handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN); handle.close()


def status_path(resolved, root):
    return admin.runtime_dir(resolved, root) / 'backups' / 'status.json'


def load_status(resolved, root):
    return backup_status.load(status_path(resolved, root)) or backup_status.empty()


def find_pieces(bench, site):
    """The newest four-piece set in the Site's private/backups, by name."""
    listing = bench.run('sh', '-c', f'ls -1 {SITES}/{site}/private/backups', timeout=60).split()
    slug = backup_sets.slug(site)
    databases = sorted(name for name in listing if name.endswith(f'-{slug}-database.sql.gz'))
    if not databases:
        raise Fault(f'站点 {site} 备份后在 private/backups 下没有数据库转储；备份集不完整')
    prefix = databases[-1][:-len('-database.sql.gz')]
    pieces = {piece: f'{prefix}-{piece}' for piece in admin.BACKUP_PIECES}
    missing = [name for name in pieces.values() if name not in listing]
    if missing:
        raise Fault(f'站点 {site} 的备份集不完整，缺 {"、".join(missing)}')
    return pieces


def _sha256_of(bench, paths):
    out = bench.run('sh', '-c', 'sha256sum ' + ' '.join(paths), timeout=1800)
    digests = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            digests[parts[-1].rsplit('/', 1)[-1]] = parts[0]
    return digests


def _size_of(bench, path):
    return int(bench.run('sh', '-c', f'wc -c < {path}', timeout=60).split()[-1])


def stage_set(bench, site, pieces, *, kind, stamp, token, window, image_tag, image_id, snapshot, frappe_version):
    """Move the four pieces into the two volumes as one set and prove the copies by digest."""
    set_id = backup_sets.new_set_id(site, stamp, token)
    data_dir = f'{BACKUPS}/sets/{site}/{set_id}'
    secret_dir = f'{BACKUP_SECRETS}/{site}/{set_id}'
    source = f'{SITES}/{site}/private/backups'
    before = _sha256_of(bench, [f'{source}/{pieces[p]}' for p in backup_sets.DATA_PIECES])
    copies = ' && '.join(f'cp {source}/{pieces[p]} {data_dir}/{p}' for p in backup_sets.DATA_PIECES)
    bench.run('sh', '-c', f'umask 077 && mkdir -p {data_dir} && {copies}', timeout=3600)
    bench.run('sh', '-c', f'umask 077 && mkdir -p {secret_dir} && chmod 700 {BACKUP_SECRETS} {secret_dir} && '
                          f'mv {source}/{pieces[backup_sets.CONFIG_PIECE]} {secret_dir}/{backup_sets.CONFIG_PIECE} && '
                          f'chmod 600 {secret_dir}/{backup_sets.CONFIG_PIECE}', timeout=600)
    after = _sha256_of(bench, [f'{data_dir}/{p}' for p in backup_sets.DATA_PIECES])
    for piece in backup_sets.DATA_PIECES:
        if before.get(pieces[piece]) != after.get(piece) or not after.get(piece):
            raise Fault(f'站点 {site} 的 {piece} 复制后摘要不一致；备份集 {set_id} 作废，请检查磁盘')
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    bench.run('sh', '-c', f'umask 077 && cat > {data_dir}/snapshot.json', stdin=snapshot_text, timeout=600)
    set_doc = backup_sets.set_manifest(
        set_id=set_id, site=site, kind=kind, stamp=stamp, window=window, image_tag=image_tag, image_id=image_id,
        frappe_version=frappe_version,
        pieces={p: {'sha256': after[p], 'bytes': _size_of(bench, f'{data_dir}/{p}')} for p in backup_sets.DATA_PIECES},
        snapshot_sha256=hashlib.sha256(snapshot_text.encode()).hexdigest())
    bench.run('sh', '-c', f'umask 077 && cat > {data_dir}/set.json', stdin=json.dumps(set_doc, sort_keys=True), timeout=60)
    bench.run('sh', '-c', f'umask 077 && cat > {secret_dir}/pair.json',
              stdin=json.dumps(backup_sets.pair_manifest(set_doc), sort_keys=True), timeout=60)
    return set_doc


def _frappe_version(bench, site):
    return admin._last_line(bench.python(site, "print(frappe.__version__)", timeout=120)).strip()


def _stamp(clock):
    return time.strftime('%Y%m%d_%H%M%S', time.gmtime(clock()))


def backup_window(resolved, bench, site, *, root=ROOT, kind='scheduled', clock=time.time, sleep=time.sleep, token=None,
                  images=None):
    """Hold, wait for the Site's runs to finish, quiesce, back up and snapshot in one window,
    restore the flags, then stage. None means the Site stayed busy and was left untouched."""
    runtime = admin.runtime_dir(resolved, root)
    site_holds.hold(runtime, site, kind)
    try:
        deadline = clock() + ACTIVE_WAIT_SECONDS
        flags = admin._site_flags(bench, site)
        while flags['active']:
            if clock() >= deadline:
                return None
            sleep(ACTIVE_POLL_SECONDS)
            flags = admin._site_flags(bench, site)
        admin._set_flag(bench, site, 'maintenance_mode', 1)
        admin._set_flag(bench, site, 'pause_scheduler', 1)
        try:
            started = backup_status.now_iso(clock)
            stamp = _stamp(clock)
            bench.run('bench', '--site', site, 'backup', '--with-files', timeout=3600)
            snapshot = admin.take_snapshot(resolved, site, bench=bench)
            finished = backup_status.now_iso(clock)
        finally:
            admin._release_site(bench, site, flags)
    finally:
        site_holds.release(runtime, site)
    pieces = find_pieces(bench, site)
    image_tag = resolved['image_tag'] or 'dev'
    image_id = (images or {}).get('image_id', '')
    return stage_set(bench, site, pieces, kind=kind, stamp=stamp, token=token or secret_tokens.token_hex(3),
                     window={'started': started, 'finished': finished}, image_tag=image_tag, image_id=image_id,
                     snapshot=snapshot, frappe_version=_frappe_version(bench, site))


def prune_local(bench, site, protect=()):
    listing = bench.run('sh', '-c', f'ls -1 {BACKUPS}/sets/{site} 2>/dev/null || true', timeout=60).split()
    doomed = backup_sets.local_prune([name for name in listing if backup_sets.parse_set_id(name)], keep=LOCAL_KEEP, protect=protect)
    for set_id in doomed:
        bench.run('sh', '-c', f'rm -rf {BACKUPS}/sets/{site}/{set_id} {BACKUP_SECRETS}/{site}/{set_id}', timeout=600)
    return doomed


def backup(resolved, *, root=ROOT, runner=subprocess.run, bench_factory=None, sync=False, clock=time.time, sleep=time.sleep):
    factory = bench_factory or (lambda kind: admin.Bench(resolved, kind, root=root, runner=runner))
    report = {'ok': True, 'sites': {}, 'sets': {}, 'failed': {}, 'deferred': {}, 'pruned': {}}
    with operations_lock(resolved, root, 'backup'):
        status = load_status(resolved, root)
        path = status_path(resolved, root)
        for bench, site in admin._targets(resolved, root, factory):
            started = time.monotonic()
            try:
                set_doc = backup_window(resolved, bench, site, root=root, clock=clock, sleep=sleep)
            except Exception as error:  # a Site's failure never stops the others
                report['failed'][site] = str(error); report['ok'] = False
                backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=False, error=str(error)[:500])
                backup_status.save(path, status)
                continue
            if set_doc is None:
                report['deferred'][site] = 'busy'; report['ok'] = False
                backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=False, error=None, deferred='busy')
                backup_status.save(path, status)
                continue
            report['sets'][site] = set_doc
            backup_status.record_set(status, set_doc, 'staged')
            backup_status.record_site(status, site, 'backup', at=backup_status.now_iso(clock), ok=True,
                                      set_id=set_doc['set_id'], stamp=set_doc['stamp'])
            protect = {row['set_id'] for row in _protected_sets(status, site)}
            report['pruned'][site] = prune_local(bench, site, protect={*protect, set_doc['set_id']})
            report['sites'][site] = {'seconds': round(time.monotonic() - started, 1)}
            backup_status.save(path, status)
        backup_status.record_run(status, 'backup', at=backup_status.now_iso(clock), ok=report['ok'],
                                 error=None if report['ok'] else '有站点失败或推迟：' + '、'.join([*report['failed'], *report['deferred']]))
        backup_status.save(path, status)
    if sync:
        report['sync'] = backup_sync(resolved, root=root, runner=runner, clock=clock)  # Task 9
    return report


def _protected_sets(status, site):
    """Newest complete and newest verified set of a Site (design §4.1 step 5)."""
    rows = [dict(set_id=set_id, **row) for set_id, row in status['sets'].items() if row['site'] == site]
    protected = []
    for state in ('complete', 'verified'):
        candidates = sorted((row for row in rows if row['state'] == state), key=lambda row: row['stamp'])
        if candidates:
            protected.append(candidates[-1])
    return protected
```

`admin.main()`：加 `backup_parser = sub.add_parser('backup', help='对平台站与全部租户站在稳定窗口内生成四件套与快照，暂存为备份集；--sync 随后异地同步'); backup_parser.add_argument('--sync', action='store_true')`，分派：

```python
        if arguments.command == 'backup':
            from dsherp import backup as backup_module
            report = backup_module.backup(resolved, sync=arguments.sync)
            _print({key: value for key, value in report.items() if key != 'sets'} | {'sets': {site: doc['set_id'] for site, doc in report['sets'].items()}})
            return 0 if report['ok'] else 1
```

`release()` 与 `rollback()`、`retire_tenant()` 的函数体最外层各包一层 `with backup_module.operations_lock(resolved, root, 'release'):`（`backup` 模块在函数内延迟导入，避免循环导入）——本任务只加 `release` 与 `rollback` 的锁以让第 4 个测试通过，`retire` 在 Task 6。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_cli.py tests/test_admin_cli.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup.py dsherp/admin.py tests/test_backup_cli.py tests/conftest.py
git commit -m "feat: dsherp-admin backup——操作锁、逐站稳定窗口、四件套 + 快照暂存为备份集、本地修剪与状态记录"
```

### Task 6: 发布与下线也产出备份集；归档分目录分权限

> **契约修订（第二次审阅）**：`DSHERP_ENV=prod` 下 `retire-tenant` 与 `release` 在任何破坏性动作前要求两个仓库已配置（否则 Fault），不存在 `if backup_repository` 的隐式跳过；dev 环境只有显式 `--local-only` 才跳过上传。`retire` 的最终集必须按设计 §4.0（2.1）读回核对为 `complete` 才 drop。本阶段（阶段 1）尚无 `upload_sets`：本任务在 prod 路径上直接以 `Fault('异地上传尚未接入')` 占位并有测试证明 prod 下 retire 不会 drop——阶段 2 的 Task 9 替换为真实上传；不得留下"跳过"分支。

**Files:**
- Modify: `dsherp/admin.py`（`_archive_backup` :731-749、`retire_tenant` :509-548、`release` :953-975）
- Test: `tests/test_admin_cli.py`（改 `test_a_release_quiesces_backs_up_archives_snapshots_migrates_compares_and_restores_the_site_flags` 与 retire 相关测试；新增两个测试）

**Interfaces:**
- Consumes: `backup.stage_set`、`backup.find_pieces`、`backup.operations_lock`、`backup.upload_sets`（Task 9 提供；本任务先以 `backup.upload_sets = None` 占位——不：本任务把上传调用写成 `if resolved.get('backup_repository'): backup_module.upload_sets(...)`，Task 7 之前 `resolved` 没有该键，条件为假，不触发；Task 9 实现后自然生效并在 Task 9 补测试）。
- Produces: `_archive_backup(bench, site, tag)` 返回值不变，但 `site_config` 现在指向 `{ARCHIVED_RELEASES}/{tag}/{site}/secrets/{name}`（目录 0700、文件 0600）；`release` 报告的 `sites[site]['set_id']`；`retire_tenant` 报告的 `set_id` 与 `steps` 里的 `('set', <set_id>)`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_admin_cli.py`）

```python
def test_a_release_stages_the_pre_upgrade_backup_as_a_release_set_and_the_archive_keeps_secrets_apart(host):
    """Review §7: the backup a rollback depends on must be able to leave the host as a set;
    and the archive copy no longer puts site_config next to the dump."""
    from tests.test_backup_cli import StagingBench
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = StagingBench([SAME] * 4)
    report = admin.release(RELEASE, "v0.4.0", bench_factory=lambda kind: bench, runner=RUNNING_NEW, from_tag="v0.3.0")
    assert report["clean"]
    row = report["sites"]["acme.tenant.example.com"]
    assert row["set_id"].endswith("-acme_tenant_example_com-" + row["set_id"][-6:]) and row["backup"]["site_config"].startswith(
        admin.ARCHIVED_RELEASES + "/v0.4.0/acme.tenant.example.com/secrets/")
    assert any("chmod 700" in v and "/v0.4.0/acme.tenant.example.com/secrets" in v for v in bench.verbs)
    assert bench.written[f"/home/frappe/backups/sets/acme.tenant.example.com/{row['set_id']}/set.json"]["kind"] == "release"
    from dsherp import backup_status
    status = backup_status.load(admin.runtime_dir(RELEASE) / "backups" / "status.json")
    assert status["sets"][row["set_id"]]["state"] == "staged" and status["sets"][row["set_id"]]["kind"] == "release"


def test_retiring_a_tenant_stages_a_retire_set_before_the_site_is_dropped(host):
    from tests.test_backup_cli import StagingBench
    admin.ensure_secrets(RELEASE)
    _tenant_row()
    bench = StagingBench([SAME])
    report = admin.retire_tenant(RELEASE, "acme", bench_factory=lambda kind: bench)
    steps = [name for name, _ in report["steps"]]
    assert steps.index("set") < steps.index("site"), "the retire set exists before drop-site"
    assert bench.written[f"/home/frappe/backups/sets/acme.tenant.example.com/{report['set_id']}/set.json"]["kind"] == "retire"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_admin_cli.py -q -k "release_set or retire_set"`
Expected: KeyError `'set_id'` / AssertionError

- [ ] **Step 3: 实现**

`_archive_backup`：复制三件数据到 `{target}/`，config 件到 `{target}/secrets/`：

```python
    target = f'{ARCHIVED_RELEASES}/{tag}/{site}'
    data = ' '.join(f'{SITES}/{site}/private/backups/{pieces[p]}' for p in ('database.sql.gz', 'files.tar', 'private-files.tar'))
    config = f'{SITES}/{site}/private/backups/{pieces["site_config_backup.json"]}'
    bench.run('sh', '-c', f'umask 077 && mkdir -p {target}/secrets && chmod 700 {target}/secrets && cp {data} {target}/ && '
                          f'cp {config} {target}/secrets/ && chmod 600 {target}/secrets/{pieces["site_config_backup.json"]}', timeout=600)
    return {'database': f"{target}/{pieces['database.sql.gz']}",
            'site_config': f"{target}/secrets/{pieces['site_config_backup.json']}",
            'files': f"{target}/{pieces['files.tar']}", 'private_files': f"{target}/{pieces['private-files.tar']}"}
```

`release` 循环里，在 `before = take_snapshot(...)` 之后、`migrate` 之前加：

```python
            step = 'stage-set'
            from dsherp import backup as backup_module
            pieces = backup_module.find_pieces(bench, site)
            set_doc = backup_module.stage_set(bench, site, pieces, kind='release', stamp=time.strftime('%Y%m%d_%H%M%S', time.gmtime()),
                                              token=secrets.token_hex(3), window={'started': started_at, 'finished': backup_module.backup_status.now_iso()},
                                              image_tag=previous, image_id=(previous_images or {}).get('backend', {}).get('image_id', ''),
                                              snapshot=before, frappe_version=backup_module._frappe_version(bench, site))
            status = backup_module.load_status(resolved, root)
            backup_module.backup_status.record_set(status, set_doc, 'staged')
            backup_module.backup_status.save(backup_module.status_path(resolved, root), status)
            if resolved.get('backup_repository'):
                backup_module.upload_sets(resolved, [set_doc], root=root, runner=runner, fatal=False)
```

（注意顺序：`_archive_backup` 先复制，`stage_set` 再**移动** config 件——`find_pieces` 在 `_archive_backup` 之后仍能看到它，因为归档是复制。`report['sites'][site]['set_id'] = set_doc['set_id']`。）

`retire_tenant`：`with backup_module.operations_lock(resolved, root, 'retire-tenant'):` 包住函数体；`bench backup` 之后：

```python
        pieces = backup_module.find_pieces(tenant, site)
        snapshot = take_snapshot(resolved, site, bench=tenant)
        set_doc = backup_module.stage_set(tenant, site, pieces, kind='retire', stamp=time.strftime('%Y%m%d_%H%M%S', time.gmtime()),
                                          token=secrets.token_hex(3), window={'started': started_at, 'finished': backup_module.backup_status.now_iso()},
                                          image_tag=resolved['image_tag'] or 'dev', image_id='', snapshot=snapshot,
                                          frappe_version=backup_module._frappe_version(tenant, site))
        (记录状态同上)
        if resolved.get('backup_repository'):
            backup_module.upload_sets(resolved, [set_doc], root=root, runner=runner, fatal=True)   # Fault 则不 drop
        steps.append(('set', set_doc['set_id']))
```

`retire_tenant` 的 `bench backup` 前先 `_quiesce`（沿用 release 的做法，下线本来就要求无在途运行），下线成功后不必恢复标志（站已 drop）；失败路径 `finally` 恢复。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_admin_cli.py tests/test_backup_cli.py -q`
Expected: 全部 passed（既有 `_archive_backup` 相关断言按新路径更新）

- [ ] **Step 5: 提交**

```bash
git add dsherp/admin.py tests/test_admin_cli.py
git commit -m "feat: 发布前备份与下线最终备份都暂存为备份集；归档目录把 site_config 单独放进 secrets/ 并收紧权限"
```

---

## 阶段 2：双仓库同步、配对、保留与完整性检查

### Task 7: 配置键、密钥文件、doctor 与两个一次性 restic 服务

**Files:**
- Modify: `dsherp/deploy_env.py:25-46`（DEFAULTS）、`:129-165`（settings）
- Modify: `dsherp/admin.py:21-31`（`CONTROL_SECRETS_BY_ENV`）、`:572-604`（`doctor`）
- Modify: `infra/compose.prod.yml`（新服务、`backup-cache` 卷、`secrets:`）、`infra/env/prod.env.example`、`docs/engineering/runtime-baseline.md`
- Modify: `tests/test_deployment_contract.py:120-129`（去掉 `len(services) == 13`，常驻/探针断言只对无 `profiles:` 的服务）、`tests/test_deploy_env.py`、`tests/test_admin_cli.py`（doctor）

**Interfaces:**
- Produces: `resolved['backup_repository']`、`resolved['backup_secrets_repository']`（空串 = 未配置；prod 下非空时必须以 `s3:https://` 开头，否则 `ValueError`）；密钥名 `backup_repository_password`、`backup_secrets_repository_password`（进 `CONTROL_SECRETS_BY_ENV['prod']`，`secrets init` 生成）；`PROVIDED_SECRETS = ('backup_storage_credentials', 'backup_secrets_storage_credentials')`（doctor 只查存在与 0600，且只在配置了仓库时）；可选 `backup_storage_ca.pem`。compose 服务名 `backup-sync-data`、`backup-sync-secrets`。

- [ ] **Step 1: 写失败测试**

`tests/test_deploy_env.py` 追加：

```python
def test_backup_repositories_are_optional_but_production_requires_tls_object_storage():
    resolved = deploy_env.settings({**PROD_ENV, "DSHERP_BACKUP_REPOSITORY": "s3:https://s3.example.com/dsherp-data",
                                    "DSHERP_BACKUP_SECRETS_REPOSITORY": "s3:https://s3.example.com/dsherp-secrets"})
    assert resolved["backup_repository"].endswith("/dsherp-data") and resolved["backup_secrets_repository"].endswith("/dsherp-secrets")
    assert deploy_env.settings(PROD_ENV)["backup_repository"] == ""
    for bad in ("s3:http://s3.example.com/dsherp-data", "/var/backups", "sftp:host:/data"):
        with pytest.raises(ValueError):
            deploy_env.settings({**PROD_ENV, "DSHERP_BACKUP_REPOSITORY": bad, "DSHERP_BACKUP_SECRETS_REPOSITORY": bad})
    with pytest.raises(ValueError):   # both or neither
        deploy_env.settings({**PROD_ENV, "DSHERP_BACKUP_REPOSITORY": "s3:https://s3.example.com/dsherp-data"})
    dev = deploy_env.settings({"DSHERP_ENV": "dev", "DSHERP_BACKUP_REPOSITORY": "s3:http://minio:9000/data",
                               "DSHERP_BACKUP_SECRETS_REPOSITORY": "s3:http://minio:9000/secrets"})
    assert dev["backup_repository"].startswith("s3:http://")   # a local drill may use plaintext inside the host
```

（`PROD_ENV` 用该文件既有的生产设置字典名。）`tests/test_admin_cli.py` 追加：

```python
def test_doctor_demands_the_backup_key_material_only_once_repositories_are_configured(monkeypatch):
    admin.ensure_secrets(RELEASE)
    assert not [f for f in admin.doctor(RELEASE) if "backup" in f]
    configured = {**RELEASE, "backup_repository": "s3:https://s3.example.com/d", "backup_secrets_repository": "s3:https://s3.example.com/s"}
    findings = admin.doctor(configured)
    assert any("backup_storage_credentials" in f for f in findings) and any("backup_secrets_storage_credentials" in f for f in findings)
    assert any("backup_repository_password" in f for f in findings)
    directory = admin.secrets_dir(configured)
    for name in ("backup_repository_password", "backup_secrets_repository_password", "backup_storage_credentials", "backup_secrets_storage_credentials"):
        (directory / name).write_text("x\n"); (directory / name).chmod(0o600)
    assert not [f for f in admin.doctor(configured) if "backup" in f]
    (directory / "backup_storage_credentials").chmod(0o644)
    assert any("backup_storage_credentials" in f and "权限" in f for f in admin.doctor(configured))
```

`tests/test_deployment_contract.py`：把 `test_production_keeps_every_long_lived_service_supervised_and_probed` 改为只对没有 `profiles:` 的服务断言 `healthcheck:`/`restart`，删除 `len(services) == 13`；追加：

```python
def test_the_two_restic_services_are_one_shot_pinned_capability_free_and_see_only_their_own_half():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    for name, volumes, secret in (("backup-sync-data", ("tenant-backups:/backups/tenant:ro", "platform-backups:/backups/platform:ro"),
                                   "backup_repository_password"),
                                  ("backup-sync-secrets", ("tenant-backup-secrets:/backups/tenant:ro", "platform-backup-secrets:/backups/platform:ro"),
                                   "backup_secrets_repository_password")):
        block = _block(body, name)
        assert re.search(r"image: restic/restic:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", block), name
        assert "profiles: [ops]" in block and "restart: \"no\"" in block
        assert "cap_drop: [ALL]" in block and "no-new-privileges:true" in block and "read_only: true" in block
        for volume in volumes:
            assert volume in block, (name, volume)
        assert "backup-cache:/cache" in block and "networks: [provider]" in block and "ports:" not in block
        assert f"RESTIC_PASSWORD_FILE: /run/secrets/{secret}" in block and f"- {secret}" in block
    data, secrets_block = _block(body, "backup-sync-data"), _block(body, "backup-sync-secrets")
    assert "backup-secrets" not in data and "backup_secrets_repository_password" not in data
    assert "tenant-backups:" not in secrets_block and "backup_repository_password\n" not in secrets_block
    assert "env_file: ${DSHERP_SECRETS_DIR" in data and "backup_storage_credentials" in data
    assert "backup_secrets_storage_credentials" in secrets_block
    baseline = (ROOT / "docs/engineering/runtime-baseline.md").read_text()
    assert re.search(r"restic/restic:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", baseline)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_deploy_env.py tests/test_admin_cli.py tests/test_deployment_contract.py -q -k "backup or restic"`
Expected: KeyError `'backup_repository'` / AssertionError

- [ ] **Step 3: 实现**

`deploy_env.DEFAULTS` 加 `'DSHERP_BACKUP_REPOSITORY': ''`、`'DSHERP_BACKUP_SECRETS_REPOSITORY': ''`；`settings()` 末尾：

```python
    data_repo = _text(values, 'DSHERP_BACKUP_REPOSITORY')
    secrets_repo = _text(values, 'DSHERP_BACKUP_SECRETS_REPOSITORY')
    if bool(data_repo) != bool(secrets_repo):
        raise ValueError('Configure both backup repositories (data and secrets) or neither')
    for key, value in (('DSHERP_BACKUP_REPOSITORY', data_repo), ('DSHERP_BACKUP_SECRETS_REPOSITORY', secrets_repo)):
        if value and not value.startswith('s3:http'):
            raise ValueError(key + ' must be an s3: restic repository URL')
        if value and name == 'prod' and not value.startswith('s3:https://'):
            raise ValueError(key + ' must use TLS in production (ruling #8)')
    if data_repo and data_repo == secrets_repo:
        raise ValueError('The data and secrets repositories must differ')
    resolved['backup_repository'] = data_repo
    resolved['backup_secrets_repository'] = secrets_repo
```

`admin.py`：`CONTROL_SECRETS_BY_ENV['prod'] += ('backup_repository_password', 'backup_secrets_repository_password')`（dev 也加，dev 用本机 MinIO 演练）；`PROVIDED_SECRETS = ('backup_storage_credentials', 'backup_secrets_storage_credentials')`；`doctor()` 在 `if resolved.get('backup_repository'):` 下检查 `PROVIDED_SECRETS` 存在与 0600（消息 `缺少对象存储凭据文件：<name>` / `对象存储凭据文件权限过宽：<name>`），并检查两个口令文件存在（`control_secrets` 已覆盖）。

`compose.prod.yml` 新增（digest 实施时用 `docker manifest inspect restic/restic:<版本>` 取多架构清单 digest，并写入 runtime-baseline）：

```yaml
  # One-shot: dsherp-admin backup-sync runs these with `compose run --rm`. Each sees one half
  # of every backup set and holds one repository's password and one storage identity only.
  backup-sync-data:
    profiles: [ops]
    image: restic/restic:0.18.0@sha256:<digest>
    restart: "no"
    entrypoint: ["restic"]
    user: "1000:1000"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    environment:
      RESTIC_REPOSITORY: ${DSHERP_BACKUP_REPOSITORY}
      RESTIC_PASSWORD_FILE: /run/secrets/backup_repository_password
      RESTIC_CACHE_DIR: /cache
      RESTIC_CACERT: /run/secrets/backup_storage_ca.pem
    env_file: ${DSHERP_SECRETS_DIR:-../.runtime/control}/backup_storage_credentials
    secrets: [backup_repository_password, backup_storage_ca.pem]
    volumes:
      - tenant-backups:/backups/tenant:ro
      - platform-backups:/backups/platform:ro
      - backup-cache:/cache
    networks: [provider]
    logging: *logging

  backup-sync-secrets:
    profiles: [ops]
    image: restic/restic:0.18.0@sha256:<digest>
    restart: "no"
    entrypoint: ["restic"]
    user: "1000:1000"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    environment:
      RESTIC_REPOSITORY: ${DSHERP_BACKUP_SECRETS_REPOSITORY}
      RESTIC_PASSWORD_FILE: /run/secrets/backup_secrets_repository_password
      RESTIC_CACHE_DIR: /cache
      RESTIC_CACERT: /run/secrets/backup_storage_ca.pem
    env_file: ${DSHERP_SECRETS_DIR:-../.runtime/control}/backup_secrets_storage_credentials
    secrets: [backup_secrets_repository_password, backup_storage_ca.pem]
    volumes:
      - tenant-backup-secrets:/backups/tenant:ro
      - platform-backup-secrets:/backups/platform:ro
      - backup-cache:/cache
    networks: [provider]
    logging: *logging
```

`volumes:` 加 `backup-cache:`；`secrets:` 加三项（`backup_repository_password`、`backup_secrets_repository_password`、`backup_storage_ca.pem`，文件都在 `${DSHERP_SECRETS_DIR:-../.runtime/control}/`）。`RESTIC_CACERT` 指向的文件不存在时 restic 会报错：`backup-init` 在没有 CA 文件时写一个空文件？不——改为 `backup-init` 要求运维要么提供 `backup_storage_ca.pem`，要么创建一个只含注释的占位文件是错误的做法（restic 会解析失败）。决定：compose 里 **不**默认设 `RESTIC_CACERT`；`dsherp-admin backup-sync` 在 `secrets_dir/backup_storage_ca.pem` 存在时通过 `compose run -e RESTIC_CACERT=/run/secrets/backup_storage_ca.pem` 注入（Task 9 的 restic 包装负责），compose 的 `secrets:` 列表里也不写 CA，改由 `compose run -v <secrets_dir>/backup_storage_ca.pem:/run/secrets/backup_storage_ca.pem:ro` 挂入。测试相应只断言前两个 secrets。

`prod.env.example` 加两行注释键；`runtime-baseline.md` 加 restic 行（版本、digest、amd64+arm64）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_deploy_env.py tests/test_admin_cli.py tests/test_deployment_contract.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/deploy_env.py dsherp/admin.py infra/compose.prod.yml infra/env/prod.env.example docs/engineering/runtime-baseline.md tests/
git commit -m "feat: 备份仓库配置键（prod 必须 TLS）、两侧口令与凭据文件、doctor 检查、两个只见自己那一半的一次性 restic 服务"
```

### Task 8: 异地保留选择（`dsherp/backup_retention.py`）

**Files:**
- Create: `dsherp/backup_retention.py`
- Test: `tests/test_backup_retention.py`

**Interfaces:**
- Produces: `KEEP = {'daily': 7, 'weekly': 4, 'monthly': 3}`；`select(sets, now) -> dict[str, str]`（`set_id → 'keep' | 'drop'`）。`sets` 是可迭代的 dict：`set_id`、`site`、`kind`、`stamp`（`YYYYmmdd_HHMMSS`，UTC）、`state`（`SET_STATES` 之一）。规则：只有 `complete`/`verified` 参与淘汰；`staged`/pending 一律 `keep`；`kind='retire'` 一律 `keep`；每站最新 `complete` 与最新 `verified` 一律 `keep`；其余按 restic 语义——从新到旧遍历，同一 UTC 日/ISO 周/月里最新的一份分别占一个名额，日 7、周 4、月 3，被任一规则命中即 `keep`。`now` 只用于日志，不影响选择（按有快照的周期计算，与设计 §4.7 一致）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backup_retention.py
"""Retention is decided on the host, per Site, from the set stamps: restic's own grouping
would make every uniquely-tagged snapshot its own group and never expire anything."""
from datetime import datetime, timedelta, timezone

from dsherp import backup_retention


def _sets(site, days, *, state="complete", kind="scheduled", start=datetime(2026, 9, 6, 2, tzinfo=timezone.utc)):
    rows = []
    for offset in days:
        when = start - timedelta(days=offset)
        stamp = when.strftime("%Y%m%d_%H%M%S")
        rows.append({"set_id": f"{stamp}-{site.replace('.', '_')}-aaaaaa", "site": site, "kind": kind, "stamp": stamp, "state": state})
    return rows


def test_seven_daily_four_weekly_three_monthly_are_kept_per_site_and_older_ones_dropped():
    site = "acme.tenant.example.com"
    sets = _sets(site, range(0, 120))  # a set every day for 120 days, two per day would be the real 12h cadence
    decision = backup_retention.select(sets, now=datetime(2026, 9, 6, 3, tzinfo=timezone.utc))
    kept = sorted(s["stamp"] for s in sets if decision[s["set_id"]] == "keep")
    assert len([s for s in kept if s >= "20260831"]) == 7          # the last 7 days
    assert len(kept) <= 7 + 4 + 3 and len(kept) >= 7 + 3            # weekly/monthly may overlap the daily ones
    assert decision[sets[-1]["set_id"]] == "drop"                   # 120 days ago is gone
    assert decision[sets[0]["set_id"]] == "keep"


def test_twice_daily_sets_keep_only_the_newest_of_each_day():
    site = "acme.tenant.example.com"
    morning = _sets(site, range(0, 10))
    afternoon = _sets(site, range(0, 10), start=datetime(2026, 9, 6, 14, tzinfo=timezone.utc))
    decision = backup_retention.select(morning + afternoon, now=None)
    for day in range(0, 7):
        assert decision[afternoon[day]["set_id"]] == "keep" and decision[morning[day]["set_id"]] == "drop"


def test_pending_retire_and_protected_sets_are_never_dropped_and_sites_do_not_share_quota():
    a = _sets("acme.tenant.example.com", range(0, 30))
    b = _sets("beta.tenant.example.com", range(0, 30))
    pending = _sets("acme.tenant.example.com", [40], state="data_uploaded")
    retired = _sets("gone.tenant.example.com", [200], kind="retire")
    old_verified = _sets("acme.tenant.example.com", [90], state="verified")
    decision = backup_retention.select(a + b + pending + retired + old_verified, now=None)
    assert decision[pending[0]["set_id"]] == "keep" and decision[retired[0]["set_id"]] == "keep"
    assert decision[old_verified[0]["set_id"]] == "keep"                     # newest verified of the site
    assert sum(1 for s in a if decision[s["set_id"]] == "keep") == sum(1 for s in b if decision[s["set_id"]] == "keep")
    assert decision[a[0]["set_id"]] == "keep"                               # newest complete
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_retention.py -q`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# dsherp/backup_retention.py
"""Which off-site sets to keep (design §4.7). Pure: the caller lists the sets both
repositories hold, this decides, the caller forgets the dropped ones on both sides."""
from datetime import datetime, timezone

KEEP = {'daily': 7, 'weekly': 4, 'monthly': 3}
EXPIRABLE = ('complete', 'verified')


def _when(stamp):
    return datetime.strptime(stamp, '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)


def _buckets(when):
    iso = when.isocalendar()
    return {'daily': when.strftime('%Y-%m-%d'), 'weekly': f'{iso[0]}-W{iso[1]:02d}', 'monthly': when.strftime('%Y-%m')}


def select(sets, now=None):
    decision = {}
    by_site = {}
    for row in sets:
        by_site.setdefault(row['site'], []).append(row)
    for site, rows in by_site.items():
        rows = sorted(rows, key=lambda row: row['stamp'], reverse=True)  # newest first
        newest = {}
        for state in EXPIRABLE:
            for row in rows:
                if row['state'] == state:
                    newest[state] = row['set_id']; break
        seen = {rule: set() for rule in KEEP}
        for row in rows:
            if row['state'] not in EXPIRABLE or row['kind'] == 'retire' or row['set_id'] in newest.values():
                decision[row['set_id']] = 'keep'; continue
            buckets = _buckets(_when(row['stamp']))
            keep = False
            for rule, limit in KEEP.items():
                if buckets[rule] not in seen[rule] and len(seen[rule]) < limit:
                    seen[rule].add(buckets[rule]); keep = True
            decision[row['set_id']] = 'keep' if keep else 'drop'
    return decision
```

（注意：`newest` 的两条保护行本身也应占用它们所在日/周/月的名额，否则会多保留一份——按 restic 语义，保护行先登记桶：实现时在遍历前对 `newest` 中的行执行同样的桶登记但不计入 `limit`。测试 1 的 `len(kept) <= 14` 断言会暴露这一点。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_retention.py -q`
Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup_retention.py tests/test_backup_retention.py
git commit -m "feat: 异地保留按站点从备份集时间戳选择 7 日/4 周/3 月，保护最新完整与最新已验证集，pending 与下线集不淘汰"
```

### Task 9: `backup-init` / `backup-sync`——上传、配对、淘汰、完整性检查、事件式上传

> **契约修订（第二次审阅）**：`complete` 的判定按设计 §4.0/§4.2（2.1）：`snapshots --json --tag set=<id>` 取**完整** `id` → `restic dump <id> <路径>/set.json`（数据侧）与 `dump <id> <路径>/pair.json`（密钥侧）读回 → 核对 `set_id`、`site`、`format`、各件 sha256、`set_sha256`、`config_sha256` → 才 `complete`；状态文件里已有的快照 id 每次也重新 `dump` 核对，不凭本地记录跳过；`_remote_sets` 对状态文件不认识的远端集按 pending 处理并核对，只有核对过的集参与淘汰。`FakeRestic` 需要能回答 `dump`（返回配置好的清单文本、可配置成"错配"）。测试补："错配的 pair.json 保持 pending 并告警""已记录 id 的远端快照被删后退回 pending""未知远端集先核对再入 complete"。状态文件损坏时拒绝 forget/prune。计划里的 `upload_sets`/`_remote_sets` 代码块据此重写。

**Files:**
- Modify: `dsherp/backup.py`（追加）、`dsherp/admin.py`（CLI）
- Test: `tests/test_backup_cli.py`（追加）

**Interfaces:**
- Consumes: Task 7 的 compose 服务与 `resolved['backup_repository']`；Task 8 的 `select`。
- Produces:
  - `restic(resolved, side, arguments, *, root, runner, timeout, extra_env=()) -> str`：`side ∈ ('data', 'secrets')`；argv = `['docker','compose','-p',project,'--env-file',<prod.env>,'-f',<compose>,'run','--rm','-T', *(['-v', f'{ca}:/run/secrets/backup_storage_ca.pem:ro','-e','RESTIC_CACERT=/run/secrets/backup_storage_ca.pem'] if ca exists), f'backup-sync-{side}', *arguments]`；非零退出 → `Fault`（尾部按 `secrets` 脱敏，包含两侧口令与凭据文件内容）。
  - `backup_init(resolved, *, root, runner) -> dict`（两侧 `cat config` 成功即 `kept`，否则 `init`）。
  - `upload_sets(resolved, set_docs, *, root, runner, fatal, clock=time.time) -> dict`：对每个集：缺数据侧则 `restic backup --host <project> --tag site=<site> --tag set=<id> --tag kind=<kind> /backups/<bench>/sets/<site>/<id>`（`<bench>` = `platform` 或 `tenant`，按站判断）；缺密钥侧则对 `/backups/<bench>/<site>/<id>` 同法；然后两侧 `restic snapshots --json --tag set=<id>` 读回；两侧都有且 `pair.json` 与 `set.json` 一致（`pair_matches`）→ `complete` 并记 `data_snapshot`/`secrets_snapshot`；只有一侧 → 相应 pending 状态；`fatal=True` 时任一失败抛 `Fault`，否则记入返回值与状态。`offsite.last_success.at` = 变为 complete 的时刻。
  - `backup_sync(resolved, *, root, runner, clock) -> dict`：锁（若调用方已持有则复用——`backup()` 内部调用时不再加锁：用参数 `locked=True`）；`upload_sets` 对所有 `state ∉ (complete, verified)` 的集；淘汰：两侧 `restic snapshots --json` 列出 `set=` 标签 → 组装 `sets`（状态取自状态文件，未知集视为 `complete`，若两侧都有）→ `select` → 对两侧都 `drop` 的集 `restic forget <id…>`（每侧一次）→ 每侧 `restic prune`；检查：每侧 `restic check --read-data-subset=<n>/7`（`n = (当日序数 % 7) + 1`）；状态记 `sync`/`check` 两个 run 与每站 `offsite`。
  - CLI：`dsherp-admin backup-init`、`dsherp-admin backup-sync`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_backup_cli.py`）

```python
class FakeRestic:
    """Answers `docker compose run --rm -T backup-sync-<side> restic …` like two repositories would."""

    def __init__(self):
        self.calls = []
        self.snapshots = {"data": {}, "secrets": {}}   # side -> set_id -> snapshot id
        self.fail = set()                                # (side, verb) that fail
        self.pairs = {}                                  # set_id -> pair.json text the secrets repo would hold

    def __call__(self, command, **kwargs):
        text = " ".join(command)
        self.calls.append(text)
        if "run --rm" not in text:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        side = "data" if "backup-sync-data" in text else "secrets"
        verb = command[command.index(f"backup-sync-{side}") + 1]
        if (side, verb) in self.fail:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Fatal: {verb} failed on {side}"})()
        out = ""
        if verb == "backup":
            set_id = [a for a in command if a.startswith("set=")][0][4:]
            self.snapshots[side][set_id] = f"{side[:1]}{set_id[:8]}"
        if verb == "snapshots":
            wanted = [a for a in command if a.startswith("set=")]
            rows = [{"short_id": sid, "tags": [f"set={set_id}", f"site={'acme.tenant.example.com' if 'acme' in set_id else 'platform.tenant.example.com'}", "kind=scheduled"],
                     "time": "2026-09-06T02:01:00Z"} for set_id, sid in self.snapshots[side].items() if not wanted or f"set={set_id}" in wanted]
            out = json.dumps(rows)
        if verb == "cat":
            return type("R", (), {"returncode": 0 if self.snapshots[side] or (side, "init") in self.fail else 1, "stdout": "", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


CONFIGURED = {**RELEASE, "backup_repository": "s3:https://s3.example.com/data", "backup_secrets_repository": "s3:https://s3.example.com/secrets"}


def _staged(host, *set_ids):
    status = backup_status.empty()
    for set_id in set_ids:
        site = "acme.tenant.example.com" if "acme" in set_id else "platform.tenant.example.com"
        backup_status.record_set(status, {"set_id": set_id, "site": site, "kind": "scheduled", "stamp": set_id[:15]}, "staged")
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    return status


def test_sync_uploads_both_halves_reads_them_back_and_only_then_marks_the_set_complete(host):
    _prepare()
    set_id = "20260906_020007-acme_tenant_example_com-k3f9qx"
    _staged(host, set_id)
    restic = FakeRestic()
    report = backup.backup_sync(CONFIGURED, runner=restic, clock=lambda: 1_757_124_100.0)
    data_upload = [c for c in restic.calls if "backup-sync-data backup" in c][0]
    assert f"--tag site=acme.tenant.example.com --tag set={set_id} --tag kind=scheduled /backups/tenant/sets/acme.tenant.example.com/{set_id}" in data_upload
    assert "--host dsherp" in data_upload
    secrets_upload = [c for c in restic.calls if "backup-sync-secrets backup" in c][0]
    assert f"/backups/tenant/acme.tenant.example.com/{set_id}" in secrets_upload and "sets/" not in secrets_upload
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    row = status["sets"][set_id]
    assert row["state"] == "complete" and row["data_snapshot"] and row["secrets_snapshot"]
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"]["set_id"] == set_id
    assert report["complete"] == [set_id]
    assert any("backup-sync-data check --read-data-subset=" in c for c in restic.calls)
    assert any("backup-sync-secrets check --read-data-subset=" in c for c in restic.calls)
    assert "RESTIC_REPOSITORY" not in " ".join(restic.calls)   # compose supplies it; argv carries no repository or credential


def test_a_half_uploaded_set_stays_pending_is_completed_next_run_and_sync_success_time_moves_only_then(host):
    _prepare()
    set_id = "20260906_020007-acme_tenant_example_com-k3f9qx"
    _staged(host, set_id)
    restic = FakeRestic(); restic.fail.add(("secrets", "backup"))
    report = backup.backup_sync(CONFIGURED, runner=restic, clock=lambda: 1_757_124_100.0)
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_id]["state"] == "data_uploaded" and report["pending"] == [set_id]
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"] is None
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_attempt"]["ok"] is False
    assert status["runs"]["sync"]["last_attempt"]["ok"] is False
    restic.fail.clear()
    backup.backup_sync(CONFIGURED, runner=restic, clock=lambda: 1_757_167_300.0)
    assert [c for c in restic.calls if "backup-sync-data backup" in c] .__len__() == 1, "the data half is not uploaded twice"
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sets"][set_id]["state"] == "complete"
    assert status["sites"]["acme.tenant.example.com"]["offsite"]["last_success"]["at"] == "2026-09-06T14:01:40Z"


def test_unreachable_storage_is_a_recorded_failure_not_a_crash_and_nothing_is_forgotten(host):
    _prepare()
    _staged(host, "20260906_020007-acme_tenant_example_com-k3f9qx")
    restic = FakeRestic(); restic.fail |= {("data", "backup"), ("secrets", "backup"), ("data", "snapshots"), ("secrets", "snapshots")}
    report = backup.backup_sync(CONFIGURED, runner=restic, clock=lambda: 1_757_124_100.0)
    assert report["ok"] is False and not any(" forget " in c or " prune" in c for c in restic.calls)
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert "failed on data" in status["runs"]["sync"]["last_attempt"]["error"]


def test_retention_forgets_only_sets_dropped_on_both_sides_then_prunes(host, monkeypatch):
    _prepare()
    status = backup_status.empty()
    ids = [f"2026{m:02d}01_020000-acme_tenant_example_com-aaaaaa" for m in range(1, 9)]  # 8 monthly sets
    for set_id in ids:
        backup_status.record_set(status, {"set_id": set_id, "site": "acme.tenant.example.com", "kind": "scheduled", "stamp": set_id[:15]},
                                 "complete", data_snapshot="d" + set_id[:6], secrets_snapshot="s" + set_id[:6])
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    restic = FakeRestic()
    for set_id in ids:
        restic.snapshots["data"][set_id] = "d" + set_id[:6]; restic.snapshots["secrets"][set_id] = "s" + set_id[:6]
    del restic.snapshots["secrets"][ids[0]]   # the oldest one is already gone on the secrets side
    backup.backup_sync(CONFIGURED, runner=restic, clock=lambda: 1_757_124_100.0)
    forget_data = [c for c in restic.calls if "backup-sync-data forget" in c]
    forget_secrets = [c for c in restic.calls if "backup-sync-secrets forget" in c]
    assert forget_data and forget_secrets
    assert "d202601" not in forget_data[0], "a set the other side no longer holds is not forgotten here"
    assert "d202602" in forget_data[0] and "s202602" in forget_secrets[0]
    assert "d202608" not in forget_data[0]  # newest complete is protected
    assert [c for c in restic.calls if c.endswith("backup-sync-data prune")] and [c for c in restic.calls if c.endswith("backup-sync-secrets prune")]


def test_backup_init_is_idempotent_and_refuses_without_repositories(host):
    _prepare()
    with pytest.raises(admin.Fault, match="DSHERP_BACKUP_REPOSITORY"):
        backup.backup_init(RELEASE, runner=FakeRestic())
    restic = FakeRestic()
    assert backup.backup_init(CONFIGURED, runner=restic) == {"data": "created", "secrets": "created"}
    restic.snapshots["data"]["x"] = "1"; restic.snapshots["secrets"]["x"] = "1"
    assert backup.backup_init(CONFIGURED, runner=restic) == {"data": "kept", "secrets": "kept"}


def test_a_private_ca_is_handed_to_restic_only_when_present(host):
    _prepare()
    restic = FakeRestic()
    backup.backup_init(CONFIGURED, runner=restic)
    assert "RESTIC_CACERT" not in " ".join(restic.calls)
    ca = admin.secrets_dir(CONFIGURED) / "backup_storage_ca.pem"; ca.write_text("-----BEGIN CERTIFICATE-----\n"); ca.chmod(0o600)
    restic = FakeRestic()
    backup.backup_init(CONFIGURED, runner=restic)
    assert f"-v {ca}:/run/secrets/backup_storage_ca.pem:ro -e RESTIC_CACERT=/run/secrets/backup_storage_ca.pem" in restic.calls[0]


def test_retire_refuses_to_drop_when_the_final_set_cannot_leave_the_host_and_release_only_warns(host, monkeypatch):
    from tests.test_admin_cli import RUNNING_NEW
    _prepare()
    bench = StagingBench([SAME] * 4)
    restic = FakeRestic(); restic.fail.add(("data", "backup"))
    with pytest.raises(admin.Fault, match="异地"):
        admin.retire_tenant(CONFIGURED, "acme", bench_factory=lambda kind: bench, runner=restic)
    assert not any("drop-site" in v for v in bench.verbs) and admin.load_tenants(CONFIGURED)
    bench = StagingBench([SAME] * 4)
    report = admin.release(CONFIGURED, "v0.4.0", bench_factory=lambda kind: bench, runner=lambda c, **k: RUNNING_NEW(c, **k) if "compose" in c and "ps" in c or c[0] == "docker" and c[1] == "inspect" else restic(c, **k), from_tag="v0.3.0")
    assert report["clean"] and report["sites"]["acme.tenant.example.com"]["offsite"]["ok"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_cli.py -q -k "sync or init or retention or ca or retire_refuses"`
Expected: `AttributeError: module 'dsherp.backup' has no attribute 'backup_sync'`

- [ ] **Step 3: 实现（追加到 `dsherp/backup.py`）**

```python
def _compose_run(resolved, root, service, arguments, ca=None):
    file = Path(root) / admin.COMPOSE[resolved['env']]
    command = ['docker', 'compose', '-p', resolved['project']]
    env_file = deploy_env.env_file(resolved['env'], root)
    if env_file.exists():
        command += ['--env-file', str(env_file)]
    command += ['-f', str(file), 'run', '--rm', '-T']
    if ca is not None:
        command += ['-v', f'{ca}:/run/secrets/backup_storage_ca.pem:ro', '-e', 'RESTIC_CACERT=/run/secrets/backup_storage_ca.pem']
    return command + [service, *arguments]


def _require_repositories(resolved):
    if not resolved.get('backup_repository'):
        raise Fault('没有配置异地仓库：在 infra/env/prod.env 写 DSHERP_BACKUP_REPOSITORY 与 DSHERP_BACKUP_SECRETS_REPOSITORY，'
                    '放好口令与凭据文件（doctor 会检查），再 backup-init')


def _redactions(resolved, root):
    values = []
    for name in ('backup_repository_password', 'backup_secrets_repository_password',
                 'backup_storage_credentials', 'backup_secrets_storage_credentials'):
        path = admin.secrets_dir(resolved, root) / name
        if path.exists():
            values += [line.split('=', 1)[-1].strip() for line in path.read_text().splitlines() if line.strip()]
    return values


def restic(resolved, side, arguments, *, root=ROOT, runner=subprocess.run, timeout=3600):
    """Run restic against one repository through its one-shot compose service."""
    ca = admin.secrets_dir(resolved, root) / 'backup_storage_ca.pem'
    command = _compose_run(resolved, root, f'backup-sync-{side}', arguments, ca=ca if ca.exists() else None)
    result = runner(command, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-6:])
        for value in _redactions(resolved, root):
            if value:
                tail = tail.replace(value, '[redacted]')
        raise Fault(f'restic（{side} 仓库）{arguments[0]} 失败：\n{tail}')
    return result.stdout


def backup_init(resolved, *, root=ROOT, runner=subprocess.run):
    _require_repositories(resolved)
    outcome = {}
    for side in ('data', 'secrets'):
        try:
            restic(resolved, side, ['cat', 'config'], root=root, runner=runner, timeout=300)
            outcome[side] = 'kept'
        except Fault:
            restic(resolved, side, ['init'], root=root, runner=runner, timeout=600)
            outcome[side] = 'created'
    return outcome


def _bench_of(resolved, site):
    return 'platform' if site == resolved['platform_site'] else 'tenant'


def _snapshot_for(resolved, side, set_id, *, root, runner):
    rows = json.loads(restic(resolved, side, ['snapshots', '--json', '--tag', f'set={set_id}'], root=root, runner=runner, timeout=300) or '[]')
    rows = [row for row in rows if f'set={set_id}' in (row.get('tags') or [])]
    return rows[-1]['short_id'] if rows else None


def upload_sets(resolved, set_docs, *, root=ROOT, runner=subprocess.run, fatal=False, clock=time.time, status=None):
    _require_repositories(resolved)
    own_status = status is None
    status = status if status is not None else load_status(resolved, root)
    outcome = {'complete': [], 'pending': [], 'failed': {}}
    for doc in set_docs:
        set_id, site, kind = doc['set_id'], doc['site'], doc['kind']
        bench = _bench_of(resolved, site)
        row = status['sets'].get(set_id, {})
        ids = {'data': row.get('data_snapshot'), 'secrets': row.get('secrets_snapshot')}
        paths = {'data': f'/backups/{bench}/sets/{site}/{set_id}', 'secrets': f'/backups/{bench}/{site}/{set_id}'}
        errors = []
        for side in ('data', 'secrets'):
            if ids[side]:
                continue
            try:
                restic(resolved, side, ['backup', '--host', resolved['project'], '--tag', f'site={site}', '--tag', f'set={set_id}',
                                        '--tag', f'kind={kind}', paths[side]], root=root, runner=runner, timeout=3600)
                ids[side] = _snapshot_for(resolved, side, set_id, root=root, runner=runner)
                if not ids[side]:
                    errors.append(f'{side}: 上传后读不回快照')
            except Fault as error:
                errors.append(str(error))
        state = 'complete' if ids['data'] and ids['secrets'] else 'data_uploaded' if ids['data'] else 'secrets_uploaded' if ids['secrets'] else row.get('state', 'staged')
        backup_status.record_set(status, doc, state, data_snapshot=ids['data'], secrets_snapshot=ids['secrets'])
        at = backup_status.now_iso(clock)
        if state == 'complete':
            outcome['complete'].append(set_id)
            backup_status.record_site(status, site, 'offsite', at=at, ok=True, set_id=set_id, stamp=doc['stamp'])
        else:
            outcome['pending'].append(set_id)
            outcome['failed'][set_id] = '；'.join(errors) or f'只上传了一侧（{state}）'
            backup_status.record_site(status, site, 'offsite', at=at, ok=False, error=outcome['failed'][set_id][:500])
    if own_status:
        backup_status.save(status_path(resolved, root), status)
    if fatal and outcome['failed']:
        raise Fault('备份集没有完整到达异地：' + '；'.join(f'{k}: {v}' for k, v in outcome['failed'].items()))
    return outcome
```

`backup_sync`（同文件）：

```python
def _remote_sets(resolved, status, *, root, runner):
    """Every set either repository holds, as retention input; the status file says its state."""
    held = {}
    for side in ('data', 'secrets'):
        for row in json.loads(restic(resolved, side, ['snapshots', '--json'], root=root, runner=runner, timeout=600) or '[]'):
            tags = dict(tag.split('=', 1) for tag in (row.get('tags') or []) if '=' in tag)
            if 'set' in tags:
                held.setdefault(tags['set'], {'site': tags.get('site'), 'kind': tags.get('kind', 'scheduled')})[side] = row['short_id']
    sets = []
    for set_id, row in held.items():
        parsed = backup_sets.parse_set_id(set_id)
        if not parsed or not row.get('site'):
            continue
        known = status['sets'].get(set_id, {})
        state = known.get('state') or ('complete' if row.get('data') and row.get('secrets') else 'data_uploaded' if row.get('data') else 'secrets_uploaded')
        sets.append({'set_id': set_id, 'site': row['site'], 'kind': known.get('kind', row['kind']), 'stamp': parsed['stamp'],
                     'state': state, 'data': row.get('data'), 'secrets': row.get('secrets')})
    return sets


def backup_sync(resolved, *, root=ROOT, runner=subprocess.run, clock=time.time, locked=False):
    _require_repositories(resolved)
    with (operations_lock(resolved, root, 'backup-sync') if not locked else _nothing()):
        status = load_status(resolved, root)
        path = status_path(resolved, root)
        report = {'ok': True, 'complete': [], 'pending': [], 'forgotten': [], 'errors': []}
        pending = [dict(set_id=set_id, **row) for set_id, row in status['sets'].items() if row['state'] not in ('complete', 'verified')]
        try:
            outcome = upload_sets(resolved, pending, root=root, runner=runner, clock=clock, status=status)
            report['complete'], report['pending'] = outcome['complete'], outcome['pending']
            if outcome['failed']:
                report['ok'] = False; report['errors'] += list(outcome['failed'].values())
            remote = _remote_sets(resolved, status, root=root, runner=runner)
            decision = backup_retention.select(remote, now=None)
            doomed = [row for row in remote if decision[row['set_id']] == 'drop' and row.get('data') and row.get('secrets')]
            for side in ('data', 'secrets'):
                ids = [row[side] for row in doomed]
                if ids:
                    restic(resolved, side, ['forget', *ids], root=root, runner=runner, timeout=1800)
                    restic(resolved, side, ['prune'], root=root, runner=runner, timeout=1800)
            report['forgotten'] = [row['set_id'] for row in doomed]
            for row in doomed:
                status['sets'].pop(row['set_id'], None)
        except Fault as error:
            report['ok'] = False; report['errors'].append(str(error))
        backup_status.record_run(status, 'sync', at=backup_status.now_iso(clock), ok=report['ok'], error='；'.join(report['errors']) or None)
        subset = f'{int(time.strftime("%j", time.gmtime(clock()))) % 7 + 1}/7'
        check_ok, check_errors = True, []
        for side in ('data', 'secrets'):
            try:
                restic(resolved, side, ['check', f'--read-data-subset={subset}'], root=root, runner=runner, timeout=3600)
            except Fault as error:
                check_ok = False; check_errors.append(str(error))
        backup_status.record_run(status, 'check', at=backup_status.now_iso(clock), ok=check_ok, error='；'.join(check_errors) or None)
        report['check_ok'] = check_ok
        report['ok'] = report['ok'] and check_ok
        backup_status.save(path, status)
    return report


@contextmanager
def _nothing():
    yield None
```

`backup()` 里 `if sync:` 改为 `backup_sync(resolved, root=root, runner=runner, clock=clock, locked=True)` 并放在 `with operations_lock` 之内。CLI：`sub.add_parser('backup-init', …)`、`sub.add_parser('backup-sync', …)`，分派 `_print(backup_module.backup_init(resolved))`、`report = backup_module.backup_sync(resolved); _print(report); return 0 if report['ok'] else 1`。`retire_tenant` 的 `upload_sets(..., fatal=True)` 已在 Task 6 接线；`release` 把 `upload_sets(..., fatal=False)` 的 `outcome` 记入 `report['sites'][site]['offsite'] = {'ok': not outcome['failed']}`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_cli.py tests/test_admin_cli.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup.py dsherp/admin.py tests/test_backup_cli.py
git commit -m "feat: backup-init/backup-sync——两侧上传后读回配对才算完整、半成功保持 pending、宿主侧保留选择后两侧同步 forget/prune、按日轮换的数据读取检查"
```

---

## 阶段 3：隔离恢复、解密核验、精确清理

### Task 10: 隔离恢复栈 `infra/compose.restore.yml`

> **契约修订（第二次审阅）**：两个 fetch 服务各挂各自的取回卷（`restore-fetched-data:/fetched`、`restore-fetched-secrets:/fetched`），只有 backend 同时挂两个（`/home/frappe/fetched/data`、`/home/frappe/fetched/secrets`）。契约测试断言的是行为：所有服务只在 `restore` 网络（fetch 服务另加 `provider`）、没有 `ports:`、fetch 服务互不共享卷、backend 镜像来自 `${DSHERP_IMAGE_REGISTRY}/dsherp-frappe:${DSHERP_IMAGE_TAG}`、第三方镜像 digest 与生产一致；不固定服务集合的精确相等，也不锁文本排版。

**Files:**
- Create: `infra/compose.restore.yml`
- Modify: `dsherp/deploy_env.py:190-205`（`DEPLOYMENT_FILES` 加该文件）、`tests/test_deployment_contract.py`（`IMAGE_SOURCES` 加该文件；新增行为测试）

**Interfaces:**
- Produces: compose 项目名由调用方给（`dsherp-restore`）；服务 `db`（与 prod 同一 mariadb digest）、`redis-cache`、`redis-queue`、`backend`（`${DSHERP_IMAGE_REGISTRY}/dsherp-frappe:${DSHERP_IMAGE_TAG}`，`command: ["start.sh"]`，挂 `restore-sites`、`restore-logs`、`restore-fetched:/home/frappe/fetched`）、`restore-fetch-data`、`restore-fetch-secrets`（restic，`profiles: [fetch]`，各自 `secrets`/`env_file`，挂 `restore-fetched:/fetched`）；网络只有 `restore: {internal: true}`——`restore-fetch-*` 例外地需要出网取对象存储，所以它们另接 `provider: {}`；没有任何 `ports:`；卷 `restore-db`、`restore-sites`、`restore-logs`、`restore-fetched`、`restore-cache`。
- 容器内路径 `FETCHED = '/home/frappe/fetched'`（backend 视角），`/fetched`（restic 视角）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_deployment_contract.py`）

```python
def test_the_restore_stack_is_isolated_from_the_outside_and_from_production():
    """Design §4.5: a restored copy has no ingress, no worker, no scheduler, no mail and no
    route out except the fetch containers that pull the off-site set."""
    text = (ROOT / "infra/compose.restore.yml").read_text()
    body = text.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    services = set(re.findall(r"^  ([a-z0-9-]+):$", body, re.MULTILINE))
    assert services == {"db", "redis-cache", "redis-queue", "backend", "restore-fetch-data", "restore-fetch-secrets"}
    assert "ports:" not in text and "caddy" not in text and "worker" not in text.replace("bench worker", "")
    assert re.search(r"^  restore: \{internal: true\}$", text, re.MULTILINE)
    for name in ("db", "redis-cache", "redis-queue", "backend"):
        assert "networks: [restore]" in _block(body, name), name
    for name in ("restore-fetch-data", "restore-fetch-secrets"):
        block = _block(body, name)
        assert "profiles: [fetch]" in block and "networks: [restore, provider]" in block and "restore-fetched:/fetched" in block
        assert re.search(r"image: restic/restic:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", block)
    assert f"mariadb:11.8@sha256:{DB_V16}" in text or f"@sha256:{DB_V16}" in _block(body, "db")
    assert "${DSHERP_IMAGE_REGISTRY}/dsherp-frappe:${DSHERP_IMAGE_TAG}" in _block(body, "backend")
    assert "restore-fetched:/home/frappe/fetched" in _block(body, "backend")
    volumes = text.split("\nvolumes:\n", 1)[1]
    assert not re.search(r"^  - [$./]", volumes, re.MULTILINE)
    assert "infra/compose.restore.yml" in (ROOT / "dsherp/deploy_env.py").read_text()
```

（把 `"infra/compose.restore.yml"` 加进本文件顶部的 `IMAGE_SOURCES`，让 digest 一致性测试也覆盖它。）

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py -q -k restore_stack`
Expected: FileNotFoundError

- [ ] **Step 3: 实现**

以 `infra/compose.prod.yml` 的 `db`、`redis-cache`、`redis-queue`、`backend` 块为蓝本（同 digest、同 healthcheck、同 `x-frappe` 锚点、同 `secrets: [db_root_password]`——root 口令文件由 `restore_drill` 在临时目录生成并通过 `DSHERP_SECRETS_DIR` 指向），去掉 `ports`、`agent`/`worker`/`site` 网络与归档卷，backend 只接 `restore` 网络并加 `restore-fetched:/home/frappe/fetched`；两个 fetch 服务复制 Task 7 的 restic 块，改名、`profiles: [fetch]`、卷 `restore-fetched:/fetched`（可写）、`restore-cache:/cache`、网络 `[restore, provider]`。文件头注释写明：由 `dsherp-admin restore-drill` 以独立项目名拉起、用毕 `down -v`；`DSHERP_IMAGE_TAG` 由命令按备份集记录传入。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add infra/compose.restore.yml dsherp/deploy_env.py tests/test_deployment_contract.py
git commit -m "feat: 隔离恢复栈 compose——internal 网络、无入口无 worker，只有取回容器可出网"
```

### Task 11: `dsherp-admin restore-drill`——取回、隔离恢复、比对、解密抽样、精确清理

> **契约修订（第二次审阅）**：① **先元数据后起栈**：在生产栈用 `backup.restic(resolved, 'data', ['dump', <完整 id>, <路径>/set.json])` 读回各站最新 `complete` 集的 `set.json`，按 `(image_tag, image_id)` 分组，每组用该 tag 起隔离栈，并用 `docker inspect` 核对隔离栈 backend 的运行镜像 id == 集的 `image_id`（清单存在时也 == 清单）；镜像不在本机即该组失败。② **接线**：`admin.Bench` 增加可选 `project`、`compose_file`、`env` 参数（默认不变），`DrillStack.bench()` 用同一份环境（tag、密钥目录）构造；增加 `Bench.script(body, secrets=())`：`env/bin/python -` 读 stdin 的进程内脚本（不连站点），用于 `frappe.installer._new_site(...)` 与恢复函数——root/admin 口令与 `encryption_key` 只出现在 stdin 脚本里，隔离栈的 `common_site_config.json` 只含 db/redis 地址。③ 取回核对增加 `snapshot_sha256`（重算 `snapshot.json`）与 `config_sha256`。④ 阶段 3 末用真实隔离栈跑通（冷启动 bench、凭据、CA、镜像核验），假 Bench 只覆盖分支。计划里的 `restore_drill` 代码块据此重写。

**Files:**
- Create: `dsherp/restore_drill.py`
- Modify: `dsherp/admin.py`（CLI）
- Test: `tests/test_restore_drill.py`

**Interfaces:**
- Consumes: `backup.restic`（不直接用——drill 栈有自己的 fetch 服务；本模块用 `_compose_run` 同款构造但项目名与 compose 文件不同）、`backup.load_status`、`backup_status.*`、`backup_sets.*`、`admin.take_snapshot`、`admin.compare_snapshots`、`admin.RESTORE_EXPECTATIONS`、`admin._manifest_image_id`（G2）、`admin.Bench`（以 `kind` 之外的方式指定服务：本模块构造 `Bench` 后覆盖 `service='backend'`、`root`、`resolved` 为 drill 专用的 `resolved`）。
- Produces:
  - `DRILL_PROJECT = 'dsherp-restore'`、`DRILL_COMPOSE = 'infra/compose.restore.yml'`、`FETCHED = '/home/frappe/fetched'`、`FAILED_KEEP_DAYS = 14`
  - `class DrillStack`：`__init__(resolved, root, runner, image_tag, secrets_dir)`；`up()`（`compose -p dsherp-restore -f … up -d db redis-cache redis-queue backend`，然后轮询 `compose ps --format json` 直到四个 `healthy`，上限 300s）；`fetch(side, snapshot_id, set_id)`（`compose run --rm -T restore-fetch-<side> restore <snapshot_id> --target /fetched/<side>`）；`bench()` 返回指向本栈 backend 的 `admin.Bench`；`down(volumes: bool)`；`volumes_exist() -> bool`（`docker volume ls -q --filter name=dsherp-restore_`）。
  - `DECRYPT_CHECK`：容器脚本（无三引号）：取 `__Auth` 最多 20 行 `(doctype, name, fieldname)`，逐个 `frappe.utils.password.get_decrypted_password(doctype, name, fieldname, raise_exception=True)`，输出 `DSHERP_DECRYPT {"checked": n, "failed": [...]}`。
  - `restore_drill(resolved, sites=None, *, root=ROOT, runner=subprocess.run, bench_factory=None, clock=time.time, discard_failed=False) -> dict`（`sites=None` = 平台 + 全部租户）；退出码约定同前。
  - `_carried_keys(config_backup) -> dict`：只取 `encryption_key`（设计 §4.5 第 3 步）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_restore_drill.py
"""A restore drill proves the newest complete off-site set restores, matches the snapshot
taken in its window, and decrypts — inside a stack that cannot reach anything."""
import json

import pytest

from dsherp import admin, backup, backup_status, restore_drill
from tests.test_admin_cli import RELEASE, SAME, _tenant_row
from tests.test_backup_cli import CONFIGURED


class DrillRunner:
    """docker/compose for the drill stack: up, health, fetch, volume listing."""

    def __init__(self, healthy=True, volumes=False):
        self.calls = []
        self.healthy = healthy
        self.volumes = volumes

    def __call__(self, command, **kwargs):
        text = " ".join(command)
        self.calls.append(text)
        out = ""
        if "ps --format json" in text:
            out = "\n".join(json.dumps({"Service": s, "Health": "healthy" if self.healthy else "starting"}) for s in ("db", "redis-cache", "redis-queue", "backend"))
        if text.startswith("docker volume ls"):
            out = "dsherp-restore_restore-db\n" if self.volumes else ""
        if "down" in text and "-v" in text:
            self.volumes = False
        if "up -d" in text:
            self.volumes = True
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


class DrillBench:
    """The drill stack's backend: answers sha256sum, cat of set/pair, new-site/restore verbs, snapshots, decrypt check."""

    def __init__(self, snapshot, decrypt_failed=(), pair_ok=True):
        self.verbs, self.stdin, self.snapshot, self.decrypt_failed, self.pair_ok = [], {}, snapshot, list(decrypt_failed), pair_ok
        self.service = "backend"

    def run(self, *arguments, stdin=None, timeout=900, secrets=()):
        self.verbs.append(" ".join(arguments))
        if stdin is not None:
            self.stdin[" ".join(arguments)] = stdin
        text = arguments[2] if arguments[:2] == ("sh", "-c") else ""
        if "cat " in text and text.endswith("set.json"):
            return json.dumps({"format": 1, "set_id": SET_ID, "site": SITE, "kind": "scheduled", "stamp": SET_ID[:15], "image_tag": "v0.4.0",
                               "image_id": "sha256:id-v0.4.0", "frappe_version": "16.31.0", "window": {"started": "x", "finished": "y"},
                               "pieces": {"database.sql.gz": {"sha256": "a" * 64, "bytes": 1}, "files.tar": {"sha256": "b" * 64, "bytes": 1},
                                          "private-files.tar": {"sha256": "c" * 64, "bytes": 1}}, "snapshot_sha256": "d" * 64})
        if "cat " in text and text.endswith("pair.json"):
            from dsherp import backup_sets
            doc = json.loads(self.run("sh", "-c", "cat set.json"))
            pair = backup_sets.pair_manifest(doc)
            if not self.pair_ok:
                pair["set_sha256"] = "0" * 64
            return json.dumps(pair)
        if "cat " in text and text.endswith("site_config_backup.json"):
            return json.dumps({"db_password": "secret-db", "encryption_key": "FERNETKEY==", "host_name": "https://old", "dsherp_agent_sources": ["10.0.0.0/8"]})
        if "sha256sum" in text:
            names = [w for w in text.split() if w.endswith((".sql.gz", ".tar"))]
            digest = {"database.sql.gz": "a", "files.tar": "b", "private-files.tar": "c"}
            return "".join(f"{digest[n.rsplit('/', 1)[-1]] * 64}  {n}\n" for n in names)
        return ""

    def python(self, site, body, timeout=900):
        if "DSHERP_SNAPSHOT" in body:
            return "DSHERP_SNAPSHOT " + json.dumps(self.snapshot) + "\n"
        if "DSHERP_DECRYPT" in body:
            return "DSHERP_DECRYPT " + json.dumps({"checked": 3, "failed": self.decrypt_failed}) + "\n"
        if "update_site_config" in body:
            self.verbs.append("python update_site_config " + site)
            return "[\"encryption_key\"]\n"
        return ""


SITE = "acme.tenant.example.com"
SET_ID = "20260906_020007-acme_tenant_example_com-k3f9qx"


def _complete_set(host):
    admin.ensure_secrets(RELEASE); _tenant_row()
    status = backup_status.empty()
    backup_status.record_set(status, {"set_id": SET_ID, "site": SITE, "kind": "scheduled", "stamp": SET_ID[:15]}, "complete",
                             data_snapshot="d2026090", secrets_snapshot="s2026090")
    older = "20260905_020007-acme_tenant_example_com-000000"
    backup_status.record_set(status, {"set_id": older, "site": SITE, "kind": "scheduled", "stamp": older[:15]}, "complete",
                             data_snapshot="dold", secrets_snapshot="sold")
    pending = "20260906_140007-acme_tenant_example_com-111111"
    backup_status.record_set(status, {"set_id": pending, "site": SITE, "kind": "scheduled", "stamp": pending[:15]}, "data_uploaded", data_snapshot="dnew")
    backup_status.save(backup.status_path(RELEASE, admin.ROOT), status)
    (host / "infra" / "releases").mkdir(parents=True)
    (host / "infra" / "releases" / "v0.4.0.json").write_text(json.dumps({"tag": "v0.4.0", "images": {
        "registry.example.com/dsherp/dsherp-frappe:v0.4.0": {"id": "sha256:id-v0.4.0"}}}))


def test_the_drill_restores_the_newest_complete_pair_in_the_isolated_stack_verifies_and_removes_it(host):
    _complete_set(host)
    runner = DrillRunner()
    bench = DrillBench(SAME)
    report = restore_drill.restore_drill(CONFIGURED, [SITE], root=host, runner=runner, bench_factory=lambda **kw: bench,
                                         clock=lambda: 1_757_200_000.0)
    assert report["ok"] is True and report["sites"][SITE]["set_id"] == SET_ID, "the pending newer set is not chosen"
    calls = runner.calls
    up = [c for c in calls if "up -d" in c][0]
    assert "-p dsherp-restore" in up and "compose.restore.yml" in up and "DSHERP_IMAGE_TAG" not in up
    assert any("restore-fetch-data restore d2026090 --target /fetched/data" in c for c in calls)
    assert any("restore-fetch-secrets restore s2026090 --target /fetched/secrets" in c for c in calls)
    assert calls.index([c for c in calls if "restore-fetch-data" in c][0]) > calls.index(up)
    verbs = bench.verbs
    new_site = [v for v in verbs if v.startswith("bench new-site")][0]
    assert "--db-root-password" not in new_site and "--admin-password" not in new_site, "no secret on argv"
    assert any("common_site_config.json" in v for v in verbs) and any("root_password" in s for s in bench.stdin.values())
    restore = [v for v in verbs if " restore " in v][0]
    assert "--force" in restore and "--with-public-files" in restore and "--with-private-files" in restore and "--db-root-password" not in restore
    assert not any("set-config" in v and "encryption_key" in v for v in verbs), "the key goes through stdin, not argv"
    assert any(v == f"python update_site_config {SITE}" for v in verbs)
    assert not any("migrate" in v for v in verbs)
    assert [c for c in calls if "down" in c][-1].endswith("down -v --remove-orphans")
    status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
    assert status["sites"][SITE]["verified"]["last_success"] == {"at": "2026-09-06T23:06:40Z", "first_at": "2026-09-06T23:06:40Z",
                                                                "set_id": SET_ID, "image_tag": "v0.4.0"}
    assert status["sets"][SET_ID]["state"] == "verified" and status["runs"]["drill"]["last_success"]


def test_a_mismatching_pair_or_digest_or_snapshot_or_decrypt_failure_fails_the_drill_and_keeps_the_stack_for_inspection(host):
    _complete_set(host)
    from tests.test_admin_cli import DRIFTED
    for bench in (DrillBench(SAME, pair_ok=False), DrillBench(DRIFTED), DrillBench(SAME, decrypt_failed=[["OAuth Client", "x", "client_secret"]])):
        runner = DrillRunner()
        report = restore_drill.restore_drill(CONFIGURED, [SITE], root=host, runner=runner, bench_factory=lambda **kw: bench,
                                             clock=lambda: 1_757_200_000.0)
        assert report["ok"] is False and report["sites"][SITE]["error"]
        assert [c for c in runner.calls if " down" in c][-1].endswith("down --remove-orphans"), "volumes kept for inspection"
        bundle = admin.runtime_dir(RELEASE) / "backups" / "drills" / report["drill_id"]
        assert (bundle / "report.json").exists()
        status = backup_status.load(backup.status_path(RELEASE, admin.ROOT))
        assert status["sites"][SITE]["verified"]["last_attempt"]["ok"] is False and status["sites"][SITE]["verified"]["last_success"] is None


def test_a_leftover_failed_drill_blocks_the_next_one_until_discarded_or_expired(host):
    _complete_set(host)
    runner = DrillRunner(volumes=True)
    with pytest.raises(admin.Fault, match="上一次演练"):
        restore_drill.restore_drill(CONFIGURED, [SITE], root=host, runner=runner, bench_factory=lambda **kw: DrillBench(SAME))
    report = restore_drill.restore_drill(CONFIGURED, [SITE], root=host, runner=runner, bench_factory=lambda **kw: DrillBench(SAME),
                                         discard_failed=True, clock=lambda: 1_757_200_000.0)
    assert report["ok"] and runner.calls[0].endswith("down -v --remove-orphans")


def test_the_drill_runs_the_set_s_own_image_tag_and_refuses_when_that_build_is_not_on_record(host):
    _complete_set(host)
    (host / "infra" / "releases" / "v0.4.0.json").unlink()
    with pytest.raises(admin.Fault, match="v0.4.0 的发布清单"):
        restore_drill.restore_drill(CONFIGURED, [SITE], root=host, runner=DrillRunner(), bench_factory=lambda **kw: DrillBench(SAME))
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_restore_drill.py -q`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# dsherp/restore_drill.py
"""Restore drills (design §4.5): the newest complete off-site set of each Site is restored
into an isolated stack running the image the set recorded, compared with the snapshot
taken in its window, sampled for decryptability, and the stack is removed — exactly and
only the stack this drill created."""
import json
import secrets as tokens
import subprocess
import time
from pathlib import Path

from dsherp import admin, backup, backup_sets, backup_status, deploy_env
from dsherp.admin import Fault, ROOT, SITES

DRILL_PROJECT = 'dsherp-restore'
DRILL_COMPOSE = 'infra/compose.restore.yml'
FETCHED = '/home/frappe/fetched'
FAILED_KEEP_DAYS = 14
HEALTH_WAIT_SECONDS = 300
CARRIED_KEYS = ('encryption_key',)
DECRYPT_CHECK = ("rows=frappe.db.sql('select doctype,name,fieldname from `__Auth` where encrypted=1 limit 20')\n"
                 "from frappe.utils.password import get_decrypted_password\n"
                 "failed=[]\n"
                 "for doctype,name,fieldname in rows:\n"
                 "    try:get_decrypted_password(doctype,name,fieldname,raise_exception=True)\n"
                 "    except Exception:failed.append([doctype,name,fieldname])\n"
                 "print('DSHERP_DECRYPT '+json.dumps({'checked':len(rows),'failed':failed}))")


class DrillStack:
    def __init__(self, resolved, root, runner, image_tag, secrets_dir):
        self.resolved, self.root, self.runner, self.image_tag, self.secrets_dir = resolved, Path(root), runner, image_tag, Path(secrets_dir)

    def _compose(self, *arguments):
        command = ['docker', 'compose', '-p', DRILL_PROJECT]
        env_file = deploy_env.env_file(self.resolved['env'], self.root)
        if env_file.exists():
            command += ['--env-file', str(env_file)]
        return command + ['-f', str(self.root / DRILL_COMPOSE), *arguments]

    def _run(self, command, timeout=600, stdin=None):
        import os
        env = {**os.environ, 'DSHERP_IMAGE_TAG': self.image_tag, 'DSHERP_SECRETS_DIR': str(self.secrets_dir)}
        io = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
        result = self.runner(command, text=True, capture_output=True, timeout=timeout, env=env, **io)
        if result.returncode:
            raise Fault('恢复栈命令失败：' + ' '.join(command[-4:]) + '\n' + '\n'.join((result.stderr or result.stdout or '').splitlines()[-6:]))
        return result.stdout

    def volumes_exist(self):
        out = self.runner(['docker', 'volume', 'ls', '-q', '--filter', f'name={DRILL_PROJECT}_'], text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        return bool((out.stdout or '').strip())

    def up(self, clock=time.time, sleep=time.sleep):
        self._run(self._compose('up', '-d', 'db', 'redis-cache', 'redis-queue', 'backend'), timeout=900)
        deadline = clock() + HEALTH_WAIT_SECONDS
        while True:
            rows = [json.loads(line) for line in self._run(self._compose('ps', '--format', 'json'), timeout=60).splitlines() if line.strip()]
            healthy = {row.get('Service') for row in rows if row.get('Health') == 'healthy'}
            if {'db', 'redis-cache', 'redis-queue', 'backend'} <= healthy:
                return
            if clock() >= deadline:
                raise Fault('恢复栈 300 秒内没有全部健康：' + ', '.join(sorted(healthy)))
            sleep(5)

    def fetch(self, side, snapshot_id):
        self._run(self._compose('run', '--rm', '-T', f'restore-fetch-{side}', 'restore', snapshot_id, '--target', f'/fetched/{side}'), timeout=3600)

    def bench(self):
        bench = admin.Bench(self.resolved, 'tenant', root=self.root, runner=self.runner)
        bench.service = 'backend'
        bench._compose = lambda *arguments: self._compose(*arguments)
        return bench

    def down(self, volumes):
        arguments = ['down', '-v', '--remove-orphans'] if volumes else ['down', '--remove-orphans']
        self._run(self._compose(*arguments), timeout=600)


def _newest_complete(status, site):
    rows = [dict(set_id=set_id, **row) for set_id, row in status['sets'].items() if row['site'] == site and row['state'] in ('complete', 'verified')]
    if not rows:
        raise Fault(f'站点 {site} 没有任何完整配对的异地备份集，无法演练')
    return max(rows, key=lambda row: row['stamp'])


def _read_json(bench, path):
    return json.loads(bench.run('sh', '-c', f'cat {path}', timeout=60))


def _verify_fetched(bench, site, set_id):
    # The path restic restores keeps the original absolute layout under --target:
    # /fetched/data/backups/<bench>/sets/<site>/<set_id>/…; find it rather than assume the bench name.
    base = bench.run('sh', '-c', f'ls -d {FETCHED}/data/backups/*/sets/{site}/{set_id}', timeout=60).strip().splitlines()[-1]
    secret_base = bench.run('sh', '-c', f'ls -d {FETCHED}/secrets/backups/*/{site}/{set_id}', timeout=60).strip().splitlines()[-1]
    set_doc = _read_json(bench, f'{base}/set.json')
    pair = _read_json(bench, f'{secret_base}/pair.json')
    if set_doc['set_id'] != set_id or not backup_sets.pair_matches(set_doc, pair):
        raise Fault(f'备份集 {set_id} 的数据侧与密钥侧不配对（pair.json 与 set.json 不一致）')
    out = bench.run('sh', '-c', 'sha256sum ' + ' '.join(f'{base}/{p}' for p in backup_sets.DATA_PIECES), timeout=1800)
    digests = {line.split()[-1].rsplit('/', 1)[-1]: line.split()[0] for line in out.splitlines() if line.strip()}
    for piece in backup_sets.DATA_PIECES:
        if digests.get(piece) != set_doc['pieces'][piece]['sha256']:
            raise Fault(f'备份集 {set_id} 取回后 {piece} 摘要不符')
    config = _read_json(bench, f'{secret_base}/{backup_sets.CONFIG_PIECE}')
    snapshot = _read_json(bench, f'{base}/snapshot.json')
    return set_doc, base, config, snapshot


def _restore_into(bench, site, base, config, root_password, admin_password):
    common = {'db_host': 'db', 'redis_cache': 'redis://redis-cache:6379', 'redis_queue': 'redis://redis-queue:6379',
              'root_password': root_password, 'admin_password': admin_password, 'mute_emails': 1, 'pause_scheduler': 1}
    bench.run('sh', '-c', f'umask 077 && cat > {SITES}/common_site_config.json', stdin=json.dumps(common), timeout=60,
              secrets=(root_password, admin_password))
    bench.run('bench', 'new-site', site, '--db-host', 'db', '--db-root-username', 'root', '--mariadb-user-host-login-scope', '%',
              '--no-mariadb-socket', timeout=1800, secrets=(root_password, admin_password))
    bench.run('bench', '--site', site, 'restore', f'{base}/database.sql.gz', '--with-public-files', f'{base}/files.tar',
              '--with-private-files', f'{base}/private-files.tar', '--db-root-username', 'root', '--force', timeout=3600,
              secrets=(root_password, admin_password))
    carried = {key: config[key] for key in CARRIED_KEYS if key in config}
    admin.ensure_site_config(bench, site, {**carried, 'mute_emails': 1, 'pause_scheduler': 1})  # stdin script, not argv


def _decrypt_check(bench, site):
    line = admin._last_line(bench.python(site, DECRYPT_CHECK, timeout=300))
    if not line.startswith('DSHERP_DECRYPT '):
        raise Fault(f'{site} 的解密抽样脚本没有输出结果')
    result = json.loads(line[len('DSHERP_DECRYPT '):])
    if result['failed']:
        raise Fault(f'{site} 恢复后有 {len(result["failed"])} 个密码字段无法解密（如 {result["failed"][0]}）')
    return result


def restore_drill(resolved, sites=None, *, root=ROOT, runner=subprocess.run, bench_factory=None, clock=time.time,
                  discard_failed=False, sleep=time.sleep):
    backup._require_repositories(resolved)
    status = backup.load_status(resolved, root)
    path = backup.status_path(resolved, root)
    targets = sites or [row['site'] for row in admin.load_tenants(resolved, root)] + [resolved['platform_site']]
    drill_id = time.strftime('%Y%m%d_%H%M%S', time.gmtime(clock())) + '-' + tokens.token_hex(3)
    report = {'ok': True, 'drill_id': drill_id, 'sites': {}}
    chosen = {site: _newest_complete(status, site) for site in targets}
    image_tag = resolved['image_tag']  # set.json's image_tag is read after fetch; the stack must already run it
    with backup.operations_lock(resolved, root, 'restore-drill'):
        secrets_dir = admin.runtime_dir(resolved, root) / 'backups' / 'drills' / drill_id / 'secrets'
        root_password, admin_password = tokens.token_urlsafe(24), tokens.token_urlsafe(24)
        admin._write_private(secrets_dir / 'db_root_password', root_password + '\n')
        for name in ('backup_repository_password', 'backup_secrets_repository_password', 'backup_storage_credentials',
                     'backup_secrets_storage_credentials', 'backup_storage_ca.pem'):
            source = admin.secrets_dir(resolved, root) / name
            if source.exists():
                admin._write_private(secrets_dir / name, source.read_text())
        stack = DrillStack(resolved, root, runner, image_tag, secrets_dir)
        if stack.volumes_exist():
            if not discard_failed:
                raise Fault('上一次演练留下的恢复栈仍在（保留 14 日供排查）；先看 backups/drills/ 下的诊断包，'
                            '确认后用 --discard-failed 清掉再演练')
            stack.down(volumes=True)
        admin._manifest_image_id(resolved, root, image_tag, resolved['frappe_image'])  # the build must be on record (G2 rule)
        stack.up(clock=clock, sleep=sleep)
        bench = bench_factory(stack=stack) if bench_factory else stack.bench()
        failed = False
        for site, row in chosen.items():
            started = time.monotonic()
            try:
                stack.fetch('data', row['data_snapshot']); stack.fetch('secrets', row['secrets_snapshot'])
                set_doc, base, config, expected = _verify_fetched(bench, site, row['set_id'])
                if set_doc['image_tag'] != image_tag:
                    raise Fault(f'备份集记录的镜像 tag 是 {set_doc["image_tag"]}，恢复栈运行的是 {image_tag}；按备份记录的版本恢复，升级另走 release')
                _restore_into(bench, site, base, config, root_password, admin_password)
                restored = admin.take_snapshot(resolved, site, bench=bench, hash_columns=admin._hash_columns_of(expected))
                comparison = admin.compare_snapshots(expected, restored, admin.RESTORE_EXPECTATIONS)
                if not comparison['clean']:
                    raise Fault(f'{site} 恢复后与备份窗口快照有 {comparison["summary"]["undeclared"]} 处未声明差异')
                decrypt = _decrypt_check(bench, site)
                report['sites'][site] = {'set_id': row['set_id'], 'seconds': round(time.monotonic() - started, 1),
                                         'comparison': comparison['summary'], 'decrypt_checked': decrypt['checked']}
                backup_status.record_site(status, site, 'verified', at=backup_status.now_iso(clock), ok=True,
                                          set_id=row['set_id'], image_tag=image_tag)
                backup_status.record_set(status, {**status['sets'][row['set_id']], 'set_id': row['set_id']}, 'verified')
            except Exception as error:
                failed = True
                report['sites'][site] = {'set_id': row['set_id'], 'error': str(error)[:800]}
                backup_status.record_site(status, site, 'verified', at=backup_status.now_iso(clock), ok=False, error=str(error)[:500])
            backup_status.save(path, status)
        report['ok'] = not failed
        backup_status.record_run(status, 'drill', at=backup_status.now_iso(clock), ok=report['ok'],
                                 error=None if report['ok'] else '；'.join(f'{s}: {r["error"]}' for s, r in report['sites'].items() if 'error' in r))
        backup_status.save(path, status)
        bundle = admin.runtime_dir(resolved, root) / 'backups' / 'drills' / drill_id
        admin._write_json(bundle / 'report.json', report)
        stack.down(volumes=report['ok'])
        if report['ok']:
            import shutil
            shutil.rmtree(secrets_dir, ignore_errors=True)
    return report
```

（实现细节：`image_tag` 取 `resolved['image_tag']` 并在取回后与 `set.json` 核对——若两者不同就失败并提示；`--no-mariadb-socket` 按 Frappe 16 的 `new-site` 选项存在与否决定是否保留。`_verify_fetched` 里第一行的占位表达式删掉。演练失败保留栈：`down --remove-orphans` 不带 `-v`；`FAILED_KEEP_DAYS` 用于 `restore_drill` 开头：若 `volumes_exist()` 且诊断包 `report.json` 的 mtime 早于 14 日，则视同 `discard_failed`。）CLI：`restore-drill [--all | site …] [--discard-failed]`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_restore_drill.py -q`
Expected: `4 passed`

- [ ] **Step 5: 提交**

```bash
git add dsherp/restore_drill.py dsherp/admin.py tests/test_restore_drill.py
git commit -m "feat: restore-drill——最新完整配对集在隔离栈按记录的镜像 tag 恢复、与窗口快照比对、解密抽样，成功 down -v、失败保留栈与诊断包"
```

### Task 12: `dsherp-admin restore-site`（G3 异机恢复路径）

> **契约修订（第二次审阅）**：按设计 §4.5（2.1）的冷启动契约实现与测试：① 目标站已存在即拒绝；② 先读回核对元数据与制品（两侧 dump 配对、各件/快照/config 摘要），再核对本栈运行镜像 tag/id == 集记录；③ 取回到本栈备份卷的临时目录，凭据只在 `Bench.script` 里使用，**不**把 root 口令写进生产共享的 `common_site_config.json`；④ provision → 进程内恢复 → 注入 `encryption_key` → 快照比对 → 再 provision → 解密抽样；⑤ 全部通过才解除维护并写 `current.json`，失败保持维护。测试覆盖每个拒绝点与成功路径；阶段 3 末在第二个本机栈真实跑通。

**Files:**
- Modify: `dsherp/restore_drill.py`（追加）、`dsherp/admin.py`（CLI）
- Test: `tests/test_restore_drill.py`（追加）

**Interfaces:**
- Produces: `restore_site(resolved, site, *, set_id=None, root, runner, bench_factory, clock) -> dict`：在**当前生产栈**（新主机按 runbook §1–§6 拉起、`prod.env` 的 tag = 集记录的 tag）上：`_require_repositories` → 用 `backup.restic(resolved, side, ['snapshots','--json','--tag',f'site={site}'])` 列两侧快照，取最新两侧都有的 `set=` 标签（或指定 `set_id`），`pair` 校验在取回后做 → 取回到本栈的备份卷临时目录（`backup-sync-*` 服务只读挂载，所以取回用 `compose run --rm -T -v <bench>-backups:/restore backup-sync-data restore <id> --target /restore/incoming` 这类可写覆盖挂载：为此在 prod compose 的两个服务上**不**加可写卷，而是由 `restore_site` 用 `run -v` 覆盖挂载同一卷为可写）→ `provision-platform`/`provision-tenant` 幂等建站（复用 `admin.provision_*`）→ `bench restore --force`（root 口令走 `common_site_config` 的 `root_password`？生产 bench 的 `common_site_config.json` 不能长期含 root 口令：改为临时写入、恢复完成后删除该键——用 `ensure_site_config` 的同款 stdin 脚本对 common config 操作）→ 注入 `encryption_key` → 快照比对 → 再跑一次 provision（重算主机相关配置）→ 报告（含用时，供 RTO 记录）。
- 本任务的测试用 `StagingBench` + `FakeRestic`（扩展 `restore` 动词），断言：选择的是最新完整配对；`provision` 前后各一次；`restore` argv 无口令；common config 里的 `root_password` 在结束时被删除；`encryption_key` 走 stdin；比对不干净时站点保持维护模式并 `Fault`。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_restore_site_takes_the_newest_paired_set_provisions_restores_reinjects_the_key_and_reprovisions(host, monkeypatch):
    from tests.test_backup_cli import FakeRestic, StagingBench
    _complete_set(host)
    restic = FakeRestic()
    restic.snapshots["data"] = {SET_ID: "d2026090", "20260906_140007-acme_tenant_example_com-111111": "dnew"}
    restic.snapshots["secrets"] = {SET_ID: "s2026090"}
    bench = DrillBench(SAME)
    provisioned = []
    monkeypatch.setattr(admin, "provision_tenant", lambda resolved, slug, **kw: provisioned.append(slug) or {"site": SITE})
    report = restore_drill.restore_site(CONFIGURED, SITE, root=host, runner=restic, bench_factory=lambda **kw: bench, clock=lambda: 1_757_200_000.0)
    assert report["set_id"] == SET_ID and report["ok"] and provisioned == ["acme", "acme"]
    assert any("backup-sync-data restore d2026090 --target" in c for c in restic.calls)
    assert any("backup-sync-secrets restore s2026090 --target" in c for c in restic.calls)
    restore = [v for v in bench.verbs if " restore " in v][0]
    assert "--db-root-password" not in restore and "--force" in restore
    assert any("root_password" in s for s in bench.stdin.values())
    assert bench.verbs[-1].startswith("python update_site_config") or any("root_password" in v and "del" in v for v in bench.verbs)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_restore_drill.py -q -k restore_site`
Expected: AttributeError

- [ ] **Step 3: 实现**

在 `restore_drill.py` 追加 `restore_site(...)`：按上面接口描述实现，取回路径 `/restore/incoming/<side>`（覆盖挂载 `-v {project}_{bench}-backups:/restore`），校验与 `_verify_fetched` 同款（把该函数的根路径参数化：`_verify_fetched(bench, site, set_id, data_root, secret_root)`），`_restore_into` 复用但 `new-site` 由 `provision_*` 完成、`common_site_config` 只临时加 `root_password`（结束时用 stdin 脚本 `frappe.installer.update_site_config('root_password', None, site_config_path=<common>)` 删除）；比对不干净 → 站点保持维护、`Fault`。CLI：`restore-site <site> [--set SET_ID]`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_restore_drill.py tests/test_backup_cli.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/restore_drill.py dsherp/admin.py tests/test_restore_drill.py
git commit -m "feat: restore-site——在新栈上从最新完整配对的异地集恢复一个站：建站、恢复、注入 encryption_key、比对、重算主机配置"
```

---

## 阶段 4：定时、可见性、worker 停止时的兜底、文档与演练

### Task 13: systemd 单元（两对 timer/service + `OnFailure` 模板）与 `notify-failure`

> **契约修订（第二次审阅）**：`OnCalendar=*-*-* 02,14:00:00 Asia/Shanghai`（systemd 小时列表语法 + 显式时区）；单元测试只断言表达式与关键指令（`Type`、`ExecStart`、`User`、`OnFailure`、`Persistent`、沙箱行），阶段 4 末在带 systemd 的 Linux 容器里用 `systemd-analyze calendar` 核对下一次触发时刻并记入证据。`notify_failure` 直接 `httpx.post(...).raise_for_status()`，成功才返回 `posted`；失败返回 `failed` + 错误类，退出码 1；journal 行始终打印；不使用会吞异常的 `Notifier`。测试用抛 `ConnectionError` 的假客户端证明不伪报。计划里的 `notify_failure` 代码块据此重写。

**Files:**
- Modify: `infra/render_worker_units.py`（新模板与 `render_backup_units`、`main()` 输出）
- Modify: `dsherp/backup.py`（`notify_failure`）、`dsherp/admin.py`（CLI `notify-failure`）
- Test: `tests/test_deployment_contract.py`（追加）、`tests/test_backup_cli.py`（追加）

**Interfaces:**
- Produces: `render_backup_units(root, *, user, group='dsherp', target_dir=None) -> dict[str, Path]`，键 `backup.service`、`backup.timer`、`drill.service`、`drill.timer`、`failure.service`，文件名 `dsherp-backup.service`、`dsherp-backup.timer`、`dsherp-backup-drill.service`、`dsherp-backup-drill.timer`、`dsherp-backup-failure@.service`。
- `backup.notify_failure(resolved, unit, *, root, profile_path=None, client=None) -> dict`：读 worker profile（`<root>/.runtime/context-worker-sites.json` 或 `--profile`）的 `alert_webhook`；用 `alerts.Notifier(webhook=…, cooldown=0)` 发出 `Alert('backup_unit_failed','critical',f'systemd 单元 {unit} 失败')`；同时把 JSON 行打印到 stdout（journal）。

- [ ] **Step 1: 写失败测试**

`tests/test_deployment_contract.py` 追加：

```python
def test_the_backup_timers_run_the_cli_twice_a_day_and_weekly_and_a_failure_notifies_without_the_worker(tmp_path):
    from infra.render_worker_units import render_backup_units
    units = render_backup_units(ROOT, user="dsherp", group="dsherp", target_dir=tmp_path)
    backup_service = units["backup.service"].read_text()
    assert "Type=oneshot" in backup_service
    assert f"ExecStart={ROOT}/bin/dsherp-admin backup --sync" in backup_service
    assert "User=dsherp" in backup_service and "SupplementaryGroups=docker" in backup_service
    assert "Environment=DSHERP_ENV=prod" in backup_service and "OnFailure=dsherp-backup-failure@%n.service" in backup_service
    assert "ProtectSystem=strict" in backup_service and f"ReadWritePaths={ROOT}/.runtime" in backup_service
    assert re.search(r"^TimeoutStartSec=3h$", backup_service, re.MULTILINE)
    timer = units["backup.timer"].read_text()
    assert re.search(r"^OnCalendar=\*-\*-\* 02:00,14:00:00$", timer, re.MULTILINE) and "Persistent=true" in timer
    assert re.search(r"^RandomizedDelaySec=5min$", timer, re.MULTILINE) and "WantedBy=timers.target" in timer
    drill = units["drill.service"].read_text()
    assert f"ExecStart={ROOT}/bin/dsherp-admin restore-drill --all" in drill and re.search(r"^TimeoutStartSec=6h$", drill, re.MULTILINE)
    assert re.search(r"^OnCalendar=Sun \*-\*-\* 04:00:00$", units["drill.timer"].read_text(), re.MULTILINE)
    failure = units["failure.service"].read_text()
    assert units["failure.service"].name == "dsherp-backup-failure@.service"
    assert f"ExecStart={ROOT}/bin/dsherp-admin notify-failure %i" in failure and "User=dsherp" in failure
    for bad in ("bad user", ""):
        with pytest.raises(ValueError):
            render_backup_units(ROOT, user=bad, target_dir=tmp_path)
```

`tests/test_backup_cli.py` 追加：

```python
def test_notify_failure_posts_to_the_alert_webhook_and_prints_a_journal_line_without_the_worker(host, capsys, monkeypatch):
    profile = host / "profile.json"
    profile.write_text(json.dumps({"alert_webhook": "https://hooks.example.com/x", "sites": []}))
    posted = []

    class Client:
        def post(self, url, json=None, timeout=None):
            posted.append((url, json)); return type("R", (), {"raise_for_status": lambda self: None})()
    result = backup.notify_failure(RELEASE, "dsherp-backup.service", profile_path=profile, client=Client())
    assert posted and posted[0][0] == "https://hooks.example.com/x" and posted[0][1]["key"] == "backup_unit_failed"
    assert "dsherp-backup.service" in posted[0][1]["message"] and result["webhook"] == "posted"
    out = capsys.readouterr().out
    assert '"key": "backup_unit_failed"' in out or "backup_unit_failed" in out
    profile.write_text(json.dumps({"sites": []}))
    assert backup.notify_failure(RELEASE, "dsherp-backup.service", profile_path=profile, client=Client())["webhook"] == "not configured"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py tests/test_backup_cli.py -q -k "timers or notify_failure"`
Expected: ImportError / AttributeError

- [ ] **Step 3: 实现**

`infra/render_worker_units.py` 新增模板（沙箱段从 `UNIT` 抽成常量 `SANDBOX` 复用）：

```python
BACKUP_SERVICE = """[Unit]
Description=dsherp backup: stable-window sets for every Site, then off-site sync
Documentation=file://{root}/docs/engineering/deployment-runbook.md
Requires=docker.service
After=docker.service network-online.target
OnFailure=dsherp-backup-failure@%n.service

[Service]
Type=oneshot
User={user}
Group={group}
SupplementaryGroups=docker
Environment=DSHERP_ENV=prod
WorkingDirectory={root}
ExecStart={root}/bin/dsherp-admin backup --sync
TimeoutStartSec=3h
StandardError=journal
SyslogIdentifier=dsherp-backup
{sandbox}
"""
BACKUP_TIMER = """[Unit]
Description=dsherp backup every 12 hours

[Timer]
OnCalendar=*-*-* 02:00,14:00:00
Persistent=true
RandomizedDelaySec=5min
Unit=dsherp-backup.service

[Install]
WantedBy=timers.target
"""
DRILL_SERVICE = BACKUP_SERVICE.replace('backup --sync', 'restore-drill --all').replace('TimeoutStartSec=3h', 'TimeoutStartSec=6h') \
    .replace('Description=dsherp backup: stable-window sets for every Site, then off-site sync', 'Description=dsherp weekly restore drill in the isolated stack') \
    .replace('SyslogIdentifier=dsherp-backup', 'SyslogIdentifier=dsherp-backup-drill')
DRILL_TIMER = BACKUP_TIMER.replace('every 12 hours', 'restore drill weekly').replace('*-*-* 02:00,14:00:00', 'Sun *-*-* 04:00:00') \
    .replace('RandomizedDelaySec=5min', 'RandomizedDelaySec=30min').replace('dsherp-backup.service', 'dsherp-backup-drill.service')
FAILURE_SERVICE = """[Unit]
Description=dsherp: report a failed backup unit (%i) even when the worker is down

[Service]
Type=oneshot
User={user}
Group={group}
Environment=DSHERP_ENV=prod
WorkingDirectory={root}
ExecStart={root}/bin/dsherp-admin notify-failure %i
StandardError=journal
SyslogIdentifier=dsherp-backup-failure
"""


def render_backup_units(root=ROOT, *, user, group='dsherp', target_dir=None):
    root = Path(root).resolve()
    for value in (user, group):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise ValueError('Invalid service account: ' + repr(value))
    directory = Path(target_dir) if target_dir else root / '.runtime'
    sandbox = ('ProtectSystem=strict\nProtectHome=read-only\nPrivateTmp=true\nNoNewPrivileges=true\n'
               f'ReadWritePaths={root}/.runtime')
    names = {'backup.service': ('dsherp-backup.service', BACKUP_SERVICE), 'backup.timer': ('dsherp-backup.timer', BACKUP_TIMER),
             'drill.service': ('dsherp-backup-drill.service', DRILL_SERVICE), 'drill.timer': ('dsherp-backup-drill.timer', DRILL_TIMER),
             'failure.service': ('dsherp-backup-failure@.service', FAILURE_SERVICE)}
    return {key: _write(directory / name, text.format(root=root, user=user, group=group, sandbox=sandbox))
            for key, (name, text) in names.items()}
```

`main()` 里在打印 worker/firewall 单元后 `for path in render_backup_units(...).values(): print(path)`（`--target` 给了就用它的父目录）。`backup.notify_failure`：

```python
def notify_failure(resolved, unit, *, root=ROOT, profile_path=None, client=None):
    from dsherp import alerts
    profile = Path(profile_path) if profile_path else Path(root) / '.runtime' / 'context-worker-sites.json'
    webhook = None
    try:
        webhook = json.loads(profile.read_text()).get('alert_webhook')
    except (OSError, ValueError):
        pass
    alert = alerts.Alert('backup_unit_failed', 'critical', f'systemd 单元 {unit} 失败；查 journalctl -u {unit} 与 backups/status.json')
    print(json.dumps({'event': 'alert', **alert._asdict()}, ensure_ascii=False))
    if not webhook:
        return {'unit': unit, 'webhook': 'not configured'}
    alerts.Notifier(sink=lambda *a, **k: None, webhook=webhook, cooldown=0, client=client).emit([alert], time.time())
    return {'unit': unit, 'webhook': 'posted'}
```

CLI：`notify_parser = sub.add_parser('notify-failure'); notify_parser.add_argument('unit')` → `_print(backup_module.notify_failure(resolved, arguments.unit))`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_deployment_contract.py tests/test_backup_cli.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add infra/render_worker_units.py dsherp/backup.py dsherp/admin.py tests/test_deployment_contract.py tests/test_backup_cli.py
git commit -m "feat: 备份每 12h、演练每周的 systemd 单元，OnFailure 模板单元不经 worker 直接通知"
```

### Task 14: 状态文件 → gauge/告警（`backup_status.evaluate`）与 worker 接入

> **契约修订（第二次审阅）**：期望站点只来自 `tenants.json` + 平台站；清单读不出来时不回退到 worker profile、不缩小范围计算，只发 `backup_scope_unknown`（critical）并把 `dsherp_backup_sites_expected` 置 -1。状态文件缺失与损坏都发 `backup_status_missing`。测试补这两条。

**Files:**
- Modify: `dsherp/backup_status.py`（追加 `evaluate` 与常量）、`dsherp/context_worker.py`（gauge、读取、`serve_once`/`monitor_ops`）、`dsherp/alerts.py`（无新规则，只复用 `Alert`）
- Test: `tests/test_backup_status.py`（追加）、`tests/test_context_worker.py`（追加）

**Interfaces:**
- Produces: 常量 `RPO_WARNING_HOURS = 20`、`RPO_HOURS = 24`、`RUN_MISSING_HOURS = 13`、`VERIFY_DAYS = 8`；`stamp_age_hours(stamp, now) -> float`；`evaluate(status, expected_sites, now, status_age_seconds=None) -> dict` 返回 `{'gauges': {name: value}, 'alerts': [Alert]}`，gauge 名：`dsherp_backup_sites_expected`、`dsherp_backup_sites_rpo_ok`、`dsherp_backup_offsite_oldest_hours`（-1 = 有站从未完整）、`dsherp_backup_local_oldest_hours`、`dsherp_backup_status_age_seconds`、`dsherp_backup_last_run_ok`、`dsherp_backup_unverified_days_max`；告警键 `backup_status_missing`、`backup_rpo_warning`、`backup_rpo_unmet`、`backup_run_failed`、`restore_unverified`。
- worker：`monitor_backups(runtime_dir, expected_sites, notifier, state, now)`，每 60s 一次（与 `monitor_ops` 同节奏），`serve_once` 里独立 try 块；`expected_sites` = `resolved['platform_site']` + `admin.load_tenants(resolved)` 的 `site`（读 `tenants.json`；读失败视为只含 profile 里的站并记日志）。

- [ ] **Step 1: 写失败测试**

`tests/test_backup_status.py` 追加：

```python
from dsherp.backup_status import evaluate, empty, record_site, record_run, record_set

NOW = 1_757_203_200.0  # 2026-09-07T00:00:00Z


def _healthy(sites=("acme.tenant.example.com", "platform.tenant.example.com"), stamp="20260906_140007", verified_at="2026-09-06T04:30:00Z"):
    status = empty()
    for site in sites:
        set_id = f"{stamp}-{site.replace('.', '_')}-aaaaaa"
        record_site(status, site, "backup", at="2026-09-06T14:01:00Z", ok=True, set_id=set_id, stamp=stamp)
        record_site(status, site, "offsite", at="2026-09-06T14:03:00Z", ok=True, set_id=set_id, stamp=stamp)
        record_site(status, site, "verified", at=verified_at, ok=True, set_id=set_id, image_tag="v0.4.0")
    record_run(status, "backup", at="2026-09-06T14:01:00Z", ok=True); record_run(status, "sync", at="2026-09-06T14:03:00Z", ok=True)
    record_run(status, "check", at="2026-09-06T14:05:00Z", ok=True); record_run(status, "drill", at=verified_at, ok=True)
    return status


def _keys(result):
    return sorted(alert.key for alert in result["alerts"])


def test_a_healthy_status_yields_no_alerts_and_the_ages_as_gauges():
    result = evaluate(_healthy(), ["acme.tenant.example.com", "platform.tenant.example.com"], NOW, status_age_seconds=120)
    assert _keys(result) == []
    g = result["gauges"]
    assert g["dsherp_backup_sites_expected"] == 2 and g["dsherp_backup_sites_rpo_ok"] == 2 and g["dsherp_backup_last_run_ok"] == 1
    assert abs(g["dsherp_backup_offsite_oldest_hours"] - 9.99) < 0.01 and g["dsherp_backup_status_age_seconds"] == 120


def test_rpo_is_judged_from_the_set_stamp_with_a_warning_at_20h_and_unmet_at_24h():
    assert _keys(evaluate(_healthy(stamp="20260906_040100"), ["acme.tenant.example.com"], NOW)) == ["backup_rpo_warning"]   # 19h59m: ok? -> 19.98h no; use 20h boundary
    assert _keys(evaluate(_healthy(stamp="20260906_035959"), ["acme.tenant.example.com"], NOW)) == ["backup_rpo_warning"]  # 20h00m01s
    assert _keys(evaluate(_healthy(stamp="20260906_040001"), ["acme.tenant.example.com"], NOW)) == []                       # 19h59m59s
    assert _keys(evaluate(_healthy(stamp="20260905_235959"), ["acme.tenant.example.com"], NOW)) == ["backup_rpo_unmet"]     # 24h00m01s
    unmet = [a for a in evaluate(_healthy(stamp="20260905_235959"), ["acme.tenant.example.com"], NOW)["alerts"] if a.key == "backup_rpo_unmet"][0]
    assert "acme.tenant.example.com" in unmet.message and unmet.severity == "critical"


def test_expected_sites_come_from_the_caller_and_a_site_without_a_complete_pair_is_unmet():
    status = _healthy(sites=("acme.tenant.example.com",))
    result = evaluate(status, ["acme.tenant.example.com", "new.tenant.example.com"], NOW)
    assert _keys(result) == ["backup_rpo_unmet", "restore_unverified"]
    assert result["gauges"]["dsherp_backup_offsite_oldest_hours"] == -1 and result["gauges"]["dsherp_backup_sites_rpo_ok"] == 1
    assert "new.tenant.example.com" in [a for a in result["alerts"] if a.key == "backup_rpo_unmet"][0].message


def test_a_failed_attempt_after_the_last_success_is_a_warning_and_a_missing_or_stale_status_is_critical():
    status = _healthy()
    record_site(status, "acme.tenant.example.com", "offsite", at="2026-09-06T23:00:00Z", ok=False, error="restic: timeout")
    assert _keys(evaluate(status, ["acme.tenant.example.com"], NOW)) == ["backup_run_failed"]
    assert _keys(evaluate(None, ["acme.tenant.example.com"], NOW)) == ["backup_status_missing"]
    status = _healthy()
    record_run(status, "backup", at="2026-09-06T10:59:00Z", ok=True)   # 13h01m ago: the timer did not fire
    assert "backup_status_missing" in _keys(evaluate(status, ["acme.tenant.example.com"], NOW))


def test_unverified_for_more_than_eight_days_or_never_is_a_warning_with_a_grace_for_new_sites():
    old = _healthy(verified_at="2026-08-29T23:59:00Z")
    assert _keys(evaluate(old, ["acme.tenant.example.com"], NOW)) == ["restore_unverified"]
    fresh = _healthy(verified_at="2026-08-30T00:01:00Z")
    assert _keys(evaluate(fresh, ["acme.tenant.example.com"], NOW)) == []
    status = _healthy(); status["sites"]["acme.tenant.example.com"]["verified"] = {"last_attempt": None, "last_success": None}
    status["sites"]["acme.tenant.example.com"]["backup"]["last_success"]["at"] = "2026-09-06T14:01:00Z"  # first backup yesterday
    assert _keys(evaluate(status, ["acme.tenant.example.com"], NOW)) == []                                  # within the 8-day grace
    status["sites"]["acme.tenant.example.com"]["backup"]["last_success"]["first_at"] = "2026-08-20T00:00:00Z"
    assert _keys(evaluate(status, ["acme.tenant.example.com"], NOW)) == ["restore_unverified"]
```

（`first_at`：`record_site` 在第一次成功时写入 `first_at` 并此后保留——补进 Task 3 的实现与测试。）`tests/test_context_worker.py` 追加：

```python
def test_the_worker_exports_backup_gauges_and_alerts_from_the_status_file_for_every_expected_site(tmp_path, monkeypatch):
    from dsherp import backup_status, context_worker
    emitted = []
    notifier = type("N", (), {"emit": lambda self, alerts, now: emitted.extend(alerts)})()
    context_worker.monitor_backups(tmp_path, ["acme.tenant.example.com"], notifier, {}, now=1_757_203_200.0)
    assert [a.key for a in emitted] == ["backup_status_missing"]
    assert "dsherp_backup_status_age_seconds" in context_worker.REGISTRY.render()
    status = backup_status.empty()
    backup_status.record_site(status, "acme.tenant.example.com", "offsite", at="2026-09-06T23:00:00Z", ok=True,
                              set_id="20260906_225500-acme_tenant_example_com-aaaaaa", stamp="20260906_225500")
    backup_status.save(tmp_path / "backups" / "status.json", status)
    emitted.clear()
    context_worker.monitor_backups(tmp_path, ["acme.tenant.example.com"], notifier, {}, now=1_757_203_200.0)
    assert "backup_status_missing" not in [a.key for a in emitted]
    assert "dsherp_backup_sites_rpo_ok 1" in context_worker.REGISTRY.render()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_backup_status.py tests/test_context_worker.py -q -k "evaluate or rpo or unverified or backup_gauges or missing"`
Expected: ImportError `evaluate` / AttributeError `monitor_backups`

- [ ] **Step 3: 实现**

`backup_status.py` 追加：

```python
import calendar
from dsherp.alerts import Alert

RPO_WARNING_HOURS = 20
RPO_HOURS = 24
RUN_MISSING_HOURS = 13
VERIFY_DAYS = 8
GAUGES = ('dsherp_backup_sites_expected', 'dsherp_backup_sites_rpo_ok', 'dsherp_backup_offsite_oldest_hours',
          'dsherp_backup_local_oldest_hours', 'dsherp_backup_status_age_seconds', 'dsherp_backup_last_run_ok',
          'dsherp_backup_unverified_days_max')


def _epoch(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


def stamp_age_hours(stamp, now):
    return (now - calendar.timegm(time.strptime(stamp, '%Y%m%d_%H%M%S'))) / 3600


def evaluate(status, expected_sites, now, status_age_seconds=None):
    alerts = []
    gauges = {name: 0 for name in GAUGES}
    gauges['dsherp_backup_sites_expected'] = len(expected_sites)
    if status_age_seconds is not None:
        gauges['dsherp_backup_status_age_seconds'] = status_age_seconds
    if status is None:
        alerts.append(Alert('backup_status_missing', 'critical', '备份状态文件缺失或损坏：backups/status.json'))
        gauges['dsherp_backup_offsite_oldest_hours'] = -1
        return {'gauges': gauges, 'alerts': alerts}
    attempt = (status['runs'].get('backup') or {}).get('last_attempt')
    if attempt is None or (now - _epoch(attempt['at'])) / 3600 > RUN_MISSING_HOURS:
        alerts.append(Alert('backup_status_missing', 'critical', f'备份任务超过 {RUN_MISSING_HOURS} 小时没有运行；查 systemctl list-timers dsherp-backup.timer'))
    unmet, warning, unverified, failed = [], [], [], []
    offsite_oldest, local_oldest, unverified_days = 0.0, 0.0, 0.0
    never = False
    for site in expected_sites:
        row = status['sites'].get(site, {})
        complete = (row.get('offsite') or {}).get('last_success')
        if not complete:
            unmet.append(site); never = True
        else:
            age = stamp_age_hours(complete['stamp'], now)
            offsite_oldest = max(offsite_oldest, age)
            if age >= RPO_HOURS:
                unmet.append(site)
            elif age >= RPO_WARNING_HOURS:
                warning.append(site)
        local = (row.get('backup') or {}).get('last_success')
        if local:
            local_oldest = max(local_oldest, stamp_age_hours(local['stamp'], now))
        for phase in ('backup', 'offsite', 'verified'):
            phase_row = row.get(phase) or {}
            last_attempt, last_success = phase_row.get('last_attempt'), phase_row.get('last_success')
            if last_attempt and not last_attempt.get('ok') and not last_attempt.get('deferred') and (
                    not last_success or _epoch(last_attempt['at']) > _epoch(last_success['at'])):
                failed.append(f'{site}/{phase}')
        verified = (row.get('verified') or {}).get('last_success')
        if verified:
            days = (now - _epoch(verified['at'])) / 86400
            unverified_days = max(unverified_days, days)
            if days > VERIFY_DAYS:
                unverified.append(site)
        else:
            first = (local or {}).get('first_at') or (local or {}).get('at')
            if first and (now - _epoch(first)) / 86400 > VERIFY_DAYS:
                unverified.append(site)
    for phase in ('sync', 'check', 'drill'):
        run = status['runs'].get(phase) or {}
        last_attempt, last_success = run.get('last_attempt'), run.get('last_success')
        if last_attempt and not last_attempt.get('ok') and (not last_success or _epoch(last_attempt['at']) > _epoch(last_success['at'])):
            failed.append(phase)
    gauges['dsherp_backup_sites_rpo_ok'] = len(expected_sites) - len(unmet)
    gauges['dsherp_backup_offsite_oldest_hours'] = -1 if never else round(offsite_oldest, 2)
    gauges['dsherp_backup_local_oldest_hours'] = round(local_oldest, 2)
    gauges['dsherp_backup_last_run_ok'] = 1 if attempt and attempt.get('ok') else 0
    gauges['dsherp_backup_unverified_days_max'] = round(unverified_days, 2)
    if unmet:
        alerts.append(Alert('backup_rpo_unmet', 'critical', '24 小时内没有完整异地备份：' + '、'.join(unmet)))
    if warning:
        alerts.append(Alert('backup_rpo_warning', 'warning', '异地备份已超过 20 小时：' + '、'.join(warning)))
    if failed:
        alerts.append(Alert('backup_run_failed', 'warning', '备份链最近一次尝试失败：' + '、'.join(failed)))
    if unverified:
        alerts.append(Alert('restore_unverified', 'warning', '超过 8 日未做恢复验证：' + '、'.join(unverified)))
    return {'gauges': gauges, 'alerts': alerts}
```

`context_worker.py`：在 gauge 定义区为 `GAUGES` 每个名字 `REGISTRY.gauge(name, help)` 存入 `BACKUP_GAUGES` 字典；新增：

```python
def monitor_backups(runtime_dir,expected_sites,notifier,state,now=None):
    if now is None:now=time.time()
    last=state.get('last_backups')
    if last is not None and now-last<60:return
    state['last_backups']=now
    path=Path(runtime_dir)/'backups'/'status.json'
    status=backup_status.load(path)
    age=None
    try:age=now-path.stat().st_mtime
    except OSError:pass
    result=backup_status.evaluate(status,expected_sites,now,status_age_seconds=age)
    for name,value in result['gauges'].items():BACKUP_GAUGES[name].set(value)
    if notifier is not None:notifier.emit(result['alerts'],now)
```

`serve_once(coordinator,sites,notifier,ops_state,now=None,backups=None)`：`backups` 为 `(runtime_dir, expected_sites_loader)`；在 `monitor_ops` 之后另起 try 块调用 `monitor_backups(runtime_dir, expected_sites_loader(), notifier, ops_state)`。`main()`：`resolved=deploy_env.settings()`；`expected=lambda:[row['site'] for row in admin.load_tenants(resolved)]+[resolved['platform_site']]`（`admin` 延迟导入；读失败回落到 profile 站名并 `worker_log.log('tenants_unreadable',…)`）。dev 环境（本机）同样接入，方便集成验证。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_backup_status.py tests/test_context_worker.py tests/test_alerts.py tests/test_metrics.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dsherp/backup_status.py dsherp/context_worker.py tests/test_backup_status.py tests/test_context_worker.py
git commit -m "feat: 备份状态 → 七个 gauge 与五条告警（RPO 按数据时点 20h/24h、定时失联 13h、运行失败、8 日未验证），期望站点来自清单"
```

### Task 15: 文档

**Files:**
- Modify: `docs/engineering/deployment-runbook.md`（§3、§6、§7、§9、§11、新 §12）、`docs/superpowers/specs/2026-09-03-production-hardening-design.md`（工作流 E 行 :135、计划 4 行 :190）、`infra/env/prod.env.example`、`docs/engineering/data-governance-evidence.md`（「切片 2」骨架：设计、测试表；演练结果在 Task 16 填）

- [ ] **Step 1: 写**：runbook §3 加四个密钥文件与两个访问身份、CA、保管规则（"丢失即等于丢失全部异地备份"）；§6 服务列表加两个一次性服务（`compose --profile ops`）；§7 加 `render_worker_units.py` 输出的五个单元与 `systemctl enable --now dsherp-backup.timer dsherp-backup-drill.timer`；§9 验收表加 `systemctl list-timers 'dsherp-backup*'`、`curl -s 127.0.0.1:9109/metrics | grep dsherp_backup_`、`dsherp-admin backup-init`；§11 说明归档目录现在把 `site_config` 放在 `secrets/`；新 §12「备份与容灾」：RPO/RTO 口径、12h 周期、备份集协议、状态文件与告警键表、`backup-init`/`backup`/`backup-sync`/`restore-drill`/`restore-site` 用法、异机恢复步骤（runbook §1–§6 → 带入四个密钥文件 → `backup-init`（`kept`）→ `restore-site <site>` 逐站 → `release` 升级）、保留语义（按有快照的周期计算；淘汰只在任务运行时发生；下线集不自动淘汰）与裁决 #10 口径、worker 停止时的 `OnFailure` 兜底与残余（timer 本身失联只能靠 `backup_status_missing`，worker 也停时靠外部监控）。
- [ ] **Step 2: 自查**：每个命令名、文件名、告警键与代码一致（grep）。
- [ ] **Step 3: 提交**

```bash
git add docs infra/env/prod.env.example
git commit -m "docs: runbook 新增备份与容灾一节，密钥保管、定时器、验收行；上位设计同步为宿主 timer 调原生 bench backup"
```

### Task 16: 集成与演练，证据

**Files:**
- Modify: `docs/engineering/data-governance-evidence.md`（「切片 2」演练表、门禁、未闭合）、`docs/engineering/runtime-baseline.md`（MinIO digest 行，仅演练用）

- [ ] **Step 1: dev 栈集成**：停常驻 worker（`launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16`）；`DSHERP_ENV=dev ./bin/dsherp-admin backup`（dev compose 已加卷）；容器内核对：`sets/<site>/<set_id>/` 五个文件、`backup-secrets/<site>/<set_id>/` 两个文件、目录 0700/文件 0600、`private/backups` 里已无 `site_config_backup.json`、`sha256sum` 与 `set.json` 一致。写入 `tests/integration/test_backup_sets.py`（不跳过、缺前提 `pytest.fail`）。
- [ ] **Step 2: 本机 MinIO**（演练专用 compose `infra/drills/minio.yml`：`minio/minio@sha256:…`，自签证书由 `openssl` 生成到 scratch 目录，`backup_storage_ca.pem` 放进密钥目录）；两个桶、两个访问身份（`mc admin user add` + 只读/只写各自桶的策略）；`backup-init` → `backup --sync` → 状态文件 `complete` → 第二次 `backup --sync` 验证只补缺、`forget` 不动、`check` 轮换。
- [ ] **Step 3: 故障注入（G6）**：停 MinIO → `backup --sync` 退出码 1 → worker 5 分钟内 `backup_run_failed`（记录 worker 日志原文与 `/metrics` 行）；删状态文件 → `backup_status_missing`；改 `complete` 集 stamp 为 25h 前 → `backup_rpo_unmet`；停 worker 后让 `dsherp-backup.service`（本机无 systemd：改为直接调用 `notify-failure` 并记录 webhook 收到的 JSON）。
- [ ] **Step 4: 周演练**：`restore-drill --all` 在 `dsherp-restore` 项目上成功（记录用时、比对摘要、解密抽样数）；人为改坏一件后再跑，确认失败保留栈与诊断包、下一次拒绝、`--discard-failed` 清理。
- [ ] **Step 5: 异机自证**：本机生产形态第二项目 `dsherp-drill`（独立 `prod.env`、runtime/secrets 目录、端口、卷）按 runbook §1–§6 拉起，带入四个密钥文件，`backup-init` 报 `kept`，`restore-site` 逐站恢复、比对干净、`__Auth` 抽样解密通过；核实库账号 host 范围（`select host from mysql.user`）；记录 RTO 用时；拆栈 `down -v`。
- [ ] **Step 6: 保留淘汰真实验证**：用 `--stamp` 伪造历史集（或改 set.json 时间戳重新上传）造出 10 个跨月集，跑 `backup-sync`，核对两侧 `restic snapshots` 只剩策略允许的集，且最新完整/最新已验证/`retire` 集仍在。
- [ ] **Step 7: 证据**：`data-governance-evidence.md` 「切片 2」：设计要点、测试表（每个测试文件与它钉住的行为）、演练表（时间、命令、退出码、用时、告警时延）、门禁（非集成 pytest 计数、集成计数）、未闭合（G3 正式验收、真实异地、worker 与 timer 同时停止的盲区、历史 bench.log）；README 状态段更新；提交并推送 `plan4/backup`，开 PR。

---

## 自查记录（写计划时）

- 设计 §4.0–§4.8 每条都有对应任务：协议（1）、保持与互斥（2、5）、状态（3、14）、卷与目录（4）、生成（5）、事件集与归档分目录（6）、配置/密钥/服务（7）、保留（8）、同步配对检查（9）、隔离栈（10）、演练与清理（11）、异机路径（12）、定时与 OnFailure（13）、可见性（14）、文档（15）、演练证据（16）。
- 名称一致性：`backup.stage_set / find_pieces / backup_window / operations_lock / upload_sets / backup_sync / backup_init / notify_failure / restic / status_path / load_status`；`backup_status.record_run / record_site / record_set / evaluate / load / save / empty / now_iso`；`backup_sets.new_set_id / parse_set_id / set_manifest / pair_manifest / pair_matches / set_sha256 / local_prune / slug`；`backup_retention.select`；`site_holds.hold / release / held`；`restore_drill.DrillStack / restore_drill / restore_site`；`render_backup_units`。
- Task 3 已含 `first_at`（首次成功时写入并保留，Task 14 的新站宽限据此判断）；Task 5 的 `backup()` 里 `sync=True` 调用 `backup_sync(..., locked=True)`；Task 11 的 `_verify_fetched` 去掉占位行；Task 7 决定 CA 不进 compose `secrets:`，由 `restic()` 包装以 `run -v/-e` 注入。

---

## 实施记录（2026-09-06，同会话顺序 TDD）

计划里的代码块是候选方案；实际实现与它在下列地方不同，都是真实链路逼出来的（每一条都有测试与代码注释）：

| 与计划不同之处 | 原因 |
|---|---|
| `SITE_FLAGS` 增加 `running`/`counts`，窗口只等 `Running`/`Cancelling` | `active` 含 Queued，排队的运行会让备份永远推迟 |
| 窗口多一步"排空写入者"（RQ 作业 + 非空闲数据库连接连续两轮为 0） | 维护标志不终止已经开始的 HTTP 写入与后台作业 |
| `claim_run` 增加 `dsherp_hold` 闸门 | 关掉"worker 已过保持检查、claim 尚未落地"的缝 |
| `upload_sets` 读回 `set.json`/`pair.json` 逐项核对，并在候选快照间查找配对 | 两个快照 id 不是配对证据；重试会在同一标签下留多个快照 |
| `backup_sync` 先从仓库列表调和已记录的完整集，并收养远端未知集 | 远端副本被删后记录仍显示完整；被 forget 的集残留快照再也无法淘汰 |
| 远端内容与本机暂存不符时按本机重传一次 | 远端存在不等于内容正确 |
| `evaluate` 的 RPO 取记录里最新的已核对集 | 补传历史集时"最后一次上传成功"会把站点判成陈旧 |
| `backup_sync` 先 60 秒探可达性，超时清掉 compose run 留下的容器 | restic 对不可达端点重试一刻钟；`compose run` 的客户端被杀后容器还在 |
| 同步容器以 bench uid 运行，取回容器以 root 运行并只加 `CHOWN`/`FOWNER` | `cap_drop: [ALL]` 后 root 不再绕过属主；两类容器面对的文件属主不同 |
| 恢复用一次 `_new_site(source_sql=)`，且先看磁盘再 `frappe.init(..., new_site=True)` | 分两步会在站点不存在时读配置报 404 |
| 拆栈带 `--profile fetch`，残留判定排除 restic 缓存卷 | 否则第一次演练之后每次都被自己挡住 |
| 计划里的 `PROVIDED_SECRETS`、`_manifest_path` 等命名按实际实现调整 | 与既有代码风格一致 |

阶段末的真实验证结果见[数据治理与容灾证据](../../engineering/data-governance-evidence.md)「切片 2」。
