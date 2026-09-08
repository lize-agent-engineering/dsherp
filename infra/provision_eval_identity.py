"""Business credentials for the evaluation runner on the isolated synthetic daily Site.

The daily Site already has everything an evaluation needs — a runtime identity, the business
user `daily-operator@example.invalid`, the manufacturing policy set and fixtures. The one
thing missing is a **reusable** key pair for that user: `initialize_daily_synthetic.py`
issues one but does not save it, so the runner has nothing to authenticate with.

Why daily and not the alpha validation Site: `DS Model Run.on_trash` refuses deletion
unconditionally, so every evaluation permanently leaves dozens of runs, events and pending
proposals behind. The alpha Site carries ~214 integration tests whose sweep and audit reports
would slowly be polluted by them, and that pollution cannot be undone.

Issued through the Site's own credential module (never `generate_keys`): a key with no
recorded window is refused by the Site (S2). Idempotent in two layers — an existing profile
file is reused as-is, and on the Site `credentials.current` hands back a pair that is still
comfortably inside its window rather than rotating it. Re-provisioning therefore never
invalidates a runner that is mid-flight.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.site_exec import BENCH_PYTHON, COMPOSE, service_of  # noqa: E402

DEFAULT_SITE = 'dsherp-daily.localhost'
OPERATOR = 'daily-operator@example.invalid'
DEFAULT_OUT = '.runtime/eval-users.json'

SCRIPT = r"""
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=__SITE__);frappe.connect();frappe.set_user('Administrator')
user = __USER__
if not frappe.db.exists('User', user):
    raise SystemExit('评估业务用户不存在：' + user + '；先跑 initialize_daily_synthetic.py')
# Through the Site's own credential module, never generate_keys: a key with no recorded
# window is refused by the Site (S2). `current` is the idempotent door - a pair that is
# still comfortably inside its window is handed back unchanged, so re-provisioning never
# invalidates a runner that is mid-flight.
from dsherp_bridge import credentials
before = credentials.record(user)
keys = credentials.current(user, 'eval')
frappe.db.commit()
frappe.set_user(user)
for doctype, action in (('Item', 'read'), ('Sales Order', 'read'), ('Purchase Order', 'read')):
    if not frappe.has_permission(doctype, action):
        raise SystemExit('评估用户缺少 ' + doctype + ' 的 ' + action + ' 权限')
frappe.set_user('Administrator')
print(json.dumps({'user': user, 'api_key': keys['api_key'], 'api_secret': keys['api_secret'],
                  'version': keys['version'],
                  'reused': bool(before) and int(before['version']) == int(keys['version'])},
                 ensure_ascii=False))
"""


def _script(site, user):
    return SCRIPT.replace('__SITE__', repr(site)).replace('__USER__', repr(user))


def main(argv=None, run=subprocess.run):
    parser = argparse.ArgumentParser(description='为评估运行器开通业务用户凭据（隔离合成站）')
    parser.add_argument('--site', default=DEFAULT_SITE)
    parser.add_argument('--user', default=OPERATOR)
    parser.add_argument('--out', default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    target = ROOT / args.out
    if target.is_file():
        profile = json.loads(target.read_text())
        if profile.get('site') == args.site and profile.get('operator', {}).get('api_key'):
            print(f'评估身份已存在，复用 {args.out}（不轮换密钥）')
            return profile

    command = [*COMPOSE, 'exec', '-T', service_of(args.site), BENCH_PYTHON, '-']
    result = run(command, cwd=ROOT, input=_script(args.site, args.user),
                 text=True, capture_output=True, timeout=180)
    if result.returncode:
        tail = (result.stderr or result.stdout or '').strip().splitlines()[-8:]
        raise RuntimeError('评估身份开通失败；未打印任何凭据：\n' + '\n'.join(tail))
    issued = json.loads([line for line in result.stdout.splitlines() if line.strip()][-1])
    service = json.loads((ROOT / '.runtime/context-worker-daily.json').read_text())
    profile = {'site': args.site, 'business_url': service['business_url'],
               'operator': {'user': issued['user'], 'api_key': issued['api_key'],
                            'api_secret': issued['api_secret'],
                            'site': args.site, 'base_url': service['base_url']}}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(profile, handle, ensure_ascii=False)
    print(f'评估身份已开通并写入 {args.out}（0600，不入库）')
    return profile


if __name__ == '__main__':
    main()
