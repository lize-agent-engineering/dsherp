"""The worker's side of a stable window: a held Site is not claimed for, but still heartbeats."""
import httpx
import pytest

from dsherp import site_holds
from dsherp.context_worker import Coordinator


SETTINGS = {"DEEPSEEK_API_KEY": "synthetic", "DEEPSEEK_BASE_URL": "http://synthetic", "deployment_digest": "a1" * 32}


def _client(site, claimed, heartbeats, queue=None):
    queue = ["r-" + site] if queue is None else queue

    def handler(request):
        method = request.url.path.rsplit(".", 1)[-1]
        if method == "claim_run":
            if not queue:
                return httpx.Response(200, json={"message": {}})
            claimed.append(site)
            return httpx.Response(200, json={"message": {"run_id": queue.pop(0), "scope_id": "a" * 64, "capability": "c",
                                                         "domain": "query", "budget": {"run_total_seconds": 300}}})
        if method == "worker_heartbeat":
            heartbeats.append(site)
        if method == "finish_run":
            return httpx.Response(200, json={"message": {"status": "Succeeded", "provider_failures": 0}})
        return httpx.Response(200, json={"message": {"recorded": 1, "last_seq": 1}})
    return httpx.Client(base_url="http://" + site, transport=httpx.MockTransport(handler))


def test_a_held_site_is_not_claimed_for_but_still_heartbeats_and_resumes_when_released(tmp_path):
    claimed, heartbeats = [], []
    clients = {site: _client(site, claimed, heartbeats, queue=["r1-" + site, "r2-" + site]) for site in ("acme", "beta")}
    sites = [{"site": site, "client": client, "business": {"business_url": "http://x", "site": site}}
             for site, client in clients.items()]
    try:
        coordinator = Coordinator(sites, lambda: SETTINGS, slots=2, execute=lambda *a, **k: {"status": "Succeeded", "answer": "ok"},
                                  breaker=None, probe=lambda: True, state_root=tmp_path,
                                  holds=lambda: site_holds.held(tmp_path))
        site_holds.hold(tmp_path, "acme", "backup")
        coordinator.tick(now=0)
        coordinator.wait_idle()
        assert "acme" not in claimed and "beta" in claimed
        assert "acme" in heartbeats, "a held Site keeps its heartbeat: users see a queue, not a 503"
        claimed.clear()
        site_holds.release(tmp_path, "acme")
        coordinator.tick(now=10)
        coordinator.wait_idle()
        assert "acme" in claimed
    finally:
        for client in clients.values():
            client.close()


def test_an_unreadable_holds_directory_stops_claiming_rather_than_ignoring_the_hold(tmp_path):
    """Fail closed: if the worker cannot tell whether a window is open, it does not claim."""
    claimed, heartbeats = [], []
    client = _client("acme", claimed, heartbeats)
    def broken():
        raise OSError("permission denied")
    try:
        coordinator = Coordinator([{"site": "acme", "client": client, "business": {"business_url": "http://x", "site": "acme"}}],
                                  lambda: SETTINGS, slots=1, execute=lambda *a, **k: {"status": "Succeeded"},
                                  breaker=None, probe=lambda: True, state_root=tmp_path, holds=broken)
        assert coordinator.tick(now=0) == 0 and claimed == []
        assert heartbeats, "the heartbeat is not gated by the hold check"
    finally:
        client.close()


def test_the_coordinator_rejects_a_holds_source_that_is_not_callable(tmp_path):
    with pytest.raises(ValueError):
        Coordinator([{"site": "a", "client": object(), "business": {}}], lambda: SETTINGS, 1, lambda *a: None, None,
                    lambda: False, tmp_path, holds="acme")


def test_the_worker_reports_the_backup_record_for_every_expected_site(tmp_path):
    """The record is written by dsherp-admin; the worker only reads it, so a backup that never
    ran is as visible as one that failed."""
    from dsherp import backup_status, context_worker

    emitted = []
    notifier = type("N", (), {"emit": lambda self, alerts, now: emitted.extend(alerts)})()
    context_worker.monitor_backups(tmp_path, ["acme.tenant.example.com"], notifier, {}, now=1_788_739_200.0)
    assert [alert.key for alert in emitted] == ["backup_status_missing"]
    rendered = context_worker.REGISTRY.render()
    assert "dsherp_backup_sites_expected 1" in rendered and "dsherp_backup_offsite_oldest_hours -1" in rendered

    status = backup_status.empty()
    backup_status.record_site(status, "acme.tenant.example.com", "backup", at="2026-09-06T22:55:00Z", ok=True,
                              set_id="20260906_225500-acme_tenant_example_com-aaaaaa", stamp="20260906_225500")
    backup_status.record_site(status, "acme.tenant.example.com", "offsite", at="2026-09-06T23:00:00Z", ok=True,
                              set_id="20260906_225500-acme_tenant_example_com-aaaaaa", stamp="20260906_225500")
    backup_status.record_run(status, "backup", at="2026-09-06T22:55:00Z", ok=True)
    backup_status.save(tmp_path / "backups" / "status.json", status)
    emitted.clear()
    context_worker.monitor_backups(tmp_path, ["acme.tenant.example.com"], notifier, {}, now=1_788_739_200.0)
    assert [alert.key for alert in emitted] == []
    assert "dsherp_backup_sites_rpo_ok 1" in context_worker.REGISTRY.render()

    emitted.clear()
    context_worker.monitor_backups(tmp_path, None, notifier, {}, now=1_788_739_200.0)
    assert [alert.key for alert in emitted] == ["backup_scope_unknown"], "the scope is never quietly narrowed"


def test_a_backup_reporting_failure_never_costs_the_business_tick(tmp_path, monkeypatch):
    from dsherp import context_worker

    ticked = []
    coordinator = type("C", (), {"tick": lambda self, now: ticked.append(now)})()
    monkeypatch.setattr(context_worker, "monitor_ops", lambda *a, **k: None)
    monkeypatch.setattr(context_worker, "monitor_backups", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))
    context_worker.STOPPING.clear()
    assert context_worker.serve_once(coordinator, [{"site": "a", "client": object()}], None, {},
                                     now=0, backups={"runtime_dir": tmp_path, "sites": lambda: ["a"]})
    assert ticked == [0], "the heartbeat still ran"


def test_a_hold_left_behind_by_a_killed_command_stops_blocking_when_it_expires(tmp_path):
    claimed, heartbeats = [], []
    client = _client("acme", claimed, heartbeats, queue=["r1-acme", "r2-acme"])
    import time
    clock = {"now": time.time()}
    try:
        coordinator = Coordinator([{"site": "acme", "client": client, "business": {"business_url": "http://x", "site": "acme"}}],
                                  lambda: SETTINGS, slots=1, execute=lambda *a, **k: {"status": "Succeeded", "answer": "ok"},
                                  breaker=None, probe=lambda: True, state_root=tmp_path,
                                  holds=lambda: site_holds.held(tmp_path, now=clock["now"]))
        site_holds.hold(tmp_path, "acme", "backup", ttl_seconds=60)
        coordinator.tick(now=0)
        coordinator.wait_idle()
        assert "acme" not in claimed
        clock["now"] += 61            # the command that wrote the hold was killed; its end passes
        coordinator.tick(now=10)
        coordinator.wait_idle()
        assert "acme" in claimed
    finally:
        client.close()
