app_name = "dsherp_bridge"
app_title = "DSHERP Bridge"
app_publisher = "dsherp"
app_description = "Restricted ERPNext read validation"
app_email = "development@example.invalid"
required_apps = ["erpnext"]
app_include_js = ["/assets/dsherp_bridge/dist/context-agent.js"]
auth_hooks = ["dsherp_bridge.sso.validate_session"]
after_request = ["dsherp_bridge.configuration_locks.release"]
extend_bootinfo = ["dsherp_bridge.preview.boot"]
doc_events = {
    doctype: {event: "dsherp_bridge.configuration_locks.lock_native"
              for event in ("before_validate", "before_rename", "on_trash")}
    for doctype in ("DocType", "Custom Field", "Property Setter", "Workflow")
}
doc_events.update({doctype: {"before_validate": "dsherp_bridge.preview.prevent_external_configuration"}
                   for doctype in ("Webhook", "Email Account", "Notification")})
