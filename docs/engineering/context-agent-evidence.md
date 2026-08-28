# 原生上下文 Agent 实施证据

## 2026-08-29：入口与第一批前端 TDD

持续目标 active，main 本地实施，不推送。完整范围见 [计划](../superpowers/plans/2026-08-29-context-agent-sidebar.md)。

- 现场 HEAD 起点 ffb7d52，工作区干净；计划及旧方向替代标记提交 b459297。
- 固定 Frappe 列表源码实读：get_filters_for_args()、get_checked_items(true)、cur_list 由 list_factory 切页设置；表单 is_dirty() 检查 __unsaved。
- 页面快照 7 项先失败（新适配器缺失），最小实现后全部通过。包括深冻结、发送时身份/版本、默认不含表单正文、显式字段/子表列、选中名称/筛选、旧表单不串页、未知页面及超预算拒绝。
- 侧栏 6 项先失败（未实现），实现后通过。打开仅恢复、无蒙层、关闭保留输入、发送时取快照、新会话屏蔽迟到结果、拒权清正文、取消按运行 ID、已完成历史仍重新授权轮询。测试 API 是边界替身，不能据此声称真实恢复/取消完成。
- 首次 UI 测试发现 Ant Design loading 图标影响按钮可访问名称；添加固定 aria-label，恢复期间禁用发送。重开测试等待真实异步恢复完成。
- `npm test`：7 files / 48 tests passed；`antd lint src/ContextSidebar.jsx --format json` 无问题。未新增 CI、发布门禁、角色体系。
- 尚未挂载侧栏到 Desk，未实现新业务 Site API、DSH 插件或跨进程恢复，未运行新真实模型/ERP/UI 验收；阶段一不标完成。

## 旧服务收敛（用户明确要求）

用户要求“不要加太多的门禁，之前旧的服务该停停”。执行前只读查询旧平台 DS Agent Task：2 条 Succeeded，无 Queued/Running。向已核实的旧付费 worker PID 39450 发送 SIGTERM；随后 pgrep 无进程、dsherp-agent 容器列表为空。没有丢弃运行任务、没有新付费调用。

保留原生 ERP、平台身份、数据库、Redis、原生 worker/scheduler/WebSocket，供后续会话、SSO 和配置验证；未动其他项目。旧付费工作台已不再接受实际执行，不再以该入口作为新功能可用性说明。不删除旧历史、凭证和数据卷。

下一步：接原生全局侧栏及受限同源 API；业务 Site 持久会话和权限来源；固定版原生 resume/cancel 插件协议测试。资源仍沿用现有授权，不默认增加常驻服务。
