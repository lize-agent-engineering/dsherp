import json
import subprocess

import httpx
import pytest

from dsherp import alerts, worker_log


SNAPSHOT = {
    "queued": 0,
    "queued_oldest_seconds": None,
    "running_stuck": 0,
    "backup_age_hours": 1.0,
    "last_claim_age_seconds": 10,
}
BASE = {"snapshot": SNAPSHOT, "age_seconds": 0}
METRICS = {"consecutive_run_failures": 0, "orphan_containers": 0}


def keys(snapshot=BASE, metrics=METRICS, now=1000.0):
    return sorted(alert.key for alert in alerts.evaluate(snapshot, metrics, now))


def test_rules_fire_only_on_their_condition():
    assert keys() == []
    assert keys(metrics={**METRICS, "consecutive_run_failures": 3}) == ["provider_or_runtime_failing"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 6}}) == ["queue_backlog"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 1, "queued_oldest_seconds": 601}}) == ["queue_backlog"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queue_expired_24h": 11}}) == ["queue_expiring"]
    assert alerts.evaluate({**BASE, "snapshot": {**SNAPSHOT, "queue_expired_24h": 11}}, METRICS, 1000.0) == [
        alerts.Alert("queue_expiring", "warning", "排队过期较多")
    ]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 6, "queue_expired_24h": 11}}) == ["queue_backlog", "queue_expiring"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "running_stuck": 1}}) == ["run_stuck"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "backup_age_hours": None}}) == ["backup_stale"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "backup_age_hours": 27}}) == ["backup_stale"]
    assert keys(metrics={**METRICS, "orphan_containers": 2}) == ["orphan_containers"]
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 1, "last_claim_age_seconds": 121}}) == ["worker_not_claiming"]
    assert keys(None) == ["ops_status_unavailable"]
    stale = {
        "snapshot": {**SNAPSHOT, "queued": 10, "running_stuck": 1, "backup_age_hours": None},
        "age_seconds": 901,
    }
    assert keys(stale, metrics={**METRICS, "orphan_containers": 2}) == ["ops_snapshot_stale"]


def test_evaluate_is_pure_and_thresholds_are_boundaries():
    snapshot = {**BASE, "snapshot": dict(SNAPSHOT)}
    metrics = dict(METRICS)
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 5}}) == []
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queue_expired_24h": 10}}) == []
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 1, "queued_oldest_seconds": 600}}) == []
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "backup_age_hours": 26}}) == []
    assert keys({**BASE, "snapshot": {**SNAPSHOT, "queued": 1, "last_claim_age_seconds": 120}}) == []
    alerts.evaluate(snapshot, metrics, 1000.0)
    assert snapshot == {**BASE, "snapshot": SNAPSHOT} and metrics == METRICS


def test_notifier_deduplicates_and_webhook_failure_only_logs(capsys):
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = alerts.Notifier(webhook="http://hook.invalid/x", cooldown=600, client=client)
    alert = alerts.Alert("run_stuck", "critical", "1 个运行卡住")
    notifier.emit([alert], now=0)
    notifier.emit([alert], now=100)
    notifier.emit([alert], now=700)
    lines = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
    emitted = [line for line in lines if line.get("event") == "alert"]
    failures = [line for line in lines if line.get("event") == "alert_webhook_failed"]
    assert [line["key"] for line in emitted] == ["run_stuck", "run_stuck"]
    assert len(failures) == 2 and all(line["error_class"] == "HTTPStatusError" for line in failures)
    assert len(posted) == 2 and posted[0]["key"] == "run_stuck"


def test_notifier_redacts_configured_credentials_from_log_and_webhook(capsys):
    secret = "synthetic-provider-secret-value"
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200)

    worker_log.configure([secret])
    try:
        notifier = alerts.Notifier(
            webhook="http://hook.invalid/x",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        notifier.emit([alerts.Alert("synthetic", "warning", f"failure {secret}")], now=0)
        output = capsys.readouterr().err
        assert secret not in output
        assert secret not in json.dumps(posted)
        assert "[redacted]" in output and "[redacted]" in json.dumps(posted)
    finally:
        worker_log.configure([])


def test_orphan_containers_counts_docker_ps_lines():
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="dsherp-context-a\ndsherp-context-b\n", stderr="")

    assert alerts.orphan_containers(runner=fake) == 2
    command = calls[0][0][0]
    assert command == ["docker", "ps", "--filter", "name=dsherp-context-", "--format", "{{.Names}}"]
    assert calls[0][1]["timeout"] == 10
    assert calls[0][1]["check"] is False


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["docker", "ps"]),
        subprocess.TimeoutExpired(["docker", "ps"], 10),
        OSError("synthetic docker unavailable"),
    ],
)
def test_orphan_probe_failures_only_log_and_skip(error, capsys):
    def failing(*args, **kwargs):
        raise error

    assert alerts.orphan_containers(runner=failing) is None
    lines = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "orphan_probe_failed"
    assert lines[0]["error_class"] == type(error).__name__
    assert "synthetic docker unavailable" not in json.dumps(lines)


def test_host_isolation_failure_is_a_critical_alert():
    """Claims stop while run containers can reach the host; that must be visible, not silent."""
    from dsherp import alerts
    snapshot={'age_seconds':1,'snapshot':{'queued':0,'running_stuck':0,'backup_age_hours':1}}
    keys=[alert.key for alert in alerts.evaluate(snapshot,{'host_isolation_ok':0},now=0)]
    assert 'host_isolation_failed' in keys
    assert [alert.severity for alert in alerts.evaluate(snapshot,{'host_isolation_ok':0},now=0) if alert.key=='host_isolation_failed']==['critical']
    assert 'host_isolation_failed' not in [alert.key for alert in alerts.evaluate(snapshot,{'host_isolation_ok':1},now=0)]
    assert 'host_isolation_failed' not in [alert.key for alert in alerts.evaluate(snapshot,{},now=0)]  # development: no gate
