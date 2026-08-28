# 真实 Agent 只读工作台

用户批准：登录 → 选择企业 → 自然语言需求 → Agent 实际读取 ERP → 结果与执行记录。
沿用阶段 3 平台身份方案；当前 main 分类提交，不推送。只读，不扩展写入/生成发布范围。

## 实施

1. 平台 DS Agent Task 持久化问题、所属用户/企业、状态、结果、工具记录。用户只可读自己的任务，每次请求重新检查成员。提交有 request_id 幂等；已有任务不自动重跑。
2. 专用 worker 用户只能领取任务；每次领取生成短期任务 capability，只能对该任务调用三个只读工具/提交结果，不拿业务凭证。平台服务器以任务所属用户重新校验真实业务权限。过期/撤销/结束后 capability 失效。
3. 本机协调进程调用固定版 SDK，在每个任务的独立受限 Docker 容器运行 DSH+MCP。只挂载运行代码/依赖与本任务凭据，不挂载其他任务、项目 .env 或控制面文件；单任务并发。业务工具仅 schema、record、search（原生 get_list，返回不超过 20 条获准名称）。
4. React + Ant Design 替换手工记录查询：问题输入、运行状态、回答、实际工具记录、个人任务历史；切换企业隔离旧状态，刷新可恢复任务。失败明确展示，不用预设答案。
5. 先 TDD；真实数据库权限测试、真实 SDK/MCP/ERP+本地模型替身、浏览器，以及获授权后最多三次付费真实模型分别验收。无新增授权不付费调用。

## API 契约

模块 `dsherp_platform.agent_api`，Frappe message 封装：
- POST submit_task(enterprise, question, request_id) → task
- GET list_tasks(enterprise) → task[]（最近20条）
- GET get_task(task_id) → task
- POST claim_task()，专用 DS Agent Worker 角色 → null 或 {task_id, question, capability}
- POST task_tool(task_id, capability, tool, arguments) → ERP工具返回值，工具 `erp_read_schema` / `erp_read_record` / `erp_search_records`；arguments 为JSON字符串或dict；不接受企业/用户/URL参数。
- POST finish_task(task_id, capability, status, answer='', error='') → task_id；status Succeeded/Failed，成功必须有实际读取，不接受伪造工具记录。
- GET/POST worker_heartbeat()专用worker角色，用于提交时明确报告服务不可用。
Task前端结构：{id,enterprise,question,status,answer,error,created,events:[{tool,arguments,status,result?}]}。
状态 Queued/Running/Succeeded/Failed。能力凭证只返回worker，不返回用户。工具记录由平台实际调用时保存。

## 验收边界

真实业务链使用合成企业数据，但所有读取/权限/保存都是真实执行。生产部署、注册开通、跨站SSO、草稿写入不在本次范围。模型费用授权与测试运行分别记录。容器资源合计维持4GiB/2CPU，给按需Agent容器预留384MiB/0.1CPU，原alpha后端从1024MiB降为640MiB、0.6降为0.5，实际回归确认足够。
