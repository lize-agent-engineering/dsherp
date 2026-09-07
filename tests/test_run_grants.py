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


