# 阶段 2：原生 Desk 内的前端交互原型

日期：2026-08-28。阶段 2 主流程原型已可操作；阶段 3–6 尚未实施。这里不是业务应用真实生成、部署或迁移完成声明。

## 入口与边界

- 本地入口：<http://127.0.0.1:18082/app/dsherp-studio>。需原生 Desk 用户身份；也可在原生全局搜索输入 `dsherp` 打开。
- 原生 Frappe `Page` 承载 React + Ant Design，自定义 App 提供页面元数据与脚本，不替换 Desk、不使用 iframe、不修改上游核心。
- 演示只保存在 React 页面内存。刷新、离开原型或切换企业清空状态；没有浏览器持久存储、ERP 写入 API、模型调用或部署请求。
- “企业发布者”演示角色同时赋予业务员工与设计者能力，以便走通样例；真实系统需分别授权。平台运维不进入业务与构建空间。
- 创建企业、接受邀请、实时资源、Agent 回答和应用业务测试尚未接入；页面明确标示未接入或固定样例。登录与个人管理继续使用原生入口，没有新建密码系统。
- 生成方案使用固定设备维修样例；自由输入保留为需求备注，不声称理解或生成任意应用。候选字段明确为拟新增，不声称已有 DocType。

## 实现范围

企业工作台、助手样例、来源明确的待办、任务记录、动态演示业务入口、企业设置原生链接；应用目录、构建工作区与发布记录；平台租户、技术开通任务、资源未接入提示、平台职责及操作记录。

构建工作区包含需求与方案、页面与导航、数据结构、业务规则、角色权限、测试预览、版本与发布七个标签。设备维修链为：

1. 输入需求，生成演示候选。
2. 切换报修员工/主管查看预览权限差异。
3. 查看字段、规则、权限及待执行业务测试；业务测试没有伪造通过标记。
4. 发布者确认具体企业、角色和版本；内容改变或角色变更清除确认。
5. 执行指定演示场景：成功、失败、迁移/配置冲突、部分成功、结果不明。
6. 仅演示成功后出现设备维修入口，可新增、查看内存中的演示报修。

目标版本变化拒绝覆盖；已发布候选不能重复发布，修改需求后生成新版本。部分成功/结果不明禁止重复发布；未知结果可模拟核实同一版本已生效，更新原任务，不新增部署任务。部分成功修复/恢复尚未实现，明确引导核实步骤，不承诺自动回滚。

## TDD 与自动化验证

- 首先建立状态行为测试并用空实现确认 13 项失败，再实现；之后重复发布回归先失败，再禁止已发布候选重复确认。
- React DOM 测试先在空组件上 4 项失败，再实现主流程、权限切换、结果核实和平台边界。
- 原生入口/资源新增测试初轮 2 项失败（Page 403、资源 404），同步 App 与挂载构建资源后通过。
- 生命周期测试先确认未卸载、后台挂载失败，再修复；资源加载失败测试先失败，再用浏览器 load/error 事件处理。
- `npm test --prefix frontend`：**21 passed**（14 状态、4 React DOM、3 Desk 加载器）。DOM 测试仅对 jsdom 缺少的浏览器布局能力作替身；不伪造业务状态转换。
- `.venv/bin/python -m pytest tests -q --tb=short`：**43 passed in 25.38s**，包括原生登录/静态资产、普通身份实时连接、外部 Origin 拒绝、新 Page 读取及 Guest 拒绝、既有 ERP/DSH 工具链回归。
- `npm run build --prefix frontend`：通过。
- `antd lint frontend/src --version 5.27.6 --format json`：0 issues。CLI 默认最新版本规则曾对 v5 的 Alert.message 报 v6 弃用警告，按项目准确版本重新验证后无问题，没有改用不匹配 API。
- 官方 npm registry 的 `npm audit --prefix frontend --registry=https://registry.npmjs.org --audit-level=high`：0 vulnerabilities；本机默认镜像未提供 audit 接口，未将镜像 404 当作审计通过。

## 真实浏览器验证

在现有管理员原生会话中检查；演示角色选择没有修改真实用户权限：

