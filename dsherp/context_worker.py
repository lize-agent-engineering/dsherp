"""Single business Site coordinator. Native Agent execution stays in containers."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid
import httpx
from dsherp.agent_worker import ROOT,IMAGE,load_settings
from dsherp.context_container import docker_command
from dsherp.context_mcp import post
from dsherp.runtime_revision import configuration_revision


def run_container(task,settings,directory):
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.is_symlink():raise ValueError('Session directory must not be a symbolic link')
    (ROOT/'work').mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='context-run-',dir=ROOT/'work') as temporary:
        secret=Path(temporary)/'run.json'
        fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as file:
            json.dump({**task,**settings,'business_url':'http://dsherp-validation-backend-1:8000','site':'dsherp-validation.localhost'},file)
        name='dsherp-context-'+uuid.uuid4().hex
        try:
            result=subprocess.run(docker_command(ROOT,secret,directory,name),capture_output=True,text=True,timeout=140)
            if result.returncode:raise RuntimeError('Isolated business runtime failed')
            output=json.loads(result.stdout)
            if set(output)!={'status','answer'} or output['status'] not in ('Succeeded','Cancelled'):
                raise RuntimeError('Invalid business runtime result')
            if not isinstance(output['answer'],str) or (output['status']=='Succeeded' and not output['answer'].strip()):
                raise RuntimeError('Missing business answer')
            return output
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)


def run_once(client,settings,state_root,*,execute=run_container):
    task=post(client,'claim_run',runtime_revision=configuration_revision(settings))
    if task is None:return False
    cap={key:task[key] for key in ('run_id','capability')}
    try:
        scope=task.get('scope_id')
        if not isinstance(scope,str) or not re.fullmatch('[a-f0-9]{64}',scope):
            raise ValueError('Invalid server session scope')
        result=execute({**task,'resume':'inspect'},settings,Path(state_root)/scope)
    except Exception as exc:
        post(client,'finish_run',**cap,status='Failed',error='业务运行失败：'+type(exc).__name__)
        return True
    # Never retry a model run or overwrite an ambiguous finish response.
    post(client,'finish_run',**cap,**result)
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--provider-env',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    load_settings(args.provider_env)
    profile=json.loads(args.profile.read_text())
    if profile.get('base_url')!='http://127.0.0.1:18081' or profile.get('site')!='dsherp-validation.localhost':
        raise ValueError('Expected local alpha business Site profile')
    for key in ('api_key','api_secret'):
        if not isinstance(profile.get(key),str) or not profile[key].strip():raise ValueError('Missing service credential')
    state_root=ROOT/'.runtime'/'business-sessions';state_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Shared with the old coordinator: only one paid runtime in the existing budget.
    with (ROOT/'.runtime'/'agent-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        subprocess.run(['docker','image','inspect',IMAGE],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','volume','inspect','dsherp-agent-runtime'],check=True,stdout=subprocess.DEVNULL)
        with httpx.Client(base_url=profile['base_url'],headers={'X-Frappe-Site-Name':profile['site'],
            'Authorization':'token '+profile['api_key']+':'+profile['api_secret']},timeout=25,trust_env=False,follow_redirects=False) as client:
            while True:
                run_once(client,load_settings(args.provider_env),state_root)
                if args.once:return 0
                time.sleep(3)


if __name__=='__main__':raise SystemExit(main())
