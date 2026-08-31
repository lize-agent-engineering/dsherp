# ERPNext 中文术语纠正包设计

日期：2026-08-31。状态：等待检查点 1 人工评审；批准前不进入代码实现。

## 目标与价值

为简体中文业务站点提供一套可审阅、可重复生成、随 `dsherp_bridge` 安装生效的 ERPNext 术语纠正包，把关键制造、库存、购销和财务词汇统一为大陆企业熟悉的 ERP 表达。

“一键更正”不新建运行时功能：新站点安装 App 后自动获得词条；存量站点更新 App 源码后执行原生 `bench clear-cache`，浏览器硬刷新即可生效。ERPNext/Frappe 继续负责语言选择、翻译加载、缓存和企业热修。

首版只解决会直接影响业务理解的错误、歧义和非大陆惯用译法。固定版本中已经正确的 `Item→物料`、`UOM→计量单位`、`Warehouse→仓库`、`Supplier→供应商`、`Customer→客户`、`Sales Order→销售订单`、`Purchase Order→采购订单`、`Chart of Accounts→会计科目表`、`Balance Sheet→资产负债表`、`Cost Center→成本中心` 不重复覆盖，以缩小升级漂移面。

## 固定版本原生机制

T0 在 validation 容器内只读核验了 Frappe `15.118.0`、ERPNext `15.119.3` 的实际实现：

- `frappe.translate.get_translations_from_csv()` 从每个 App 的 `translations/<language>.csv` 加载翻译；CSV 为 `source,translated[,context]`。
- `get_translations_from_apps()` 按安装 App 顺序逐项合并，后一个 App 覆盖前一个 App。
- alpha、daily、beta 三个业务站点的实际顺序均为 `frappe → erpnext → dsherp_bridge`，所以 `dsherp_bridge` 能覆盖上游同键词条。
- 服务端与浏览器翻译函数都先查 `source:context`，没有专用 context 时回退裸 `source`。
- `get_all_translations()` 在 App 翻译后再合并 Translation DocType，因此企业自定义的当前语言词条优先级最高。
- 简体中文 Language 代码和三个站点默认语言都是 `zh`；首版文件名是 `zh.csv`，不是 `zh_CN.csv` 或 `zh-CN.csv`。
- `frappe.translate.clear_cache()` 清理合并翻译、企业翻译和 bootinfo 缓存；浏览器还持有已加载翻译包，需要硬刷新。

结论：原生机制与本设计一致，不需要修改 Frappe/ERPNext，也不需要新增运行时 hook、API、缓存或同步服务。

## 架构与优先级

```text
Frappe zh.csv
    ↓ 后装 App 覆盖
ERPNext zh.csv
    ↓ 后装 App 覆盖
dsherp_bridge/translations/zh.csv（平台统一词条）
    ↓ 原生最后合并
Translation DocType（企业管理员热修，最高优先级）
```

术语包位于 `dsherp_bridge`，因为所有业务站点安装 `{frappe, erpnext, dsherp_bridge}`，而 `dsherp_platform` 只安装在内部平台站。平台站不承载 ERP 业务页面，首版不复制该词包。

平台永不创建、更新或删除 Translation 记录。企业管理员继续使用原生 Translation DocType；平台升级 CSV 后，企业同键记录仍自然覆盖平台词条，不需要第二套授权、合并或冲突系统。

## 主词表与确定性生成

唯一新增的开发期机制是一份主词表和一个纯生成脚本：

- `config/terminology/glossary.csv` 是唯一事实源，列为 `source,context,zh,note`。
- `infra/build_translation_packs.py` 校验格式与重复键，按稳定顺序生成 `frappe_app/dsherp_bridge/translations/zh.csv`。
- 同一 `source + context` 只能出现一次；空 context 生成普通两/三列兼容行，非空 context 生成原生第三列。
- 重复运行必须字节级一致；生成文件不得手改，测试用重新生成结果防漂移。
- 词条必须来自固定版本源码或上游 `zh.csv` 的精确 msgid，不翻译 DocType 内部名、路由名或数据库值。

当前 49 个源串在排除 translations/dist 的已安装 Frappe/ERPNext 源码中均有实际命中。`General Ledger` 另有 `Warehouse` context 调用，因此首批为 50 行；裸词条和 context 词条都要写，否则仓库页按钮会继续命中上游旧译“总帐”。

## 国家、语言与应用方式

