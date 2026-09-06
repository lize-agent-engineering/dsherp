"""Native session directories, laid out so they can be found again.

They used to be one flat level of sha256 names: the scope id binds site, owner, conversation,
domain and the rotating session, so nothing could enumerate a user's sessions and a rotated
session left a directory nobody could attribute. The layout is now
`<site>/<conversation>/<scope>` - the scope keeps its meaning for the runner, and a person's
sessions are reachable through the conversations they own."""
import time
from pathlib import Path

import pytest

from dsherp import sessions
from tests.test_admin_cli import host as host_runtime  # noqa: F401


SCOPE = "a" * 64
OTHER = "b" * 64


def test_a_session_lives_under_its_site_and_conversation(tmp_path):
    path = sessions.directory(tmp_path, site="acme.tenant.example.com", conversation="conv1", scope=SCOPE)
    assert path == tmp_path / "acme.tenant.example.com" / "conv1" / SCOPE
    with pytest.raises(ValueError):
        sessions.directory(tmp_path, site="acme.tenant.example.com", conversation="../escape", scope=SCOPE)
    with pytest.raises(ValueError):
        sessions.directory(tmp_path, site="../escape", conversation="conv1", scope=SCOPE)
    with pytest.raises(ValueError):
        sessions.directory(tmp_path, site="acme.tenant.example.com", conversation="conv1", scope="not-a-scope")


def test_the_sessions_of_a_conversation_are_all_of_them_including_superseded_ones(tmp_path):
    """A rotated runtime session makes a new scope; the old directory stays and must still be
    found - that was the orphan problem."""
    for scope in (SCOPE, OTHER):
        sessions.directory(tmp_path, site="acme", conversation="conv1", scope=scope).mkdir(parents=True)
    sessions.directory(tmp_path, site="acme", conversation="conv2", scope=SCOPE).mkdir(parents=True)
    found = sessions.of_conversations(tmp_path, "acme", ["conv1"])
    assert sorted(path.name for path in found) == sorted([SCOPE, OTHER])
    assert sessions.of_conversations(tmp_path, "acme", ["conv1", "conv2"]).__len__() == 3
    assert sessions.of_conversations(tmp_path, "acme", ["missing"]) == []


def test_removing_a_user_s_sessions_removes_whole_conversations_and_reports_what_it_did(tmp_path):
    for conversation in ("conv1", "conv2", "keep"):
        path = sessions.directory(tmp_path, site="acme", conversation=conversation, scope=SCOPE)
        path.mkdir(parents=True)
        (path / "state.json").write_text("{}")
    removed = sessions.remove(tmp_path, "acme", ["conv1", "conv2"])
    assert sorted(removed) == ["conv1", "conv2"]
    assert not (tmp_path / "acme" / "conv1").exists() and not (tmp_path / "acme" / "conv2").exists()
    assert (tmp_path / "acme" / "keep" / SCOPE / "state.json").exists()
    assert sessions.remove(tmp_path, "acme", ["conv1"]) == [], "removing twice is not an error"


def test_the_sweep_removes_what_is_older_than_the_window_and_leaves_the_rest(tmp_path):
    old = sessions.directory(tmp_path, site="acme", conversation="old", scope=SCOPE)
    fresh = sessions.directory(tmp_path, site="acme", conversation="fresh", scope=SCOPE)
    for path in (old, fresh):
        path.mkdir(parents=True)
        (path / "state.json").write_text("{}")
    ancient = time.time() - 91 * 86400
    import os
    os.utime(old / "state.json", (ancient, ancient))
    os.utime(old, (ancient, ancient))
    report = sessions.sweep(tmp_path, days=90, now=time.time())
    assert report["removed"] == [f"acme/old/{SCOPE}"]
    assert not old.exists() and fresh.exists()
    assert report["kept"] == 1


def test_directories_that_predate_the_layout_are_reported_not_guessed_at(tmp_path):
    """The old flat names were one-way hashes of five values; nothing can attribute them to a
    person now, so they are named as unattributable rather than silently deleted or claimed."""
    (tmp_path / SCOPE).mkdir(parents=True)
    (tmp_path / SCOPE / "state.json").write_text("{}")
    sessions.directory(tmp_path, site="acme", conversation="conv1", scope=OTHER).mkdir(parents=True)
    report = sessions.sweep(tmp_path, days=90, now=time.time())
    assert report["unattributed"] == [SCOPE]
    assert (tmp_path / SCOPE).exists(), "an unattributable session is not deleted behind the operator's back"


def test_the_worker_hands_the_runner_a_directory_under_the_site_and_conversation(tmp_path):
    """The runner still gets one directory named by the scope; what changed is where it sits."""
    import httpx

    from dsherp.context_worker import Coordinator

    SETTINGS = {"DEEPSEEK_API_KEY": "synthetic", "DEEPSEEK_BASE_URL": "http://synthetic",
                "deployment_digest": "a1" * 32}
    given = []
    queue = ["r1"]

    def handler(request):
        method = request.url.path.rsplit(".", 1)[-1]
        if method == "claim_run":
            if not queue:
                return httpx.Response(200, json={"message": {}})
            return httpx.Response(200, json={"message": {
                "run_id": queue.pop(0), "session_id": "conv1", "scope_id": SCOPE, "capability": "c",
                "domain": "query", "budget": {"run_total_seconds": 300}}})
        if method == "finish_run":
            return httpx.Response(200, json={"message": {"status": "Succeeded", "provider_failures": 0}})
        return httpx.Response(200, json={"message": {"recorded": 1, "last_seq": 1}})

    def execute(task, settings, directory, timeout):
        given.append(Path(directory))
        return {"status": "Succeeded", "answer": "ok"}

    with httpx.Client(base_url="http://acme", transport=httpx.MockTransport(handler)) as client:
        coordinator = Coordinator([{"site": "acme.tenant.example.com", "client": client,
                                    "business": {"business_url": "http://x", "site": "acme.tenant.example.com"}}],
                                  lambda: SETTINGS, slots=1, execute=execute, breaker=None,
                                  probe=lambda: True, state_root=tmp_path)
        coordinator.tick(now=0)
        coordinator.wait_idle()
    assert given, "the run was never executed"
    assert given[0] == tmp_path / "acme.tenant.example.com" / "conv1" / SCOPE


def test_a_claim_without_a_usable_conversation_name_does_not_write_outside_the_root(tmp_path):
    with pytest.raises(ValueError):
        sessions.directory(tmp_path, site="acme", conversation="", scope=SCOPE)
    with pytest.raises(ValueError):
        sessions.directory(tmp_path, site="acme", conversation=None, scope=SCOPE)


def test_the_command_reports_what_the_host_holds_and_only_sweeps_when_asked(host_runtime):
    from dsherp import admin
    from tests.test_admin_cli import RELEASE

    root = admin.runtime_dir(RELEASE) / admin.SESSION_ROOT
    sessions.directory(root, site="acme", conversation="conv1", scope=SCOPE).mkdir(parents=True)
    (root / SCOPE).mkdir(parents=True)
    report = admin.sessions_report(RELEASE)
    assert report["kept"] == 1 and report["unattributed"] == [SCOPE] and report["swept"] is False
    assert (root / SCOPE).exists() and report["retention_days"] == 90
    swept = admin.sessions_report(RELEASE, days=1, sweep=True)
    assert swept["swept"] is True and swept["retention_days"] == 1
    with pytest.raises(admin.Fault):
        admin.sessions_report(RELEASE, days=0, sweep=True)
