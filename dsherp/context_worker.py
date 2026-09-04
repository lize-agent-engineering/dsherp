"""Single business Site coordinator. Native Agent execution stays in containers."""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait
from contextlib import ExitStack,contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
import uuid
from urllib.parse import urlsplit
import httpx
from dsherp import alerts,metrics,worker_log
from dsherp.runtime_host import ROOT,IMAGE,load_settings
from dsherp.context_container import docker_command
from dsherp.context_mcp import BusinessRuntimeError,post
from dsherp.provider_circuit import CircuitBreaker,probe_models
from dsherp.runtime_revision import configuration_revision

REGISTRY=metrics.Registry()
CLAIMS_TOTAL=REGISTRY.counter('dsherp_claims_total','Claimed runs',labels=('site',))
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
SLOTS_BUSY=REGISTRY.gauge('dsherp_slots_busy','Busy business runtime slots')
PROVIDER_CIRCUIT_OPEN=REGISTRY.gauge('dsherp_provider_circuit_open','Provider circuit open state')
_consecutive=0


def set_consecutive_failures(value):
    global _consecutive
    _consecutive=value
    CONSECUTIVE_FAILURES.set(value)


def start_metrics(profile,once):
    if once:return None
    try:
        return metrics.serve(REGISTRY,profile.get('metrics_port',9109))
    except OSError as error:
        worker_log.log('metrics_start_failed',error_class=type(error).__name__)
        return None


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
        if orphan is not None:ORPHAN_CONTAINERS.set(orphan)
    notifier.emit(alerts.evaluate(status,{
        'consecutive_run_failures':_consecutive,'orphan_containers':orphan},now),now)


def _note_run(status,duration_ms):
    RUNS_TOTAL.inc(status=status)
    RUN_DURATION.observe(duration_ms/1000)
    if status=='Failed':set_consecutive_failures(_consecutive+1)
    elif status in ('Succeeded','Cancelled','NeedsInput'):set_consecutive_failures(0)


def profile_business(profile):
    site=profile.get('site');business_url=profile.get('business_url')
    if not isinstance(site,str) or not site or '/' in site:
        raise ValueError('Missing business Site')
    parts=urlsplit(business_url) if isinstance(business_url,str) else None
    if not parts or parts.scheme not in ('http','https') or not parts.netloc or parts.path not in ('','/'):
        raise ValueError('Missing business container URL')
    return {'business_url':business_url.rstrip('/'),'site':site}


def normalize_profile(profile):
    if not isinstance(profile,dict):raise ValueError('Invalid worker profile')
    slots=profile.get('slots',3)
    metrics_port=profile.get('metrics_port',9109)
    if type(slots) is not int or slots<1:raise ValueError('Invalid worker slots')
    if type(metrics_port) is not int or not 1<=metrics_port<=65535:raise ValueError('Invalid metrics port')
    if 'sites' in profile:
        raw_sites=profile['sites']
    else:
        raw_sites=[{key:profile.get(key) for key in ('site','base_url','business_url','api_key','api_secret')}]
    if not isinstance(raw_sites,list) or not raw_sites:raise ValueError('Missing worker sites')
    sites=[];names=set()
    for raw in raw_sites:
        if not isinstance(raw,dict):raise ValueError('Invalid worker site')
        business=profile_business(raw)
        base=urlsplit(raw.get('base_url',''))
        if base.scheme not in ('http','https') or not base.netloc or base.path not in ('','/'):
            raise ValueError('Missing business coordinator URL')
        for key in ('api_key','api_secret'):
            if not isinstance(raw.get(key),str) or not raw[key].strip():
                raise ValueError('Missing service credential')
        if business['site'] in names:raise ValueError('Duplicate worker Site')
        names.add(business['site'])
        sites.append({key:raw[key] for key in ('site','base_url','business_url','api_key','api_secret')})
    webhook=profile.get('alert_webhook')
    if webhook is not None and (not isinstance(webhook,str) or not webhook.strip()):
        raise ValueError('Invalid alert webhook')
    return {'slots':slots,'metrics_port':metrics_port,'alert_webhook':webhook,'sites':sites}