语言绑定复用 Frappe 原生 Language 与站点/用户语言设置。首版内容仅为 `zh`；未来确需地区变体时，新增原生 Language 记录及对应同名 CSV，不在首版预建地区规则引擎。

- 新站点：开站脚本继续设置站点语言并安装 `dsherp_bridge`，词包随 App 自动加载。
- 存量站点：更新主词表并重新生成 CSV，逐站执行 `bench clear-cache`，用户在浏览器硬刷新。
- 平台统一推送是相同制品和相同激活动作的幂等重复执行，不写企业 Translation 记录。
- 回滚使用 `git revert` 恢复上一版主词表/CSV，再逐站 clear-cache 和浏览器硬刷新。

## 首批完整词条表

| # | source | context | 当前上游译文 | 目标译文 | note |
| ---: | --- | --- | --- | --- | --- |
| 1 | Item Name |  | 项目名称 | 物料名称 | 消除“项目”歧义，与 Item=物料一致。 |
| 2 | Item Group |  | 物料群组 | 物料组 | 采用大陆 ERP 常用主数据称谓。 |
| 3 | Item Tax Template |  | 物品税模板 | 物料税费模板 | 与物料主数据统一，“税费”覆盖 taxes and charges 语义。 |
| 4 | Include Item In Manufacturing |  | 包括制造业中的项目 | 纳入生产用料 | 表达复选项的实际业务含义。 |
| 5 | Quotation |  | 报价 | 销售报价单 | 明确单据属性及销售方向。 |
| 6 | Delivery Note |  | 销售出货 | 销售出库单 | 对齐库存出库单据语义。 |
| 7 | Sales Invoice |  | 销售费用清单 | 销售发票 | 对齐财务单据标准称谓。 |
| 8 | Purchase Receipt |  | 采购收货单 | 采购入库单 | 明确收货会形成库存入库。 |
| 9 | Purchase Invoice |  | 采购费用清单 | 采购发票 | 对齐财务单据标准称谓。 |
| 10 | Material Request |  | 材料申请 | 物料需求单 | 覆盖采购、调拨、生产等多种需求目的。 |
| 11 | Material Request Type |  | 材料申请类型 | 物料需求类型 | 与“物料需求单”统一。 |
| 12 | Request for Quotation |  | 询价 | 询价单 | 明确单据属性。 |
| 13 | Supplier Quotation |  | 供应商报价 | 供应商报价单 | 明确单据属性。 |
| 14 | Stock Entry |  | 手工库存移动 | 库存单据 | 覆盖入库、出库、调拨、生产等业务，不仅是手工移动。 |
| 15 | Stock Entry Type |  | 库存进入类型 | 库存业务类型 | 表达 Stock Entry 的业务分类。 |
| 16 | Stock Entry Detail |  | 手工库存移动信息 | 库存单据明细 | 与主单名称统一。 |
| 17 | Stock Reconciliation |  | 库存盘点 | 库存盘点单 | 明确单据属性。 |
| 18 | Stock Ledger |  | 库存总帐 | 库存台账 | 区分库存数量台账与财务总账，并使用现代“账”字。 |
| 19 | Stock Ledger Entry |  | 库存分类帐分录 | 库存台账明细 | 表达库存台账逐笔明细。 |
| 20 | Actual Qty |  | 实际数量 | 实际库存 | Bin 字段表示当前实存数量。 |
| 21 | Projected Qty |  | 预计数量 | 预计库存 | Bin 字段表示考虑供需后的预计库存。 |
| 22 | Valuation Rate |  | 库存评估价 | 估值单价 | 明确单位成本而非总额。 |
| 23 | Incoming Rate |  | 入库库存评估价 | 入库单价 | 精简并明确单位价格。 |
| 24 | Material Transfer for Manufacture |  | 材料移送用于制造 | 生产领料 | 对齐生产工单的领料动作。 |
| 25 | Material Receipt |  | 材料收讫 | 其他入库 | 作为 Stock Entry Purpose，与标准库存业务称谓一致。 |
| 26 | Material Transfer |  | 材料转移 | 库存调拨 | 作为 Stock Entry Purpose，明确仓库间调拨。 |
| 27 | Send to Subcontractor |  | 发送给分包商 | 委外发料 | 对齐企业供料委外流程。 |
| 28 | Pick List |  | 选择列表 | 拣货单 | 对齐仓储拣货业务单据。 |
| 29 | Delivery Note Item |  | 销售出货单项 | 销售出库单明细 | 与销售出库单统一。 |
| 30 | BOM |  | BOM | 物料清单 | 首次出现即可被大陆 ERP 用户直接理解。 |
| 31 | Bill of Materials |  | 材料清单 | 物料清单 | 采用制造业标准称谓。 |
| 32 | Work Order |  | 工单 | 生产工单 | 区分维护、服务等其他工单。 |
| 33 | Work Order Item |  | 工单项 | 生产工单明细 | 与生产工单统一。 |
| 34 | Job Card |  | 工作卡 | 工序作业单 | 表达车间工序执行记录。 |
| 35 | Operation |  | 操作 | 工序 | 制造模块中的标准术语。 |
| 36 | Routing |  | 路由 | 工艺路线 | 消除网络术语歧义。 |
| 37 | Workstation |  | 工作站 | 工作中心 | 对齐制造资源与产能语义。 |
| 38 | Quality Inspection |  | 质量检验 | 质量检验单 | 明确业务记录/单据属性。 |
| 39 | Subcontracting Order |  | 外协订单 | 委外订单 | 与 ERPNext 委外业务链统一。 |
| 40 | Subcontracting Receipt |  | 外协收据 | 委外入库单 | Receipt 在此为委外成品收货入库，不是财务收据。 |
| 41 | Journal Entry |  | 手工凭证 | 记账凭证 | 对齐大陆财务软件标准称谓。 |
| 42 | Payment Entry |  | 付款凭证 | 收付款单 | 同一 DocType 同时覆盖收款与付款。 |
| 43 | General Ledger |  | 总帐 | 总账 | 使用大陆会计标准称谓与现代规范字。 |
| 44 | General Ledger | Warehouse | 总帐 | 总账 | 覆盖仓库页 context 专用键，防止其遮蔽裸词条。 |
| 45 | Trial Balance |  | 试算平衡表 | 科目余额表 | 对齐大陆财务软件高频报表称谓。 |
| 46 | Profit and Loss Statement |  | 损益表 | 利润表 | 对齐大陆会计报表法定称谓。 |
| 47 | Accounts Receivable |  | 应收帐款 | 应收账款 | 使用现代规范字。 |
| 48 | Accounts Payable |  | 应付帐款 | 应付账款 | 使用现代规范字。 |
| 49 | Credit Note |  | 换货凭单 | 贷项通知单 | 修正错误业务含义。 |
| 50 | Bank Reconciliation |  | 银行对帐 | 银行对账 | 使用现代规范字。 |

