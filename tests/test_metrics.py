import httpx

from dsherp import worker_log
from dsherp.metrics import Registry, serve


def test_registry_renders_counter_gauge_and_cumulative_histogram():
    registry = Registry()
    runs = registry.counter("dsherp_runs_total", "Finished runs", labels=("status",))
    queue = registry.gauge("dsherp_queue_depth", "Queued runs")
    duration = registry.histogram("dsherp_run_duration_seconds", "Run duration", (5, 15, 30))
    runs.inc(status="Succeeded")
    runs.inc(status="Succeeded")
    queue.set(3)
    duration.observe(7)
    text = registry.render()
    assert "# TYPE dsherp_runs_total counter" in text
    assert 'dsherp_runs_total{status="Succeeded"} 2' in text
    assert "dsherp_queue_depth 3" in text
    assert 'dsherp_run_duration_seconds_bucket{le="5"} 0' in text
    assert 'dsherp_run_duration_seconds_bucket{le="15"} 1' in text
    assert 'dsherp_run_duration_seconds_bucket{le="+Inf"} 1' in text
    assert "dsherp_run_duration_seconds_sum 7" in text
    assert "dsherp_run_duration_seconds_count 1" in text


def test_metrics_http_is_loopback_only_and_has_one_route():
    registry = Registry()
    registry.gauge("dsherp_queue_depth", "Queued runs").set(1)
    server = serve(registry, 0)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        response = httpx.get(f"http://127.0.0.1:{port}/metrics", trust_env=False)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "dsherp_queue_depth 1" in response.text
        assert httpx.get(f"http://127.0.0.1:{port}/other", trust_env=False).status_code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_registry_render_never_exposes_configured_setting_values():
    secret = "synthetic-metrics-secret-value"
    worker_log.configure([secret])
    try:
        registry = Registry()
        counter = registry.counter("dsherp_runs_total", f"Finished {secret}", labels=("status",))
        counter.inc(status=secret)
        output = registry.render()
        assert secret not in output
        assert "[redacted]" in output
    finally:
        worker_log.configure([])