def cleanup_stale_runtime_artifacts(runner=subprocess.run):
    listed=runner(['docker','ps','-a','--filter','name=dsherp-context-','--format','{{.Names}}'],
                  capture_output=True,text=True)
    listed.check_returncode()
    for raw in listed.stdout.splitlines():
        name=raw.strip()
        if not re.fullmatch('dsherp-context-[0-9a-f]{32}',name):
            continue
        removed=runner(['docker','rm','-f',name],capture_output=True,text=True)
        removed.check_returncode()
    work=ROOT/'work'
    if not work.is_dir():
        return
    for path in list(work.iterdir()):
        if not path.name.startswith('context-run-'):
            continue
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def run_container(task,settings,directory,timeout=170):
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
            result=subprocess.run(docker_command(ROOT,secret,directory,name),capture_output=True,text=True,timeout=timeout)
            if result.returncode:
                # Only the runner's value-free stack diagnostic, never raw SDK
                # stderr, provider exceptions, request bodies or credentials.
                for line in result.stderr.splitlines():
                    if line.startswith('DSHERP_DIAGNOSTIC '):
                        diagnostic=json.loads(line.removeprefix('DSHERP_DIAGNOSTIC '))
                        worker_log.log('runtime_diagnostic',**diagnostic)
                raise RuntimeError('Isolated business runtime failed')
            output=json.loads(result.stdout)
            if set(output)!={'status','answer'} or output['status'] not in ('Succeeded','Cancelled','NeedsInput'):
                raise RuntimeError('Invalid business runtime result')
            if not isinstance(output['answer'],str) or (output['status']=='Succeeded' and not output['answer'].strip()):
                raise RuntimeError('Missing business answer')
            return output
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)


