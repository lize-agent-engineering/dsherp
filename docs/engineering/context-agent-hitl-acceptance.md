# 原生侧栏 HITL 真实验收

范围：本机 alpha 合成 Site，不是生产上线。固定 DSH 0.1.1rc1，已授权 deepseek-v4-flash，仍为一次一个384MiB/.1CPU容器。

## 独立普通用户登录

- 为保留 localhost 只读用户和127.0.0.1管理员会话，增加同一Site的确切本地域名 `http://dsherp-validation.localhost:18082`，没有新增服务、端口或资源。原生nginx Host/Origin配置明确增加该来源，外国Host仍421、外国Origin仍403。
- 真实HTTP测试先421，配置后 `test_desk_origin.py` 通过。只重建现有两个nginx容器使共享模板参数一致，业务后端/数据库未重建。平台既有Host范围未扩展。
- `infra/provision_context_writer.py` 建立合成普通用户 `dsherp-writer@example.invalid`，沿用现有Item Manager/Sales User；明确不能修改Administrator用户。原生User对自身的编辑权限不能误当作提权，预检以确切Administrator记录验证。
- 仅复制项目原有合成Item为专用DSHERP-HITL-ITEM。登录密码随机生成，只保存本地0600 profile，不输出或提交。保留此合成账号/物料用于后续业务验收，不用于日常企业Site。

## 真实模型提出修改，浏览器确认

- 浏览器从原生登录、物料列表、物料表单进入侧栏，用户选择业务操作领域，请求只修改专用Item的item_name。
- 单次真实模型运行Succeeded、4次模型调用，生成一个Pending提案。容器及消费者退出后才允许确认；确认前独立数据库读取仍为“HITL 确认前物料”。
- 等待期间在原生表单填写“用户尚未保存的草稿名称”，不保存也不提供给模型。模型差异卡仍使用数据库原值，而不是未保存草稿。
- 浏览器点击一次确认执行后显示成功。独立数据库核对名称变为“HITL 真实模型确认物料”，modified_by为普通writer，保存版本2026-08-29 03:28:59.056088；只有一份Succeeded执行记录。原生可见输入仍保留用户草稿，没有自动刷新或覆盖。
- 操作运行ID：90a3183f0d4eca8f741ad57a9b9544cd549999cac6beb59a07ee0de0eb682cea；提案jab3r75vof；执行jmelv87seo。均为合成验收证据，不含生产记录或凭证。

## 当前后续

同页继续查询最初因旧页面版本409被阻断，正在修复并重新验收；保存执行自身的版本绑定不放松。Customer实际保存/创建、Item创建、Sales Order、填表、Unknown核实、配置预览发布和日常Site仍待完成。