- 需求 → 候选 v2 → 发布确认 → 演示成功 → 报修页面 → 保存 `演示空压机 A / 运行时异响`，表格显示 `DEMO-1 / 待分配`。全程有合成数据与不写 ERP 提示。
- 切换演示企业后，旧报修、发布菜单和候选状态清空，回到工作台。
- 在构建页切为业务员工后，构建入口消失，当前页显示“无权访问此空间”。
- 平台运维只显示平台管理、账号入口；平台页面仅技术示例，没有客户业务记录。
- 发布失败、迁移/配置冲突、结果不明及部分成功均实际点击验收；失败不出现设备维修菜单，未知结果只提供核实入口，部分成功列出“创建演示字段”并阻止重发。
- 核实未知任务后显示“没有再次执行发布”，允许打开演示业务页。
- 1280×720 截图检查了七标签构建布局和确认弹窗，没有遮挡主要确认操作。
- 独立审查发现 Ant Design body portal 可能在原生切页后残留。修复前点击 App Logo 返回原生主页，确实残留确认弹窗；修复后同一路径 dialog 数量为 0，通过原生全局搜索返回后可重新构建。

## 原生扩展契约与资源

固定镜像内核验：

- `frappe/model/sync.py` 同步自定义 App 的标准 Page JSON；`frappe/modules/import_file.py` 原生导入负责校验标志及创建 Module Def，不开启 developer_mode、不直接 SQL。
- `frappe/desk/desk_page.py` 的 `getpage` 实际允许 Guest 调用，因此 Page 显式配置原生 `Desk User` 角色，由 `is_permitted()` 拒绝 Guest；不是只靠 `/app` 登录跳转。
- `views/container.js` 在切页触发 jQuery `hide`；`views/pageview.js` 调用 `on_page_show`。据此卸载 React，清除 portal、事件与内存；返回重新挂载。
- 固定版 `frappe.require` 没有资源失败 reject 路径，失败时可能留下全局 freeze。项目 Page 用浏览器原生 script/link 的 load/error 加载自有产物，不冻结 Desk；失败在页面显示明确错误。不是修改上游加载器，也不是静默降级。
- 仅增加 nginx 对自有 `public/` 的只读资产挂载。没有新增进程、容器或提高资源限额，仍为 3712 MiB / 2 CPU；前端依赖约 201 MiB、产物约 1 MiB。未提交 node_modules、dist、运行状态或日志。

准确版本：Node `26.7.0`、npm `11.19.0`、React/React DOM `18.3.1`、Ant Design `5.27.6`、esbuild `0.28.2`、Vitest `4.1.11`、jsdom `30.0.1`。依赖锁定在 frontend/package-lock.json。仅声明当前 macOS 与固定容器实测。

参考：[React createRoot](https://react.dev/reference/react-dom/client/createRoot)、[Ant Design 官方集成说明](https://ant.design/docs/react/use-with-vite/)，组件属性另外通过版本为 5.27.6 的官方 CLI 元数据核验。

## 本地复现

```sh
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
docker compose -f infra/compose.validation.yml up -d --no-deps frontend
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-validation.localhost clear-cache
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-validation.localhost execute frappe.model.sync.sync_for --kwargs '{"app_name":"dsherp_bridge","force":1}'
docker compose -f infra/compose.validation.yml exec -T backend bench --site dsherp-validation.localhost clear-cache
.venv/bin/python -m pytest tests -q --tb=short
```

新增模块前先清缓存再同步；否则长期运行站点缓存的 app_modules 可能还没有本模块。Frappe 会缓存 Page 脚本；本地修改后使用原生重新加载清除旧页面缓存，不能以旧标签页判断新代码。当前固定文件名资源仅用于本地原型，生产发布还需制品版本及资源缓存策略。

## 下一阶段决策

进入真实身份接入前，需确认统一身份的 Site 拓扑：建议独立平台 Frappe Site 负责认证及跨企业成员关系，业务 Site 保持独立用户与业务权限。用户已确认采用此方案；实施状态另见阶段 3 记录，也未把当前演示企业选择变成真实跨站点授权。必须核对原生认证协议、资源与登录切换行为，不用相同邮箱推断成员关系。

本轮没有新增付费模型调用、真实应用生成/迁移、生产发布或 Git 推送。真实 ERP 写入仅阶段 1 已记录的合成草稿。
