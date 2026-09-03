import json
import subprocess

import httpx

from dsherp import alerts


BASE = {
    "queued": 0,
    "queued_oldest_seconds": None,
    "running_stuck": 0,
    "backup_age_hours": 1.0,
    "last_claim_age_seconds": 10,
}
METRICS = {"consecutive_run_failures": 0, "orphan_containers": 0}


def keys(snapshot=BASE, metrics=METRICS, now=1000.0):
    return sorted(alert.key for alert in alerts.evaluate(snapshot, metrics, now))


def test_rules_fire_only_on_their_condition():
    assert keys() == []
    assert keys(metrics={**METRICS, "consecutive_run_failures": 3}) == ["provider_or_runtime_failing"]
    assert keys({**BASE, "queued": 6}) == ["queue_backlog"]
    assert keys({**BASE, "queued": 1, "queued_oldest_seconds": 601}) == ["queue_backlog"]
    assert keys({**BASE, "running_stuck": 1}) == ["run_stuck"]
    assert keys({**BASE, "backup_age_hours": None}) == ["backup_stale"]
    assert keys({**BASE, "backup_age_hours": 27}) == ["backup_stale"]
    assert keys(metrics={**METRICS, "orphan_containers": 2}) == ["orphan_containers"]
    assert keys({**BASE, "queued": 1, "last_claim_age_seconds": 121}) == ["worker_not_claiming"]
    assert keys(None) == ["ops_status_unavailable"]


def test_evaluate_is_pure_and_thresholds_are_boundaries():
    snapshot = dict(BASE)
    metrics = dict(METRICS)
    assert keys({**BASE, "queued": 5}) == []
    assert keys({**BASE, "queued": 1, "queued_oldest_seconds": 600}) == []
    assert keys({**BASE, "backup_age_hours": 26}) == []
    assert keys({**BASE, "queued": 1, "last_claim_age_seconds": 120}) == []
    alerts.evaluate(snapshot, metrics, 1000.0)
    assert snapshot == BASE and metrics == METRICS


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


def test_orphan_containers_counts_docker_ps_lines():
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="dsherp-context-a\ndsherp-context-b\n", stderr="")

    assert alerts.orphan_containers(runner=fake) == 2
    command = calls[0][0][0]
    assert command == ["docker", "ps", "--filter", "name=dsherp-context-", "--format", "{{.Names}}"]
    assert calls[0][1]["timeout"] == 10
