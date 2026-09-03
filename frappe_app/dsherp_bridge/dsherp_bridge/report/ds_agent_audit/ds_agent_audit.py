"""Cross-user Agent audit aggregates for System Manager."""

import frappe
from frappe.utils import add_to_date, get_datetime, getdate

LIMIT = 2000
COLUMNS = (
    ("name", "Data", 260),
    ("owner", "Data", 200),
    ("creation", "Datetime", 160),
    ("domain", "Data", 90),
    ("status", "Data", 90),
    ("model_calls", "Int", 80),
    ("proposals", "Int", 80),
    ("executions", "Int", 80),
    ("events", "Int", 80),
    ("error", "Data", 300),
)


def execute(filters=None):
    filters = filters or {}
    runs = frappe.get_all(
        "DS Model Run",
        filters=_run_filters(filters),
        fields=["name", "owner", "creation", "domain", "status", "model_calls", "error"],
        order_by="creation desc",
        limit_page_length=LIMIT,
        ignore_permissions=True,
    )
    names = [row.name for row in runs]
    proposals = _counts("DS Operation Proposal", "model_run", names)
    events = _counts("DS Run Event", "run", names)
    executions = _execution_counts(names)
    columns = [
        {"fieldname": name, "label": name, "fieldtype": fieldtype, "width": width}
        for name, fieldtype, width in COLUMNS
    ]
    data = []
    for row in runs:
        data.append(
            {
                "name": row.name,
                "owner": row.owner,
                "creation": row.creation,
                "domain": row.domain,
                "status": row.status,
                "model_calls": row.model_calls or 0,
                "proposals": proposals.get(row.name, 0),
                "executions": executions.get(row.name, 0),
                "events": events.get(row.name, 0),
                "error": (row.error or "")[:80],
            }
        )
    return columns, data


def _run_filters(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    if not from_date or not to_date:
        frappe.throw("审计报表需要 from_date 与 to_date")
    conditions = {
        "creation": [
            "between",
            [
                get_datetime(getdate(from_date)),
                add_to_date(get_datetime(getdate(to_date)), days=1, seconds=-1),
            ],
        ]
    }
    if filters.get("user"):
        conditions["owner"] = filters["user"]
    if filters.get("status"):
        conditions["status"] = filters["status"]
    return conditions


def _counts(doctype, field, names):
    if not names:
        return {}
    rows = frappe.get_all(
        doctype,
        filters={field: ["in", names]},
        fields=[field, {"COUNT": "name", "as": "n"}],
        group_by=field,
        order_by=field,
        ignore_permissions=True,
    )
    return {row[field]: row.n for row in rows}


def _execution_counts(names):
    if not names:
        return {}
    proposals = frappe.get_all(
        "DS Operation Proposal",
        filters={"model_run": ["in", names]},
        fields=["name", "model_run"],
        order_by="name",
        ignore_permissions=True,
    )
    if not proposals:
        return {}
    run_by_proposal = {row.name: row.model_run for row in proposals}
    executed = {}
    for row in frappe.get_all(
        "DS Execution Record",
        filters={"proposal": ["in", list(run_by_proposal)], "status": "Succeeded"},
        fields=["proposal", {"COUNT": "name", "as": "n"}],
        group_by="proposal",
        order_by="proposal",
        ignore_permissions=True,
    ):
        run = run_by_proposal.get(row.proposal)
        if run:
            executed[run] = executed.get(run, 0) + row.n
    return executed
