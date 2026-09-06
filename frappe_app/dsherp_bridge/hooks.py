app_name = "dsherp_bridge"
app_title = "DSHERP Bridge"
app_publisher = "dsherp"
app_description = "Restricted ERPNext read validation"
app_email = "development@example.invalid"
add_to_apps_screen = [{
    "name": app_name,
    "title": app_title,
    "route": "/desk/dsherp-agent",
}]
required_apps = ["erpnext"]
app_include_js = ["/assets/dsherp_bridge/dist/context-agent.js"]
app_include_css = ["/assets/dsherp_bridge/dist/context-agent.css"]
boot_session = "dsherp_bridge.boot.boot_session"
auth_hooks = ["dsherp_bridge.sso.validate_session"]
after_request = ["dsherp_bridge.configuration_locks.release"]
doc_events = {
    doctype: {event: "dsherp_bridge.configuration_locks.lock_native"
              for event in ("before_validate", "before_rename", "on_trash")}
    for doctype in ("DocType", "Custom Field", "Property Setter", "Workflow", "Workflow State", "Workflow Action Master")
}
doc_events["*"]={"before_insert":"dsherp_bridge.configuration_locks.check_new_custom_record",
                 # 单据被取消或删除时，产生它的执行记录留下并标注（T3）。
                 "on_cancel":"dsherp_bridge.document_links.mark",
                 "on_trash":"dsherp_bridge.document_links.mark"}
# 审计记录不该成为用户删不掉自己单据的理由：链接存在性检查跳过它。
ignore_links_on_delete = ["DS Execution Record"]
doc_events.update({doctype: {"before_validate": "dsherp_bridge.preview.prevent_external_configuration"}
                   for doctype in ("Webhook", "Email Account", "Notification")})

scheduler_events = {
    "cron": {
        "*/5 * * * *": ["dsherp_bridge.ops.collect_snapshot"],
        "*/10 * * * *": ["dsherp_bridge.operations.expire_proposals"],
    }
}
