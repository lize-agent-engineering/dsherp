import os
import subprocess
import sys


def test_missing_erp_config_fails_without_starting_a_server():
    env = {k: v for k, v in os.environ.items() if k != "DSHERP_ERP_CONFIG"}
    result = subprocess.run([sys.executable, "-m", "dsherp.erp_mcp"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "DSHERP_ERP_CONFIG is required" in result.stderr
    assert "Traceback" not in result.stderr