class Coordinator:
    def __init__(self,sites,settings_loader,slots,execute,breaker,probe,state_root,clock=time.monotonic,notifier=None):
        if not isinstance(sites,list) or not sites:raise ValueError('Missing coordinator sites')
        if type(slots) is not int or slots<1:raise ValueError('Invalid coordinator slots')
        if not callable(settings_loader) or not callable(execute) or not callable(probe) or not callable(clock):
            raise ValueError('Invalid coordinator dependency')
        self.sites=sites
        self.settings_loader=settings_loader
        self.slots=slots
        self.execute=execute
        self.breaker=breaker
        self.probe=probe
        self.clock=clock
        self.notifier=notifier
        self.state_root=Path(state_root)
        self.last_claim={site['site']:None for site in sites}
        self._executor=ThreadPoolExecutor(max_workers=slots,thread_name_prefix='dsherp-run')
        self._futures=set()
        self._futures_lock=threading.Lock()
        self._circuit_lock=threading.Lock()
        self._next_site=0
        self._last_probe=None
        SLOTS_BUSY.set(0);PROVIDER_CIRCUIT_OPEN.set(0)
        for site in sites:CLAIMS_TOTAL.inc(0,site=site['site'])

    def _reap(self):
        with self._futures_lock:
            done={future for future in self._futures if future.done()}
            self._futures-=done
            busy=len(self._futures)
        for future in done:
            try:future.result()
            except Exception as error:
                WORKER_ERRORS.inc(error_class=type(error).__name__)
                worker_log.log('worker_error',error_class=type(error).__name__)
        SLOTS_BUSY.set(busy)
        return busy

    def _record_outcome(self,outcome,now):
        if outcome=='provider_failure':PROVIDER_FAILURES.inc()
        if self.breaker is None:return
        opened=False
        with self._circuit_lock:
            before=self.breaker.state
            self.breaker.record(outcome,now)
            if self.breaker.state=='open' and before!='open':
                self._last_probe=now
                opened=True
            PROVIDER_CIRCUIT_OPEN.set(1 if self.breaker.state=='open' else 0)
        if opened and self.notifier is not None:
            self.notifier.emit([alerts.Alert('provider_circuit_open','critical','模型服务熔断已打开')],now)

    def _heartbeat_sites(self):
        for site in self.sites:
            try:post(site['client'],'worker_heartbeat')
            except Exception as error:
                WORKER_ERRORS.inc(error_class=type(error).__name__)
                worker_log.log('worker_error',site=site['site'],error_class=type(error).__name__)

    def _release_trial(self,trial):
        if not trial or self.breaker is None:return
        with self._circuit_lock:self.breaker.release_trial()

    def _circuit_allows(self,now):
        if self.breaker is None:
            PROVIDER_CIRCUIT_OPEN.set(0);return True,False
        should_probe=False
        with self._circuit_lock:
            if self.breaker.state=='open':
                if self._last_probe is None:self._last_probe=now
                elif now-self._last_probe>=60:
                    self._last_probe=now;should_probe=True
                if not should_probe:
                    PROVIDER_CIRCUIT_OPEN.set(1)
                    return False,False
        if should_probe:
            if not self.probe():
                with self._circuit_lock:PROVIDER_CIRCUIT_OPEN.set(1)
                return False,False
            # 探针成功只让电路进入试探态：下一条真实运行成功才算恢复，失败立即重新打开。
            with self._circuit_lock:self.breaker.half_open()
        with self._circuit_lock:
            allowed=self.breaker.allow(now)
            trial=allowed and self.breaker.state=='half_open'
            PROVIDER_CIRCUIT_OPEN.set(1 if self.breaker.state=='open' else 0)
            return allowed,trial

    def run_claimed(self,site,task,settings):
        cap={};started=time.monotonic();execution_error=None;outcome='other'
        def writeback(items):
            try:post(site['client'],'record_run_event',timeout=5,**cap,events=items)
            except Exception as error:
                worker_log.log('event_writeback_failed',run_id=cap['run_id'],
                               error_class=type(error).__name__)
        try:
            cap={key:task[key] for key in ('run_id','capability')}
            try:
                scope=task.get('scope_id')
                if not isinstance(scope,str) or not re.fullmatch('[a-f0-9]{64}',scope):
                    raise ValueError('Invalid server session scope')
                budget=task.get('budget')
                total=None if not isinstance(budget,dict) else budget.get('run_total_seconds')
                if type(total) is not int or total<1:raise ValueError('Missing run total budget')
                timeout=total+30
                result=self.execute({**task,**site.get('business',{}),'resume':'inspect'},settings,
                                    self.state_root/scope,timeout)
                if not isinstance(result,dict) or result.get('status') not in ('Succeeded','Cancelled','NeedsInput'):
                    raise RuntimeError('Invalid business runtime result')
            except Exception as error:
                execution_error=error
                duration=int((time.monotonic()-started)*1000)
                worker_log.log('runtime_failed',run_id=cap['run_id'],error_class=type(error).__name__,
                               duration_ms=duration)
                writeback([{'kind':'runtime_failed','source':'worker','error_class':type(error).__name__,
                            'payload':{'duration_ms':duration}}])
                finish=post(site['client'],'finish_run',**cap,status='Failed',
                            error='业务运行失败：'+type(error).__name__)
                completed='Failed'
            else:
                duration=int((time.monotonic()-started)*1000)
                worker_log.log('container_finished',run_id=cap['run_id'],status=result['status'],
                               duration_ms=duration)
                writeback([{'kind':'container_finished','source':'worker',
                            'payload':{'duration_ms':duration,'status':result['status']}}])
                finish=post(site['client'],'finish_run',**cap,**result)
                completed=finish.get('status',result['status'])
            failures=finish.get('provider_failures')
            if failures is not None and (type(failures) is not int or failures<0):
                raise RuntimeError('Invalid provider failure count')
            if failures:
                outcome='provider_failure'
            elif execution_error is not None and failures is None:
                outcome='provider_failure'
            elif completed in ('Succeeded','Cancelled','NeedsInput'):
                outcome='ok'
            else:
                outcome='other'
            _note_run(completed,duration)
        except Exception as error:
            WORKER_ERRORS.inc(error_class=type(error).__name__)
            worker_log.log('worker_error',run_id=cap.get('run_id'),error_class=type(error).__name__)
            outcome='provider_failure' if execution_error is not None else 'other'
        finally:
            self._record_outcome(outcome,self.clock())

    def tick(self,now):
        self._heartbeat_sites()
        busy=self._reap()
        allowed,trial=self._circuit_allows(now)
        if busy>=self.slots:
            self._release_trial(trial);return 0
        if not allowed:return 0
        capacity=1 if trial else self.slots-busy
        claimed=0
        try:
            settings=self.settings_loader()
            start=self._next_site
            for offset in range(len(self.sites)):
                if claimed>=capacity:break
                site=self.sites[(start+offset)%len(self.sites)]
                try:
                    task=post(site['client'],'claim_run',runtime_revision=configuration_revision(settings))
                except Exception as error:
                    WORKER_ERRORS.inc(error_class=type(error).__name__)
                    worker_log.log('worker_error',site=site['site'],error_class=type(error).__name__)
                    continue
                if not task:continue
                CLAIMS_TOTAL.inc(site=site['site']);self.last_claim[site['site']]=now
                worker_log.log('claimed',site=site['site'],run_id=task['run_id'])
                future=self._executor.submit(self.run_claimed,site,task,settings)
                with self._futures_lock:self._futures.add(future)
                claimed+=1;SLOTS_BUSY.set(busy+claimed)
            self._next_site=(start+1)%len(self.sites)
            return claimed
        finally:
            if claimed==0:self._release_trial(trial)

    def wait_idle(self):
        while True:
            with self._futures_lock:futures=tuple(self._futures)
            if not futures:return None
            wait(futures);self._reap()


