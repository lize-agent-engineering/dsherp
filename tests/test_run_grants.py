"""A run's platform authorization is held beside the run for as long as it runs, not in it.

The design (workflow B, S2) says the grant leaves the DS Model Run row and stays with the
session; a background executor still has to ask the platform on every step, so the grant it
acts under lives in the Site cache under the run's id, bounded by the run's budget and dropped
at the run's end. The real-Site behaviour is in tests/integration/test_run_grants.py."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "frappe_app/dsherp_bridge"


def test_the_run_row_no_longer_has_a_column_for_the_grant():
    definition = json.loads((BRIDGE / "dsherp_bridge/doctype/ds_model_run/ds_model_run.json").read_text())
    assert "platform_grant" not in definition["field_order"]
    assert not any(field["fieldname"] == "platform_grant" for field in definition["fields"])
    patches = (BRIDGE / "patches.txt").read_text()
    assert "drop_run_grant" in patches, "existing rows must lose the value, not only the definition"


def test_sending_a_message_stashes_the_grant_beside_the_run_instead_of_writing_it():
    source = (BRIDGE / "context_api.py").read_text()
    body = source.split("def send_message", 1)[1].split("\n@frappe.whitelist", 1)[0]
    assert "'platform_grant':grant" not in body
    assert "grants.stash(run_id,grant,domain)" in body.replace(" ", "").replace("grants.stash(run_id,grant,domain)", "grants.stash(run_id,grant,domain)")


def test_the_executor_reads_the_grant_from_the_cache_and_refuses_to_act_without_one_under_sso():
    """A missing grant used to mean "skip the platform check". Under SSO enforcement every
    session comes from the platform, so an executor with no grant on file is refused (R6)."""
    source = (BRIDGE / "context_execution.py").read_text()
    actor = source.split("def _actor", 1)[1].split("\n\n\ndef ", 1)[0]
    assert "grants.of(run.name)" in actor
    assert "_password_login_disabled()" in actor and "PermissionError" in actor
    assert "platform_grant" not in actor


def test_the_grant_is_dropped_on_every_terminal_path():
    source = (BRIDGE / "context_execution.py").read_text()
    finish = source.split("def finish_run", 1)[1]
    assert "grants.drop(run.name)" in finish
    sweep = source.split("def claim_run", 1)[1].split("def run_status", 1)[0]
    assert sweep.count("grants.drop(") >= 3, "queue expiry, lease expiry and an unreadable conversation"
    api = (BRIDGE / "context_api.py").read_text().split("def cancel_run", 1)[1]
    assert "grants.drop(run.name)" in api


def test_the_lifetime_is_bounded_by_the_run_budget():
    source = (BRIDGE / "grants.py").read_text()
    assert "queue_expires_seconds" in source and "run_total_seconds" in source and "expires_in_sec" in source
