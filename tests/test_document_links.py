"""An execution record points at the document it produced, and keeps pointing after that
document is cancelled or deleted.

The link is a Dynamic Link so the document side finds it through Frappe's own Connections;
it is exempted from the link-exists check so it can never stop a user from cancelling or
deleting their own business document - the record is marked instead."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_execution_record/ds_execution_record.json"
OPERATIONS = ROOT / "frappe_app/dsherp_bridge/operations.py"


def test_the_record_carries_a_dynamic_link_to_the_document_it_produced():
    fields = {field["fieldname"]: field for field in json.loads(RECORD.read_text())["fields"]}
    assert fields["target_doctype"]["fieldtype"] == "Link" and fields["target_doctype"]["options"] == "DocType"
    assert fields["target_name"]["fieldtype"] == "Dynamic Link"
    assert fields["target_name"]["options"] == "target_doctype"
    assert fields["target_state"]["fieldtype"] == "Select"
    assert set(fields["target_state"]["options"].split()) >= {"Present", "Cancelled", "Deleted"}


def test_the_record_is_written_with_its_target_where_the_result_is_finalised():
    source = OPERATIONS.read_text()
    finalise = source.split("execution.status = result['status']", 1)[1][:600]
    assert "target_doctype" in finalise and "target_name" in finalise


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


