"""Run one case through the real container with a scripted provider inside it.

The command is the **production** `docker_command` with exactly two additions — two read-only
mounts and a `-c` entrypoint script. That is the same handful of changes
`tests/integration/test_context_mcp_chain.py:72-73` already makes, and slice 0 confirmed the
path still works today. Everything that makes the container a container (`--read-only`, `--cap-drop`, `--user`,
`--network`, the code mounts, the runtime volume) is left exactly as production sets it: an
evaluation run under different isolation would be measuring a different system.
"""
import json
from pathlib import Path

from dsherp.context_container import docker_command

MODEL_FIXTURE = '/run/model_fixture.py'
SCRIPT_MOUNT = '/run/eval_script.json'

# Runs inside the container. **stdout must carry exactly what `dsherp.context_runner`'s
# __main__ prints** — a bare `{"status": ..., "answer": ...}` — because the worker's
# `run_container` does `json.loads(result.stdout)` on it and a prefixed line becomes a
# JSONDecodeError that surfaces as a runtime failure with no hint of the real cause.
# Everything the oracle needs beyond the result (the wire, for the envelope assertion) is
# written into the session directory, which is the one rw mount the container has.
EVAL_CONTAINER = r'''
import json,runpy
from pathlib import Path
from dsherp.context_runner import run_business
module=runpy.run_path('/run/model_fixture.py')
settings,requests,state=module['serve']('/run/eval_script.json')
try:
    config=json.loads(Path('/run/business.json').read_text());config.update(settings)
    path=Path('/tmp/business.json');path.write_text(json.dumps(config));path.chmod(0o600)
    result=run_business(path,Path('/session'))
finally:
    try:
        Path('/session/eval-wire.json').write_text(json.dumps(
            {'requests':requests,'compaction_calls':state['compaction_calls']},
            ensure_ascii=False,default=str))
    finally:
        state['shutdown']()
print(json.dumps(result,ensure_ascii=False))
'''
WIRE_FILE = 'eval-wire.json'


def replay_command(root, secret, session_dir, name, script_path, resolved=None):
    """The production container command, plus the stub and the script, read-only."""
    root = Path(root).resolve()
    command = docker_command(root, secret, session_dir, name, resolved=resolved)
    command[2:2] = ['-v', f'{root}/evals/model_server.py:{MODEL_FIXTURE}:ro',
                    '-v', f'{Path(script_path).resolve()}:{SCRIPT_MOUNT}:ro']
    command[-2:] = ['-c', EVAL_CONTAINER]
    return command


def replay_settings(deployment_digest):
    """What the run.json needs so the runtime talks to the stub on its own loopback."""
    return {'DEEPSEEK_API_KEY': 'synthetic-not-a-credential',
            'DEEPSEEK_BASE_URL': 'http://127.0.0.1:38127/v1',
            'deployment_digest': deployment_digest}


def newest_wire(state_root):
    """The wire the container just recorded, from the newest `eval-wire.json` under the
    session root. Returns None when there is none - a live run records no wire, and the
    envelope assertion is simply not applied then."""
    found = sorted(Path(state_root).rglob(WIRE_FILE), key=lambda path: path.stat().st_mtime)
    if not found:
        return None
    return json.loads(found[-1].read_text(encoding='utf-8'))
