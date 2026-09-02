# frappe.io 官方主站地图（版本动态与发布渠道）

来源 `https://frappe.io/sitemap.xml`，2026-08-31 统计约 1450 页。frappe.io 是 Frappe/ERPNext 官方主站：产品介绍、**版本发布与动态更新渠道**、公司手册、知识库；技术文档在 docs.frappe.io（见 [frappe-docs-map.md](frappe-docs-map.md)）。

## 版本现状（版本动态 2026-08-31；项目运行基线 2026-09-02）

- ERPNext 最新 release：`v16.33.0`（2026-08-25）；Frappe 框架 v16 线最新 `v16.32.0`。**v16 是现行主线。**
- v15 维护线并行发版（Frappe `v15.119.1` 2026-08-27）：官方双线制——主线之外，各稳定版本线每周二例行发版（见发布流程）。这是 v15 历史证据的上游维护背景，不是项目当前运行版本。
- 项目当前隔离合成容器运行 Frappe `16.31.0` / ERPNext `16.33.0`，固定镜像 digest 为 `sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd`。字段、签名和行为以这两个固定 tag 源码与实际 Site 为准；C4/C5 未完成，不据此宣称迁移整体完成或可上线。
- v15 时期历史基线为 Frappe `15.118.0` / ERPNext `15.119.3`；只用于读取旧证据，不是当前运行口径。
- v16 官方口径（发布页）：50+ 新功能（会计/制造/库存等）、后台任务处理更可靠、大数据集性能提升、Framework 界面重构。

## 动态更新渠道（按权威度排序）

| 渠道 | URL | 说明 |
|---|---|---|
| GitHub Releases API | `https://api.github.com/repos/frappe/erpnext/releases`、`.../frappe/releases` | 权威、可脚本化；`/releases/latest` 返回的是标记 latest 的 release（可能是 v15 维护线），看主线用列表接口 |
| v16 发布页 | https://frappe.io/releases/version-16 | 官方大版本发布说明 |
| The Frappe Times | https://frappe.io/times （月度归档如 /times/july-2026） | 全线产品月度动态，2026-07 仍在更新 |
| 发布说明博客 | https://frappe.io/blog/release-notes 、 https://frappe.io/blog/product-updates | 版本与产品更新文章 |
| 发布流程手册 | https://frappe.io/handbook/release_process | 双线制：develop 主线不定期发；稳定版本线每周二经 hotfix 分支发版；语义化版本自动生成 |

检查项目当前 v16 版本线是否有新补丁（结果只用于发现，不自动改变固定基线）：

```bash
curl -s "https://api.github.com/repos/frappe/frappe/releases?per_page=10" | grep -o '"tag_name": "v16[^"]*"'
```

## 栏目总览（sitemap 页数）

| 栏目 | 页数 | 说明 |
|---|---|---|
| `blog` | 819 | 博客（含 release-notes、product-updates、announcements 等分类） |
| `erpnext` | 156 | ERPNext 产品/功能介绍页（营销口径，非技术文档） |
| `handbook` | 108 | Frappe 公司手册（发布流程、工程实践） |
| `newsletters` | 42 | 邮件通讯归档 |
| `times` | 38 | The Frappe Times 月度动态 |
| `cloud` | 30 | Frappe Cloud 产品页 |
| `hr` / `crm` / `helpdesk` / `framework` / `learning` / `lending` / `school` | 6–16 each | 各产品介绍页 |
| `kb` | 4 | 知识库零散文章（accounting/selling/hr/customization 各 1） |

robots.txt 禁抓 `/app/`、`/files`、`/pages` 等运行时路径；以上公开栏目均可直接访问。

## 使用规则

1. 查"某版本有什么新特性/是否已修复"→ 先 GitHub Releases API，再发布页/博客佐证。
2. 跟踪生态动态（新产品、路线图）→ The Frappe Times 月度页。
3. frappe.io 产品页是营销口径，不作技术依据；技术事实仍按 SKILL.md 主规则：实际站点 metadata 与 GitHub tag 源码优先。
