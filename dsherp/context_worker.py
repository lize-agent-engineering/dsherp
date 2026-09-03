"""Single business Site coordinator. Native Agent execution stays in containers."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid
from urllib.parse import urlsplit
import httpx
from dsherp import alerts,metrics,worker_log
from dsherp.runtime_host import ROOT,IMAGE,load_settings
from dsherp.context_container import docker_command
from dsherp.context_mcp import BusinessRuntimeError,post
from dsherp.runtime_revision import configuration_revision

REGISTRY=metrics.Registry()
CLAIMS_TOTAL=REGISTRY.counter('dsherp_claims_total','Claimed runs')
RUNS_TOTAL=REGISTRY.counter('dsherp_runs_total','Finished runs',labels=('status',))
RUN_DURATION=REGISTRY.histogram('dsherp_run_duration_seconds','Run duration',(5,15,30,60,120,300))
WORKER_ERRORS=REGISTRY.counter('dsherp_worker_errors_total','Continuable worker errors',labels=('error_class',))
CONSECUTIVE_FAILURES=REGISTRY.gauge('dsherp_consecutive_run_failures','Consecutive failed runs')
QUEUE_DEPTH=REGISTRY.gauge('dsherp_queue_depth','Queued runs')
RUNNING_STUCK=REGISTRY.gauge('dsherp_running_stuck','Stuck running runs')
BACKUP_AGE=REGISTRY.gauge('dsherp_backup_age_hours','Hours since last backup')
ORPHAN_CONTAINERS=REGISTRY.gauge('dsherp_orphan_containers','Orphan context containers')
LAST_CLAIM=REGISTRY.gauge('dsherp_last_claim_timestamp_seconds','Unix time of last claim')
PROVIDER_FAILURES=REGISTRY.counter('dsherp_provider_call_failures_total','Provider call failures')
_consecutive=0


def set_consecutive_failures(value):
    global _consecutive
    _consecutive=value
    CONSECUTIVE_FAILURES.set(value)


def start_metrics(profile,once):
    if once:return None
    return metrics.serve(REGISTRY,profile.get('metrics_port',9109))


def fetch_ops(client):
    try:
        response=client.get('/api/method/dsherp_bridge.ops.ops_status',timeout=5)
        response.raise_for_status()
        return response.json()['message']
    except Exception:
        return None


def monitor_ops(client,notifier,state,now=None):
    if now is None:now=time.time()
    last=state.get('last_ops')
    if last is not None and now-last<60:
        return
    state['last_ops']=now
    status=fetch_ops(client)
    orphan=0
    snapshot=None if status is None else status.get('snapshot')
    age=None if status is None else status.get('age_seconds')
    fresh=snapshot is not None and age is not None and age<=alerts.OPS_SNAPSHOT_MAX_AGE_SECONDS
    if fresh:
        QUEUE_DEPTH.set(snapshot.get('queued') or 0)
        RUNNING_STUCK.set(snapshot.get('running_stuck') or 0)
        backup=snapshot.get('backup_age_hours')
        if backup is not None:BACKUP_AGE.set(backup)
        claim_age=snapshot.get('last_claim_age_seconds')
        if claim_age is not None:LAST_CLAIM.set(now-claim_age)
        if snapshot.get('running')==0:
            orphan=alerts.orphan_containers()
        ORPHAN_CONTAINERS.set(orphan)
    notifier.emit(alerts.evaluate(status,{
        'consecutive_run_failures':_consecutive,'orphan_containers':orphan},now),now)


def _note_run(status,duration_ms):
    RUNS_TOTAL.inc(status=status)
    RUN_DURATION.observe(duration_ms/1000)
    if status=='Failed':set_consecutive_failures(_consecutive+1)
    elif status in ('Succeeded','Cancelled'):set_consecutive_failures(0)


def profile_business(profile):
    site=profile.get('site');business_url=profile.get('business_url')
    if not isinstance(site,str) or not site or '/' in site:
        raise ValueError('Missing business Site')
    parts=urlsplit(business_url) if isinstance(business_url,str) else None
    if not parts or parts.scheme not in ('http','https') or not parts.netloc or parts.path not in ('','/'):
        raise ValueError('Missing business container URL')
    return {'business_url':business_url.rstrip('/'),'site':site}


def run_container(task,settings,directory):
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.is_symlink():raise ValueError('Session directory must not be a symbolic link')
    (ROOT/'work').mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='context-run-',dir=ROOT/'work') as temporary:
        secret=Path(temporary)/'run.json'
        fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as file:
            json.dump({**task,**settings},file)
        name='dsherp-context-'+uuid.uuid4().hex
        try:
            result=subprocess.run(docker_command(ROOT,secret,directory,name),capture_output=True,text=True,timeout=140)
            if result.returncode:
                # Only the runner's value-free stack diagnostic, never raw SDK
                # stderr, provider exceptions, request bodies or credentials.
                for line in result.stderr.splitlines():
                    if line.startswith('DSHERP_DIAGNOSTIC '):
                        diagnostic=json.loads(line.removeprefix('DSHERP_DIAGNOSTIC '))
                        worker_log.log('runtime_diagnostic',**diagnostic)
                raise RuntimeError('Isolated business runtime failed')
            output=json.loads(result.stdout)
            if set(output)!={'status','answer'} or output['status'] not in ('Succeeded','Cancelled'):
                raise RuntimeError('Invalid business runtime result')
            if not isinstance(output['answer'],str) or (output['status']=='Succeeded' and not output['answer'].strip()):
                raise RuntimeError('Missing business answer')
            return output
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)


def run_once(client,settings,state_root,*,business=None,execute=run_container):
    task=post(client,'claim_run',runtime_revision=configuration_revision(settings))
    if task is None:return False
    CLAIMS_TOTAL.inc()
    cap={key:task[key] for key in ('run_id','capability')}
    worker_log.log('claimed',run_id=cap['run_id'])
    started=time.monotonic()
    def writeback(items):
        try:post(client,'record_run_event',timeout=5,**cap,events=items)
        except Exception as error:
            worker_log.log('event_writeback_failed',run_id=cap['run_id'],error_class=type(error).__name__)
    try:
        scope=task.get('scope_id')
        if not isinstance(scope,str) or not re.fullmatch('[a-f0-9]{64}',scope):
            raise ValueError('Invalid server session scope')
        result=execute({**task,**(business or {}),'resume':'inspect'},settings,Path(state_root)/scope)
    except Exception as exc:
        duration=int((time.monotonic()-started)*1000)
        worker_log.log('runtime_failed',run_id=cap['run_id'],error_class=type(exc).__name__,duration_ms=duration)
        writeback([{'kind':'runtime_failed','source':'worker','error_class':type(exc).__name__,'payload':{'duration_ms':duration}}])
        post(client,'finish_run',**cap,status='Failed',error='业务运行失败：'+type(exc).__name__)
        _note_run('Failed',duration)
        return True
    duration=int((time.monotonic()-started)*1000)
    worker_log.log('container_finished',run_id=cap['run_id'],status=result['status'],duration_ms=duration)
    writeback([{'kind':'container_finished','source':'worker','payload':{'duration_ms':duration,'status':result['status']}}])
    # Never retry a model run or overwrite an ambiguous finish response.
    post(client,'finish_run',**cap,**result)
    _note_run(result['status'],duration)
    return True


def poll_once(client,settings,state_root,*,business=None):
    try:
        return run_once(client,settings,state_root,business=business)
    except httpx.TransportError as error:
        WORKER_ERRORS.inc(error_class=type(error).__name__)
        worker_log.log('worker_error',error_class=type(error).__name__)
        return False
    except BusinessRuntimeError as error:
        if error.status_code not in (500,502,503,504):raise
        WORKER_ERRORS.inc(error_class=type(error).__name__)
        worker_log.log('worker_error',error_class=type(error).__name__,status_code=error.status_code)
        return False


@contextmanager
def worker_pid(path):
    pid=str(os.getpid())
    temporary=path.with_name(f'.{path.name}.{pid}')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as file:file.write(pid)
        os.replace(temporary,path)
        yield
    finally:
        temporary.unlink(missing_ok=True)
        try:
            if path.read_text()==pid:path.unlink()
        except FileNotFoundError:
            pass


def exit_on_signal(_signum, _frame):
    raise SystemExit(0)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--provider-env',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    signal.signal(signal.SIGTERM,exit_on_signal)
    settings=load_settings(args.provider_env)
    profile=json.loads(args.profile.read_text())
    worker_log.configure([settings['DEEPSEEK_API_KEY'],profile.get('api_secret')])
    business=profile_business(profile)
    base=urlsplit(profile.get('base_url',''))
    if base.scheme not in ('http','https') or not base.netloc or base.path not in ('','/'):
        raise ValueError('Missing business coordinator URL')
    for key in ('api_key','api_secret'):
        if not isinstance(profile.get(key),str) or not profile[key].strip():raise ValueError('Missing service credential')
    state_root=ROOT/'.runtime'/'business-sessions';state_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Shared with the old coordinator: only one paid runtime in the existing budget.
    with (ROOT/'.runtime'/'agent-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with worker_pid(ROOT/'.runtime'/'agent-worker.pid'):
            subprocess.run(['docker','image','inspect',IMAGE],check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['docker','volume','inspect','dsherp-v16-agent-runtime'],check=True,stdout=subprocess.DEVNULL)
            with httpx.Client(base_url=profile['base_url'],headers={'X-Frappe-Site-Name':profile['site'],
                'Authorization':'token '+profile['api_key']+':'+profile['api_secret']},timeout=25,trust_env=False,follow_redirects=False) as client:
                start_metrics(profile,once=args.once)
                notifier=None
                ops_state={}
                if not args.once:
                    notifier=alerts.Notifier(webhook=profile.get('alert_webhook'),cooldown=600)
                while True:
                    settings=load_settings(args.provider_env)
                    if args.once:
                        run_once(client,settings,state_root,business=business)
                        return 0
                    monitor_ops(client,notifier,ops_state)
                    poll_once(client,settings,state_root,business=business)
                    time.sleep(3)


if __name__=='__main__':raise SystemExit(main())
