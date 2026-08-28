# 阶段 1：原生 Desk 验证记录

日期：2026-08-28。状态：服务接入完成，登录页已实测；阶段 1 尚未完成。

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

尚未输入或读取管理员密码。后续需获准将本项目 `.runtime/control/admin_password` 中的测试管理员密码输入上述本地站点，或由用户自行登录。浏览器凭证输入需要操作时授权。

## 待验收

- 使用原生 setup wizard 完成合成企业初始化（不得跳过原生初始化）。
- 中文 Workspace、物料列表、单据表单、角色管理。
- 中文搜索、字段布局、明细录入、日期数字显示。
- 普通角色与管理角色页面差异、直接请求权限拒绝。
- 后台实际任务及登录后实时事件。
- 根据实测确定必要扩展；阶段 2 原型及阶段 3–6 尚未实施。

本轮无新增付费模型调用、库存/账务提交或生产部署。既有真实模型证据仍为此前记录，本轮自动化模型侧使用本地替身。
