"""Exercise the repository's native ERPNext setup_complete payload on v16."""
import os
import frappe


SITE = "dsherp-v16probe.localhost"
COMPANY = "DSHERP v16 Probe 合成企业"


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")

try:
    if frappe.is_setup_complete():
        raise AssertionError("probe Site was unexpectedly initialized before T1.8")
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
    result = setup_complete({
        "language": "简体中文",
        "lang": "zh",
        "country": "China",
        "timezone": "Asia/Shanghai",
        "currency": "CNY",
        "enable_telemetry": 0,
        "company_name": COMPANY,
        "company_abbr": "DVP",
        "chart_of_accounts": "Standard",
        "fy_start_date": "2026-01-01",
        "fy_end_date": "2026-12-31",
        "setup_demo": 0,
    })
    if result != {"status": "ok"}:
        raise AssertionError(result)
    if not frappe.is_setup_complete() or not frappe.db.exists("Company", COMPANY):
        raise AssertionError("native setup did not persist the synthetic probe company")
    frappe.db.commit()
    print("C1-R9 PASS: native v16 setup_complete payload created the synthetic probe company")
finally:
    frappe.destroy()
