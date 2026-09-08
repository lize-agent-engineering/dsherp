"""One way into a Site's interpreter for every integration test.

`run_site_script` wraps `docker compose -f infra/compose.validation.yml exec -T <service>
/home/frappe/frappe-bench/env/bin/python -`. The body runs inside a connected Frappe context
as Administrator (or the user asked for), and `frappe.destroy()` is guaranteed on the
container side. Sites map to compose services here, so no test names a container. A body
must not call frappe.init/connect/destroy itself.

A host-side timeout kills the docker client, not the interpreter inside the container: the
body's own `finally` may never run. That is why the residue registry (residue.py) subscribes
to ON_TIMEOUT, and why anything a script commits must be registered before the script runs.

The addressing itself (`SITES`, `service_of`, `site_script`, `command_for`) lives in
`infra/site_exec.py` so the evaluation runner can use the same table without importing from
the test tree; it is re-exported here so every existing import keeps working.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infra.site_exec import (  # noqa: E402,F401  (re-exported for the integration suite)
    BENCH_PYTHON, COMPOSE, SITES, SITES_DIR, command_for, service_of, site_script,
)

# Called as observer(site, body) when the host timeout kills a script.
ON_TIMEOUT = []


def run_site_script(site, body, *, timeout=120, connect=True, user='Administrator', run=subprocess.run):
    """Run `body` on `site`; return its stdout. A non-zero exit is an AssertionError carrying
    the last lines of stderr; a timeout is announced to ON_TIMEOUT and re-raised."""
    command = command_for(site)
    try:
        result = run(command, cwd=ROOT, input=site_script(site, body, connect=connect, user=user),
                     text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        for observer in list(ON_TIMEOUT):
            observer(site, body)
        raise
    if result.returncode:
        tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-15:])
        raise AssertionError(f'站点脚本失败（{site} / {service_of(site)}，退出码 {result.returncode}）：\n{tail}')
    return result.stdout


def run_site_json(site, body, **kwargs):
    """The script's last non-empty stdout line parsed as JSON - the convention every script uses."""
    lines = [line for line in run_site_script(site, body, **kwargs).splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f'站点脚本没有输出（{site}）')
    return json.loads(lines[-1])
