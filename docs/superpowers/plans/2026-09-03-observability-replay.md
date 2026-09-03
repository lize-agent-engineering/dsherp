# 计划 1：可观测与失败回放 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 本计划在 Codex 中执行（Claude 架构+审计、Codex 执行、检查点整改-放行循环，见 AGENTS.md）。分支 `codex/observability-replay`。检查点 C0–C4，每个检查点由审计方重跑核验命令后放行；Codex 自报绿不算放行。

**Goal:** 让每一次 Agent 运行的模型调用、工具调用、失败原因都成为可查询、可回放的持久事件，并让卡死、积压、备份失效、provider 连续失败在 5 分钟内产生告警。

**Architecture:** 新增 `DS Run Event` DocType 作为事件流真相；Frappe 端在 claim/工具/预留/结束等既有事务内追加事件，容器内 runner 与宿主 worker 在 `finish_run` 之前经新的凭据端点 `record_run_event` 批量回写模型侧事件；宿主 worker 新增 JSON 行日志、Prometheus 文本指标与规则化告警；Frappe scheduler 每 5 分钟产出运维快照供 worker 与管理员读取；评估集导出脚本把历史失败运行落成 `evals/cases/*.json`。所有事件只增不删，任何凭证值不进入事件与日志。

**Tech Stack:** Frappe v16.31 / ERPNext v16.33（容器内 Python 3.14）、宿主 Python 3.12 + httpx + pytest、DSH SDK 0.1.1rc1（`RunResult.events` 与 `on_notification`）、Node 内置 `node:test`（runtime 插件）、React 18 + vitest（前端）、docker compose `dsherp-validation`。

## Global Constraints

- 任何 DocType/Report 变更后，装有 `dsherp_bridge` 的三个 Site（alpha `dsherp-validation.localhost`、daily `dsherp-daily.localhost`、beta `dsherp-beta.localhost`）都必须 `bench migrate`，并在检查点报告中逐站列出退出码；平台站只装 `dsherp_platform`，不涉及。

- 全部按 AGENTS.md：中文；TDD（先写失败测试）；fastfail；不 fork Frappe/ERPNext；按功能分类提交；不推送远端；不提交密钥、租户数据、运行日志。
- 生产化总体设计"演示与生产差异的前置约束"第 7 条：**每次模型调用与工具调用留持久事件；日志无凭证**。本计划每个任务的测试都必须包含"凭证值不出现"的断言（凭证值指 `DEEPSEEK_API_KEY`、capability、api_secret、platform_grant、密码）。
- `DS Run Event` 只增不删不改（已裁决 #3）：`validate` 拒绝改写，`on_trash` 无条件拒绝；合成测试清理只能用 `frappe.db.delete('DS Run Event',{'run':...})`，且必须在删除 `DS Model Run` 之前执行。
- 事件 payload 上限 8192 字节，字符串字段截断到 2000 字符；单次 `record_run_event` 最多 200 条。
- 修改 `config/runtime-files.json` 所列文件会作废在飞运行（设计使然）；新增被容器加载的模块（`dsherp/run_events.py`）必须加入该清单，`tests/test_runtime_revision.py` 必须继续通过。
- 观测写入失败不得改变业务结果：事件回写失败只记日志，不把 Succeeded 变成 Failed，也不重试模型运行。
- 告警只通知不自动修复（自动回收卡 Running 属计划 2）。
- 不做模型降级、不做熔断（计划 2）、不改预算数值（已裁决 #6：常量配置化在计划 2）。
- 真实 provider 调用不在本计划范围；所有自动化用本地模型替身（`tests/conftest.py` 的 `model_server`）。G6 演练用不可达的 provider 地址制造故障，不产生费用。

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_run_event/ds_run_event.{json,py}` | 事件 DocType：命名 `<run>-<seq:06d>`，不可改写、不可删除 | T1.1 |
| `frappe_app/dsherp_bridge/context_events.py` | 服务端事件写入与读取：`KINDS`、`sanitize`、`record`、`record_many`、`list_events` | T1.1 |
| `frappe_app/dsherp_bridge/context_execution.py` | 在 claim/预留/工具/结束处埋点；新增凭据端点 `record_run_event` | T1.2、T1.3 |
| `frappe_app/dsherp_bridge/context_api.py` | `send_message`/`cancel_run` 埋点；新增所有者端点 `list_run_events` | T1.2、T1.4 |
| `dsherp/run_events.py` | 容器/宿主共用：`sanitize`、`from_runtime_events`、`flush`（永不抛出） | T2.1 |
| `dsherp/context_runner.py` | `monitored_run` 收集 `RunResult.events` 与通知并回写；失败路径回写 `runtime_failed` | T2.2 |
| `dsherp/worker_log.py` | 宿主 JSON 行日志 `log(event, **fields)`，含脱敏 | T2.3 |
| `dsherp/context_worker.py` | 用结构化日志替换 print；容器结束/失败事件回写；接入指标与告警 | T2.3、T3.2、T3.3 |
| `runtime/model-guard.cjs` | 流结束时回写 `model_response`（usage）与 `model_error` | T2.4 |
| `frappe_app/dsherp_bridge/ops.py` + `.../doctype/ds_ops_snapshot/` | 5 分钟运维快照与 `ops_status` 端点；scheduler 注册 | T3.1 |
| `dsherp/metrics.py` | Prometheus 文本指标注册表与本机 HTTP 暴露 | T3.2 |
| `dsherp/alerts.py` | 规则求值 `evaluate(...)`、去重、stderr/webhook 输出 | T3.3 |
| `frappe_app/dsherp_bridge/dsherp_bridge/report/ds_agent_audit/` | 管理员跨用户审计报表 | T4.1 |
| `frontend/src/agent-transcript.js`、`AgentRecords.jsx` | 事件流可读映射与按需加载 | T4.2 |
| `dsherp/eval_cases.py`、`infra/export_eval_cases.py`、`evals/` | 失败运行导出为评估用例 | T4.3 |
| `docs/engineering/observability-evidence.md` | 探针结果、演练证据、检查点记录 | 全程 |

## 检查点总览

| 检查点 | 内容 | 放行标准（审计方重跑） |
|---|---|---|
| C0 | 探针与基线 | 证据文档列出真实事件类型清单与 usage 字段结论；历史失败运行数量入档 |
| C1 | 服务端事件流 | 集成测试：事件顺序、不可改写、不可删除、凭证不入库、所有者可读、他人 403 |
| C2 | runner/worker 回写与日志 | 非集成测试全绿；`test_runtime_revision` 绿；替身链路一次运行产生 ≥ 5 条事件；stderr 无凭证 |
| C3 | 指标与告警 | `/metrics` 可抓取；注入 provider 不可达、孤儿容器、积压三种故障各在 5 分钟内出告警行 |
| C4 | 审计视图、前端、评估导出、G6 演练 | 报表可跨用户查询；前端事件流可见且测试绿；`evals/cases/` 有全部历史失败运行；演练证据落档 |

---

## 阶段 0（C0）：探针与基线

### Task 0.1：固定 Runtime 的事件形状探针

**Files:**
- Create: `infra/probe_observability/t0_1_runtime_events.py`
- Create: `docs/engineering/observability-evidence.md`

**Interfaces:**
- Produces: 证据文档中的"事件类型清单"表与"usage 字段结论"（T2.1 的映射、T2.4 的 usage 提取依据）。

- [ ] **Step 1: 写探针脚本（只用本地模型替身，不打真实 provider）**

```python
"""Print the event/notification shapes emitted by the pinned runtime. Values are replaced by their types."""
import json,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tests.conftest import model_server as _fixture  # 复用替身：同一 SSE 协议
from dsherp.session_runtime import open_runtime

def shape(value,depth=0):
    if isinstance(value,dict):return {k:shape(v,depth+1) for k,v in list(value.items())[:30]} if depth<5 else 'dict'
    if isinstance(value,list):return [shape(value[0],depth+1)] if value else []
    return type(value).__name__

def main(directory):
    generator=_fixture.__wrapped__() if hasattr(_fixture,'__wrapped__') else None
    if generator is None:raise SystemExit('conftest model_server fixture must be a plain generator function')
    settings,requests,state=next(generator)
    state['tool_call']={'name':'skill','arguments':json.dumps({'name':'erp-query'})}
    notifications=[]
    try:
        with open_runtime(settings,Path(directory),'probe-events',resume=False) as runtime:
            result=runtime.run('probe',session_id='probe-events',on_notification=lambda n:notifications.append(n))
        types=sorted({event.get('type') for event in result.events})
        print(json.dumps({'event_types':types,
            'shapes':{t:shape(next(e for e in result.events if e.get('type')==t)) for t in types},
            'notification_methods':sorted({n.method for n in notifications}),
            'finish_reason':result.finish_reason},ensure_ascii=False,indent=1))
    finally:
        try:next(generator)
        except StopIteration:pass

if __name__=='__main__':main(sys.argv[1])
```

- [ ] **Step 2: 运行探针并把输出落档**

Run: `.venv/bin/python infra/probe_observability/t0_1_runtime_events.py work/probe-events`
Expected: 输出 JSON，`event_types` 至少含 `tool/call`、`tool/result`、`turn/end`、`assistant/message`；若 `tests/conftest.py` 的 fixture 无法作为生成器复用，改为把探针写成 `tests/test_observability_probe.py` 里一个用 `model_server` fixture 的测试并用 `-s` 打印，两种方式二选一，证据文档写明用的哪种。

- [ ] **Step 3: 写证据文档首节**

`docs/engineering/observability-evidence.md` 新建，包含：探针命令、`event_types` 全表、每类事件的顶层键名（不含值）、`tool/call` 与 `tool/result` 的键名、**usage 结论**：明确写出"哪个事件类型的哪个键携带 input/output token 计数"或"固定 Runtime 的根会话事件不携带 usage，T2.4 只记录 finish 与 chunk 顶层键名"。不得猜测。

- [ ] **Step 4: 清理并提交**

```bash
rm -rf work/probe-events
git add infra/probe_observability/t0_1_runtime_events.py docs/engineering/observability-evidence.md
git commit -m "docs: 探针固定 Runtime 事件形状"
```

### Task 0.2：历史失败运行盘点（只读）

**Files:**
- Modify: `docs/engineering/observability-evidence.md`

- [ ] **Step 1: 在 alpha 站只读统计**

```bash
docker exec -i dsherp-validation-backend-1 /home/frappe/frappe-bench/env/bin/python - <<'PY'
import os,frappe,json
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
rows=frappe.get_all('DS Model Run',fields=['status','domain','error'],limit_page_length=0)
by_status={};errors={}
for r in rows:
    by_status[r.status]=by_status.get(r.status,0)+1
    if r.status=='Failed':errors[(r.error or '')[:60]]=errors.get((r.error or '')[:60],0)+1
