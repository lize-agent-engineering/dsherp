# docs.frappe.io 文档地图

来源 `https://docs.frappe.io/sitemap.xml`，2026-08-31 统计约 6668 个 URL。找不到准确 URL 时先 grep sitemap，不要猜：

```bash
curl -s https://docs.frappe.io/sitemap.xml | grep -o '<loc>[^<]*关键词[^<]*</loc>'
```

## 空间总览

| 空间 | 页数 | 入口 | 说明 |
|---|---|---|---|
| `erpnext` | 887（现行） | /erpnext/user/manual/en/introduction | ERPNext 用户手册（现行≈v15） |
| `framework` | 218（现行） | /framework/user/en/introduction | Frappe 框架开发文档 |
| `cloud` | 183 | /cloud | Frappe Cloud 托管 |
| `hr` | 148 | /hr | HR/Payroll（v15 起独立 app） |
| `lending` | 62 | /lending | 借贷（v15 起独立 app，原 loan-management） |
| `helpdesk` | 45 | /helpdesk | Helpdesk |
| `education` | 44 | /education | Education |
| `books` | 42 | /books | Frappe Books |
| `crm` | 42 | /crm/introduction | Frappe CRM（独立 app，非 ERPNext 内置 CRM 模块） |
| `insights` | 26 | /insights | BI 分析 |
| `drive` | 11 | /drive | 文件协作 |
| `builder` | 4 | /builder | 网页构建器 |

另有 `customer-guide`、`partner-guide`、`learning`、`wiki` 等辅助栏目，以及 `framework-copy`、`test`、`index2` 等脏数据条目，忽略。

## ERPNext 用户手册

- 现行版 URL 两种等价形式：sitemap 里是 `/erpnext/user/manual/en/<slug>`，会 301 到规范短路径 `/erpnext/<slug>`（如 `/erpnext/sales-invoice`）。slug 为英文术语 kebab-case：`sales-invoice`、`payment-entry`、`work-order`、`subcontracting-inward`。
- 现行版 884 页为平铺结构，无模块目录。sitemap 中的 v14 归档路径 `/erpnext/v14/user/manual/en/<模块>/<页>` 保留了「模块/slug」结构，可用来按模块发现 slug；但访问这些 URL 会 301 到现行版 `/erpnext/<slug>`，得到的不是 v14 内容。
- v14 归档路径的模块（sitemap 页数）：accounts 115、human-resources 96、setting-up 92、stock 65、customize-erpnext 56、manufacturing 44、education 39、selling 34、using-erpnext 27、erpnext_integration 25、CRM 24、projects 22、regional 21、buying 18、asset 17、loan-management 16、website 15、e_commerce 12、non_profit 12、support 9、quality-management 8、introduction 7、agriculture 7、hospitality 5、automation 4、customer-portal 4。
- 注意：v15 中 human-resources、loan-management 已拆分为独立 app，现行文档在 `hr`、`lending` 空间。

## Frappe Framework（现行版全页面清单）

前缀 `https://docs.frappe.io/framework/user/en/`。sitemap 中的 `/framework/v13|v14|v15/user/en/` 归档路径访问时均被 301 合并到现行版（部分 v15 路径还会先跳 v14 再跳现行版），不可用作版本固定依据；固定版本行为查 GitHub tag 源码。

### api（客户端/服务端 API 参考，21 页）

api/rest、api/document、api/database、api/query-builder、api/server-calls、api/background_jobs、api/realtime、api/jinja、api/utils、api/js-utils、api/logging、api/full-text-search、api/search（见 python-api/search）、api/form、api/controls、api/list、api/page、api/tree、api/chart、api/dialog、api/py-dialog、api/scanner

### basics（架构与 DocType 基础，23 页）

