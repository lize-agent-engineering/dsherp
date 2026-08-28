# 最小 DSH 验证证据

日期：2026-08-28。SDK/Runtime `0.1.1rc1`，项目 Python `3.12.11`，Runtime 内置 Node `v24.19.0`。来源和制品哈希见 [版本基线](runtime-baseline.md)。

## 结果分层

| 层次 | 实际结果 |
| --- | --- |
| 文档/源码 | 固定版本接口已核对，安装后的三个主要 Python 源文件与 tag 提交哈希一致 |
| 依赖 | 项目 `.venv` 安装成功；`uv pip sync --python .venv/bin/python --require-hashes requirements.lock` 成功，初始 DSH 环境 12 包；加入 MCP 后为 36 包 |
| TDD | 初始 13 项失败；实现后 12 通过，1 项错误的未知模型初始化假设被真实运行纠正；改用缺失配置测试启动失败。CLI 新增 2 项先失败再实现；错误响应测试先失败再修正。最终 17 项通过 |
| SDK/Runtime 本机进程 | 真实二进制启动、initialize、会话事件、关闭成功；启动失败/回调异常/token 截断后实际 Popen 子进程均已退出 |
| 工具限制 | 捕获真实 Runtime 发往本地模型替身的 HTTP 请求，`tools` 为空；不是 grep YAML 或 dump-config 的结论 |
| 真实 DSH 模型调用 | **通过：用户授权后连接 DeepSeek 官方，deepseek-v4-flash 返回预期标记，Runtime 退出 0** |
| ERP / UI / 部署 | 后续已完成隔离 ERP 与只读工具链，见 ERP 证据；UI 和生产部署仍未执行 |

测试命令：

```sh
.venv/bin/python -m pytest tests -q
```

结果：`17 passed in 21.29s`，退出 0。模型替身仅监听动态 `127.0.0.1` 端口，测试结束关闭；未替换 SDK、Runtime 或实际子进程。上述自动化测试没有付费 provider 请求；后续真实调用单独记录如下。测试正常返回字面量 `DSHERP_OK`，不能把这个替身响应当成真实模型回答。

## 实测修正与限制

- rc1 不在初始化时拒绝未知模型名，调用方必须明确提供 provider 实际支持的 model；不能照搬 master 的校验承诺。
- 空响应可触发上游内部重试；项目不会在失败后另起一次调用。64 output tokens 是每个模型请求限制，不是整个运行的费用硬上限；30 秒 SDK timeout 也不等价于预算计量。真实调用需要明确费用授权。
- 仅 `completed` 且响应去空白后等于 `DSHERP_OK` 视为此次合成验证成功。截断、无响应、错误内容都失败。
- CLI 不输出原始响应、SDK traceback 或 Runtime diagnostics，避免供应商错误中夹带凭证；只输出错误类别或缺失配置名称。Python API 仍向调用方抛出原异常，不能直接作为生产日志内容。
- 进程清理证据覆盖本阶段零工具 Runtime；不声称已验证任意插件的进程树、SIGKILL 恢复或容器隔离。
- 会话目录用 TemporaryDirectory，每次独立并在关闭后删除；业务持久化不在本验证范围。SDK 仍继承 ambient env，不能作为租户隔离边界。
- Node 版本通过临时完整 Cordis 组合的 `persona: !!js process.version` 求值，读取本地模型替身收到的 system message 得到 `v24.19.0`；临时配置已删除。这不是 SDK 提供的版本 API。

## 真实调用（已完成）

用户完成项目 `.env` 配置并明确授权试调用后，只从本项目该文件解析三个预期键，不执行文件中的 Shell 代码、不扫描其他项目或个人凭证。确认 endpoint 为 DeepSeek 官方 HTTPS 后，以 `.venv/bin/python` 调用 `run_probe(settings, Path("work").resolve(), observe)` 一次；额外的 `observe` 仅收集事件类型和结束原因，记录实际子进程退出码。

- endpoint：`https://api.deepseek.com`
- provider/model：`deepseek-official` / `deepseek-v4-flash`
- SDK/Runtime：`0.1.1rc1`；Python `3.12.11`；内置 Node `v24.19.0`
- 固定提示：`Reply exactly DSHERP_OK.`；每请求 output cap 64 tokens
- 结果：`finish_reason=completed`，响应匹配 `DSHERP_OK`
- 耗时：1.46 秒；验证命令退出 0；启动 1 个 Runtime，实际退出码 0
- 根结束事件：仅记录到一个 `completed`
- 脱敏事件类型：`agent/inbox/spliced`、`assistant/chunk`、`assistant/message`、`request/context`、`request/header`、`session/title`、`step/end`、`step/start`、`turn/end`、`turn/start`、`user/message`

这次是官方真实模型调用，不是 SSE 替身。未采集精确 token 用量或账单金额，不声称费用为零。没有执行 ERP 工具或库存/账务动作。自动化测试证明同一组合发出的工具列表为空；本次没有抓取官方请求内容，不把事件列表当作完整网络审计。

`.env` 保留在本地且被 Git 忽略；未打印、提交密钥。临时会话与脱敏中间 JSON 已清理。验证器仍只读取环境变量，不自动加载 `.env`；本次由调用方临时解析配置传给 Python API，没有改动程序逻辑。

直接 CLI 的入口仍为 `.venv/bin/python -m dsherp.dsh_probe`，需要该 CLI 所在进程环境已含 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`。普通终端的 export 不会传回已经运行的 Codex 进程。

## ERP 工具链补充

已在 [ERP 证据](erpnext-integration-evidence.md) 单独记录真实 Runtime→MCP→ERP 链；该链的模型侧为本地替身，未扩大本次官方付费模型调用范围。当前全部测试 36 项通过，不替换本文件此前真实官方调用的历史证据。
