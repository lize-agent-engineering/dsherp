# 计划 6 证据：Agent 质量与成本

日期：2026-09-08 起。环境：本机隔离合成站（`dsherp-validation.localhost`、`dsherp-daily.localhost`、
原生测试站 `dsherp-test.localhost`），ERPNext `16.33.0` / Frappe `16.31.0`，DSH SDK/Runtime `0.1.1rc1`。
**全部数字来自合成数据；任何环境都未接入真实租户。**

计划：[2026-09-08-agent-quality](../superpowers/plans/2026-09-08-agent-quality.md)。放行门 **G8**。

## 总判定（随切片推进更新）

- 切片 0：**完成**。四项收口、外链域名白名单、两项只读探路。
- 切片 1–6：见下。

---

## 切片 0

### 探路 A：容器内 loopback 回放路径（本计划唯一的停止条件）

**结论：假设成立，停止条件未触发。**

整个切片 2 的回放器建立在一条假设上——替身 provider 可以跑在**运行容器内部**的
`127.0.0.1:38127`，不需要接进 agent 网络、不需要改 `DSHERP_AGENT_PROVIDER_BASE_URL`
（改它会连带改 `deployment_digest`，恰好损害本计划要度量的可复现性）。

证实方式是跑既有的绿色用例（**未修改**）：

```bash
launchctl bootout gui/$(id -u)/com.dsherp.agent-worker-v16
.venv/bin/python -m pytest tests/integration/test_context_mcp_chain.py -m integration -q --timeout 900
launchctl bootstrap gui/$(id -u) .runtime/com.dsherp.agent-worker-v16.plist
```

结果：**2 passed in 127.44s**（`isolated=False` 与 `isolated=True` 两个参数化）。

> 计划原文写的是 `-k isolated`，实测**选不中任何用例**：参数化 id 是 `[True]`/`[False]`，
> 名字里没有 `isolated` 这个词（`tests/integration/test_context_mcp_chain.py:37-38`）。
> 改为跑整个文件，`isolated=True` 那条正是要证的路径。

`isolated=True` 这条同时证明了三件今天可用的事（`test_context_mcp_chain.py:43,72-73,99`）：

| 事实 | 位置 |
|---|---|
| 替身以只读挂载进容器 `command[2:2]=['-v', f'{root}/tests/conftest.py:/run/model_fixture.py:ro']` | `:72` |
| 容器命令末两项换成 `['-c', CONTAINER_TEST]` | `:73` |
| `DEEPSEEK_BASE_URL='http://127.0.0.1:38127/v1'` 在容器内可达 | `:43` |
| 该 run 的 `DS Model Run.model_calls == 4`（两次运行各 2 次调用，出口隔离与 agent 网络一概未动） | `:99` |

### 探路 B：工具输出字节实测

原始测量与方法见 `work/tool-bytes.md`（**不入库**）。这里只留结论与切片 4 要用的数字。
量的是两个数：`json.dumps(..., default=str)` 的字节，以及 `pydantic_core.to_json(result, indent=2)`
的字节——**后者才是模型真正看到的**（FastMCP `_convert_to_content`，
`.venv/…/mcp/server/fastmcp/utilities/func_metadata.py:531`）。下表全部是后者。

**`erp_read_record`**

| 记录 | 全量 | 只留非空 | 不展开子表 | 字段数 / 非空 |
|---|---|---|---|---|
| Item `DSHERP-MFG-SYN-SERVICE` | 3,561 | 2,916 | — | 79 / 58 |
| Sales Order `SAL-ORD-2026-00001`（items 1 行） | 7,042 | 5,763 | 3,468 | 112 / 66 |
| Work Order（回滚事务内临时建） | 2,925 | 2,255 | 1,943 | 60 / 38 |
| Stock Entry（回滚事务内临时建） | 3,874 | 2,973 | 1,860 | 59 / 28 |