basics/architecture、basics/apps、basics/sites、basics/site_config、basics/directory-structure、basics/asset-bundling、basics/static-assets、basics/users-and-permissions、basics/why、basics/virtual_docfield、basics/doctypes、basics/doctypes/docfield、basics/doctypes/fieldtypes、basics/doctypes/naming、basics/doctypes/controllers、basics/doctypes/child-doctype、basics/doctypes/single-doctype、basics/doctypes/virtual-doctype、basics/doctypes/customize、basics/doctypes/actions-and-links、basics/doctypes/modules、basics/doctypes/form_%26_view_settings、basics/doctypes/frameworktatus

### python-api（5 页）

python-api/hooks、python-api/routing-and-rendering、python-api/response、python-api/language、python-api/search

### tutorial（10 页）

tutorial/install-and-setup-bench、tutorial/create-a-site、tutorial/create-an-app、tutorial/create-a-doctype、tutorial/types-of-doctype、tutorial/doctype-features、tutorial/controller-methods、tutorial/form-scripts、tutorial/portal-pages、tutorial/whats-next

### desk（报表与脚本，15 页）

desk/scripting、desk/scripting/client-script、desk/scripting/server-script、desk/scripting/script-api、desk/scripting/system-console、desk/reports、desk/reports/query-report、desk/reports/script-report、desk/reports/report-builder、desk/printing、desk/attachments、desk/workspace、desk/workspace/access、desk/workspace/blocks、desk/workspace/customization

### bench（30 页）

bench/bench-commands、bench/frappe-commands、bench/extending-the-cli、bench/reference（backup、bench-version、drop-site、execute、list-apps、migrate、new-site、partial-restore、reinstall、restore、set-config、show-config、transform-database、trim-database、trim-tables、uninstall-app）、bench/guides（adding-custom-domains、configuring-https、diagnosing-the-scheduler、lets-encrypt-ssl-setup、settings-limits、setup-multitenancy、setup-production）、bench/resources（background-services、bench-commands-cheatsheet、bench-procfile）

### guides（专题指南，70 页）

- 集成认证：guides/integration/rest_api（含 listing_documents、manipulating_documents、oauth-2、simple_authentication、token_based_authentication）、guides/integration/webhooks、guides/integration/how_to_set_up_oauth、guides/integration/openid_connect_and_frappe_social_login、guides/integration/google_calendar、guides/integration/google_gsuite
- 应用开发：guides/app-development/insert-a-document-via-api、executing-code-on-doctype-events、running-background-jobs、adding-custom-button-to-form、exporting-customizations、how-enable-developer-mode-in-frappe、connected-app、overriding-link-query-by-custom-script 等 20 页
- 测试：guides/automated-testing（integration-testing、unit-testing、qunit-testing）
- 部署：guides/deployment/migrations、how-to-migrate-doctype-changes-to-production、packages 等
- 门户：guides/portal-development/*（9 页）；报表打印：guides/reports-and-printing/*（5 页）；数据：guides/data/*；缓存：guides/caching

### integration / web-form / 顶层散页

integration/ldap-integration、integration/google_drive、integration/razorpay；web-form/settings、web-form/customization；顶层：installation、introduction、debugging、logging、testing、ui-testing、profiling、rate-limiting、translations、production-setup、database-migrations、database-optimization-hardware-and-configuration、security-faqs、portal-pages、using_frappe_as_oauth_service、microsoft-email-oauth、form-tours、audit-trail 等（38 页，全名单 grep sitemap）

## Frappe CRM（全 42 页）

前缀 `https://docs.frappe.io/crm/`：introduction、introduction/installation、setting-up、general-and-defaults、invite-users、inviting-your-team、lead、deal、contact、organization、note、task、call-log、comment、email-communication、email-template、view、pinned-view、public-view、quick-entry-layout、custom-fields、custom-statuses、custom-actions、custom-list-actions、custom-script、custom-branding、home-actions、assignment-rule、service-level-agreement、notification、web-form、capturing-leads/web-form、lead-syncing/meta、erpnext（与 ERPNext 集成）、exotel、twilio、whatsapp、data、profile、profile-and-preferences、mobile-app-installation、settings/sales-hierarchy
