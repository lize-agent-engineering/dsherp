---
name: erpnext-frappe-docs
description: Use when dsherp development needs official ERPNext or Frappe documentation — DocType concepts, business document flows, REST API usage, framework/bench/CRM/HR references, or Chinese-language ERPNext material — before writing integration code or explaining ERP behavior.
---

# ERPNext / Frappe 官方文档检索

## 定位与边界

本技能只解决"去哪查、怎么查官方文档"。文档用于理解概念、业务流程和官方口径；字段、可选值、方法签名以实际站点 metadata 和固定版本源码为准（见 [AGENTS](../../../AGENTS.md)「ERP 与租户」与 [erpnext-integration](../erpnext-integration/SKILL.md)）。

- 当前隔离合成运行基线（2026-09-02 实测）：ERPNext `16.33.0`、Frappe `16.31.0`、容器 Python `3.14.7`，镜像固定为 `frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd`。C4 冷静期/最终独立审计与 C5 尚未完成，不能表述为迁移整体完成或可上线。
- v15 时期历史基线：ERPNext `15.119.3`、Frappe `15.118.0`；只用于解释旧证据，不能作为当前字段、签名或运行行为依据。
- 中文术语以 [glossary.csv](../../../config/terminology/glossary.csv) 为准；erpnext.cc 译名仅供理解，冲突时用术语表。

## 文档源

| 源 | 语言/版本 | 适用 |
|---|---|---|
| `docs.frappe.io/erpnext/user/manual/en/<slug>` | 英 / 最新主线 | ERPNext 业务模块用户手册（约 884 个平铺页面） |
| `docs.frappe.io/framework/user/en/...` | 英 / 最新主线 | Frappe 框架开发：API、DocType、bench、教程 |
| `docs.frappe.io/crm`、`/hr`、`/cloud` 等 | 英 / 最新主线 | Frappe 各应用文档 |
| `frappe.io/...` | 英 / 动态 | 官方主站：版本发布页、The Frappe Times 月度动态、发布说明博客、handbook |
| `erpnext.cc/docs/V14/...` | 中 / V14 | 中文概念与流程理解，仅 51 页 |
| `erpnext.cc/best-practice/...` | 中 / 版本不一 | 社区实践文章（安装/开发/运维/财务实战），62 页，非官方规范 |

注意：docs.frappe.io 的版本归档路径（`/erpnext/v14/`、`/framework/v15/` 等）虽在 sitemap 中，访问时一律被 301 合并到现行版页面（2026-08-31 实测）；该站不提供固定版本文档。**现行主线已是 v16**（2026-08 起），项目当前容器也运行 v16，但文档“最新”口径、项目固定 tag 与实际容器版本仍须分别核验。需与当前安装严格对齐时，查 ERPNext `v16.33.0` / Frappe `v16.31.0` 的 GitHub tag 源码；v15 只保留为历史证据。版本动态与发布渠道见 [frappe-io-map.md](references/frappe-io-map.md)。

## 检索流程

1. 先查本目录索引：[frappe-docs-map.md](references/frappe-docs-map.md)（docs.frappe.io 地图＋框架全页面清单）、[frappe-io-map.md](references/frappe-io-map.md)（frappe.io 主站＋版本动态渠道）、[erpnext-cc-v14-index.md](references/erpnext-cc-v14-index.md)（中文 V14 全量索引）、[erpnext-cc-best-practice-index.md](references/erpnext-cc-best-practice-index.md)（中文社区实践 62 页全量索引）。
2. 索引未列出的 ERPNext 手册页：slug 为英文术语 kebab-case（`sales-invoice`、`payment-entry`）；不确定就 grep 线上 sitemap 取准确 URL：

   ```bash
   curl -s https://docs.frappe.io/sitemap.xml | grep -o '<loc>[^<]*payment-entry[^<]*</loc>'
   ```

3. 用 WebFetch 读具体页面。`erpnext.cc` 页面 URL 必须带尾斜杠、中文路径需百分号编码，否则 404。
4. 文档与实测行为冲突时以实测为准，差异记入 `docs/engineering/`。

## 常见错误

| 错误 | 纠正 |
|---|---|
| 凭记忆写 `docs.erpnext.com` 或凭空拼 URL | 旧域已废弃；一律先查索引或 sitemap |
| 把 V14 中文文档的字段/行为当作当前 v16 事实 | 仅用于概念理解；字段从实际站点发现 |
| 在 erpnext.cc 猜索引之外的路径 | 该站 V14 仅 51 页，索引即全集 |
| 在 docs.frappe.io 猜 `/zh/` 路径或引社区仓库当中文资料 | 官方手册仅英文；中文来源用 erpnext.cc V14 索引 |
| WebFetch erpnext.cc 不带尾斜杠 | 规范 URL 以 `/` 结尾 |
| 用 erpnext.cc 译名覆盖项目术语 | `config/terminology/glossary.csv` 优先 |
| 把 docs.frappe.io 的 vXX 归档 URL 当固定版依据 | 会 301 到现行版；固定版本行为查 GitHub tag 源码 |
| 把 v16 主线任意最新页面的新特性/字段当项目事实 | 以 ERPNext `v16.33.0` / Frappe `v16.31.0` 固定 tag 源码和实际 v16 Site 为准，不能把滚动主线文档直接当运行事实 |
