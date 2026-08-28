# 最小 DSH 调用实施计划

> 当前任务顺序执行，使用 executing-plans 与 TDD，不委派、不创建门禁。

**目标：** 固定 `0.1.1rc1` 后验证零工具会话、事件消费、失败与关闭。不构建应用服务或 ERP 全系统。

**设计：** [首版设计](../specs/2026-08-28-dsherp-design.md)；[准确接口基线](../../engineering/runtime-baseline.md)。

**技术栈：** Python 3.12.11、SDK/Runtime 0.1.1rc1、pytest 8.4.2；测试用标准库临时 HTTP 服务替代收费模型，真实 SDK 与真实 Runtime 不替换。

## 任务 1：最小验证器

文件：`dsherp/dsh_probe.py`、`config/dsh-no-tools.yml`、`tests/test_dsh_probe.py`、`tests/conftest.py`、`requirements.in`、`requirements.lock`。

接口：`run_probe(settings: Mapping[str, str], root: Path, on_notification=None) -> RunResult`；从显式 settings 读取 `DEEPSEEK_API_KEY`、`DSH_MODEL`、`DEEPSEEK_BASE_URL`。验证器只执行固定合成提示 `Reply exactly DSHERP_OK.`，输出上限 64 tokens，单请求超时 30 秒，关闭超时 2 秒。每次创建独立临时目录并在关闭后清除，不接受生产业务提示。

- [x] 先写测试：空 key/model/base URL 在启动前 `ValueError`；输入空白同样拒绝。
- [x] 执行 `.venv/bin/python -m pytest tests/test_dsh_probe.py -q`，确认缺实现的失败。
- [x] 写最小参数验证及完整 Cordis 组合，关闭 Bash/jobs/skills/workspaceContext，不加载工具插件。
- [x] 写标准库本地 SSE 模型替身。测试运行真正 Runtime，断言捕获请求 `tools` 为空、max_tokens=64、消费根 `turn/end` 和 callback；最终响应为替身字面量 `DSHERP_OK`。
- [x] 先看失败，再用 `DeepSeekHarness(..., cordis=...)`、`harness.run(..., on_notification=...)`、`finally: harness.close()` 实现。
- [x] 分别测试错误结束原因、通知回调异常、初始化失败，检查新启动的实际子进程已经退出；不只断言 close 被调用。
- [x] 增加空响应/不符合预期响应失败测试，成功后重跑全部测试。使用 `uv pip compile requirements.in --generate-hashes -o requirements.lock` 生成锁，不添加 CI/门禁。

正常配置由 Python 直接传入，CLI 从环境读值，不自动读取其他目录的密钥；错误不得输出密钥或 Runtime 原始诊断。CLI 命令：`.venv/bin/python -m dsherp.dsh_probe`。

## 任务 2：真实调用与证据

- [x] 用户配置项目凭证、model/endpoint 并授权试调用后，以临时 Python 调用方执行同一 `run_probe` 一次（不是直接 CLI）；未获得长期费用预算，不扩大调用范围。
- [x] 记录 SDK/Runtime/内置 Node 版本、退出状态和脱敏事件种类。不得记录密钥、原始会话或授权头。
- [x] 无凭证阶段如实记录阻塞；取得凭证后单独记录真实调用证据，不以 SSE 测试替代。
- [x] 按功能提交基线文档与最小调用验证器，更新 README 和原计划实际状态；清理不再需要的 work/ 下载。

ERP MCP 工具与租户安全隔离不包含在此零工具验证器中；待隔离站点就绪后独立制定准确接口测试，不以替身数据代替 ERP 验证。

执行记录：17 项测试通过；完整测试含真实 Runtime + 本地 SSE 模型替身。空响应由上游错误结束路径拒绝，不匹配响应由验证器拒绝。用户授权后真实官方调用通过：deepseek-v4-flash，completed，预期响应匹配，Runtime 退出 0；见 DSH 验证证据。
