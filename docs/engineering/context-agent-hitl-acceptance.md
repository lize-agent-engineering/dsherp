# 原生侧栏 HITL 真实验收

范围：本机 alpha 合成 Site，不是生产上线。固定 DSH 0.1.1rc1，已授权 deepseek-v4-flash，仍为一次一个384MiB/.1CPU容器。

## 独立普通用户登录

- 为保留 localhost 只读用户和127.0.0.1管理员会话，增加同一Site的确切本地域名 `http://dsherp-validation.localhost:18082`，没有新增服务、端口或资源。原生nginx Host/Origin配置明确增加该来源，外国Host仍421、外国Origin仍403。
- 真实HTTP测试先421，配置后 `test_desk_origin.py` 通过。只重建现有两个nginx容器使共享模板参数一致，业务后端/数据库未重建。平台既有Host范围未扩展。
- `infra/provision_context_writer.py` 建立合成普通用户 `dsherp-writer@example.invalid`，沿用现有Item Manager/Sales User；明确不能修改Administrator用户。原生User对自身的编辑权限不能误当作提权，预检以确切Administrator记录验证。
- 仅复制项目原有合成Item为专用DSHERP-HITL-ITEM。登录密码随机生成，只保存本地0600 profile，不输出或提交。保留此合成账号/物料用于后续业务验收，不用于日常企业Site。

## 真实模型提出修改，浏览器确认

- 浏览器从原生登录、物料列表、物料表单进入侧栏，用户选择业务操作领域，请求只修改专用Item的item_name。
- 单次真实模型运行Succeeded、4次模型调用，生成一个Pending提案。容器及消费者退出后才允许确认；确认前独立数据库读取仍为“HITL 确认前物料”。
- 等待期间在原生表单填写“用户尚未保存的草稿名称”，不保存也不提供给模型。模型差异卡仍使用数据库原值，而不是未保存草稿。
- 浏览器点击一次确认执行后显示成功。独立数据库核对名称变为“HITL 真实模型确认物料”，modified_by为普通writer，保存版本2026-08-29 03:28:59.056088；只有一份Succeeded执行记录。原生可见输入仍保留用户草稿，没有自动刷新或覆盖。
- 操作运行ID：90a3183f0d4eca8f741ad57a9b9544cd549999cac6beb59a07ee0de0eb682cea；提案jab3r75vof；执行jmelv87seo。均为合成验收证据，不含生产记录或凭证。

## 同页连续查询与历史恢复

- 确认后仍在原表单询问保存结果，最初真实HTTP409暴露旧页面版本不必要地阻断查询。TDD将旧“查询一律拒绝旧版本”用例替换为“保留页面version，服务端独立填写server_version”，客户端伪造server_version会被覆盖；列表页不允许附带该单据版本。未知字段/身份和业务确认版本冲突仍拒绝。
- 不刷新原表单，只刷新侧栏记录后重新发出已明确失败的请求。第二次真实deepseek-v4-flash运行Succeeded、4次模型调用，实际ERP读取返回“HITL 真实模型确认物料”及保存版本；运行ceb107b3262ebd36efc62ff79febdb79c78610f450e1b9afd9fe629dc56ac247。未保存草稿没有传给模型，页面快照仅含dirty和两个版本。
- 新标签页以同一普通账号打开原生Item，显示已保存名称；侧栏自动恢复历史和执行成功，并明确显示页面/服务器版本差异说明。原标签页仍保留“用户尚未保存的草稿名称”。执行记录仍唯一，两个运行均已完成，无容器或付费消费者遗留。
- 全前端56项通过，构建成功；补齐列表伪造版本负面测试后，真实会话/执行/域名回归13项通过（15.09s），运行器/配置7项通过。此前该负面测试遗留的一个未消费Queued会话已按确切ID删除；真实验收账号、物料、会话和执行记录保留用于后续验收。

## 当前后续

已完成alpha普通用户的真实模型→提案→浏览器确认→原生保存→连续实际查询及历史恢复闭环，范围仅为Item标量修改。Customer实际保存/创建、Item创建、Sales Order、填表、Unknown核实、配置预览发布和日常Site仍待完成，不能将本次验收表述为完整阶段二或生产上线。

## 后续补齐：Item/Customer 原生创建与 Customer 真实 UI

