"""The vulnerability gate decides the same way in CI as it does by hand."""
import json

import pytest

from dsherp import deploy_env
from infra import supply_chain


def _report(*severities):
    return {"matches": [{"vulnerability": {"id": f"CVE-{index}", "severity": severity,
                                           "fix": {"versions": ["1.2.3"]}},
                         "artifact": {"name": "openssl"}}
                        for index, severity in enumerate(severities)]}


def test_only_findings_at_or_above_the_threshold_block_a_release():
    report = _report("Low", "Medium", "High", "Critical")
    assert [row["severity"] for row in supply_chain.blocking_findings(report, "high")] == ["high", "critical"]
    assert len(supply_chain.blocking_findings(report, "medium")) == 3
    assert supply_chain.blocking_findings(_report("Low"), "high") == []


def test_an_unreadable_scan_result_is_never_treated_as_a_pass():
    for broken in ({}, {"matches": None}, {"matches": "none"}, []):
        with pytest.raises(ValueError):
            supply_chain.blocking_findings(broken, "high")


def test_an_unknown_severity_threshold_is_refused():
    with pytest.raises(ValueError):
        supply_chain.blocking_findings(_report("High"), "catastrophic")
    with pytest.raises(ValueError):
        supply_chain.scan_command("image", "catastrophic")


def test_the_commands_name_the_image_and_the_output_format():
    assert supply_chain.sbom_command("dsherp-frappe:v1", "/tmp/a.json") == [
        "syft", "dsherp-frappe:v1", "-o", "cyclonedx-json", "--file", "/tmp/a.json"]
    assert supply_chain.scan_command("dsherp-frappe:v1") == [
        "grype", "dsherp-frappe:v1", "-o", "json", "--fail-on", "high"]


def test_the_pinned_base_image_records_the_platforms_it_actually_serves():
    class Runner:
        def __call__(self, command, **kwargs):
            assert command[:3] == ["docker", "manifest", "inspect"]
            payload = {"manifests": [
                {"platform": {"os": "linux", "architecture": "amd64"}},
                {"platform": {"os": "linux", "architecture": "arm64"}},
                {"platform": {"os": "unknown", "architecture": "unknown"}}]}
            return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

    assert supply_chain.architectures(deploy_env.BASE_IMAGE, runner=Runner()) == ["linux/amd64", "linux/arm64"]


def test_an_unreadable_manifest_stops_the_release():
    class Runner:
        def __call__(self, command, **kwargs):
            return type("Result", (), {"returncode": 1, "stdout": ""})()

    with pytest.raises(SystemExit):
        supply_chain.architectures("dsherp-frappe:v1", runner=Runner())
