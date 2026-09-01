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
import sys
from urllib.parse import urlsplit
import httpx
from dsherp.runtime_host import ROOT,IMAGE,load_settings
from dsherp.context_container import docker_command
from dsherp.context_mcp import BusinessRuntimeError,post
from dsherp.runtime_revision import configuration_revision


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
                        print(json.dumps(diagnostic),file=sys.stderr)
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
    cap={key:task[key] for key in ('run_id','capability')}
    try:
        scope=task.get('scope_id')
        if not isinstance(scope,str) or not re.fullmatch('[a-f0-9]{64}',scope):
            raise ValueError('Invalid server session scope')
        result=execute({**task,**(business or {}),'resume':'inspect'},settings,Path(state_root)/scope)
    except Exception as exc:
        post(client,'finish_run',**cap,status='Failed',error='业务运行失败：'+type(exc).__name__)
        return True
    # Never retry a model run or overwrite an ambiguous finish response.
    post(client,'finish_run',**cap,**result)
    return True


def poll_once(client,settings,state_root,*,business=None):
    try:
        return run_once(client,settings,state_root,business=business)
    except httpx.TransportError as error:
        print(json.dumps({'worker_error':type(error).__name__}),file=sys.stderr)
        return False
    except BusinessRuntimeError as error:
        if error.status_code not in (500,502,503,504):raise
        print(json.dumps({'worker_error':type(error).__name__,'status_code':error.status_code}),file=sys.stderr)
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
    load_settings(args.provider_env)
    profile=json.loads(args.profile.read_text())
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
                while True:
                    settings=load_settings(args.provider_env)
                    if args.once:
                        run_once(client,settings,state_root,business=business)
                        return 0
                    poll_once(client,settings,state_root,business=business)
                    time.sleep(3)


if __name__=='__main__':raise SystemExit(main())
