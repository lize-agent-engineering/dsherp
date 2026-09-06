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
