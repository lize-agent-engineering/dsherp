"""One-time synthetic validation provisioning, run in a short-lived control container."""
import json
from pathlib import Path
import subprocess
import sys

kind = sys.argv[1]
if kind not in ('platform', 'beta'):
    raise SystemExit('Expected platform or beta')
site = f'dsherp-{kind}.localhost'
site_root = Path('/home/frappe/frappe-bench/sites')
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
args=['bench','new-site',site,'--db-host','db','--db-root-username','root',
      '--db-root-password',Path('/run/dsherp-control/db_root_password').read_text().strip(),
      '--admin-password',Path(f'/run/dsherp-control/{kind}_admin_password').read_text().strip(),
      '--mariadb-user-host-login-scope','%','--set-default']
if kind == 'beta': args += ['--install-app','erpnext']
subprocess.run(args,check=True)
subprocess.run(['bench','--site',site,'install-app',app],check=True)