每条 Sales Order Item 子表行 **2,179 字节**；同一张单 5 行外推 **15,758**（限内，余量 626），
20 行外推 **48,443**（超 16,384 约 3.0×）。

**`erp_read_schema`**（今天子表总是内联展开）

| DocType | 内联展开（今天） | 不内联子表 | 顶层字段 |
|---|---|---|---|
| Sales Order | **62,674** | 23,102 | 111 |
| Purchase Order | 57,183 | — | 104 |
| Stock Entry | 28,185 | — | 58 |
| Item | 28,002 | — | 79 |
| Work Order | 27,079 | — | 59 |

**对切片 4 的三条结论：**

1. **`read_schema` 才是真正超限的那个，而且「默认不展开子表」还不够。** 五个 DocType 全部超
   16,384（最小的 Work Order 也是 1.65×）；Sales Order 即使不内联子表仍有 23,102（1.41×）。
   ⇒ `after_fieldname` 游标与截断是**主路径**而不是备用路径，测试必须覆盖续读能读全。
2. **`read_record` 的超限来自子表行数而不是字段数。** 不展开子表把 Sales Order 从 7,042 降到
   3,468（-51%）、Stock Entry 从 3,874 降到 1,860（-52%）；「默认只回非空」再省 15–23%。
3. **合成站现有单据零条会被 16KB 截断**（最大 7,042）。切片 4 那句「合成单据零条意外受影响」
   到时按同一口径复测，不能只看测试是否变红。

**顺带实测到、切片 2 与 3 都会撞上的一件事：** `api.read_record` 的返回里带 Python `datetime`
对象，`json.dumps` 直接 `TypeError`，`pydantic_core.to_json` 能序列化。切片 3 的 `envelope()`
只在外层包一个 dict、不做序列化，安全；但**切片 2 的预言机与报表凡是对工具返回做 `json.dumps`
的地方都必须带 `default=str`**，否则会在第一条 case 上炸成 `evaluator_failed`。

### 交付的三项收口与一项防护

| 项 | 结果 |
|---|---|
| 上位设计与证据口径 | spec `:41` G9 判据指向「计划 5 偏离表」；实施顺序表第 6 行依赖改为「5（串行；执行方式按裁决 #11）」；裁决 #7 改写为事实；新增裁决 #11、#12 |
| G9 起算绿夜的触发方式 | `quality-gates-evidence.md` 写明是 `workflow_dispatch`；`schedule` 路径尚未证明（2026-09-08 核实：nightly 最近 20 次里只有 run 34150819690 是 `schedule`，红） |
| `crypto.randomUUID` 回退 | `frontend/src/context-api.js` 的 `requestId()`：`randomUUID` → `getRandomValues` → **抛错**（不退化到 `Math.random`，可预测的标识会让一次请求被当成另一次重放）；九处调用点改用它 |
| 长列表 200 条封顶 | `LoadMore` 达到 `LOAD_MORE_CAP=200` 后按钮消失、**哨兵不再 observe**（否则滚到底仍会继续拉），提示改用搜索。替代 spec:179 的虚拟化，见偏离表 |
| 模型答复外链域名白名单 | `boot.py` 的 `link_hosts()` 按 `frappe.conf.dsherp_link_hosts` 下发（list、小写主机名、≤20 项、去重排序；非法则空表 + `log_error`，**不 500**）；`agent-ui.jsx` 的 `isSameSiteHref(href, hosts)` 只放行站内相对路径与白名单内的 http/https 主机。**默认空表 = 今天的行为**，`img` 与 `urlTransform` 一字未改 |

白名单的三条判据由测试钉住：`javascript:`/`data:` 伪协议不成锚（`new URL` 成功但 protocol 不在白名单）、
协议相对 `//host` 不成锚（`new URL('//host/x')` 抛错）、同后缀冒名主机（`evil-erp.example.com`、
`a.erp.example.com`）不成锚（`hostname` 精确相等）。
