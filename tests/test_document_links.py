"""An execution record points at the document it produced, and keeps pointing after that
document is cancelled or deleted.

The link is a Dynamic Link so the document side finds it through Frappe's own Connections;
it is exempted from the link-exists check so it can never stop a user from cancelling or
deleting their own business document - the record is marked instead."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_execution_record/ds_execution_record.json"
HOOKS = ROOT / "frappe_app/dsherp_bridge/hooks.py"
OPERATIONS = ROOT / "frappe_app/dsherp_bridge/operations.py"


def test_the_record_carries_a_dynamic_link_to_the_document_it_produced():
    fields = {field["fieldname"]: field for field in json.loads(RECORD.read_text())["fields"]}
    assert fields["target_doctype"]["fieldtype"] == "Link" and fields["target_doctype"]["options"] == "DocType"
    assert fields["target_name"]["fieldtype"] == "Dynamic Link"
    assert fields["target_name"]["options"] == "target_doctype"
    assert fields["target_state"]["fieldtype"] == "Select"
    assert set(fields["target_state"]["options"].split()) >= {"Present", "Cancelled", "Deleted"}


def test_the_link_never_blocks_a_user_from_cancelling_or_deleting_their_own_document():
    """Frappe refuses to delete a document something links to; an audit record must not be the
    reason a user cannot manage their own data, so it is listed as ignorable on delete."""
    hooks = HOOKS.read_text()
    assert "ignore_links_on_delete" in hooks
    assert "DS Execution Record" in hooks.split("ignore_links_on_delete", 1)[1][:200]


def test_cancelling_or_deleting_the_document_marks_the_record_rather_than_erasing_it():
    hooks = HOOKS.read_text()
    for event in ("on_cancel", "on_trash"):
        assert event in hooks, event
    assert "document_links.mark" in hooks or "mark_target" in hooks


def test_the_record_is_written_with_its_target_where_the_result_is_finalised():
    source = OPERATIONS.read_text()
    finalise = source.split("execution.status = result['status']", 1)[1][:600]
    assert "target_doctype" in finalise and "target_name" in finalise


def test_only_the_target_state_may_change_after_a_result_is_final():
    """The record stays immutable; the one field a later cancellation may set is named."""
    controller = (ROOT / "frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_execution_record"
                  / "ds_execution_record.py").read_text()
    assert "target_state" in controller


import pytest   # noqa: E402
import sys, types   # noqa: E402


def _document_links():
    """Import the module with a stub frappe: the pure part must not need a Site."""
    if "frappe" not in sys.modules:
        stub = types.ModuleType("frappe")
        stub.get_all = lambda *a, **k: []
        stub.db = types.SimpleNamespace(set_value=lambda *a, **k: None)
        stub.log_error = lambda *a, **k: None
        sys.modules["frappe"] = stub
    import importlib
    return importlib.import_module("frappe_app.dsherp_bridge.document_links")


def test_only_a_finished_execution_that_really_wrote_a_document_claims_one():
    links = _document_links()
    assert links.target_of({"status": "Succeeded", "doctype": "Stock Entry", "name": "STE-1"}) == ("Stock Entry", "STE-1")
    assert links.target_of({"status": "Failed", "doctype": "Stock Entry", "name": "STE-1"}) == (None, None)
    assert links.target_of({"status": "Unknown", "doctype": "Stock Entry", "name": "STE-1"}) == (None, None)
    assert links.target_of({"status": "Succeeded"}) == (None, None)
    # a browser draft was authorized, never written by us
    assert links.target_of({"status": "Authorized", "target": "browser-draft",
                            "doctype": "Item", "name": "ITEM-1"}) == (None, None)
    assert links.target_of({"status": "Succeeded", "doctype": "Item", "name": "ITEM-1"},
                           {"action": "fill"}) == (None, None)
    assert links.target_of(None) == (None, None)


def test_marking_never_stops_the_user_from_cancelling_their_document():
    links = _document_links()
    import frappe
    def explode(*args, **kwargs):
        raise RuntimeError("database gone")
    original = frappe.get_all
    frappe.get_all = explode
    try:
        links.mark(types.SimpleNamespace(doctype="Stock Entry", name="STE-1",
                                         get=lambda key: {"doctype": "Stock Entry", "name": "STE-1"}.get(key)),
                   "on_trash")
    finally:
        frappe.get_all = original


def test_a_proposal_whose_document_was_deleted_still_reads_rather_than_raising():
    source = (ROOT / "frappe_app/dsherp_bridge/operations.py").read_text()
    section = source.split("def get_proposal", 1)[1][:2500]
    assert "frappe.db.exists" in section or "已删除" in section, \
        "reading a proposal must survive the user deleting the document it produced"
