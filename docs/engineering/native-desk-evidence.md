# 阶段 1：原生 Desk 验证记录

日期：2026-08-28。状态：阶段 1 样板验收完成；原生初始化、中文搜索、草稿明细保存回读、角色差异已实测。数字录入的自动化事件限制见下文。

## 实现与资源

- 复用已固定的 ERPNext 15.119.3 / Frappe 15.118.0 镜像及本项目独立站点。
- 新增原生 nginx、WebSocket、单 worker（short/default/long）及 scheduler 进程；未修改上游源码、AgenERP 或其他服务。
- Desk 入口 `http://127.0.0.1:18082/login`；已有 API `127.0.0.1:18081` 不变，均仅监听 loopback。
- 硬内存限额：db 1024、redis 128、backend 1024、frontend 128、websocket 256、worker 1024、scheduler 128 MiB，总计 3712 MiB；CPU 总限额 2。没有超过既有 4 GiB / 2 CPU 授权，没有拉取新镜像。
- 七个容器运行；采样内存合计约 497 MiB。单 worker 顺序消费队列是本地验证配置，不是生产吞吐承诺。数据仍用原独立卷；sites 712 KiB、logs 668 KiB（不含数据库）。10 GiB 数据预算仍是观察预算。
- nginx 和 WebSocket 的 sites 只读挂载。镜像默认入口会删除并重建 assets 链接，导致只读挂载启动失败；通过 Compose 的 entrypoint 清空，仅启动镜像内现有服务命令，由 backend 维护资产链接，不修改上游脚本。
- `bench doctor` 确认 1 个 worker online；scheduler 进程运行，但 Site 调度仍 disabled/inactive。站点未完成原生 setup wizard，不伪造 setup_complete 或直接改状态跳过初始化。

## TDD 与真实 HTTP

`tests/integration/test_desk.py` 在新增服务前 3 项失败（连接被拒绝）。服务接入后分别验证：

1. 原生登录页 200，页面引用的 CSS/JS 均真实返回 200 且不是 HTML。
2. Socket.IO polling 握手返回 200 和 session id；尚不等于已验证登录后的实时事件。
3. Guest 打开 `/app` 被重定向到登录页。实物使用 301，按实际 Frappe 契约修正初始仅接受 302/303 的测试假设。

完整回归 `.venv/bin/python -m pytest tests -q --tb=short`：**39 passed in 26.03s**，退出 0，含真实 ERP HTTP 和真实 Runtime→MCP→ERP（模型为本地替身）。Compose 解析后的资源限额总和也已断言核对。

目标测试最终 3 passed；无重试兜底、无 CI 或新增门禁。

## 浏览器观察

Codex 内置浏览器已打开真实登录页，DOM 包含中文“电子邮件”“密码”“登录”“忘了密码？”，标题仍为 `Login to Frappe`，邮件链接仍为英文。不能因此声称整站中文适配完成。初次访问发生于服务启动失败时；服务恢复后用新标签页成功确认表单。

用户后续明确授权后，已读取本项目测试管理员密码并仅输入本地测试站点登录表单；未打印密码、未提交凭证。

## 初轮待验收（下文已补验）

- 原生明细的数量、金额、日期可靠录入与保存回读。
- 普通角色浏览器页面差异（管理员页面及普通 HTTP 权限已验证）。
- 日期覆盖设置及用户时区的实际页面复验。
- 后台实际任务类型与必要中文扩展。
- 阶段 2 原型及阶段 3–6 尚未实施。

本轮无新增付费模型调用、库存/账务提交或生产部署。既有真实模型证据仍为此前记录，本轮自动化模型侧使用本地替身。

## 授权后续验收

### 已完成

- 真实管理员登录成功。初始化向导只显示 ERPNext 组织页：固定版本的 `InstalledApplications.update_versions()` 在有普通用户时视为 Frappe 初始化已完成，而先前 API 验证创建的用户使国家、语言等仍为空。通过原生 `frappe.client.set_value`（内部 `get_doc/save`）补齐 China / zh / Asia/Shanghai / CNY，关闭测试站点遥测；未设置 setup_complete。
- 在浏览器原生向导填写合成公司 `DSHERP 原生验收测试公司`、缩写 `DVT`，使用标准科目表、2026 财年，不生成全套演示业务数据。点击完成安装后观察到原生进度，最后进入中文 Workspace。独立回读公司币种和国家正确，`setup_complete=1` 来自原生初始化。
- 通过原生 System Settings 开启本站 scheduler；`bench doctor` 不再报 disabled/inactive，1 个 worker 在线。尚未独立验收全部后台任务类型。
- 桌面 1440×1000 检查 Workspace 和物料双栏表单；中文菜单与标签可见，无需重写整个 Desk。
- 浏览器通过原生快速新增保存非库存物料 `DSHERP-UI-ITEM / 验收中文维修服务`。独立 API 回读 item_code/item_name/is_stock_item/stock_uom 与 UI 一致。
- 物料列表输入“验收中文”后仅命中该中文物料；全局输入“销售订单”显示原生列表、新建和报表入口；订单明细 Link 输入同样中文可查到该物料。
- 真实打开原生角色列表、DSHERP Reader 角色表单和角色权限管理矩阵。Reader 对 Customer/Item 有读取、没有写入/创建/删除权限；未修改权限。

