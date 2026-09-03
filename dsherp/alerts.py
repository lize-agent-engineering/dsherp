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

Alert=namedtuple('Alert','key severity message')


def evaluate(snapshot,metrics,now):
    found=[]
    if snapshot is None:
        found.append(Alert('ops_status_unavailable','warning','运维快照不可用'))
    else:
        age=snapshot.get('age_seconds')
        current=snapshot.get('snapshot')
        if current is None or age is None or age>OPS_SNAPSHOT_MAX_AGE_SECONDS:
            found.append(Alert('ops_snapshot_stale','warning','运维快照陈旧'))
            current=None
        if current is not None:
            queued=current.get('queued') or 0
            oldest=current.get('queued_oldest_seconds')
            if queued>QUEUE_DEPTH or (oldest is not None and oldest>QUEUED_OLDEST_SECONDS):
                found.append(Alert('queue_backlog','warning','队列积压'))
            if (current.get('running_stuck') or 0)>0:
                found.append(Alert('run_stuck','critical','运行卡住'))
            backup=current.get('backup_age_hours')
            if backup is None or backup>BACKUP_AGE_HOURS:
                found.append(Alert('backup_stale','critical','备份过期'))
            claim_age=current.get('last_claim_age_seconds')
            if claim_age is not None and claim_age>UNCLAIMED_SECONDS and queued>0:
                found.append(Alert('worker_not_claiming','critical','有排队但未领取'))
    if (metrics.get('consecutive_run_failures') or 0)>=CONSECUTIVE_RUN_FAILURES:
        found.append(Alert('provider_or_runtime_failing','critical','连续运行失败'))
    if snapshot is not None and current is not None and (metrics.get('orphan_containers') or 0)>0:
        found.append(Alert('orphan_containers','warning','存在孤儿容器'))
    return found


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
            last=self._last.get(alert.key)
            if last is not None and now-last<self.cooldown:
                continue
            self._last[alert.key]=now
            self.sink('alert',key=alert.key,severity=alert.severity,message=alert.message)
            if not self.webhook:
                continue
            try:
                response=(self.client or httpx).post(self.webhook,json=alert._asdict(),timeout=5)
                response.raise_for_status()
            except Exception as error:
                self.sink('alert_webhook_failed',key=alert.key,error_class=type(error).__name__)
