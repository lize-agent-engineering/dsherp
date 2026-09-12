"""`requirements.in` and `requirements.lock` have to still be the same set of decisions.

CI installs the lock (`uv pip sync --require-hashes requirements.lock`) and never reads
`requirements.in`, so until now the two could drift apart with nothing failing: a version
bumped in `.in` and not recompiled runs the old one everywhere, and a requirement dropped
from `.in` stays installed forever. `uv pip compile` records `# via -r requirements.in` on
exactly the direct requirements, which makes the comparison exact and needs no network.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "requirements.in"
LOCK = ROOT / "requirements.lock"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+?)(?: \\)?$")


def _canonical(name):
    """PEP 503 normalisation: `pytest_timeout`, `Pytest-Timeout` and `pytest-timeout` are one."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requested():
    pins = {}
    for line in SOURCE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN.match(line)
        assert match, f"requirements.in 里这一行不是 name==version 的固定版本：{line}"
        pins[_canonical(match.group(1))] = match.group(2)
    assert pins, "requirements.in 是空的"
    return pins


def _locked():
    """{name: (version, [what pulled it in])} for every entry in the lock."""
    entries = {}
    current = None
    for line in LOCK.read_text().splitlines():
        match = PIN.match(line)
        if match:
            current = _canonical(match.group(1))
            entries[current] = (match.group(2), [])
            continue
        stripped = line.strip()
        if not current or not stripped.startswith("#"):
            continue
        # Both shapes uv writes: `# via -r requirements.in` for one source, and a `# via`
        # header followed by `#   <source>` lines when there are several.
        item = stripped.lstrip("#").strip()
        if item.startswith("via"):
            item = item[len("via"):].strip()
        if item:
            entries[current][1].append(item)
    return entries


def test_the_lock_pins_exactly_the_requirements_that_were_asked_for():
    requested = _requested()
    locked = _locked()
    roots = {name for name, (_, via) in locked.items() if "-r requirements.in" in via}
    missing = sorted(set(requested) - roots)
    extra = sorted(roots - set(requested))
    assert not missing, f"requirements.in 里有这些直接依赖，锁里没有：{missing}；重跑 uv pip compile"
    assert not extra, f"锁里还留着 requirements.in 已经删掉的直接依赖：{extra}；重跑 uv pip compile"


def test_every_requested_version_is_the_version_the_lock_installs():
    """The one that actually bites: `.in` bumped, `.lock` not recompiled, CI installs the old
    version and stays green while every environment runs something nobody asked for."""
    locked = _locked()
    drifted = {name: (wanted, locked[name][0]) for name, wanted in _requested().items()
               if name in locked and locked[name][0] != wanted}
    assert not drifted, f"requirements.in 与锁的版本不一致（要求 vs 锁）：{drifted}；重跑 uv pip compile"


def test_the_lock_records_the_command_that_produced_it():
    """Without the header there is nothing tying the two files together to check at all."""
    header = "\n".join(LOCK.read_text().splitlines()[:3])
    assert "uv pip compile requirements.in" in header, header
    assert "--generate-hashes" in header and "-o requirements.lock" in header, header
