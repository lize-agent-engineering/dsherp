"""Five-minute operational snapshots for the worker and administrators."""

import json
import time
from pathlib import Path

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime


def _age_seconds(value):
    if not value:
        return None
    return max(0, int((now_datetime() - get_datetime(value)).total_seconds()))


def _backup_age_hours():
    directory = Path(frappe.get_site_path("private", "backups"))
    files = sorted(
        directory.glob("*-database.sql*"), key=lambda path: path.stat().st_mtime
    ) if directory.exists() else []
    if not files:
        return None
    return round((time.time() - files[-1].stat().st_mtime) / 3600, 2)


def collect_snapshot():
    now = now_datetime()
    queued = frappe.get_all(
        "DS Model Run",
        filters={"status": "Queued"},
        fields=["creation"],
        order_by="creation asc",
    )
    active = frappe.get_all(
        "DS Model Run",
        filters={"status": ["in", ["Running", "Cancelling"]]},
        fields=["expires_at"],
        order_by="expires_at asc",
    )
    counts = {}
    for row in frappe.get_all(
        "DS Model Run",
        filters={"creation": [">=", add_to_date(now, hours=-24)]},
        fields=["status", {"COUNT": "name", "as": "n"}],
        group_by="status",
        order_by="status asc",
    ):
        counts[row.status] = row.n
    claimed = frappe.get_all(
        "DS Run Event",
        filters={"kind": "claimed"},
        fields=["recorded_at"],
        order_by="recorded_at desc",
        limit_page_length=1,
    )
    snapshot = {
        "queued": len(queued),
        "queued_oldest_seconds": _age_seconds(queued[0].creation) if queued else None,
        "running": len(active),
        "running_stuck": sum(
            1
            for row in active
            if row.expires_at and get_datetime(row.expires_at) <= now
        ),
        "pending_proposals_expired": frappe.db.count(
            "DS Operation Proposal",
            {"status": "Pending", "expires_at": ["<=", now]},
        ),
        "last_claim_age_seconds": _age_seconds(claimed[0].recorded_at) if claimed else None,
        "runs_24h": counts,
        "backup_age_hours": _backup_age_hours(),
        "site": frappe.local.site,
        "collected_at": str(now),
    }
    frappe.get_doc(
        {
            "doctype": "DS Ops Snapshot",
            "collected_at": now,
            "payload": json.dumps(snapshot, ensure_ascii=False),
        }
    ).insert(ignore_permissions=True)
    frappe.db.delete(
        "DS Ops Snapshot",
        {"collected_at": ["<", add_to_date(now, days=-7)]},
    )
    frappe.db.commit()
    return snapshot


@frappe.whitelist(methods=["GET"])
def ops_status():
    user = frappe.session.user
    if user != frappe.conf.get("dsherp_runtime_user") and "System Manager" not in frappe.get_roles(user):
        raise frappe.PermissionError("需要运行服务身份或系统管理员")
    latest = frappe.get_all(
        "DS Ops Snapshot",
        fields=["payload", "collected_at"],
        order_by="collected_at desc",
        limit_page_length=1,
    )
    if not latest:
        return {"snapshot": None, "age_seconds": None}
    return {
        "snapshot": json.loads(latest[0].payload),
        "age_seconds": _age_seconds(latest[0].collected_at),
    }
