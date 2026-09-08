# 16 条历史失败运行（schema v1，**永不计分**）

这些是计划 1 从 alpha 验收站 `dsherp-validation.localhost` 导出的真实失败运行。
它们是**审计事实**：某年某月某次运行确实这样失败过。所以文件保持 v1 原样，
不改写、不补期望、不迁站——改写一条审计记录去让它计分，就不再是它记录的那件事了。

计分的是 `evals/cases/dsherp-daily.localhost/` 下的 v2 用例；下面 10 条在那里有改基后的对应物，
每条的 `origin.run_id` 指回这里的 `run_id`。

## 改基为计分用例的 10 条

| 原 run_id | 域 | 改基为 |
|---|---|---|
| `050d7d49…` | operation | `rebased-po-draft-01` |
| `41dd8ca5…` | operation | `rebased-po-confirm-02` |
| `8941949e…` | operation | `rebased-po-create-03` |
| `31a70310…` | operation | `rebased-sco-po-04` |
| `450d3862…` | operation | `rebased-sco-draft-05` |
| `b56d6df5…` | operation | `rebased-so-read-06` |
| `d56fb6a5…` | operation | `rebased-so-create-07` |
| `fd5650b4…` | operation | `rebased-so-propose-08` |
| `c22b5984…` | query | `rebased-item-read-09` |
| `c73a3a3b…` | operation | `rebased-so-operation-10` |

改基做了三件事：站从 alpha 换成隔离评估站 `dsherp-daily.localhost`；问题里的公司与仓库名换成
daily 站真实存在的合成名；**不复刻原来的失败**——原失败是 `RuntimeError`/`TimeoutExpired`，
那是计划 2 修掉的运行底座故障，不是 Agent 质量。改基后每条测的是这次交互**本该**怎么走。

`c73a3a3b…` 这条要特别小心：它的 `sources` 与 `proposals` **皆为空**，机械派生只会得到一个空的
工具序列，而服务端拒绝「没有任何来源」的成功结束——它的回放脚本必须真的读一次记录。

## 不改基、也永不计分的 6 条

| 原 run_id | 为什么 |
|---|---|
| `786b2f1c…` | 计划 2 C3 故障注入（provider unavailable synthetic 3），衡量的是可靠性回归，不是 Agent 质量 |
| `8a7fe8d4…` | 同上（worker not claiming synthetic） |
| `b7f4ddf0…` | 同上（provider unavailable synthetic 2） |
| `e6470cc4…` | 同上（provider unavailable synthetic 1） |
| `d0210524…` | 「运行已过期，未自动重试」——队列与租约的行为，属计划 2 |
| `ec80de7d…` | 「当前用户已无法读取会话来源」——权限撤销后的读取边界，属计划 3/4 |

四条 C3 用例带完整事件流（各 19 条），是这批里唯一有事件流的：事件流上线之前导出的 12 条都是空壳。
这本身也是一条事实——**评估集能测到什么，取决于当时记录下了什么**。
