# 最小 DSH 验证证据

日期：2026-08-28。SDK/Runtime `0.1.1rc1`，项目 Python `3.12.11`，Runtime 内置 Node `v24.19.0`。来源和制品哈希见 [版本基线](runtime-baseline.md)。

## 结果分层

| 层次 | 实际结果 |
| --- | --- |
| 文档/源码 | 固定版本接口已核对，安装后的三个主要 Python 源文件与 tag 提交哈希一致 |
| 依赖 | 项目 `.venv` 安装成功；`uv pip sync --python .venv/bin/python --require-hashes requirements.lock` 成功，12 包 |
| TDD | 初始 13 项失败；实现后 12 通过，1 项错误的未知模型初始化假设被真实运行纠正；改用缺失配置测试启动失败。CLI 新增 2 项先失败再实现；错误响应测试先失败再修正。最终 17 项通过 |
| SDK/Runtime 本机进程 | 真实二进制启动、initialize、会话事件、关闭成功；启动失败/回调异常/token 截断后实际 Popen 子进程均已退出 |
| 工具限制 | 捕获真实 Runtime 发往本地模型替身的 HTTP 请求，`tools` 为空；不是 grep YAML 或 dump-config 的结论 |
| 真实 DSH 模型调用 | **未验证：缺项目专用 key、endpoint/model 与费用授权范围** |
| ERP / UI / 部署 | 未执行，本脚本不含 ERP 工具、前后端或部署 |

测试命令：

```sh
.venv/bin/python -m pytest tests -q
```

结果：`17 passed in 21.30s`，退出 0。模型替身仅监听动态 `127.0.0.1` 端口，测试结束关闭；未替换 SDK、Runtime 或实际子进程。没有付费 provider 请求。测试正常返回字面量 `DSHERP_OK`，不能把这个替身响应当成真实模型回答。

## 实测修正与限制

- rc1 不在初始化时拒绝未知模型名，调用方必须明确提供 provider 实际支持的 model；不能照搬 master 的校验承诺。
- 空响应可触发上游内部重试；项目不会在失败后另起一次调用。64 output tokens 是每个模型请求限制，不是整个运行的费用硬上限；30 秒 SDK timeout 也不等价于预算计量。真实调用需要明确费用授权。
- 仅 `completed` 且响应去空白后等于 `DSHERP_OK` 视为此次合成验证成功。截断、无响应、错误内容都失败。
- CLI 不输出原始响应、SDK traceback 或 Runtime diagnostics，避免供应商错误中夹带凭证；只输出错误类别或缺失配置名称。Python API 仍向调用方抛出原异常，不能直接作为生产日志内容。
- 进程清理证据覆盖本阶段零工具 Runtime；不声称已验证任意插件的进程树、SIGKILL 恢复或容器隔离。
- 会话目录用 TemporaryDirectory，每次独立并在关闭后删除；业务持久化不在本验证范围。SDK 仍继承 ambient env，不能作为租户隔离边界。
- Node 版本通过临时完整 Cordis 组合的 `persona: !!js process.version` 求值，读取本地模型替身收到的 system message 得到 `v24.19.0`；临时配置已删除。这不是 SDK 提供的版本 API。

## 真实调用入口（等待授权）

调用方通过当前进程环境提供 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`。不扫描其他项目、个人 DSH home 或钥匙串；不把密钥写入 Git、文档或命令参数。

```sh
.venv/bin/python -m dsherp.dsh_probe
```

当前环境直接执行得到退出 1：`DSH probe failed: missing DEEPSEEK_API_KEY, DSH_MODEL, DEEPSEEK_BASE_URL`。未启动 Runtime、未发送真实模型请求。

拿到授权后仍需记录真实 provider 的脱敏事件与退出状态，才能勾选首次计划的真实 DSH 调用完成项。