### 实时认证修复（TDD）

旧测试仅验证 Socket.IO transport，登录后浏览器实际报 `Invalid origin`。新增普通 token 的真实 namespace 连接测试，先失败并返回原生 `Invalid origin`；新增外部 Origin 拒绝测试先失败（200）。

从固定镜像读取原生 nginx 模板、`realtime/middlewares/authenticate.js` 与 `realtime/utils.js` 确认：Origin 和 Host 主机名需一致，而且认证回调直接访问 Origin。原模板把 Origin 设为 Site、Host 保留 127.0.0.1，既不一致，也缺少正确的内部认证路径。

本项目 `infra/frappe.conf.template` 基于该镜像模板，仅调整验证环境代理：先拒绝不在允许列表的浏览器 Origin，再将内部 Host/Origin 一致指向带 Site 名称的 backend:8000；Compose 专用网络增加对应 Site DNS 别名。没有关闭 Frappe 身份校验，没有改上游核心。前端仍仅绑定 loopback。

- 5 项 Desk HTTP 测试通过，包括普通用户真实 namespace 认证和外部 Origin 403。
- 完整回归：**41 passed in 27.80s**，退出 0；真实模型仍未新增调用。
- 浏览器刷新后不再产生新的 Invalid origin 错误，原生初始化进度可见。旧 console 历史错误不视为新失败。

### 初轮未通过与限制（历史记录）

- 销售订单原生子表可选中文物料、添加行和展开编辑。自动化输入数量、单价、日期后，输入框曾显示新值，但汇总/保存校验仍使用旧值。直接填值、逐字按键和原生键盘工具均未完成可靠提交；不能将输入框文本当成已写入单据。
- 保存尝试被原生必填校验拒绝（交货日期、第二行物料等）。没有保存销售订单，没有正式提交、库存或账务变更。尚未确认是浏览器自动化事件传递问题还是当前页面行为；不修改原生字段事件来掩盖。
- 尝试通过原生 Impersonate 做只读用户 UI 验收，理由文本域同样未被表单接受，确认被“缺少需要的值”拒绝。身份仍为 Administrator，不能声称普通角色 UI 已验收；既有普通用户 HTTP 拒绝测试继续通过。
- 配置期发现 Administrator 的原生用户默认 date_format 为 dd-mm-yyyy，覆盖了 System Settings。已通过 `frappe.defaults.set_user_default` 设置为 yyyy-mm-dd，尚待重新打开日期表单复验；没有修改上游格式化器。
- 用户时区还需逐用户核对，已有 reader 表单显示 Asia/Kolkata，系统时区不代表用户时区已统一。

### 保留原生与必要扩展

保留原生 Workspace、列表筛选、Link 联想、双栏表单、子表与权限矩阵。后续通过 App 扩展资源补齐核心术语和未翻译字符串：Item Name 应为“物料名称”，不是“项目名称”；ERPNext Settings 不应为“ERP下载设置”；Search、List View、评论、权限说明仍有英文。日期/数字必须以真实角色和保存回读验证，不以配置值代替。暂不重做全站样式，不引入通用页面编辑器。

阶段 1 初轮尚未完全验收；后续补验见下文。原生合成公司与中文物料保留供后续验收，不视为临时文件清除；没有产生需保留的运行日志或截图文件。

## 授权后最终补验

- 原生 Impersonate 理由分两次工具调用完成填入与确认，切换到 `dsherp-reader@example.invalid`。物料只读，无保存按钮；直接访问角色管理和被限制的 `DSHERP-TEST-OTHER-CUSTOMER` 均显示原生权限拒绝。未提高权限。退出后重新以管理员登录，新标签页避免旧页面会话视图残留。
- 管理员新单日期及保存后回读均为 `2026-08-28`；原生日历选择交货日期 `2026-08-31`，传入明细。
- 固定版本原生数字控件监听 `change`。浏览器自动填值未可靠触发该事件；通过 CDP 对已填写的可见数量、单价 DOM 控件派发标准 `change` 事件，补齐浏览器输入事件。没有直接修改 Frappe 模型、调用写入 API 或改上游控件。这证明事件后的业务链，不能等同于纯人工键盘录入验收。
- 数量 `2.5`、单价 `1234.56` 时页面计算 `3,086.40`，保存被服务端拒绝：`Nos` 必须使用整数。保留原校验，改为数量 `2`。
- 点击原生保存成功，生成合成草稿 `SAL-ORD-2026-00001`；独立 `frappe.client.get` 回读 `docstatus=0`、物料 `DSHERP-UI-ITEM`、数量 `2`、单价 `1234.56`、总额 `2469.12`、交货日期 `2026-08-31`。浏览器重载后同样显示草案和 `CNY 2,469.12`。没有点击提交，没有库存或账务过账。
- 用户时区是独立设置；reader 仍显示 Asia/Kolkata，不宣称系统设置已统一全部用户。大写金额、日期控件月份及部分文案仍混合英文，列入后续扩展，不重做原生 Desk。

阶段 1 所要求的原生样板与权限差异已有证据，开始阶段 2。后台全部任务类型、纯人工数字输入、全站翻译不在本次完成声明内。阶段 2–6 仍需分别实现验收。
