"""Run the real configuration confirmation chain with warnings promoted to errors."""
import os
import uuid
import warnings

import frappe
from frappe.installer import update_site_config


SITE = "dsherp-v16probe.localhost"
ACTOR = "probe-configuration@example.invalid"
DOCTYPE = "DS v16 Configuration Commit Probe"


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")
conversation = bundle = proposal = None

try:
    for key in ("dsherp_preview", "mute_emails", "disable_scheduler", "pause_scheduler"):
        update_site_config(key, 1)
    frappe.clear_cache()
    user = frappe.get_doc({
        "doctype": "User",
        "email": ACTOR,
        "first_name": "v16 Configuration Probe",
        "enabled": 1,
        "send_welcome_email": 0,
        "roles": [{"role": "System Manager"}],
    }).insert()
    frappe.db.commit()
    frappe.set_user(user.name)

    from dsherp_bridge.configuration import propose_bundle
    from dsherp_bridge.configuration_execution import confirm_preview, prepare_preview

    conversation = frappe.get_doc({
        "doctype": "DS Conversation",
        "title": "v16 configuration commit probe",
    }).insert(ignore_permissions=True)
    package = {
        "version": 1,
        "doctypes": [{
            "name": DOCTYPE,
            "module": "DSHERP Bridge",
            "fields": [{"fieldname": "result", "label": "Result", "fieldtype": "Data"}],
            "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
        }],
        "extensions": [],
        "workflows": [],
    }
    bundle = propose_bundle(conversation.name, package)
    proposal = prepare_preview(bundle["id"], bundle["digest"])
    frappe.db.commit()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = confirm_preview(proposal["id"], proposal["digest"], uuid.uuid4().hex)
    assert result["status"] == "Succeeded", result
    assert frappe.db.exists("DocType", DOCTYPE)
    print("C1-R1 configuration PASS: real _confirm chain committed with warnings promoted to errors")
finally:
    frappe.db.rollback()
    frappe.set_user("Administrator")
    if proposal:
        for name in frappe.get_all(
            "DS Configuration Execution", filters={"confirmation": proposal["id"]}, pluck="name"
        ):
            frappe.delete_doc("DS Configuration Execution", name, force=True)
        if frappe.db.exists("DS Configuration Confirmation", proposal["id"]):
            frappe.delete_doc("DS Configuration Confirmation", proposal["id"], force=True)
    if bundle and frappe.db.exists("DS Configuration Bundle", bundle["id"]):
        frappe.delete_doc("DS Configuration Bundle", bundle["id"], force=True)
    if conversation and frappe.db.exists("DS Conversation", conversation.name):
        frappe.delete_doc("DS Conversation", conversation.name, force=True)
    if frappe.db.exists("DocType", DOCTYPE):
        frappe.delete_doc("DocType", DOCTYPE, force=True)
    frappe.db.commit()
    frappe.db.rollback()
    if frappe.db.exists("User", ACTOR):
        frappe.delete_doc("User", ACTOR, force=True)
    frappe.db.commit()
    if frappe.db.table_exists(DOCTYPE):
        frappe.db.sql_ddl(f"DROP TABLE `tab{DOCTYPE}`")
    frappe.destroy()
