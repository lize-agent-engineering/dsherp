# PR #9 第三轮复核：1cdb9f8

日期：2026-09-07。本地与远端 HEAD 均为 `1cdb9f8a49704964b33798bf1de202b28fdd157d`，45 个提交，[PR #9](https://github.com/lize-agent-engineering/dsherp/pull/9) 仍 OPEN。

结论：**第二轮三个原反例已通过复验；R4 的新增状态处理引入一处停用状态回归，尚不建议合并。** 本次仅审查、运行探针和记录结果，没有修改业务代码或提交、推送、合并。

## 唯一新增发现：R4 / P1，恢复会重新启用管理员已停用的企业

位置：[admin.ensure_enterprise](../../dsherp/admin.py)，第 364–365 行；由 `provision_tenant(closed=True)` 和后续普通开通连续调用。

关闭式开通将所有非 Provisioning 状态改成 Provisioning，包含既有 Disabled、Failed。随后普通开通把 Provisioning 改成 Ready。因此恢复不只处理站点数据，也撤销了原先的企业停用/失败状态。平台 `_binding` 只允许 Ready，这个变化会重新打开平台侧的企业访问条件。

函数文档明确写着 `Disabled or Failed is left alone either way`，但实现不符合这一约定。前一版本普通开通不会主动改写这两种既有状态。

本轮在真实 platform 数据库中创建独立合成企业，执行 `ensure_enterprise` 实际生成的两段脚本；只替换 commit 为 no-op，最后统一 rollback。没有操作真实企业，也没有真正建站、轮换凭据或修改权限。结果：

| 初始状态 | 关闭式开通后 | 普通开通后 | 预期 |
| --- | --- | --- | --- |
| Disabled | Provisioning | Ready | 保持 Disabled，启用应有独立明确动作 |
| Failed | Provisioning | Ready | 按现有函数约定保持 Failed |
| Ready | Provisioning | Ready | 该恢复次序可接受 |

回滚后确认所有合成企业均不存在。复现脚本：[enterprise_state_probe.py](../../work/plan4-pr9-review3/enterprise_state_probe.py)。

```sh
.venv/bin/python work/plan4-pr9-review3/enterprise_state_probe.py
```

最小修正：关闭式开通仅把可运行状态转入 Provisioning，保留既有 Disabled/Failed；新企业仍以 Provisioning 创建，正常恢复再转 Ready。增加调用真实状态处理逻辑的行为回归，覆盖新企业、Ready、Disabled、Failed。当前 R4 组合测试替换了 `ensure_enterprise`，验证的是传入的状态参数，没有执行此次有问题的状态转换。

## 三项原反例的复验

| 原项 | 结果 | 边界 |
| --- | --- | --- |
| R3，恢复目录不可用却先签发 | 通过：`remote_key_rotated_before_refusal=false`，旧 profile 未动，明确 Fault | 真实宿主函数与临时文件系统，远端签发使用现有替身；签发后副本写入失败时按本轮明确选择交付值，使用合成密钥验证 |
| R6，清除提交后误删新目录 | 通过：`new_session_survived=true`；保持覆盖结算与目录清理，只删枚举的 scope | 原审查探针独立重跑；新增单元验证保持与归还。没有重复清除 dev 读者内容 |
| R4，首次企业 Ready/发布入口早于维护 | 原反例通过：关闭式开通先维护、企业 Provisioning、不发布入口；不接受 closed 参数的注入函数明确拒绝 | 真实 provision_tenant 与 restore_site 的组合测试独立重跑；上面的既有状态回归仍需修正。未执行真机恢复 |

## 验证与当前状态

- 非集成完整独立重跑：**664 passed in 112.76s**。
- R3/R6 独立重跑上轮探针对应函数；R4 两个新增组合/拒绝测试随完整非集成套件通过。
- 本轮没有修改 Frappe App 代码、DocType 或集成夹具；此前 `19db846` 的八项定向集成证据保留，但不标为本轮重跑。执行方本轮七项定向集成与上一轮完整 210 项，均保留为执行方报告。
- 本轮没有暂停 dev 服务、重新 migrate、调用真实 provider 或部署。核验时 scheduler 两容器运行、常驻 worker 为 running，validation 心跳距核验 3.3 秒、在途 0、`dsherp_hold=0`。
- 真实平台探针已 rollback 并确认清理。保留审查文档和可复现脚本，没有留下临时合成企业。

R1、R2、R5、R7、R8、R9 保留上一轮关闭结论，R3/R6 的原残余可关闭。R4 原窗口问题已修，但本轮状态回归未关闭。继续停在阶段 5；G3 异机、真实 RPO/RTO、systemd 实际触发、真机 restore-site 与数据库直接访问边界，均不因本次代码复核自动转为通过。
