"""Probe MariaDB advisory locks and the native User row FOR UPDATE path."""
import os
import uuid

import frappe


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dsherp-v16probe.localhost")
frappe.connect()
frappe.set_user("probe-business@example.invalid")

try:
    from dsherp_bridge.configuration_locks import acquire, lock_key, release

    acquire(["Item"])
    owner, current = frappe.db.sql(
        "SELECT IS_USED_LOCK(%s), CONNECTION_ID()", lock_key("Item")
    )[0]
    assert owner == current
    release()
    assert frappe.db.sql("SELECT IS_USED_LOCK(%s)", lock_key("Item"))[0][0] is None

    from dsherp_bridge.context_api import send_message

    result = send_message(
        "v16 FOR UPDATE synthetic probe",
        {"schema_version": 1, "route": [], "page_type": "unknown"},
        uuid.uuid4().hex,
    )
    assert result["active_run"]
    frappe.db.rollback()
    print("C1-R10 PASS: GET_LOCK/IS_USED_LOCK/CONNECTION_ID and tabUser FOR UPDATE executed")
finally:
    frappe.destroy()
