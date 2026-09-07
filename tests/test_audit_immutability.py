"""Audit records are append-only: the definitions say so and the controllers enforce it.

Ruling #3 made this an external promise, so it is checked at both levels - the DocType
JSON (what a fresh Site gets) and the controller (what any code path hits, Administrator
included). The behaviour against a real Site is in tests/integration/test_audit_immutability.py."""
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "frappe_app/dsherp_bridge/dsherp_bridge/doctype"
PLATFORM = ROOT / "frappe_app/dsherp_platform/dsherp_platform/doctype"
# Every record that answers "what did the assistant do, on whose behalf, and with what
# result". Deleting one of these destroys the ability to replay or to hold anyone to account.
AUDIT_DOCTYPES = (
    "ds_model_run", "ds_run_event", "ds_operation_proposal", "ds_execution_record",
    "ds_configuration_bundle", "ds_configuration_confirmation", "ds_configuration_execution",
    "ds_configuration_transfer",
)


def _definition(name):
    for root in (BRIDGE, PLATFORM):
        path = root / name / f"{name}.json"
        if path.is_file():
            return json.loads(path.read_text())
    raise AssertionError(f"no DocType definition for {name}")


def _controller(name):
    for root in (BRIDGE, PLATFORM):
        path = root / name / f"{name}.py"
        if path.is_file():
            return path.read_text()
    raise AssertionError(f"no controller for {name}")


@pytest.mark.parametrize("name", AUDIT_DOCTYPES)
def test_an_audit_doctype_records_its_own_changes_and_grants_nobody_delete(name):
    definition = _definition(name)
    assert definition.get("track_changes") == 1, name
    for permission in definition.get("permissions", []):
        assert not permission.get("delete"), (name, permission.get("role"))
        assert not permission.get("cancel"), (name, permission.get("role"))


@pytest.mark.parametrize("name", AUDIT_DOCTYPES)
def test_an_audit_doctype_refuses_deletion_in_its_controller_not_only_in_permissions(name):
    """Permissions are bypassed by ignore_permissions and by Administrator; on_trash is not."""
    source = _controller(name)
    assert "def on_trash" in source, name
    assert "frappe.throw" in source.split("def on_trash", 1)[1], name


def test_the_policy_that_governs_execution_records_what_changed_and_why():
    """DS Doctype Policy decides what the assistant may write; it stays editable, but every
    change is versioned and has to say why."""
    definition = _definition("ds_doctype_policy")
    assert definition.get("track_changes") == 1
    fields = {field["fieldname"]: field for field in definition["fields"]}
    assert fields["change_reason"]["fieldtype"] in ("Small Text", "Data", "Long Text")
    assert fields["change_reason"].get("reqd") == 1
    source = _controller("ds_doctype_policy")
    assert "change_reason" in source


def test_the_conversation_is_not_an_audit_record_but_cannot_be_deleted_while_runs_reference_it():
    """A conversation is the user's own thread: it may be archived and its non-audit content
    removed, but deleting it while its runs exist would orphan the audit trail."""
    source = _controller("ds_conversation")
    assert "def on_trash" in source and "DS Model Run" in source


def test_a_finished_run_is_closed_to_the_document_path_and_only_cancellation_stays_open():
    """G7 is not a permission: Administrator with ignore_permissions gets past permissions.
    The controller's validate refuses any rewrite of a terminal run, and on a live run allows
    only the cancel transition. The real-site proof, three ways, is in
    tests/integration/test_audit_immutability.py."""
    source = _controller("ds_model_run")
    assert "def validate" in source
    body = source.split("def validate", 1)[1].split("def on_trash", 1)[0]
    assert "TERMINAL" in body and "frappe.throw" in body
    assert "('Queued', 'Cancelled')" in source and "('Running', 'Cancelling')" in source
    assert "cancel_request_id" in source, "the person's cancel request is the only other change allowed"
