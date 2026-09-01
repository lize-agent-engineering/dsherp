"""Create the fresh alpha validation Site and its minimal synthetic read fixture.

This script runs only in the short-lived ``validation-provision`` control
container.  Its sole stdout line is captured by the host wrapper as private
credential material; operational output goes to stderr.
"""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys


SITE = "dsherp-validation.localhost"
ROOT = Path("/home/frappe/frappe-bench")
SITES = ROOT / "sites"


def run(*arguments):
    subprocess.run(list(arguments), cwd=ROOT, check=True, stdout=sys.stderr)


if (SITES / SITE).exists():
    raise SystemExit("Site already exists; inspect before retrying")

(SITES / "apps.txt").write_text("frappe\nerpnext\ndsherp_bridge\n")
(SITES / "common_site_config.json").write_text(json.dumps({
    "db_host": "db",
    "db_port": 3306,
    "default_site": SITE,
    "redis_cache": "redis://redis:6379/0",
    "redis_queue": "redis://redis:6379/1",
    "redis_socketio": "redis://redis:6379/1",
}))
assets = SITES / "assets"
if not assets.exists():
    assets.symlink_to(ROOT / "assets", target_is_directory=True)

db_root_password = Path("/run/secrets/db_root_password").read_text().strip()
validation_admin_password = Path("/run/secrets/validation_admin_password").read_text().strip()
try:
    run(
        "bench", "new-site", SITE,
        "--db-host", "db",
        "--db-root-username", "root",
        "--db-root-password", db_root_password,
        "--admin-password", validation_admin_password,
        "--mariadb-user-host-login-scope", "%",
        "--install-app", "erpnext",
        "--set-default",
    )
    run("bench", "--site", SITE, "install-app", "dsherp_bridge")
    run("bench", "--site", SITE, "disable-scheduler")
finally:
    bench_log = ROOT / "logs" / "bench.log"
    if bench_log.exists():
        redacted = bench_log.read_text(errors="replace")
        for secret in (db_root_password, validation_admin_password):
            redacted = redacted.replace(secret, "[redacted]")
        bench_log.write_text(redacted)

import frappe
from frappe.core.doctype.user.user import generate_keys


profiles = {}
with contextlib.redirect_stdout(sys.stderr):
    os.chdir(SITES)
    frappe.init(site=SITE, sites_path=str(SITES))
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        for role in ("DSHERP Reader", "DSHERP Denied"):
            frappe.get_doc({
                "doctype": "Role", "role_name": role, "desk_access": 1,
            }).insert()
        for doctype in ("Customer", "Item"):
            frappe.permissions.add_permission(doctype, "DSHERP Reader", ptype="read")

        frappe.get_doc({
            "doctype": "UOM", "uom_name": "DSHERP-TEST-UNIT",
            "must_be_whole_number": 0,
        }).insert()
        frappe.get_doc({
            "doctype": "Item Group", "item_group_name": "DSHERP-TEST-ITEM-GROUP",
            "is_group": 0,
        }).insert()
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "DSHERP-TEST-CUSTOMER-GROUP", "is_group": 0,
        }).insert()
        frappe.get_doc({
            "doctype": "Territory", "territory_name": "DSHERP-TEST-TERRITORY",
            "is_group": 0,
        }).insert()
        frappe.get_doc({
            "doctype": "Custom Field", "dt": "Customer",
            "fieldname": "custom_dsherp_restricted",
            "label": "DSHERP restricted test", "fieldtype": "Data", "permlevel": 1,
        }).insert()
        frappe.clear_cache(doctype="Customer")
        frappe.get_doc({
            "doctype": "Item", "item_code": "DSHERP-TEST-ITEM",
            "item_name": "DSHERP-TEST-ITEM",
            "item_group": "DSHERP-TEST-ITEM-GROUP",
            "stock_uom": "DSHERP-TEST-UNIT", "is_stock_item": 0,
        }).insert()
        for name, restricted in (
            ("DSHERP-TEST-CUSTOMER", "DSHERP-TEST-RESTRICTED"),
            ("DSHERP-TEST-OTHER-CUSTOMER", None),
        ):
            frappe.get_doc({
                "doctype": "Customer", "customer_name": name,
                "customer_type": "Company",
                "customer_group": "DSHERP-TEST-CUSTOMER-GROUP",
                "territory": "DSHERP-TEST-TERRITORY",
                "custom_dsherp_restricted": restricted,
            }).insert()

        for actor, role in (("reader", "DSHERP Reader"), ("denied", "DSHERP Denied")):
            user = f"dsherp-{actor}@example.invalid"
            frappe.get_doc({
                "doctype": "User", "email": user, "first_name": f"DSHERP {actor}",
                "enabled": 1, "user_type": "System User", "send_welcome_email": 0,
                "roles": [{"role": role}],
            }).insert()
            profiles[actor] = {"user": user, **generate_keys(user)}
        frappe.get_doc({
            "doctype": "User Permission",
            "user": profiles["reader"]["user"],
            "allow": "Customer", "for_value": "DSHERP-TEST-CUSTOMER",
            "apply_to_all_doctypes": 1,
        }).insert()
        settings = frappe.get_single("System Settings")
        settings.language = "zh"
        settings.time_zone = "Asia/Shanghai"
        settings.country = "China"
        settings.currency = "CNY"
        settings.date_format = "yyyy-mm-dd"
        settings.enable_telemetry = 0
        settings.save()
        frappe.db.commit()
        frappe.clear_cache()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()

print(json.dumps(profiles, ensure_ascii=False))
