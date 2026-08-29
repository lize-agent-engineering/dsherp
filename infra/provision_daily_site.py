"""Create the empty daily Site once; real company setup stays in the native wizard."""
from pathlib import Path
import subprocess

SITE='dsherp-daily.localhost'
ROOT=Path('/home/frappe/frappe-bench')
sites=ROOT/'sites'
if (sites/SITE).exists():raise SystemExit('Daily Site already exists; inspect instead of retrying')
subprocess.run(['bench','new-site',SITE,'--db-host','db','--db-root-username','root',
    '--db-root-password',Path('/run/secrets/db_root_password').read_text().strip(),
    '--admin-password',Path('/run/secrets/daily_admin_password').read_text().strip(),
    '--mariadb-user-host-login-scope','%','--install-app','erpnext'],cwd=ROOT,check=True)
subprocess.run(['bench','--site',SITE,'install-app','dsherp_bridge'],cwd=ROOT,check=True)
print('Daily Site created without company or synthetic business fixtures.')