print(json.dumps({'total':len(rows),'by_status':by_status,'failed_error_prefixes':errors},ensure_ascii=False,indent=1))
frappe.destroy()
PY
```

- [ ] **Step 2: 结果入档**

证据文档新增"历史运行基线"节：总数、按状态计数、Failed 的 error 前缀分布。daily 站（`dsherp-validation-daily-1` 不存在时用 `docker compose -p dsherp-validation ps` 找到 daily 所在容器与站名 `dsherp-daily.localhost`）同样统计一次。此数字是 T4.3 导出条数的验收基准。

- [ ] **Step 3: 提交**

```bash
git add docs/engineering/observability-evidence.md
git commit -m "docs: 记录历史运行基线"
```

**C0 放行标准**：证据文档两节齐全；探针脚本入库；`work/` 无残留。

---

## 阶段 1（C1）：服务端事件流

### Task 1.1：`DS Run Event` DocType 与 `context_events` 写读助手

**Files:**
- Create: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_run_event/__init__.py`（空）
- Create: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_run_event/ds_run_event.json`
- Create: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_run_event/ds_run_event.py`
- Create: `frappe_app/dsherp_bridge/context_events.py`
- Test: `tests/integration/test_run_events.py`

**Interfaces:**
- Produces: `context_events.record(run: str, kind: str, payload: dict, *, source: str='server', error_class: str|None=None) -> str`（返回事件 name）；`context_events.record_many(run, items: list[dict]) -> dict{'recorded': int, 'last_seq': int}`；`context_events.list_events(run, page=1, page_length=200) -> list[dict]`；常量 `SERVER_KINDS`、`RUNNER_KINDS`、`SOURCES`、`SECRET_KEYS`、`MAX_STRING=2000`、`MAX_PAYLOAD=8192`、`MAX_BATCH=200`。

- [ ] **Step 1: 写失败的集成测试**

```python
"""Run events are append-only, sequential, secret-free and owner-readable."""
import subprocess


def test_run_events_are_sequential_immutable_and_secret_free():
    script=r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='events-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic events','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Events'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'events','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(b'cap').hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    first=ev.record(run.name,'claimed',{'domain':'query','capability':'SECRET-CAP','nested':{'api_secret':'SECRET-2','ok':'x'*3000}})
    second=ev.record(run.name,'tool_call',{'tool':'erp_read_record'},error_class=None)
    assert first==run.name+'-000001' and second==run.name+'-000002',(first,second)
    stored=frappe.get_doc('DS Run Event',first)
    assert 'SECRET' not in stored.payload, stored.payload
    assert 'capability' not in json.loads(stored.payload) and 'api_secret' not in json.loads(stored.payload)['nested']
    assert json.loads(stored.payload)['nested']['ok'].endswith('…[truncated]')
    try:
        stored.kind='changed';stored.save(ignore_permissions=True);raise AssertionError('event was rewritten')
    except frappe.ValidationError:pass
    try:
        frappe.delete_doc('DS Run Event',first,ignore_permissions=True);raise AssertionError('event was deleted')
    except frappe.ValidationError:pass
    try:
        ev.record(run.name,'not-a-kind',{});raise AssertionError('unknown kind accepted')
    except frappe.ValidationError:pass
    batch=ev.record_many(run.name,[{'kind':'runtime_started','payload':{},'source':'runner'},
                                   {'kind':'tool_error','payload':{'text':'x'},'source':'runner','error_class':'PermissionError'}])
    assert batch=={'recorded':2,'last_seq':4},batch
    listed=ev.list_events(run.name)
    assert [e['seq'] for e in listed]==[1,2,3,4] and listed[3]['error_class']=='PermissionError'
    assert set(listed[0])=={'name','seq','kind','source','error_class','payload','recorded_at'}
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
                          input=script,text=True,capture_output=True,timeout=90)
    assert result.returncode==0 and 'OK' in result.stdout, result.stdout+result.stderr
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/integration/test_run_events.py -q`
Expected: FAIL，stderr 含 `ModuleNotFoundError: dsherp_bridge.context_events` 或 `DoesNotExistError: DocType DS Run Event`。

- [ ] **Step 3: 写 DocType JSON**

`ds_run_event.json`：

```json
{
  "doctype": "DocType", "name": "DS Run Event", "module": "DSHERP Bridge", "custom": 0, "engine": "InnoDB",
  "sort_field": "creation", "sort_order": "ASC",
  "field_order": ["run", "seq", "kind", "source", "error_class", "recorded_at", "payload"],
  "fields": [
    {"fieldname": "run", "label": "run", "fieldtype": "Link", "options": "DS Model Run", "reqd": 1, "search_index": 1},
    {"fieldname": "seq", "label": "seq", "fieldtype": "Int", "reqd": 1},
    {"fieldname": "kind", "label": "kind", "fieldtype": "Data", "reqd": 1},
    {"fieldname": "source", "label": "source", "fieldtype": "Select", "options": "server\nrunner\nworker", "reqd": 1},
    {"fieldname": "error_class", "label": "error_class", "fieldtype": "Data"},
    {"fieldname": "recorded_at", "label": "recorded_at", "fieldtype": "Datetime", "reqd": 1},
    {"fieldname": "payload", "label": "payload", "fieldtype": "Long Text"}
  ],
  "permissions": []
}
```

`ds_run_event.py`：

```python
import frappe
from frappe.model.document import Document


class DSRunEvent(Document):
    def autoname(self):
        self.name = f"{self.run}-{int(self.seq):06d}"

    def validate(self):
        if self.get_doc_before_save():
            frappe.throw("运行事件不可改写")

    def on_trash(self):
        frappe.throw("运行事件不可删除")
```

- [ ] **Step 4: 写 `context_events.py`**

```python
"""Append-only per-run event stream. Never stores credential values."""
import json
import frappe
from frappe.utils import now_datetime

SERVER_KINDS=('queued','claimed','expired','cancel_requested','model_call_reserved','tool_call','finished')
RUNNER_KINDS=('runtime_started','model_request','model_response','model_error','runtime_tool_call','tool_result',
              'tool_error','compaction','turn_end','runtime_failed','container_finished','worker_error')
KINDS=SERVER_KINDS+RUNNER_KINDS
SOURCES=('server','runner','worker')
SECRET_KEYS=frozenset({'capability','capability_hash','api_key','api_secret','token','access_token','password','secret',
                       'platform_grant','authorization','deepseek_api_key','cookie'})
MAX_STRING=2000
MAX_PAYLOAD=8192
MAX_BATCH=200
MAX_ITEMS=50
MAX_DEPTH=6


def sanitize(value,depth=0):
    if depth>MAX_DEPTH:return '…[depth]'
    if isinstance(value,dict):
        return {str(k):sanitize(v,depth+1) for k,v in list(value.items())[:MAX_ITEMS] if str(k).lower() not in SECRET_KEYS}
    if isinstance(value,(list,tuple)):
        return [sanitize(v,depth+1) for v in list(value)[:MAX_ITEMS]]
    if isinstance(value,str):
        return value if len(value)<=MAX_STRING else value[:MAX_STRING]+'…[truncated]'
    if isinstance(value,(int,float,bool)) or value is None:return value
    return type(value).__name__


def _serialize(kind,payload):
    text=json.dumps(sanitize(payload if isinstance(payload,dict) else {'value':payload}),ensure_ascii=False,separators=(',',':'))
    if len(text.encode())>MAX_PAYLOAD:
        text=json.dumps({'truncated':True,'kind':kind,'bytes':len(text.encode())},separators=(',',':'))
    return text


def _insert(run,seq,kind,payload,source,error_class):
    return frappe.get_doc({'doctype':'DS Run Event','run':run,'seq':seq,'kind':kind,'source':source,
        'error_class':(error_class or '')[:140],'recorded_at':now_datetime(),
        'payload':_serialize(kind,payload)}).insert(ignore_permissions=True).name


def record(run,kind,payload,*,source='server',error_class=None):
    if kind not in KINDS:frappe.throw('未知运行事件类型')
    if source not in SOURCES:frappe.throw('未知运行事件来源')
    seq=(frappe.db.count('DS Run Event',{'run':run}) or 0)+1
    try:
        return _insert(run,seq,kind,payload,source,error_class)
    except frappe.DuplicateEntryError:
        seq=(frappe.db.sql('SELECT COALESCE(MAX(seq),0) FROM `tabDS Run Event` WHERE run=%s',(run,))[0][0] or 0)+1
        return _insert(run,seq,kind,payload,source,error_class)


def record_many(run,items):
    if not isinstance(items,list) or not 0<len(items)<=MAX_BATCH:frappe.throw('运行事件批次无效')
    last=None
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('kind'),str):frappe.throw('运行事件无效')
        source=item.get('source','runner')
        if source not in ('runner','worker') or item['kind'] not in RUNNER_KINDS:frappe.throw('运行事件来源或类型无效')
        last=record(run,item['kind'],item.get('payload') or {},source=source,error_class=item.get('error_class'))
    return {'recorded':len(items),'last_seq':int(last.rsplit('-',1)[1])}


def list_events(run,page=1,page_length=200):
    rows=frappe.get_all('DS Run Event',filters={'run':run},fields=['name','seq','kind','source','error_class','payload','recorded_at'],
                        order_by='seq asc',start=(page-1)*page_length,page_length=page_length)
    return [{'name':r.name,'seq':r.seq,'kind':r.kind,'source':r.source,'error_class':r.error_class or '',
             'payload':json.loads(r.payload or '{}'),'recorded_at':str(r.recorded_at)} for r in rows]
```

- [ ] **Step 5: 让站点识别新 DocType 并重跑测试**

Run（装有 dsherp_bridge 的三个 Site 都要迁移：alpha 与 daily 同在 backend-1 一个 bench，beta 在 beta-backend-1）：
```bash
docker exec dsherp-validation-backend-1 bench --site dsherp-validation.localhost migrate
docker exec dsherp-validation-backend-1 bench --site dsherp-daily.localhost migrate
docker exec dsherp-validation-beta-backend-1 bench --site dsherp-beta.localhost migrate
.venv/bin/python -m pytest tests/integration/test_run_events.py -q
```
Expected: PASS；三站 `frappe.db.table_exists('DS Run Event')` 均为 True。若 `migrate` 报 `custom` 缺失类错误，说明 `configuration_locks.check_new_custom_record` 的容忍分支未覆盖新表，先修 hook 再继续，不绕过。

- [ ] **Step 6: 提交**

```bash
git add frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_run_event frappe_app/dsherp_bridge/context_events.py tests/integration/test_run_events.py
git commit -m "feat: 新增只增不删的运行事件流"
```

