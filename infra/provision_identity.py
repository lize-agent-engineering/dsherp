"""One-time synthetic validation provisioning, run in a short-lived control container."""
import json
from pathlib import Path
import subprocess
import sys

kind = sys.argv[1]
if kind not in ('platform', 'beta'):
    raise SystemExit('Expected platform or beta')
site = f'dsherp-{kind}.localhost'
bench_root = Path('/home/frappe/frappe-bench')
site_root = bench_root / 'sites'
if (site_root / site).exists():
    raise SystemExit('Site already exists; inspect before any retry')
app = 'dsherp_platform' if kind == 'platform' else 'dsherp_bridge'
(site_root / 'apps.txt').write_text('frappe\nerpnext\n' + app + '\n')
(site_root / 'common_site_config.json').write_text(json.dumps({
    'db_host':'db','db_port':3306,'default_site':site,
    'redis_cache':'redis://redis:6379/0','redis_queue':'redis://redis:6379/1',
    'redis_socketio':'redis://redis:6379/1',
}))
assets=site_root / 'assets'
if not assets.exists(): assets.symlink_to('/home/frappe/frappe-bench/assets',target_is_directory=True)
db_root_password=Path('/run/dsherp-control/db_root_password').read_text().strip()
admin_password=Path(f'/run/dsherp-control/{kind}_admin_password').read_text().strip()

def run(arguments):
    try:
        subprocess.run(arguments,cwd=bench_root,check=True)
    finally:
        bench_log=bench_root/'logs'/'bench.log'
        if bench_log.exists():
            redacted=bench_log.read_text(errors='replace')
            for secret in (db_root_password,admin_password):
                redacted=redacted.replace(secret, "[redacted]")
            bench_log.write_text(redacted)

args=['bench','new-site',site,'--db-host','db','--db-root-username','root',
      '--db-root-password',db_root_password,
      '--admin-password',admin_password,
      '--mariadb-user-host-login-scope','%','--set-default']
if kind == 'beta': args += ['--install-app','erpnext']
run(args)
run(['bench','--site',site,'install-app',app])
if kind == 'beta':
    for key in ('dsherp_preview','mute_emails','disable_scheduler','pause_scheduler','disable_async'):
        run(['bench','--site',site,'set-config','--parse',key,'1'])
