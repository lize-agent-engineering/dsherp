"""The unit suite and the integration suite are two suites: the default run never touches
tests/integration (it needs the four-Site stack and reuses unit-test file names), and naming
the directory brings all of it in under the `integration` marker."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


NOTHING_COLLECTED = 5  # pytest's exit code when a selection matches no test - an answer, not a failure


def _collect(*arguments):
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
                            cwd=ROOT, text=True, capture_output=True, timeout=300)
    assert result.returncode in (0, NOTHING_COLLECTED), result.stderr[-800:] + result.stdout[-800:]
    return result.stdout


def test_the_default_run_leaves_the_integration_suite_out_and_naming_it_brings_it_in():
    default = _collect()
    assert "tests/integration/" not in default
    assert "test_collection_layout.py" in default
    named = _collect("tests/integration")
    listed = [line for line in named.splitlines() if "::" in line]
    assert listed and all(line.startswith("tests/integration/") for line in listed)
    # Not `"error" not in named`: three collected test names legitimately contain the word.
    # A collection error is an ERROR line or the interrupted-collection summary.
    assert [line for line in named.splitlines()
            if line.startswith("ERROR") or "error during collection" in line] == []


def test_every_integration_item_carries_the_marker_and_nothing_else_does():
    total = len(re.findall(r"^tests/integration/.*::", _collect("tests/integration"), re.MULTILINE))
    marked = len(re.findall(r"^tests/integration/.*::", _collect("tests/integration", "-m", "integration"), re.MULTILINE))
    unmarked = re.findall(r"^tests/integration/.*::", _collect("tests/integration", "-m", "not integration"), re.MULTILINE)
    assert total > 0 and marked == total and unmarked == []
    assert "integration" not in _collect("tests/test_collection_layout.py", "-m", "integration")