### Task 1.2：服务端事务内埋点（claim / 预留 / 工具 / 结束 / 发送 / 取消）

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_execution.py`（`claim_run`、`reserve_model_call`、`run_tool`、`finish_run`）
- Modify: `frappe_app/dsherp_bridge/context_api.py:294-347`（`send_message`、`cancel_run`）
- Test: `tests/integration/test_run_events.py`（新增用例）

**Interfaces:**
- Consumes: `context_events.record`。
- Produces: 事件序列契约（T4.2 前端与 T4.3 导出依赖）：`queued` → `claimed` → (`model_call_reserved` | `tool_call`)* → `finished`；取消路径多一条 `cancel_requested`；过期回收多一条 `expired`。`tool_call` payload 固定键：`tool`、`arguments`、`duration_ms`、`result`（读工具为 `{'records':int,'fields':int}`，提案工具为 `{'proposal':str}`，配置工具为 `{'exists':bool}`）。

- [ ] **Step 1: 写失败的集成测试（追加到 test_run_events.py）**

```python
def test_server_records_the_run_lifecycle_in_order():
    script=r'''
import os,uuid,json,hashlib,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_api as api,context_events as ev
from dsherp_bridge.context_execution import claim_run,run_tool,reserve_model_call,finish_run
from dsherp.runtime_revision import configuration_revision  # 不可用时改为 'a'*64
conversation=None;actor=None;runs=[]
try:
    frappe.set_user('Administrator')
    actor='lifecycle-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic lifecycle','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    session=api.send_message('读取测试物料',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex,domain='query')
    conversation=session['id'];run_id=session['messages'][0]['id'];runs.append(run_id)
    frappe.db.commit()
    frappe.set_user(frappe.conf.get('dsherp_runtime_user'))
    claim=claim_run('a'*64)
    assert claim and claim['run_id']==run_id,claim
    cap={'run_id':run_id,'capability':claim['capability']}
    frappe.set_user('Guest')
    reserve_model_call(**cap,input_bytes=100,max_output_tokens=512,provider='deepseek-official',model='deepseek-v4-flash',purpose='conversation',runtime_revision='a'*64,domain='query')
    run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    finish_run(**cap,status='Succeeded',answer='完成')
    frappe.db.commit()
    kinds=[e['kind'] for e in ev.list_events(run_id)]
    assert kinds==['queued','claimed','model_call_reserved','tool_call','finished'],kinds
    tool=ev.list_events(run_id)[3]['payload']
    assert tool['tool']=='erp_read_record' and tool['result']['records']==1 and isinstance(tool['result']['fields'],int),tool
    assert isinstance(tool['duration_ms'],int)
    assert all('capability' not in json.dumps(e['payload']) and claim['capability'] not in json.dumps(e['payload']) for e in ev.list_events(run_id))
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in runs:
        frappe.db.delete('DS Run Event',{'run':name})
        frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
                          input=script,text=True,capture_output=True,timeout=120)
    assert result.returncode==0 and 'OK' in result.stdout, result.stdout+result.stderr
```

注意：`claim_run` 会因站上存在其他 Running 运行而返回 `None`；测试前用 `infra/v16_integration_queue.py` 既有的队列清理（`tests/integration/conftest.py` autouse 已做），若仍返回 None，断言信息要打印当前 Running 运行名，不重试。`from dsherp.runtime_revision import ...` 在容器内不可用，直接用 `'a'*64` 作为 runtime_revision（claim 只校验格式）。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/integration/test_run_events.py::test_server_records_the_run_lifecycle_in_order -q`
Expected: FAIL，`kinds==[]`。

- [ ] **Step 3: 埋点实现**

`context_execution.py` 顶部加 `import time` 与 `from dsherp_bridge import context_events as events`。

`claim_run`：过期回收循环内每条 `set_value` 之后加 `events.record(name,'expired',{'reason':'lease_expired'})`；在最后 `frappe.db.set_value('DS Model Run',run.name,{'status':'Running',...})` 之后、`return` 之前加：

```python
    events.record(run.name,'claimed',{'domain':domain,'permission_revision':permission_revision,
        'runtime_revision':runtime_revision,'native_session_id':conversation.runtime_session})
```

`reserve_model_call`：在 `frappe.db.set_value(... 'model_calls':calls+1 ...)` 之后加：

```python
    events.record(run.name,'model_call_reserved',{'call_index':calls+1,'input_bytes':input_bytes,
        'max_output_tokens':max_output_tokens,'purpose':purpose,'model':model})
```

`run_tool`：把现有函数体整体改名为 `_run_tool(run,tool,arguments)`（保留全部逻辑与返回值），新的入口：

```python
@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    run=_run(run_id,capability)
    started=time.perf_counter()
    result=_run_tool(run,tool,arguments)
    summary=_tool_summary(tool,result)
    events.record(run.name,'tool_call',{'tool':tool,'arguments':arguments if isinstance(arguments,dict) else {'raw':str(arguments)[:200]},
        'duration_ms':int((time.perf_counter()-started)*1000),'result':summary})
    return result


def _tool_summary(tool,result):
    if tool=='erp_read_schema':return {'records':0,'fields':len(result.get('fields',[]))}
    if tool=='erp_read_record':return {'records':1,'fields':len(result.get('fields',{}))}
    if tool=='erp_search_records':return {'records':len(result),'fields':len(result[0]) if result else 0}
    if tool=='erp_read_configuration':return {'exists':bool(result.get('exists'))}
    if isinstance(result,dict) and result.get('id'):return {'proposal':result['id']}
    return {'keys':sorted(result)[:20] if isinstance(result,dict) else type(result).__name__}
```

`_run_tool` 的第一行原本是 `run=_run(run_id,capability)`，删除该行（run 已由入口传入）。工具抛出异常时不记录事件（事务会回滚，错误由 runner 侧 `tool_error` 事件记录，见 T2.2）。

`finish_run`：在 `frappe.db.set_value(...'capability_hash':''...)` 之前加：

```python
    events.record(run.name,'finished',{'status':status,'answer_chars':len(answer) if isinstance(answer,str) else 0,
        'error':(error or '')[:500],'model_calls':run.model_calls or 0})
```

`context_api.send_message`：新建 `DS Model Run` 之后、`return _public(doc)` 之前加：

```python
    from dsherp_bridge import context_events as events
    events.record(run_id,'queued',{'domain':domain,'question_chars':len(question.strip()),'page_type':snapshot.get('page_type')})
```

`context_api.cancel_run`：`run.save(ignore_permissions=True)` 之后加：

```python
        from dsherp_bridge import context_events as events
        events.record(run.name,'cancel_requested',{'to_status':run.status})
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest tests/integration/test_run_events.py -q`
Expected: 2 passed。

- [ ] **Step 5: 更新既有集成测试的清理顺序**

Run: `grep -rln "delete_doc('DS Model Run'" tests/integration`
对每个命中的文件，在 `delete_doc('DS Model Run',name,...)` 所在循环体内、该行之前插入 `frappe.db.delete('DS Run Event',{'run':name})`（变量名按各文件实际）。然后：

Run: `.venv/bin/python -m pytest tests/integration/test_context_sessions.py tests/integration/test_context_transcript.py tests/integration/test_context_claim_cancel.py tests/integration/test_context_worker_chain.py -q`
Expected: 全绿；若某文件因事件链接残留报 `LinkExistsError`，说明漏改，补上后重跑。

- [ ] **Step 6: 提交**

```bash
git add frappe_app/dsherp_bridge/context_execution.py frappe_app/dsherp_bridge/context_api.py tests/integration
git commit -m "feat: 运行生命周期在服务端事务内落事件"
```

### Task 1.3：凭据端点 `record_run_event`（runner/worker 回写入口）

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_execution.py`
- Test: `tests/integration/test_run_events.py`

**Interfaces:**
- Produces: `POST /api/method/dsherp_bridge.context_execution.record_run_event`，参数 `run_id, capability, events: list[{kind, payload, source, error_class?}]`，返回 `{'recorded': int, 'last_seq': int}`；仅在运行 Running/Cancelling 且凭据有效时接受；`kind` 限 `RUNNER_KINDS`，`source` 限 `runner|worker`。

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_runner_batch_endpoint_requires_live_capability_and_runner_kinds():
    script=r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as ev
from dsherp_bridge.context_execution import record_run_event
from dsherp_bridge.context_permissions import revision
conversation=None;run=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='batch-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic batch','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Batch'}).insert(ignore_permissions=True)
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'batch','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    frappe.set_user('Guest')
    out=record_run_event(run.name,capability,[{'kind':'runtime_started','payload':{'api_key':'SECRET'},'source':'runner'},
                                              {'kind':'tool_error','payload':{'text':'拒绝'},'source':'runner','error_class':'PermissionError'}])
    assert out=={'recorded':2,'last_seq':2},out
    for bad in ([{'kind':'claimed','payload':{},'source':'runner'}],[{'kind':'runtime_started','payload':{},'source':'server'}],[]):
        try:record_run_event(run.name,capability,bad);raise AssertionError('accepted %r'%bad)
        except (frappe.ValidationError,frappe.PermissionError):pass
    try:record_run_event(run.name,'wrong',[{'kind':'runtime_started','payload':{},'source':'runner'}]);raise AssertionError('bad capability accepted')
    except frappe.PermissionError:pass
    assert 'SECRET' not in ''.join(json.dumps(e['payload']) for e in ev.list_events(run.name))
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name});frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation:frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
                          input=script,text=True,capture_output=True,timeout=90)
    assert result.returncode==0 and 'OK' in result.stdout, result.stdout+result.stderr
```

- [ ] **Step 2: 运行确认失败**（`ImportError: record_run_event`）

- [ ] **Step 3: 实现**

```python
@frappe.whitelist(allow_guest=True,methods=['POST'])
def record_run_event(run_id,capability,events):
    run=_run(run_id,capability)
    items=json.loads(events) if isinstance(events,str) else events
    from dsherp_bridge import context_events
    return context_events.record_many(run.name,items)
```

端点参数名必须叫 `events`（runner、worker、model-guard 三处调用方都用这个键）；函数体内用局部导入避免与 T1.2 引入的模块别名 `events` 冲突。

- [ ] **Step 4: 运行测试** → 3 passed。

- [ ] **Step 5: 提交**

```bash
git add frappe_app/dsherp_bridge/context_execution.py tests/integration/test_run_events.py
git commit -m "feat: 运行凭据可批量回写模型侧事件"
```

### Task 1.4：所有者读取端点 `list_run_events`

**Files:**
- Modify: `frappe_app/dsherp_bridge/context_api.py`
- Test: `tests/integration/test_run_events.py`

**Interfaces:**
- Produces: `GET /api/method/dsherp_bridge.context_api.list_run_events?run_id=&page=1` → `{'run_id','page','events':[...],'has_more':bool}`；运行所有者可读；持 `System Manager` 角色的用户可读任意运行（T4.1 报表复用）；其他用户 `PermissionError`。不进入 `_public`（避免加重 R1）。

- [ ] **Step 1: 写失败测试（追加）**：以所有者读取得到 T1.1 同样结构；另一合成普通用户读取抛 `PermissionError`；`page=2` 且总数 ≤200 时 `events==[]` 且 `has_more` 为 False。测试骨架与 T1.3 相同（创建 run、记录 2 条事件、切换用户调用 `api.list_run_events`），断言：

```python
    frappe.set_user(actor);page=api.list_run_events(run.name)
    assert page['run_id']==run.name and [e['seq'] for e in page['events']]==[1,2] and page['has_more'] is False
    frappe.set_user(other)
    try:api.list_run_events(run.name);raise AssertionError('other user read events')
    except frappe.PermissionError:pass
```

- [ ] **Step 2: 确认失败** → `AttributeError: list_run_events`。

- [ ] **Step 3: 实现（context_api.py 末尾）**

```python
@frappe.whitelist(methods=['GET'])
def list_run_events(run_id,page=1):
    user=_user();page=_page(page)
    run=frappe.get_doc('DS Model Run',run_id)
    if run.owner!=user and 'System Manager' not in frappe.get_roles(user):
        raise frappe.PermissionError('运行不属于当前用户')
    from dsherp_bridge import context_events as events
    rows=events.list_events(run.name,page=page,page_length=201)
    return {'run_id':run.name,'page':page,'events':rows[:200],'has_more':len(rows)>200}
```

- [ ] **Step 4: 运行测试** → 4 passed。

- [ ] **Step 5: 提交**

```bash
git add frappe_app/dsherp_bridge/context_api.py tests/integration/test_run_events.py
git commit -m "feat: 运行所有者与管理员可读取事件流"
```

**C1 放行标准**：`.venv/bin/python -m pytest tests/integration/test_run_events.py -q` 全绿；`tests/integration` 中改过清理顺序的文件全绿；审计方在 alpha 站抽一条真实运行的事件 payload，grep `capability|api_secret|DEEPSEEK` 为 0 命中；alpha、daily、beta 三站 `bench migrate` 无报错且 `DS Run Event` 表均存在；服务端事件写入失败不改变业务结果（见 C1 整改 P1-1 的测试）。

---

## 阶段 2（C2）：runner 与 worker 回写、结构化日志

### Task 2.1：宿主/容器共用模块 `dsherp/run_events.py`

**Files:**
- Create: `dsherp/run_events.py`
- Modify: `config/runtime-files.json`（加入 `dsherp/run_events.py`，放在 `dsherp/context_runner.py` 之后）
- Test: `tests/test_run_events.py`

**Interfaces:**
- Produces: `sanitize(value) -> value`（规则与服务端一致）；`from_runtime_events(events: list[dict], notifications: list) -> list[dict]`（每项 `{'kind','payload','source':'runner','error_class'?}`）；`flush(post, run_id, capability, items, *, batch=100) -> dict{'sent': int, 'error': str|None}`（永不抛出）；常量 `RUNNER_KINDS`、`SECRET_KEYS`、`MAX_STRING`。
- Consumes: `dsherp.context_mcp.post(client, method, **data)`。

- [ ] **Step 1: 写失败测试**

```python
import ast,json
from pathlib import Path
import httpx
from dsherp.context_mcp import post
from dsherp import run_events as re_


def test_constants_match_server_module():
    source=(Path(__file__).resolve().parents[1]/'frappe_app/dsherp_bridge/context_events.py').read_text()
    tree=ast.parse(source);found={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and node.targets[0].id in ('RUNNER_KINDS','MAX_STRING','MAX_PAYLOAD','MAX_BATCH'):
            found[node.targets[0].id]=ast.literal_eval(node.value)
        if isinstance(node,ast.Assign) and node.targets[0].id=='SECRET_KEYS':
            found['SECRET_KEYS']=frozenset(ast.literal_eval(node.value.args[0]))
    assert found['RUNNER_KINDS']==re_.RUNNER_KINDS and found['SECRET_KEYS']==re_.SECRET_KEYS
    assert (found['MAX_STRING'],found['MAX_PAYLOAD'],found['MAX_BATCH'])==(re_.MAX_STRING,re_.MAX_PAYLOAD,re_.MAX_BATCH)


def test_runtime_events_map_to_tool_and_turn_records_without_secrets():
    # 形状来自 C0 探针证据（observability-evidence.md）：arguments 是 JSON 字符串，结果嵌在 message.content[] 内
    events=[{'type':'turn/start','seq':1,'data':{'turn':1}},
            {'type':'tool/call','seq':2,'data':{'turn':1,'step':1,'callId':'c1','name':'mcp__erp__erp_read_record',
                                                 'arguments':json.dumps({'doctype':'Item','name':'I','capability':'SECRET'})}},
            {'type':'tool/result','seq':3,'data':{'turn':1,'step':1,'message':{'source':{'kind':'tool','callId':'c1'},'role':'tool','id':'m1',
                'content':[{'type':'tool_result','toolCallId':'c1','isError':True,'content':[{'type':'text','text':'业务运行请求未完成（HTTP 417）'}]}]},
                'error':{'name':'ToolExecutionError','code':'E_TOOL'}}},
            {'type':'assistant/message','seq':4,'data':{'message':{'content':[{'type':'text','text':'x'*5000}]}}},
            {'type':'turn/end','seq':5,'data':{'reason':{'kind':'completed'}}}]
    items=re_.from_runtime_events(events,[])
    kinds=[i['kind'] for i in items]
    assert kinds==['runtime_tool_call','tool_error','turn_end'],kinds
    assert items[0]['payload']['name']=='mcp__erp__erp_read_record' and items[0]['payload']['call_id']=='c1'
    assert items[0]['payload']['arguments']=={'doctype':'Item','name':'I'}
    assert items[1]['error_class']=='ToolExecutionError' and '417' in items[1]['payload']['text'] and items[1]['payload']['call_id']=='c1'
    assert items[2]['payload']['reason']=='completed' and all(i['payload'].get('seq') for i in items)
    assert 'SECRET' not in json.dumps(items)


def test_flush_batches_and_never_raises():
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'message':{'recorded':len(seen[-1]['events']),'last_seq':1}}) if len(seen)<3 else httpx.Response(503)
    items=[{'kind':'turn_end','payload':{'i':i},'source':'runner'} for i in range(250)]
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        out=re_.flush(lambda **kw:post(client,'record_run_event',**kw),'r','cap',items,batch=100)
    assert out['sent']==200 and 'BusinessRuntimeError' in out['error']
    assert all(len(b['events'])<=100 and b['run_id']=='r' for b in seen)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_run_events.py -q`
Expected: FAIL，`ModuleNotFoundError: dsherp.run_events`。

- [ ] **Step 3: 实现**

```python
"""Runner/worker side event records. Mirrors frappe_app/dsherp_bridge/context_events.py constants."""
import json

RUNNER_KINDS=('runtime_started','model_request','model_response','model_error','runtime_tool_call','tool_result',
              'tool_error','compaction','turn_end','runtime_failed','container_finished','worker_error')
SECRET_KEYS=frozenset({'capability','capability_hash','api_key','api_secret','token','access_token','password','secret',
                       'platform_grant','authorization','deepseek_api_key','cookie'})
MAX_STRING=2000
MAX_PAYLOAD=8192
MAX_BATCH=200
MAX_ITEMS=50
MAX_DEPTH=6


def sanitize(value,depth=0):
    if depth>MAX_DEPTH:return '…[depth]'
    if isinstance(value,dict):
        return {str(k):sanitize(v,depth+1) for k,v in list(value.items())[:MAX_ITEMS] if str(k).lower() not in SECRET_KEYS}
    if isinstance(value,(list,tuple)):return [sanitize(v,depth+1) for v in list(value)[:MAX_ITEMS]]
    if isinstance(value,str):return value if len(value)<=MAX_STRING else value[:MAX_STRING]+'…[truncated]'
    if isinstance(value,(int,float,bool)) or value is None:return value
    return type(value).__name__


def _text(content):
    """Flatten nested content blocks ({type,text} or {content:[...]}) into one string."""
    if isinstance(content,str):return content
    if isinstance(content,dict):return _text(content.get('text',content.get('content')))
    if isinstance(content,list):return ''.join(_text(block) for block in content)
    return '' if content is None else str(content)


def _arguments(raw):
    if isinstance(raw,str):
        try:return json.loads(raw)
        except ValueError:return {'raw':raw}
    return raw


def from_runtime_events(events,notifications):
    """Shapes per C0 probe: tool/call.data{callId,name,arguments:str}; tool/result.data{message{content[{toolCallId,content,isError}]},error{name,code}}."""
    items=[];compactions=0
    for event in events:
        kind=event.get('type') or '';data=event.get('data') if isinstance(event.get('data'),dict) else {};seq=event.get('seq')
        if kind=='tool/call':
            items.append({'kind':'runtime_tool_call','source':'runner','payload':sanitize({'seq':seq,'call_id':data.get('callId'),
                'name':data.get('name'),'arguments':_arguments(data.get('arguments'))})})
        elif kind=='tool/result':
            message=data.get('message') if isinstance(data.get('message'),dict) else {}
            blocks=[b for b in (message.get('content') or []) if isinstance(b,dict)]
            error=data.get('error') if isinstance(data.get('error'),dict) else None
            is_error=bool(error) or any(b.get('isError') for b in blocks)
            call_id=next((b.get('toolCallId') for b in blocks if b.get('toolCallId')),(message.get('source') or {}).get('callId'))
            items.append({'kind':'tool_error' if is_error else 'tool_result','source':'runner',
                'error_class':(error or {}).get('name') or ('ToolError' if is_error else None),
                'payload':sanitize({'seq':seq,'call_id':call_id,'text':_text([b.get('content') for b in blocks]),'code':(error or {}).get('code')})})
        elif kind=='turn/end':
            reason=data.get('reason') if isinstance(data.get('reason'),dict) else {}
            items.append({'kind':'turn_end','source':'runner','payload':{'seq':seq,'reason':reason.get('kind')}})
        elif 'compact' in kind:
            compactions+=1
    if compactions:items.append({'kind':'compaction','source':'runner','payload':{'count':compactions}})
    return items


def flush(post,run_id,capability,items,*,batch=100):
    sent=0
    for start in range(0,len(items),batch):
        chunk=items[start:start+batch]
        try:
            post(run_id=run_id,capability=capability,events=chunk)
        except Exception as error:
            return {'sent':sent,'error':type(error).__name__}
        sent+=len(chunk)
    return {'sent':sent,'error':None}
```

C0 证据：通知方法只有 `session.event`、`session.status`，不存在 `compaction/*` 通知，故压缩以事件类型含 `compact` 计数；`notifications` 参数保留但本任务不解析它。若 C2 替身链路中压缩事件的类型名不含 `compact`，以 `tests/test_context_compaction.py` 实测的类型名替换该判断并记入证据。

- [ ] **Step 4: 加入运行时文件清单并重跑相关测试**

`config/runtime-files.json` 在 `"dsherp/context_runner.py",` 之后插入 `"dsherp/run_events.py",`。

Run: `.venv/bin/python -m pytest tests/test_run_events.py tests/test_runtime_revision.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add dsherp/run_events.py config/runtime-files.json tests/test_run_events.py
git commit -m "feat: 运行时事件映射与批量回写"
```

### Task 2.2：runner 在 `finish_run` 之前回写事件，失败路径回写 `runtime_failed`

**Files:**
- Modify: `dsherp/context_runner.py:37-93`
- Test: `tests/test_context_runner.py`

**Interfaces:**
- `monitored_run(runtime,question,session_id,status,*,poll_interval=2,record=None)`：`record(items: list[dict]) -> None` 由 `run_business` 提供；成功与失败都在返回/重抛前调用一次。
- `run_business` 内 `record=lambda items: run_events.flush(lambda **kw:post(client,'record_run_event',**kw),cap['run_id'],cap['capability'],items)`；`flush` 的返回值若 `error` 非空，向 stderr 打印 `DSHERP_DIAGNOSTIC {"type":"EventFlushFailed","error":...}`（不影响结果）。

- [ ] **Step 1: 写失败测试（追加到 tests/test_context_runner.py）**

```python
def test_monitored_run_records_tool_and_turn_events_before_returning(model_server,tmp_path):
    import json
    settings,requests,state=model_server
    state['tool_call']={'name':'skill','arguments':json.dumps({'name':'erp-query'})}
    recorded=[]
    with open_runtime(settings,tmp_path,'events-run',resume=False) as runtime:
        result=monitored_run(runtime,'hello','events-run',lambda:'Running',record=recorded.append)
    assert result['status']=='Succeeded'
    kinds=[item['kind'] for batch in recorded for item in batch]
    assert 'runtime_tool_call' in kinds and kinds[-1]=='turn_end',kinds
    assert settings['DEEPSEEK_API_KEY'] not in json.dumps(recorded)


def test_monitored_run_records_runtime_failed_when_model_never_completes(model_server,tmp_path):
    settings,requests,state=model_server
    state['finish_reason']='length';state['content']=''
    recorded=[]
    with open_runtime(settings,tmp_path,'failed-run',resume=False) as runtime:
        with pytest.raises(RuntimeError):
            monitored_run(runtime,'hello','failed-run',lambda:'Running',record=recorded.append)
    items=[item for batch in recorded for item in batch]
    assert items[-1]['kind']=='runtime_failed' and items[-1]['error_class']=='RuntimeError'
    assert set(items[-1]['payload'])=={'type','frames'}
```

- [ ] **Step 2: 确认失败** → `TypeError: unexpected keyword argument 'record'`。

- [ ] **Step 3: 实现**

`context_runner.py` 顶部加 `from dsherp import run_events`。`monitored_run` 改为：

```python
def monitored_run(runtime,question,session_id,status,*,poll_interval=2,record=None):
    notifications=[]
    def emit(items):
        if record and items:
            try:record(items)
            except Exception as error:print('DSHERP_DIAGNOSTIC '+json.dumps({'type':'EventRecordFailed','error':type(error).__name__}),file=sys.stderr)
    def check():
        value=status()
        if value not in ('Running','Cancelling'):raise RuntimeError('Unexpected business run status')
        return value
    def cancel():
        result=runtime.client.request('dsherp/session/cancel',{'sessionId':session_id},response_model=Cancelled)
        if result.sessionId!=session_id or result.status!='idle':raise RuntimeError('Native cancellation did not settle')
    if check()=='Cancelling':return {'status':'Cancelled','answer':''}
    emit([{'kind':'runtime_started','source':'runner','payload':{'session_id':session_id}}])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(runtime.run,question,session_id=session_id,on_notification=notifications.append)
        try:
            while True:
                try:
                    result=future.result(timeout=poll_interval);break
                except FutureTimeout:
                    if future.done():raise
                if check()=='Cancelling':
                    cancel();future.result(timeout=5)
                    emit([{'kind':'turn_end','source':'runner','payload':{'reason':'cancelled'}}])
                    return {'status':'Cancelled','answer':''}
            emit(run_events.from_runtime_events(result.events,notifications))
            if check()=='Cancelling':return {'status':'Cancelled','answer':''}
            if result.finish_reason!='completed' or not result.final_response.strip():
                raise RuntimeError('Native Agent did not complete with an answer')
            return {'status':'Succeeded','answer':result.final_response.strip()}
        except BaseException as error:
            emit([{'kind':'runtime_failed','source':'runner','error_class':type(error).__name__,'payload':failure_diagnostic(error)}])
            cancel()
            raise
```

`run_business` 中把 `return monitored_run(runtime,prompt,config['native_session_id'],status)` 改为：

```python
                def record(items):
                    out=run_events.flush(lambda **kw:post(client,'record_run_event',**kw),cap['run_id'],cap['capability'],items)
                    if out['error']:print('DSHERP_DIAGNOSTIC '+json.dumps({'type':'EventFlushFailed','error':out['error']}),file=sys.stderr)
                return monitored_run(runtime,prompt,config['native_session_id'],status,record=record)
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_context_runner.py tests/test_context_runtime.py tests/test_model_guard.py -q`
Expected: PASS（含既有取消/撤销用例）。

- [ ] **Step 5: 提交**

```bash
git add dsherp/context_runner.py tests/test_context_runner.py
git commit -m "feat: runner 在结束前回写模型侧事件"
```

### Task 2.3：宿主 worker JSON 行日志与容器结束/失败事件

**Files:**
- Create: `dsherp/worker_log.py`
- Modify: `dsherp/context_worker.py:33-90`
- Test: `tests/test_worker_log.py`、`tests/test_context_worker.py`

**Interfaces:**
- Produces: `worker_log.log(event: str, **fields) -> None`（stderr 一行 JSON：`{"ts": ISO8601, "event": ..., 其余字段}`）；`worker_log.redactor(values: list[str]) -> Callable[[dict],dict]`（把任何等于凭证值的字符串替换为 `[redacted]`）；`worker_log.configure(secrets: list[str])` 设置全局脱敏。
- `run_once` 在 `finish_run` 之前调用 `post(client,'record_run_event',...)` 一次：成功为 `container_finished {'duration_ms','status'}`，失败为 `runtime_failed {'error_class','duration_ms'}`；回写失败只记日志。

- [ ] **Step 1: 写失败测试**

`tests/test_worker_log.py`：

```python
import json
from dsherp import worker_log


def test_log_writes_one_json_line_with_redacted_secrets(capsys):
    worker_log.configure(['sk-synthetic-secret'])
    worker_log.log('claimed',run_id='r1',note='key=sk-synthetic-secret',count=2)
    line=capsys.readouterr().err.strip().splitlines()[-1]
    record=json.loads(line)
    assert record['event']=='claimed' and record['run_id']=='r1' and record['count']==2
    assert 'sk-synthetic-secret' not in line and '[redacted]' in record['note']
    assert set(record)>={'ts','event'}
```

`tests/test_context_worker.py` 追加：

```python
def test_worker_records_container_outcome_before_finishing(tmp_path):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append((method,json.loads(request.content)))
        if method=='claim_run':return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'d'*64,'capability':'cap'}})
        return httpx.Response(200,json={'message':{'recorded':1,'last_seq':9} if method=='record_run_event' else {'status':'Failed'}})
    def execute(task,settings,directory):raise RuntimeError('boom')
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=execute)
    assert [m for m,_ in calls]==['claim_run','record_run_event','finish_run']
    event=calls[1][1]['events'][0]
    assert event['kind']=='runtime_failed' and event['error_class']=='RuntimeError' and event['source']=='worker'
    assert 'boom' not in json.dumps(calls)
    assert calls[-1][1]['status']=='Failed'


def test_event_writeback_failure_does_not_change_the_run_result(tmp_path,capsys):
    calls=[]
    def handler(request):
        method=request.url.path.rsplit('.',1)[-1];calls.append(method)
        if method=='record_run_event':return httpx.Response(503)
        return httpx.Response(200,json={'message':{'run_id':'r','scope_id':'e'*64,'capability':'cap'} if method=='claim_run' else {'status':'Succeeded'}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(handler)) as client:
        assert run_once(client,SETTINGS,tmp_path,execute=lambda *a:{'status':'Succeeded','answer':'ok'})
    assert calls==['claim_run','record_run_event','finish_run']
    assert 'event_writeback_failed' in capsys.readouterr().err
```

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError: dsherp.worker_log`；worker 用例 `record_run_event` 缺失。

- [ ] **Step 3: 实现 `worker_log.py`**

```python
"""One JSON line per worker event on stderr; credential values never appear."""
import json,sys
from datetime import datetime,timezone
_SECRETS=[]


def configure(secrets):
    _SECRETS[:]=[s for s in secrets if isinstance(s,str) and s]


def _redact(value):
    if isinstance(value,str):
        for secret in _SECRETS:value=value.replace(secret,'[redacted]')
        return value
    if isinstance(value,dict):return {k:_redact(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_redact(v) for v in value]
    return value


def log(event,**fields):
    record={'ts':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),'event':event,**_redact(fields)}
    print(json.dumps(record,ensure_ascii=False,default=str),file=sys.stderr,flush=True)
```

- [ ] **Step 4: 改 `context_worker.py`**

顶部 `from dsherp import worker_log`。`main()` 里 `settings=load_settings(args.provider_env)` 之后加 `worker_log.configure([settings['DEEPSEEK_API_KEY'],profile['api_secret']])`。

`run_once` 改为：

```python
def run_once(client,settings,state_root,*,business=None,execute=run_container):
    task=post(client,'claim_run',runtime_revision=configuration_revision(settings))
    if task is None:return False
    cap={key:task[key] for key in ('run_id','capability')}
    worker_log.log('claimed',run_id=cap['run_id'])
    started=time.monotonic()
    def writeback(items):
        try:post(client,'record_run_event',**cap,events=items)
        except Exception as error:worker_log.log('event_writeback_failed',run_id=cap['run_id'],error_class=type(error).__name__)
    try:
        scope=task.get('scope_id')
        if not isinstance(scope,str) or not re.fullmatch('[a-f0-9]{64}',scope):
            raise ValueError('Invalid server session scope')
        result=execute({**task,**(business or {}),'resume':'inspect'},settings,Path(state_root)/scope)
    except Exception as exc:
        duration=int((time.monotonic()-started)*1000)
        worker_log.log('runtime_failed',run_id=cap['run_id'],error_class=type(exc).__name__,duration_ms=duration)
        writeback([{'kind':'runtime_failed','source':'worker','error_class':type(exc).__name__,'payload':{'duration_ms':duration}}])
        post(client,'finish_run',**cap,status='Failed',error='业务运行失败：'+type(exc).__name__)
        return True
    duration=int((time.monotonic()-started)*1000)
    worker_log.log('container_finished',run_id=cap['run_id'],status=result['status'],duration_ms=duration)
    writeback([{'kind':'container_finished','source':'worker','payload':{'duration_ms':duration,'status':result['status']}}])
    # Never retry a model run or overwrite an ambiguous finish response.
    post(client,'finish_run',**cap,**result)
    return True
```

`poll_once` 与 `run_container` 中的 `print(json.dumps({...}),file=sys.stderr)` 全部改为 `worker_log.log('worker_error',error_class=...,status_code=...)` / `worker_log.log('runtime_diagnostic',**diagnostic)`。

- [ ] **Step 5: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_worker_log.py tests/test_context_worker.py -q`
Expected: PASS（既有 `test_worker_poll_survives_...` 断言 stderr 含 `ReadError` 与 `500` 仍成立）。

- [ ] **Step 6: 提交**

```bash
git add dsherp/worker_log.py dsherp/context_worker.py tests/test_worker_log.py tests/test_context_worker.py
git commit -m "feat: worker 结构化日志与容器结果事件"
```

### Task 2.4：model-guard 回写 `model_response` / `model_error`

**Files:**
- Modify: `runtime/model-guard.cjs:7-25,64-75`
- Test: `runtime/model-guard.test.cjs`

**Interfaces:**
- `createGuard(authorize, check=()=>{}, report=async()=>{})`：`report({kind:'model_response'|'model_error', payload, error_class?})` 在 finish 后 / 出错时各调用一次，永不影响流；`apply` 中的 `report` 把记录 POST 到 `record_run_event`（5 秒超时，失败静默）。`model_response.payload = {usage: chunk.usage ?? null, chunk_keys: Object.keys(chunk), purpose, model}`。C0 证据：根会话事件不携带 usage，因此 finish chunk 是唯一可能的来源；`chunk_keys` 只记键名不记值，用于在 C2 证据中确认固定 Runtime 是否提供 usage，禁止估算。

- [ ] **Step 1: 写失败测试（追加）**

```js
test('finish and errors are reported without affecting the stream',async()=>{
  const reports=[];
  const guard=createGuard(async()=>{},()=>{},async r=>{reports.push(r);throw new Error('sink down');});
  const next=async function*(){yield {type:'chunk'};yield {type:'finish',usage:{input:3,output:4}};};
  const delivered=[];for await(const item of guard(request,next))delivered.push(item);
  assert.equal(delivered.length,2);
  assert.deepEqual(reports.map(r=>r.kind),['model_response']);
  assert.deepEqual(reports[0].payload.usage,{input:3,output:4});assert.deepEqual(reports[0].payload.chunk_keys,['type','usage']);
  const failing=createGuard(async()=>{},()=>{},async r=>{reports.push(r);});
  await assert.rejects(consume(failing(request,async function*(){throw new Error('provider down');})),/provider down/);
  assert.equal(reports.at(-1).kind,'model_error');assert.equal(reports.at(-1).error_class,'Error');
  assert.ok(!JSON.stringify(reports).includes('provider down'));
});
```

- [ ] **Step 2: 确认失败**

Run: `node --test runtime/model-guard.test.cjs`
Expected: FAIL，`reports` 为空。

- [ ] **Step 3: 实现**

`createGuard`：

```js
function createGuard(authorize,check=()=>{},report=async()=>{}){
  let disabled=false;
  const safeReport=async record=>{try{await report(record);}catch{}};
  return async function*(options,next){
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      const input_bytes=Buffer.byteLength(JSON.stringify({system:options.system,messages:options.messages,tools:options.tools}),'utf8');
      await authorize({input_bytes,max_output_tokens:options.maxTokens,provider:options.provider,model:options.model,purpose:options.purpose??'conversation'});
    }catch(error){disabled=true;throw error;}
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      for await(const chunk of next()){
        if(chunk.type==='finish'){check();await safeReport({kind:'model_response',payload:{usage:chunk.usage??null,chunk_keys:Object.keys(chunk),purpose:options.purpose??'conversation',model:options.model}});}
        yield chunk;
      }
      check();
    }catch(error){disabled=true;await safeReport({kind:'model_error',error_class:error?.name??'Error',payload:{purpose:options.purpose??'conversation'}});throw error;}
  };
}
```

`chunk.usage` 的取法按 T0.1 证据调整（若 usage 在别的键或事件里，此处改为相应表达式，并在证据文档注明）。

`apply` 中：

```js
  const recordEndpoint=new URL('/api/method/dsherp_bridge.context_execution.record_run_event',config.business_url);
  const report=async record=>{
    await fetch(recordEndpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(5000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,events:[{...record,source:'runner'}]})});
  };
  ctx.on('llm/stream',createGuard(async metadata=>{ ...原有 authorize 体不变... },check,report));
```

- [ ] **Step 4: 运行测试**

Run: `node --test runtime/model-guard.test.cjs && .venv/bin/python -m pytest tests/test_model_guard.py tests/test_context_compaction.py tests/test_runtime_revision.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add runtime/model-guard.cjs runtime/model-guard.test.cjs
git commit -m "feat: 模型调用结果与错误回写事件流"
```

**C2 放行标准**：`.venv/bin/python -m pytest tests --ignore=tests/integration -q` 全绿且收集数比基线（130）增加 ≥ 7；替身链路中至少一条 `model_response` 事件的 `chunk_keys` 写入证据文档并给出 usage 有无的结论；`node --test runtime/*.test.cjs` 全绿；用替身跑一次 `tests/integration/test_context_worker_chain.py` 后，该运行的 `list_run_events` 含 `queued, claimed, runtime_started, model_call_reserved, runtime_tool_call, tool_call, model_response, turn_end, container_finished, finished`（顺序按 seq，缺哪一类要说明原因）；worker 的 stderr（`~/Library/Logs/dsherp-agent-worker-v16.log`）每行可被 `json.loads`，grep provider key 为 0。

---

## 阶段 3（C3）：运维快照、指标与告警

### Task 3.1：Frappe 运维快照 `ops.collect_snapshot` 与 `ops_status`

**Files:**
- Create: `frappe_app/dsherp_bridge/ops.py`
- Create: `frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_ops_snapshot/{__init__.py,ds_ops_snapshot.json,ds_ops_snapshot.py}`
- Modify: `frappe_app/dsherp_bridge/hooks.py`（新增 `scheduler_events`）
- Test: `tests/integration/test_ops_snapshot.py`

**Interfaces:**
- Produces: `ops.collect_snapshot() -> dict`（同时写一条 `DS Ops Snapshot`：字段 `collected_at`、`payload`），键固定：`queued`、`queued_oldest_seconds`、`running`、`running_stuck`（Running/Cancelling 且 `expires_at` 已过）、`pending_proposals_expired`、`last_claim_age_seconds`（最近一条 `claimed` 事件距今；无则 null）、`runs_24h: {status: count}`、`backup_age_hours`（`sites/<site>/private/backups` 下最新 `*-database.sql*` 距今；无则 null）、`site`。
- `ops_status()`：whitelisted GET，仅 `frappe.conf.dsherp_runtime_user` 或 System Manager 可调，返回最新快照 payload（无快照则即时 `collect_snapshot()`）。
- hooks：`scheduler_events={"cron":{"*/5 * * * *":["dsherp_bridge.ops.collect_snapshot"]}}`。

- [ ] **Step 1: 写失败集成测试**：创建一条 `expires_at` 过去 1 分钟的 Running 运行与一条 Queued 运行，调用 `collect_snapshot()`，断言 `running_stuck==1`、`queued>=1`、`queued_oldest_seconds>=0`、`'backup_age_hours' in snapshot`；再以 `dsherp_runtime_user` 调用 `ops_status()` 得到同样键；以普通合成用户调用抛 `PermissionError`。清理同 T1.x（先删事件再删运行）。

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError: dsherp_bridge.ops`。

- [ ] **Step 3: 实现 `ops.py`**

```python
"""Five-minute operational snapshot for the worker and administrators. Measures only; recovery belongs to plan 2."""
import json,os,time
from pathlib import Path
import frappe
from frappe.utils import now_datetime,get_datetime,add_to_date

def _age_seconds(value):
    return None if not value else max(0,int((now_datetime()-get_datetime(value)).total_seconds()))

def _backup_age_hours():
    directory=Path(frappe.get_site_path('private','backups'))
    files=sorted(directory.glob('*-database.sql*'),key=lambda p:p.stat().st_mtime) if directory.exists() else []
    return None if not files else round((time.time()-files[-1].stat().st_mtime)/3600,2)

def collect_snapshot():
    now=now_datetime()
    queued=frappe.get_all('DS Model Run',filters={'status':'Queued'},fields=['creation'],order_by='creation asc')
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']]},fields=['expires_at'])
    since=add_to_date(now,hours=-24)
    counts={}
    for row in frappe.get_all('DS Model Run',filters={'creation':['>=',since]},fields=['status','count(name) as n'],group_by='status'):
        counts[row.status]=row.n
    claimed=frappe.get_all('DS Run Event',filters={'kind':'claimed'},fields=['recorded_at'],order_by='recorded_at desc',limit_page_length=1)
    snapshot={'site':frappe.local.site,'collected_at':str(now),'queued':len(queued),
        'queued_oldest_seconds':_age_seconds(queued[0].creation) if queued else None,
        'running':len(active),'running_stuck':sum(1 for r in active if r.expires_at and get_datetime(r.expires_at)<=now),
        'pending_proposals_expired':frappe.db.count('DS Operation Proposal',{'status':'Pending','expires_at':['<=',now]}),
        'last_claim_age_seconds':_age_seconds(claimed[0].recorded_at) if claimed else None,
        'runs_24h':counts,'backup_age_hours':_backup_age_hours()}
    frappe.get_doc({'doctype':'DS Ops Snapshot','collected_at':now,'payload':json.dumps(snapshot,ensure_ascii=False)}).insert(ignore_permissions=True)
    frappe.db.commit()
    return snapshot

@frappe.whitelist(methods=['GET'])
def ops_status():
    user=frappe.session.user
    if user!=frappe.conf.get('dsherp_runtime_user') and 'System Manager' not in frappe.get_roles(user):
        raise frappe.PermissionError('需要运行服务身份或系统管理员')
    latest=frappe.get_all('DS Ops Snapshot',fields=['payload'],order_by='collected_at desc',limit_page_length=1)
    return json.loads(latest[0].payload) if latest else collect_snapshot()
```

`DS Ops Snapshot` JSON：`autoname: hash`，字段 `collected_at`（Datetime，reqd，search_index）、`payload`（Long Text）；`permissions: []`；py 仅 `pass`（快照可被计划 4 的保留策略清理，不加 on_trash）。`collect_snapshot` 末尾另删除 7 天前的快照：`frappe.db.delete('DS Ops Snapshot',{'collected_at':['<',add_to_date(now,days=-7)]})`。

- [ ] **Step 4: 注册 scheduler 并验证**

`hooks.py` 末尾加 `scheduler_events={"cron":{"*/5 * * * *":["dsherp_bridge.ops.collect_snapshot"]}}`。

Run: `docker exec dsherp-validation-backend-1 bench --site dsherp-validation.localhost migrate && .venv/bin/python -m pytest tests/integration/test_ops_snapshot.py -q`
Expected: PASS。再执行一次 `docker compose -f infra/compose.validation.yml -p dsherp-validation --profile scheduled up -d` 并在 5 分钟后核对 `DS Ops Snapshot` 新增一条、`Scheduled Job Log` 无 Failed。

- [ ] **Step 5: 提交**：`git commit -m "feat: 五分钟运维快照与运行服务只读状态"`。

### Task 3.2：worker 指标 `/metrics`

**Files:**
- Create: `dsherp/metrics.py`
- Modify: `dsherp/context_worker.py`（`main` 启动 HTTP 线程；`run_once` 计数）
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Registry` 类：`counter(name, help, labels=())`、`gauge(name, help)`、`histogram(name, help, buckets)`；`Registry.render() -> str`（Prometheus 文本 0.0.4）；`serve(registry, port) -> http.server.ThreadingHTTPServer`（绑定 `127.0.0.1`，`GET /metrics`）。
- 指标名固定：`dsherp_claims_total`、`dsherp_runs_total{status}`、`dsherp_run_duration_seconds`（buckets 5,15,30,60,120,300）、`dsherp_worker_errors_total{error_class}`、`dsherp_consecutive_run_failures`、`dsherp_queue_depth`、`dsherp_running_stuck`、`dsherp_backup_age_hours`、`dsherp_orphan_containers`、`dsherp_last_claim_timestamp_seconds`、`dsherp_provider_call_failures_total`。
- profile 新增可选键 `metrics_port`（默认 9109）；`--once` 模式不起 HTTP。

- [ ] **Step 1: 写失败测试**：`Registry` 渲染包含 `# TYPE dsherp_runs_total counter`、`dsherp_runs_total{status="Succeeded"} 2`、直方图 `_bucket{le="+Inf"}`/`_sum`/`_count` 行；`serve` 后 `httpx.get('http://127.0.0.1:%d/metrics')` 返回 200 且 `content-type` 以 `text/plain` 开头；`/other` 返回 404。

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError`。

- [ ] **Step 3: 实现**：无第三方依赖，`Registry` 内部用 `dict` + `threading.Lock`；直方图为累计桶；`serve` 用 `ThreadingHTTPServer` + daemon 线程，`log_message` 静默。worker：`main` 在进入循环前 `metrics.serve(REGISTRY, profile.get('metrics_port',9109))`（非 `--once`）；`run_once` 里 `claimed` 时 `dsherp_claims_total+1`、结束时 `dsherp_runs_total{status}` 与 `dsherp_run_duration_seconds.observe(duration/1000)`；`poll_once` 捕获处 `dsherp_worker_errors_total{error_class}`；连续失败计数在结果为 Failed 时 +1、Succeeded/Cancelled 时清零。

- [ ] **Step 4: 运行** `.venv/bin/python -m pytest tests/test_metrics.py tests/test_context_worker.py -q` → PASS。

- [ ] **Step 5: 提交**：`git commit -m "feat: worker 暴露 Prometheus 指标"`。

### Task 3.3：告警规则、去重与输出（含孤儿容器与运维快照同步）

**Files:**
- Create: `dsherp/alerts.py`
- Modify: `dsherp/context_worker.py`（每 60 秒拉一次 `ops_status`，刷新 gauge，求值告警）
- Test: `tests/test_alerts.py`、`tests/test_context_worker.py`

**Interfaces:**
- Produces: `evaluate(snapshot: dict|None, metrics: dict, now: float) -> list[Alert]`，`Alert=namedtuple('Alert','key severity message')`；`Notifier(sink, webhook=None, cooldown=600)` 的 `emit(alerts, now)` 去重（同 key 冷却期内不重发）并输出 `worker_log.log('alert', key=..., severity=..., message=...)`，可选 POST webhook（JSON，5 秒超时，失败只记日志）；`orphan_containers(runner=subprocess.run) -> int`（`docker ps --filter name=dsherp-context- --format {{.Names}}` 行数，仅在没有在飞运行时计入）。
- 规则（阈值为常量，写在 `alerts.py` 顶部）：`consecutive_run_failures>=3` → `provider_or_runtime_failing`（critical）；`queue_depth>5 or queued_oldest_seconds>600` → `queue_backlog`（warning）；`running_stuck>0` → `run_stuck`（critical）；`backup_age_hours is None or >26` → `backup_stale`（critical）；`orphan_containers>0` → `orphan_containers`（warning）；`last_claim_age_seconds>120 and queue_depth>0` → `worker_not_claiming`（critical）；`snapshot is None`（拉取失败） → `ops_status_unavailable`（warning）。
- profile 可选键 `alert_webhook`。

- [ ] **Step 1: 写失败测试**（`tests/test_alerts.py`）

```python
import json,subprocess
from dsherp import alerts

BASE={'queued':0,'queued_oldest_seconds':None,'running_stuck':0,'backup_age_hours':1.0,'last_claim_age_seconds':10}
METRICS={'consecutive_run_failures':0,'orphan_containers':0}

def keys(snapshot=BASE,metrics=METRICS,now=1000.0):
    return sorted(a.key for a in alerts.evaluate(snapshot,metrics,now))

def test_rules_fire_only_on_their_condition():
    assert keys()==[]
    assert keys(metrics={**METRICS,'consecutive_run_failures':3})==['provider_or_runtime_failing']
    assert keys({**BASE,'queued':6})==['queue_backlog'] and keys({**BASE,'queued':1,'queued_oldest_seconds':601})==['queue_backlog']
    assert keys({**BASE,'running_stuck':1})==['run_stuck']
    assert keys({**BASE,'backup_age_hours':None})==['backup_stale'] and keys({**BASE,'backup_age_hours':27})==['backup_stale']
    assert keys(metrics={**METRICS,'orphan_containers':2})==['orphan_containers']
    assert keys({**BASE,'queued':1,'last_claim_age_seconds':121})==['worker_not_claiming']
    assert keys(None)==['ops_status_unavailable']

def test_notifier_deduplicates_within_cooldown_and_posts_webhook(capsys):
    import httpx
    posted=[]
    transport=httpx.MockTransport(lambda r:(posted.append(json.loads(r.content)),httpx.Response(503))[1])
    notifier=alerts.Notifier(webhook='http://hook.invalid/x',cooldown=600,client=httpx.Client(transport=transport))
    alert=alerts.Alert('run_stuck','critical','1 个运行卡住')
    notifier.emit([alert],now=0);notifier.emit([alert],now=100);notifier.emit([alert],now=700)
    lines=[json.loads(l) for l in capsys.readouterr().err.strip().splitlines() if '"alert"' in l]
    assert [l['key'] for l in lines]==['run_stuck','run_stuck'] and len(posted)==2 and posted[0]['key']=='run_stuck'

def test_orphan_containers_counts_docker_ps_lines():
    fake=lambda *a,**k:subprocess.CompletedProcess(a,0,stdout='dsherp-context-a\ndsherp-context-b\n',stderr='')
    assert alerts.orphan_containers(runner=fake)==2
````test_context_worker.py` 加：`poll_once` 循环中每 60 秒调用一次 `ops_status`（用可注入时钟），拉取失败产出 `ops_status_unavailable` 告警行。

- [ ] **Step 2: 确认失败**。

- [ ] **Step 3: 实现**（`evaluate` 为纯函数；`Notifier` 持 `dict[key]->last_emit`）；worker `main` 循环：`if now-last_ops>=60: snapshot=fetch_ops(client); metrics 刷新; notifier.emit(evaluate(snapshot,REGISTRY.values(),now),now)`，其中 `fetch_ops` 用 `client.get('/api/method/dsherp_bridge.ops.ops_status')`，任何异常返回 None。

- [ ] **Step 4: 运行** `.venv/bin/python -m pytest tests/test_alerts.py tests/test_context_worker.py tests/test_metrics.py -q` → PASS。

- [ ] **Step 5: 提交**：`git commit -m "feat: worker 规则化告警与孤儿容器检测"`。

**C3 放行标准**：重启 LaunchAgent 后 `curl -s 127.0.0.1:9109/metrics | grep -c '^dsherp_'` ≥ 10；三项注入各在 5 分钟内出现 `"event": "alert"` 行：(a) 把 provider env 文件的 `DEEPSEEK_BASE_URL` 临时指到 `http://127.0.0.1:9`（worker 每轮重读 .env）后连发 3 条消息 → `provider_or_runtime_failing`；(b) `docker run -d --name dsherp-context-orphan-test alpine sleep 600` → `orphan_containers`（随后 `docker rm -f` 清理）；(c) 停 worker 后发 1 条消息等 2 分钟再启 worker → `worker_not_claiming`；恢复 .env 后 alpha 站正常运行 1 次成功。证据（告警行、时间戳）写入 `observability-evidence.md`。

---

## 阶段 4（C4）：管理员审计视图、前端事件流、评估集导出、G6 演练

### Task 4.1：管理员审计报表 `DS Agent Audit`

**Files:**
- Create: `frappe_app/dsherp_bridge/dsherp_bridge/report/__init__.py`、`.../report/ds_agent_audit/{__init__.py,ds_agent_audit.json,ds_agent_audit.py}`
- Modify: `frappe_app/pyproject.toml`（package-data 加 `dsherp_bridge/report/**/*.json`）
- Test: `tests/integration/test_agent_audit_report.py`

**Interfaces:**
- Produces: Script Report，`execute(filters) -> (columns, data)`；filters：`from_date`、`to_date`、`user`（可空）、`status`（可空）；每行：运行 `name`、`owner`、`creation`、`domain`、`status`、`model_calls`、`proposals`（该运行产生的提案数）、`executions`（Succeeded 执行记录数）、`events`（事件数）、`error`（前 80 字）。`roles: [System Manager]`，`is_standard: Yes`，`report_type: Script Report`，`ref_doctype: DS Model Run`。

- [ ] **Step 1: 写失败集成测试**：以 Administrator 创建两个合成用户各一条运行（一条 Failed），调用 `frappe.desk.query_report.run('DS Agent Audit', filters={'from_date': today, 'to_date': today})`，断言两条运行都在 `result` 且 Failed 行 `error` 非空；再以其中一个普通用户调用同一报表抛 `PermissionError`。

- [ ] **Step 2: 确认失败** → 报表不存在。

- [ ] **Step 3: 实现 `ds_agent_audit.py`**

```python
import frappe
from frappe.utils import get_datetime

def execute(filters=None):
    filters=filters or {}
    conditions={'creation':['between',[get_datetime(filters.get('from_date')),get_datetime(filters.get('to_date'))]]}
    if filters.get('user'):conditions['owner']=filters['user']
    if filters.get('status'):conditions['status']=filters['status']
    runs=frappe.get_all('DS Model Run',filters=conditions,fields=['name','owner','creation','domain','status','model_calls','error'],order_by='creation desc',limit_page_length=2000)
    names=[r.name for r in runs] or ['']
    def counts(doctype,field,extra=None):
        rows=frappe.get_all(doctype,filters={field:['in',names],**(extra or {})},fields=[field,'count(name) as n'],group_by=field)
        return {row[field]:row.n for row in rows}
    proposals=counts('DS Operation Proposal','model_run');events=counts('DS Run Event','run')
    executed={}
    for row in frappe.get_all('DS Execution Record',filters={'status':'Succeeded'},fields=['proposal']):
        run=frappe.db.get_value('DS Operation Proposal',row.proposal,'model_run')
        if run in names:executed[run]=executed.get(run,0)+1
    columns=[{'fieldname':f,'label':f,'fieldtype':t,'width':w} for f,t,w in (('name','Data',260),('owner','Data',200),('creation','Datetime',160),
        ('domain','Data',90),('status','Data',90),('model_calls','Int',80),('proposals','Int',80),('executions','Int',80),('events','Int',80),('error','Data',300))]
    data=[{**r,'proposals':proposals.get(r.name,0),'executions':executed.get(r.name,0),'events':events.get(r.name,0),'error':(r.error or '')[:80]} for r in runs]
    return columns,data
```

`ds_agent_audit.json`：`{"doctype":"Report","name":"DS Agent Audit","report_name":"DS Agent Audit","report_type":"Script Report","ref_doctype":"DS Model Run","module":"DSHERP Bridge","is_standard":"Yes","roles":[{"role":"System Manager"}]}`。`DS Model Run` 的 `permissions: []` 会让原生报表入口拒绝非 System Manager；System Manager 需要对 `DS Model Run` 有 read 权限才能打开报表，若 `query_report.run` 因此抛权限错，在 `ds_model_run.json` 增加 `{"role":"System Manager","read":1}`（只读，不给 write/delete），并把该变更写进证据文档。

- [ ] **Step 4: 运行** `docker exec ... bench --site dsherp-validation.localhost migrate && .venv/bin/python -m pytest tests/integration/test_agent_audit_report.py -q` → PASS。

- [ ] **Step 5: 提交**：`git commit -m "feat: 系统管理员跨用户 Agent 审计报表"`。

### Task 4.2：前端事件流（按需加载，不进 `_public`）

**Files:**
- Modify: `frontend/src/context-api.js`（新增 `listRunEvents(runId,page=1)`）
- Modify: `frontend/src/agent-transcript.js`（新增 `runEventRows(events)`）
- Modify: `frontend/src/AgentRecords.jsx`（每条消息下"事件流"折叠面板，展开时加载）
- Test: `frontend/src/agent-transcript.test.js`、`frontend/src/AgentRecords.test.jsx`

**Interfaces:**
- `runEventRows(events) -> [{seq, time, label, detail, tone}]`：`label` 映射表——`queued`→"已排队"、`claimed`→"已领取"、`runtime_started`→"运行时启动"、`model_call_reserved`→"模型调用 #n"、`model_response`→"模型返回"、`model_error`→"模型错误"、`runtime_tool_call`→"调用工具 <name>"、`tool_call`→"服务端执行 <tool>（n ms）"、`tool_result`→"工具返回"、`tool_error`→"工具错误"、`compaction`→"上下文压缩"、`turn_end`→"回合结束（reason）"、`runtime_failed`→"运行失败（error_class）"、`container_finished`→"容器结束（status）"、`finished`→"运行结束（status）"、`expired`→"运行过期"、`cancel_requested`→"已请求取消"、`worker_error`→"worker 错误"；未知 kind 原样显示。`tone`：错误类为 `danger`，其余 `default`。

- [ ] **Step 1: 写失败测试**：`runEventRows` 对上述每类各一条断言 label；`AgentRecords` 渲染一条消息，点击"事件流"后调用 `listRunEvents` 一次并显示行；接口失败显示服务端错误文本而非白屏（用 `vi.fn` 模拟 reject）。

- [ ] **Step 2: 确认失败**：`cd frontend && npm test -- agent-transcript AgentRecords` 红。

- [ ] **Step 3: 实现**：`context-api.js` 复用既有 `apiFetch`（GET `dsherp_bridge.context_api.list_run_events`）；`AgentRecords.jsx` 用 antd `Collapse`，`onChange` 首次展开时 `useState` 记录 `{loading,error,rows}`；错误信息直接显示 `error.message`。

- [ ] **Step 4: 运行并重建产物**

Run: `cd frontend && npm test && node build.mjs && cd .. && .venv/bin/python -m pytest tests/test_desk_assets.py -q`
Expected: 全绿；`git status` 显示 `frappe_app/dsherp_bridge/public/dist/*` 变更。

- [ ] **Step 5: 提交**：`git add frontend/src frappe_app/dsherp_bridge/public/dist && git commit -m "feat: 工作台按需展示运行事件流"`。

### Task 4.3：失败运行导出为评估用例

**Files:**
- Create: `dsherp/eval_cases.py`（纯函数 `build_case(run: dict, events: list, proposals: list) -> dict`）
- Create: `infra/export_eval_cases.py`（宿主脚本，经 `docker exec` 只读读取，写 `evals/cases/<site>/<run_id>.json`）
- Create: `evals/README.md`
- Test: `tests/test_eval_cases.py`

**Interfaces:**
- `build_case` 返回 `{'schema_version':1,'site','run_id','domain','question','page_context','status','error','sources','events','proposals','created'}`；对 `question`、`page_context`、`events` 应用 `run_events.sanitize`；`proposals` 只保留 `id/status/summary` 三键。
- `infra/export_eval_cases.py --site dsherp-validation.localhost --container dsherp-validation-backend-1 [--status Failed]`：容器内脚本用 `frappe.get_all` 读运行、`context_events.list_events` 读事件、`get_proposal` 读提案摘要，stdout 输出 JSON 行，宿主逐行写文件；已存在文件跳过（幂等）；结束打印 `{'exported': n, 'skipped': m}`。

- [ ] **Step 1: 写失败测试**（`tests/test_eval_cases.py`）

```python
import json
from dsherp.eval_cases import build_case

def test_build_case_strips_secrets_and_trims_proposals():
    run={'name':'r1','domain':'operation','question':'q','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[],'capability':'S'}),
         'status':'Failed','error':'业务运行失败：RuntimeError','sources':'[]','creation':'2026-09-01 00:00:00','capability_hash':'S','platform_grant':'S'}
    case=build_case(run,[{'kind':'tool_error','payload':{'text':'x','api_secret':'S'}}],[{'id':'p1','status':'Pending','summary':'s','payload':{'api_secret':'S'}}],site='alpha')
    assert case['schema_version']==1 and case['run_id']=='r1' and case['site']=='alpha'
    assert case['proposals']==[{'id':'p1','status':'Pending','summary':'s'}]
    assert 'S' not in json.dumps(case,ensure_ascii=False).replace('schema_version','')
    assert build_case({**run,'name':'r2'},[],[],site='alpha')['events']==[]
```

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError`。

- [ ] **Step 3: 实现** 两个文件；`evals/README.md` 说明：用例来自隔离合成站，真实租户导出必须先经计划 4 的脱敏与授权；`evals/cases/` 入库，`evals/runs/` gitignore（本计划不建断言，断言与运行器属计划 6）。

- [ ] **Step 4: 执行导出**

Run: `.venv/bin/python infra/export_eval_cases.py --site dsherp-validation.localhost --container dsherp-validation-backend-1 --status Failed` 与 daily 站同样一次。
Expected: `exported` 之和等于 T0.2 记录的 Failed 数；`grep -rl "capability\|api_secret\|DEEPSEEK" evals/cases` 为 0。

- [ ] **Step 5: 提交**：`git add dsherp/eval_cases.py infra/export_eval_cases.py evals tests/test_eval_cases.py .gitignore && git commit -m "feat: 失败运行导出为评估用例"`。

### Task 4.4：G6 演练与证据

**Files:**
- Modify: `docs/engineering/observability-evidence.md`

- [ ] **Step 1: 失败回放演练**：在 alpha 站用替身链路（`tests/integration/test_context_mcp_chain.py` 的方式）制造一次工具权限失败（用无 Item 读权限的合成用户提问"读取测试物料"），取该运行 `run_id`，以所有者调用 `list_run_events`，把事件序列（kind、error_class、payload 键名）贴入证据文档，证明能看到 `tool_error` 的文本与 `runtime_failed`/`finished` 的先后。
- [ ] **Step 2: 告警演练**：按 C3 放行标准的三项注入，记录每项从注入到告警行的时间差（须 < 5 分钟）。
- [ ] **Step 3: 报表与前端**：截图 `DS Agent Audit` 跨两个用户的结果与工作台事件流面板，落档 `docs/engineering/evidence/observability/`。
- [ ] **Step 4: 提交**：`git commit -m "docs: 可观测性 G6 演练证据"`。

### Task 4.5：文档收口

- [ ] README"当前状态"加一句：运行事件流、指标与告警已在隔离合成站落地，证据见 observability-evidence.md；测试数字更新为实际收集数。
- [ ] `docs/engineering/runtime-baseline.md` 增加"运行时文件清单变更"节（新增 `dsherp/run_events.py`）。
- [ ] `docs/superpowers/specs/2026-09-03-production-hardening-design.md` 实施顺序表计划 1 行后追加"（2026-xx-xx C4 通过）"。
- [ ] 本计划复选框逐项勾选（勾选前对仓库文件核验），独立 `docs:` 提交。

**C4 放行标准**：`DS Agent Audit` 由审计方以 Administrator 打开并按用户过滤成功；工作台事件流面板对一条真实运行可展开且行数等于 `list_run_events` 返回数；`evals/cases` 数量与 T0.2 基线一致且 grep 密钥为 0；证据文档三段演练齐全；全量门：`.venv/bin/python -m pytest tests --ignore=tests/integration -q` 全绿、`cd frontend && npm test` 全绿、`node --test runtime/*.test.cjs` 全绿、`.venv/bin/python -m pytest tests/integration -q` 全绿（集成全量约 12 分钟）。

## 验证方式汇总

- 非集成：`.venv/bin/python -m pytest tests --ignore=tests/integration -q`
- runtime 插件：`node --test runtime/*.test.cjs`
- 前端：`cd frontend && npm test && node build.mjs`
- 集成：`.venv/bin/python -m pytest tests/integration -q`（需四站运行与 `.runtime/` 凭证）
- 运行态：`curl -s 127.0.0.1:9109/metrics`、LaunchAgent 日志 `~/Library/Logs/dsherp-agent-worker-v16.log`、`DS Ops Snapshot` 列表、`Scheduled Job Log`

## 回退方案

- C1 前：删除分支即可。
- C1–C2：`DS Run Event`/`DS Ops Snapshot` 是新增表，回退 = revert 提交 + `bench migrate`（表保留无害，可后续 `bench --site ... drop-doctype` 无此命令时手工删表，记录在证据）。
- 任何阶段：worker 改动可通过重启到旧 tag 的 LaunchAgent 回退；事件写入失败不阻塞业务，回退无数据风险。

## 需要用户授权/确认的点

1. 本计划不调用真实 provider，不需要费用授权。
2. T4.1 若必须给 System Manager 加 `DS Model Run` 只读权限，属于权限面变更，执行方需在检查点报告中单列，由用户确认。
3. C3 演练会短暂改写 `.env` 的 `DEEPSEEK_BASE_URL`（随后恢复），执行前告知用户，演练期间不发起真实业务请求。