- 创建提案与修改共用确认、执行记录和权限/有效期检查；创建绑定读取的schema版本及明确字段，确认前不调用原生命名或insert。确认后原生insert负责命名、默认值和业务校验，并逐字段回读；不能拿“提案已保存”冒充“业务已创建”。
- 真实Frappe参数化测试分别覆盖Item/Customer：提案前后业务数量不变、确认只创建一条、不同请求ID重复确认仍只一条、继续修改新记录并核对普通用户modified_by。日期字段测试先因原生Date对象与JSON字符串比较失败，改为同一规范日期表示后通过，未取消回读一致性检查。
- erp-operation升级固定1.1.0并更新内容摘要。operation新增erp_propose_create，服务端要求本轮实际读过同一schema版本；query不获得创建能力，模型仍无确认/insert/save工具。字段值和命名不能由任意代码执行补足。
- 浏览器普通writer在Customer列表请求创建DSHERP-HITL-CUSTOMER，只填写customer_name、customer_type、customer_group、territory；分组与区域从明确参考客户实际读取。真实deepseek-v4-flash运行1eddbffb8768c99c3034a45aac7a032d67647c4a42bcc433d668e6ed84f7bedb，5次模型调用，Succeeded并释放容器。
- 提案r4ms2dmtuu待确认时，独立数据库计数客户0、执行0。浏览器确认后客户1、执行1；执行rf5pnlvij7，原生结果名DSHERP-HITL-CUSTOMER，版本2026-08-29 03:42:14.981676。owner/modified_by均为普通writer；关联Contact/Address数量0。原生列表与表单均显示四个正确字段。
- 测试证据：57项前端通过、构建成功；创建/操作真实Frappe7项（11.78s）；Runtime技能/MCP13项（6.83s）。真实模型调用单独如上，不与SSE替身合并表述。合成真实UI客户保留给后续Sales Order验收，临时自动化创建记录/账号已清理。
- 当前新增证明Item与Customer创建/修改的原生方法，以及Customer创建的真实模型/UI链；Item创建和Customer修改尚未各自单独重跑真实模型UI组合。接续Sales Order和填表，之后Unknown核实、剩余阶段一验收及阶段三/四。

## Sales Order 真实创建与明细修改

- alpha普通writer在原生销售订单列表请求创建草稿，新会话9rf91o7b4k、运行3cc287e0626d0f2af3490df8103a91c8dddc3f5b9a0567f09141d194e65046f4真实模型Succeeded，6次模型调用/16384预留输出（含压缩），容器退出。提案aiuvfqojko确认前订单总数1、执行0；浏览器确认后生成SAL-ORD-2026-00002，执行b4iep0huik Succeeded。
- 原生新订单表单显示客户DSHERP-HITL-CUSTOMER、日期2026-08-29、交货2026-08-31、唯一物料DSHERP-UI-ITEM/验收中文维修服务、qty2、rate1234.56、合计2469.12、草稿。独立数据库owner/modified_by均writer，版本2026-08-29 04:08:59.555355。没有复制原订单其他子表或修改原订单。
- 新标签页默认query；第一次修改请求实际记录为query运行e761eb2469d0acadcd02781cc3d2f6e02f832e24a3aad503a14d8e3863d5842f（2次），模型只给口头“提案”，未生成确认卡，不能算真实操作通过。后续明确观察领域已为operation再发送。UI仍需改进普通回答与真正提案的区分，不能凭回答措辞认定业务成功。
- operation运行c73a3a3b2ee89763e336bccff5442bfee8b8947c3cadfb912b55239182a6f68b（1次）失败，新安全诊断定位Python业务HTTP RemoteProtocolError；没有新提案。后端日志未见相应worker超时/重启。状态轮询取消连接复用（不自动重试），真实HTTP连接红绿测试验证每次新连接；不能据此宣称已穷尽原始网络故障根因。
- 修复后同会话运行ec669173b6bcc51304a0459b76d3b3f7fa5445ed9c591802cfe4c50555227579，operation真实模型6次Succeeded。提案dturkuf9tp冻结行b4jk0v3hkp的qty2→3；确认前数据库仍2，浏览器确认后唯一执行e8cq7vqn5s Succeeded。qty3、rate1234.56、合计3703.68、docstatus0，版本2026-08-29 04:14:18.971349，modified_bywriter。
- 接续真实模型提交/取消分别确认、明细差异易读展示、填表；当前创建/修改真实链不代表所有销售订单验收通过。合成订单保留继续验证，无常驻消费者，无生产写入。

## Sales Order 真实提交与取消

- 首次提交运行b56d6df5cc85ebe7707069edb1e9ad8f98dc37b52e3c3bff03303f36d10ca9c3生成Pending提案fi3af9hog4后，原生自动压缩再次申请额度被HTTP417拒绝，guard停止后续模型调用。运行Failed，6次调用/15360输出预留；提案不可确认，docstatus仍0。原生日志确认是摘要预算不足，不再把该失败泛称网络问题。
- thresholdRatio原为0.005，在实际订单schema/多轮下频繁摘要。以普通多轮“不应连续摘要”的新行为先复现两次摘要后，调整为0.02；压力用例增大输入，仍验证原生自动摘要、摘要403拒绝后停止、checkpoint恢复保留来源。每次输入/每轮总预算、摘要输出2048、operation输出3072及容器预算不变。
- 重提运行1ce8930bc52adbdd9ad3a2b857039fb626f8e0d6afa6ce890c423a64ca757ae4真实模型4次Succeeded，新提交提案h1qjte5ufe。确认前docstatus0；浏览器确认后docstatus1、status To Deliver and Bill、版本2026-08-29 04:19:32.464234，执行habpoalf3m Succeeded，modified_bywriter。
- 另行发出取消请求，运行a267266ad55e34ad95e63c168002a6ec392773d8415588bfaca096528cc5bc85真实模型3次Succeeded，提案hpvgd78iqv绑定上述新版本。侧栏明确显示“已提交→已取消”和取消影响；确认前docstatus1，确认后docstatus2/status Cancelled、版本2026-08-29 04:20:46.770552，执行i1i1kksga5 Succeeded，modified_bywriter。
- 新标签页原生订单显示“取消”、原生“修订”按钮、只读明细qty3/rate1234.56/amount3703.68。参考订单00001未修改；失败提案保留审计但未执行。当前无付费消费者，所有操作仅alpha合成Site。
- 压缩/模型授权/运行器组合14项通过（14.75s）。Sales Order创建、明细数量修改、提交、取消均已有真实模型→侧栏确认→原生回读证据；仍需完成明细差异易读展示、填表、Unknown核实、权限/并发补充验收及阶段三/四，不能据此宣称整体完成。

