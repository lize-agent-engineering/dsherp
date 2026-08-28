"""Single-concurrency host coordinator; never runs a business SDK on the host."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import uuid

from dotenv import dotenv_values
import httpx

from dsherp.task_mcp import PlatformError, post

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341'
KEYS = ('DEEPSEEK_API_KEY', 'DSH_MODEL', 'DEEPSEEK_BASE_URL')


def load_settings(path):
    values = dotenv_values(path, interpolate=False)
    if any(not isinstance(values.get(k), str) or not values[k].strip() for k in KEYS):
        raise ValueError('Explicit provider file must contain all three required settings')
    return {k: values[k] for k in KEYS}


def container_base(name):
    return ['docker', 'run', '--rm', '--pull=never', '--name', name,
            '--memory', '384m', '--memory-swap', '384m', '--cpus', '0.1', '--pids-limit', '96',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user', '0:0',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m', '--network', 'dsherp-validation_api']


def docker_command(root, secret, name):
    return container_base(name)+[
            '-v', f'{root}/dsherp:/opt/dsherp/dsherp:ro', '-v', f'{root}/config:/opt/dsherp/config:ro',
            '-v', 'dsherp-agent-runtime:/opt/runtime:ro', '-v', f'{secret}:/run/task.json:ro',
            '-e', 'PYTHONPATH=/opt/dsherp', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '--workdir', '/tmp', '--entrypoint', '/opt/runtime/bin/python', IMAGE,
            '-m', 'dsherp.task_runner']


def run_container(task, settings, root=ROOT):
    (root / 'work').mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='agent-', dir=root / 'work') as directory:
        secret = Path(directory) / 'task.json'
        fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({k: task[k] for k in ('task_id', 'question', 'capability')} | settings, stream)
        name = 'dsherp-agent-' + uuid.uuid4().hex
        try:
            result = subprocess.run(docker_command(root, secret, name), capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError('Isolated Agent process failed')
            data = json.loads(result.stdout)
            if not isinstance(data.get('answer'), str) or not data['answer'].strip():
                raise RuntimeError('Agent returned no answer')
            return data['answer']
        finally:
            # Also handles timeout/interruption; do not leave a detached runtime behind.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)


def run_once(client, settings):
    post(client, 'worker_heartbeat')
    task = post(client, 'claim_task')
    if task is None:
        return False
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_container, task, settings)
            while True:
                try:
                    answer = future.result(timeout=15)
                    break
                except FutureTimeout:
                    if future.done():
                        raise
                    post(client, 'worker_heartbeat')
    except Exception as exc:
        post(client, 'finish_task', task_id=task['task_id'], capability=task['capability'],
             status='Failed', error=f'Agent execution failed: {type(exc).__name__}')
        return True
    # A finish response may be lost; never execute the task again or overwrite it as failed.
    try:
        post(client, 'finish_task', task_id=task['task_id'], capability=task['capability'],
             status='Succeeded', answer=answer)
    except PlatformError as exc:
        if exc.status_code not in (403, 417):
            raise
        # Only explicit business rejection is certain; never overwrite ambiguous failures.
        post(client, 'finish_task', task_id=task['task_id'], capability=task['capability'],
             status='Failed', error='任务结果未通过权限或实际读取校验，请检查权限或补充查询需求。')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--provider-env', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    settings = load_settings(args.provider_env)
    profile = json.loads(args.profile.read_text())
    if profile.get('base_url') != 'http://127.0.0.1:18083' or profile.get('site') != 'platform.localhost':
        raise ValueError('Worker profile must target the local platform endpoint')
    for key in ('api_key', 'api_secret'):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError('Missing dedicated worker credential')
    (ROOT / '.runtime').mkdir(exist_ok=True)
    with (ROOT / '.runtime/agent-worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Fail before claiming: no automatic pull, installation, or host fallback.
        subprocess.run(['docker', 'image', 'inspect', IMAGE], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'inspect', 'dsherp-agent-runtime'], check=True, stdout=subprocess.DEVNULL)
        with httpx.Client(base_url=profile['base_url'], headers={'Host': profile['site'],
                'Authorization': 'token ' + profile['api_key'] + ':' + profile['api_secret']},
                timeout=25, trust_env=False, follow_redirects=False) as client:
            while True:
                run_once(client, settings)
                if args.once:
                    return 0
                time.sleep(3)


if __name__ == '__main__':
    raise SystemExit(main())