## 安全边界与明确不做

- 不修改 Frappe/ERPNext 上游文件，不 fork 上游。
- 不写运行时翻译代码、hook、patch、API、worker 或独立缓存。
- 平台不写 Translation 记录，不建设企业词表管理 UI。
- 不把词表或生成 CSV 纳入 `config/runtime-files.json` 的 Agent Runtime 摘要。
- 不改 `Submit`、`Cancel` 等状态机动词；它们保持“提交”“取消”，并由常驻测试作为红线。
- 首版只提供 `zh`，不提供 `zh-TW` 或自造 `zh-CN` Language。
- 不做 wheel 打包验证；`pyproject.toml` 仍需声明 package data，验证拓扑则继续使用只读源码挂载。
- 不自动猜测 ERPNext 升级后的 msgid；升级后需重跑精确源串与 context 审核。

## 验证与验收边界

实现阶段采用 TDD：先确认主词表/生成器行为测试失败，再写最小实现；随后验证确定性、幂等、重复键 fast-fail、状态机动词红线、生成文件防漂移。

集成验收在 alpha 合成站点完成：抽样调用 `frappe._()`、验证企业 Translation 覆盖平台 CSV、验证配置锁允许核心 Translation DocType、验证 clear-cache 前后行为，并证明上游文件未改。daily 应用前必须先备份、验证四件套并到检查点 2 等待人工授权。文档、单测、真实 ERP、浏览器 UI 和部署状态分别报告，不能互相替代。

## 风险

- 通用词会影响所有使用同一 msgid 的页面；检查点 1 由人工逐条审阅全表。
- context 专用词可遮蔽裸词条；当前已识别并单列 `General Ledger:Warehouse`，以后新增词条也必须检查 context。
- 服务端和浏览器存在两层缓存；运维步骤必须同时包含 clear-cache 与浏览器硬刷新。
- App 顺序决定覆盖关系；集成测试固定抽样，站点安装顺序变化时显式失败。
- ERPNext 升级可能改动 msgid 或上游译文；升级后人工复核，不静默生成近似键。