## 当前表单填入与独立原生保存

- 原生Item/DSHERP-HITL-ITEM表单新会话m6k47jhjhl，真实模型运行60bb856ea771cdc6ddeafd499788d500d98222e2d38e259daf533c3b7c3c6a93，operation4次Succeeded；fill提案ml25lnkbcv要求item_name从“HITL 真实模型确认物料”填为“HITL 仅填入草稿名称”。
- 侧栏“确认填入”后可见原生input变为建议值，原生页面显示“尚未保存”，侧栏显示“已填入当前草稿，尚未保存或提交”。独立数据库名称仍为原值、modified仍2026-08-29 03:28:59.056088；唯一授权记录mtmqohlfbl状态Authorized、target browser-draft，不声称ERP已保存。
- 关闭侧栏后单独点击原生“保存”，未叠加Agent确认。数据库此时才变为建议值，版本2026-08-29 04:29:24.661284，modified_by普通writer；fill授权记录仍只有1份。原生手动保存与Agent填入保持分离。
- 模型回答曾把十分钟有效期误说“约一小时后”；实际expires_at和确认检查未变化。固定operation skill1.4.0增加不自行推算相对期限的规则；后续不能依赖模型文字判断授权是否有效。
- 随后TDD补用户显式提供的标量草稿form_before：数据库before/版本仍绑定确认，前端用form_before匹配当前草稿和展示差异；后来再编辑仍停止。65项前端/构建、7项真实Frappe和8项Runtime/技能通过。此扩展尚未单独重跑真实模型UI，子表填入/易读明细等仍待推进。

## Phase 4 / T4.1：制造订单缺料解释与预算文案

- 2026-09-01 在 alpha 先只给既有合成普通账号 `dsherp-writer@example.invalid` 增加原生 `Manufacturing User`、`Purchase User`、`Purchase Master Manager`、`Stock User` 角色，并逐项回读 BOM/Bin、Work Order、Stock Entry、Purchase Order/Receipt、Subcontracting Order/Receipt、Delivery Note 权限；未改平台 alpha 的只读成员绑定，未使用 Administrator 执行业务请求。后端首次打开 Desk 暴露旧进程仍加载 C1 前模块，确切报错为 `boot.py` 无法导入 `require_policy_schema`；只重启既有 business backend 使已提交代码生效，没有重建数据库或重放请求。
- 不修改 `SAL-ORD-2026-00001/00002`。通过原生业务方法建立专用合成验收草稿 `SAL-ORD-2026-00003`（客户采购订单标记 `DSHERP-PHASE4-MFG-ACCEPTANCE`）：唯一明细为制造 fixture 成品 `DSHERP-MFG-SYN-FG` 60 Nos，目标为合成成品仓，BOM `BOM-DSHERP-MFG-SYN-FG-001` 每件耗 `DSHERP-MFG-SYN-RM` 2 Nos。该明确 fixture 写入落盘后才开始 T4.1 零意外写入基线。
- 普通 writer 在该销售订单原生表单侧栏新建查询会话 `hcadbl4q4h`，真实 `deepseek-v4-flash` 运行 `4e321463b8bebee30ba896b118e80237159aefc6434ca5c705eda959a0c7ee09` 为 `Succeeded`。模型按 Sales Order → BOM → Bin 顺序完成 7 次有来源 ERP 读取，页面明确给出需求 `60 × 2 = 120`、原料仓 `actual_qty/projected_qty = 100/100`、缺口 `20 Nos`，并说明没有修改记录。
- 该运行正好使用 8 次模型调用、`412983` 输入字节、`16384` 预留输出 token，均未超过单运行上限；没有静默放松常量，也没有再次运行。把第 9 次预约的明确错误永久锁定为 `本轮模型调用预算已用尽`，`tests/integration/test_context_execution.py` 现场 **1 passed / 6.67s**。
- 运行后会话内提案 0、执行记录 0（只读查询的预期形状），制造业务单据仍为 Work Order/Stock Entry/Purchase Order/Purchase Receipt/Subcontracting Order/Subcontracting Receipt/Delivery Note 各 0；销售订单仅新增上述 fixture 至总数 3。原料 Bin 仍 `100/100`、成品 Bin 仍 `0/0`。因此 T4.1 的唯一运行、零意外写入和预算未超三项通过；查询段不伪造一条 operation 执行记录。