def run_once(client,settings,state_root,*,business=None,execute=run_container):
    task=post(client,'claim_run',runtime_revision=configuration_revision(settings))
    if task is None:return False
    site_name=(business or {}).get('site') or task.get('site') or 'legacy'
    coordinator=Coordinator([{'site':site_name,'client':client,'business':business or {}}],lambda:settings,
                            slots=1,execute=execute,breaker=None,probe=lambda:False,state_root=state_root)
    CLAIMS_TOTAL.inc(site=site_name);worker_log.log('claimed',site=site_name,run_id=task['run_id'])
    coordinator.run_claimed(coordinator.sites[0],task,settings)
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
    profile=normalize_profile(json.loads(args.profile.read_text()))
    worker_log.configure([settings['DEEPSEEK_API_KEY'],*[site['api_secret'] for site in profile['sites']]])
    state_root=ROOT/'.runtime'/'business-sessions';state_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Shared with the old coordinator: only one paid runtime in the existing budget.
    with (ROOT/'.runtime'/'agent-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with worker_pid(ROOT/'.runtime'/'agent-worker.pid'):
            subprocess.run(['docker','image','inspect',IMAGE],check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['docker','volume','inspect','dsherp-v16-agent-runtime'],check=True,stdout=subprocess.DEVNULL)
            cleanup_stale_runtime_artifacts()
            with ExitStack() as stack:
                sites=[]
                for item in profile['sites']:
                    client=stack.enter_context(httpx.Client(base_url=item['base_url'],
                        headers={'X-Frappe-Site-Name':item['site'],
                                 'Authorization':'token '+item['api_key']+':'+item['api_secret']},
                        timeout=25,trust_env=False,follow_redirects=False))
                    sites.append({'site':item['site'],'client':client,'business':profile_business(item)})
                start_metrics(profile,once=args.once)
                notifier=None
                ops_state={}
                if not args.once:
                    notifier=alerts.Notifier(webhook=profile.get('alert_webhook'),cooldown=600)
                settings_loader=lambda:load_settings(args.provider_env)
                def probe():
                    current=settings_loader()
                    return probe_models(current['DEEPSEEK_BASE_URL'],current['DEEPSEEK_API_KEY'])
                coordinator=Coordinator(sites,settings_loader,profile['slots'],run_container,CircuitBreaker(),probe,state_root,notifier=notifier)
                while True:
                    if args.once:
                        run_once(sites[0]['client'],settings,state_root,business=sites[0]['business'])
                        return 0
                    try:
                        monitor_ops(sites[0]['client'],notifier,ops_state)
                        coordinator.tick(time.monotonic())
                    except Exception as error:
                        WORKER_ERRORS.inc(error_class=type(error).__name__)
                        worker_log.log('worker_error',error_class=type(error).__name__)
                    time.sleep(3)


if __name__=='__main__':raise SystemExit(main())
