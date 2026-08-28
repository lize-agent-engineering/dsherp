app_name = "dsherp_bridge"
app_title = "DSHERP Bridge"
app_publisher = "dsherp"
app_description = "Restricted ERPNext read validation"
app_email = "development@example.invalid"
required_apps = ["erpnext"]
app_include_js = ["/assets/dsherp_bridge/dist/context-agent.js"]
auth_hooks = ["dsherp_bridge.sso.validate_session"]
