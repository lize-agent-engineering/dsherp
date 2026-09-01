"""Prove bind-mounted apps, boot_session, and a real doc_events dispatch on v16."""
import os
import pymysql
import frappe
import frappe.sessions


SITE = "dsherp-v16probe.localhost"
DOCTYPE = "DS V16 Probe Record"


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")

try:
    installed = set(frappe.get_installed_apps())
    expected = {"frappe", "erpnext", "dsherp_bridge", "dsherp_platform"}
    if not expected.issubset(installed):
        raise AssertionError(f"installed apps missing: {sorted(expected - installed)}")

    boot_hooks = frappe.get_hooks("boot_session", app_name="dsherp_bridge")
    if "dsherp_bridge.boot.boot_session" not in boot_hooks:
        raise AssertionError(f"boot_session hook missing: {boot_hooks}")
    bootinfo = frappe._dict()
    frappe.get_attr("dsherp_bridge.boot.boot_session")(bootinfo)
    if bootinfo.dsherp_context_doctypes != []:
        raise AssertionError(f"unexpected Administrator boot payload: {bootinfo}")
    frappe.local.request = None
    native_boot = frappe.sessions.get()
    if native_boot.dsherp_context_doctypes != []:
        raise AssertionError(f"native boot_session dispatch missing: {native_boot}")

    event_hooks = frappe.get_hooks("doc_events", app_name="dsherp_bridge")
    if event_hooks.get("*", {}).get("before_insert") != [
        "dsherp_bridge.configuration_locks.check_new_custom_record"
    ]:
        raise AssertionError(f"doc_events hook missing: {event_hooks.get('*')}")

    if not frappe.db.exists("DocType", DOCTYPE):
        frappe.get_doc({
            "doctype": "DocType",
            "name": DOCTYPE,
            "module": "Custom",
            "custom": 1,
            "autoname": "field:title",
            "fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1}],
        }).insert()
        frappe.db.commit()

    from dsherp_bridge.configuration_locks import lock_key, release
    release()
    frappe.db.commit()
    config = frappe.get_site_config()
    external = pymysql.connect(
        host=config.db_host,
        port=int(config.get("db_port") or 3306),
        user=config.db_name,
        password=config.db_password,
        database=config.db_name,
        autocommit=True,
    )
    key = lock_key(DOCTYPE)
    try:
        with external.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (key,))
            if cursor.fetchone()[0] != 1:
                raise AssertionError("external advisory lock was not acquired")
        try:
            frappe.get_doc({"doctype": DOCTYPE, "title": "blocked-by-doc-event"}).insert()
        except frappe.ValidationError as error:
            if "新应用配置正在应用" not in str(error):
                raise
        else:
            raise AssertionError("doc_events hook did not reject the locked custom record")
        finally:
            frappe.db.rollback()
    finally:
        with external.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (key,))
        external.close()

    print("C1-R13 PASS: bind-mounted apps installed; boot_session and doc_events executed")
finally:
    frappe.destroy()
