"""Rule-based worker alerts from ops snapshots and in-memory metrics."""
from collections import namedtuple
import subprocess
import httpx
from dsherp import worker_log

CONSECUTIVE_RUN_FAILURES=3
QUEUE_DEPTH=5
QUEUED_OLDEST_SECONDS=600
BACKUP_AGE_HOURS=26
UNCLAIMED_SECONDS=120
OPS_SNAPSHOT_MAX_AGE_SECONDS=900
NOTIFIER_COOLDOWN=600

# `site` travels with every Site-derived alert: the notifier deduplicates by key, and
# without it a second tenant's backlog is swallowed by the first one's cooldown.
Alert=namedtuple('Alert','key severity message site',defaults=(None,))


def fresh_snapshot(status):
    """The usable half of an ops status, or None when it is missing or too old."""
    if status is None:
        return None
    current=status.get('snapshot')
    age=status.get('age_seconds')
    if current is None or age is None or age>OPS_SNAPSHOT_MAX_AGE_SECONDS:
        return None
    return current


def evaluate_snapshot(status,now,site=None):
    """Alerts about one Site, tagged with which Site they are about."""
    found=[]
    if status is None:
        found.append(Alert('ops_status_unavailable','warning','运维快照不可用',site))
        return found
    current=fresh_snapshot(status)
    if current is None:
        found.append(Alert('ops_snapshot_stale','warning','运维快照陈旧',site))
        return found
    queued=current.get('queued') or 0
    oldest=current.get('queued_oldest_seconds')
    if queued>QUEUE_DEPTH or (oldest is not None and oldest>QUEUED_OLDEST_SECONDS):
        found.append(Alert('queue_backlog','warning','队列积压',site))
    if (current.get('queue_expired_24h') or 0)>10:
        found.append(Alert('queue_expiring','warning','排队过期较多',site))
    if (current.get('running_stuck') or 0)>0:
        found.append(Alert('run_stuck','critical','运行卡住',site))
    backup=current.get('backup_age_hours')
    if backup is None or backup>BACKUP_AGE_HOURS:
        found.append(Alert('backup_stale','critical','备份过期',site))
    claim_age=current.get('last_claim_age_seconds')
    if claim_age is not None and claim_age>UNCLAIMED_SECONDS and queued>0:
        found.append(Alert('worker_not_claiming','critical','有排队但未领取',site))
    return found


def evaluate_host(metrics,observed=True):
    """Alerts about the host rather than any one Site, so they carry no `site`.

    `observed` is "every Site's snapshot was readable". A run container is named
    `dsherp-context-<hex>` and carries no Site, so one Site's live run is indistinguishable
    from an orphan unless all of them have been asked."""
    found=[]
    if (metrics.get('consecutive_run_failures') or 0)>=CONSECUTIVE_RUN_FAILURES:
        found.append(Alert('provider_or_runtime_failing','critical','连续运行失败'))
    # Production only reports this gauge; while it is 0 the worker claims nothing.
    if metrics.get('host_isolation_ok')==0:
        found.append(Alert('host_isolation_failed','critical','运行容器可达宿主，已停止领取'))
    if observed and (metrics.get('orphan_containers') or 0)>0:
        found.append(Alert('orphan_containers','warning','存在孤儿容器'))
    return found


def evaluate(status,metrics,now,site=None):
    """One Site's alerts plus the host's - the shape from before there was more than one."""
    return evaluate_snapshot(status,now,site)+evaluate_host(metrics,observed=fresh_snapshot(status) is not None)


def orphan_containers(runner=subprocess.run):
    try:
        result=runner(['docker','ps','--filter','name=dsherp-context-','--format','{{.Names}}'],
            capture_output=True,text=True,check=False,timeout=10)
        result.check_returncode()
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError) as error:
        worker_log.log('orphan_probe_failed',error_class=type(error).__name__)
        return None
    return sum(1 for line in result.stdout.splitlines() if line.strip())


class Notifier:
    def __init__(self,sink=worker_log.log,webhook=None,cooldown=NOTIFIER_COOLDOWN,client=None):
        self.sink=sink
        self.webhook=webhook
        self.cooldown=cooldown
        self.client=client
        self._last={}

    def emit(self,alerts,now):
        for alert in alerts:
            # Per (key, site): the same rule firing on two Sites is two alerts, not a repeat.
            window=(alert.key,alert.site)
            last=self._last.get(window)
            if last is not None and now-last<self.cooldown:
                continue
            self._last[window]=now
            # Which Site, when the rule is about one: a journal line naming only the rule
            # sends the operator to the wrong tenant.
            self.sink('alert',key=alert.key,severity=alert.severity,message=alert.message,
                      **({'site':alert.site} if alert.site else {}))
            if not self.webhook:
                continue
            try:
                response=(self.client or httpx).post(self.webhook,json=worker_log.redact(alert._asdict()),timeout=5)
                response.raise_for_status()
            except Exception as error:
                self.sink('alert_webhook_failed',key=alert.key,error_class=type(error).__name__)
