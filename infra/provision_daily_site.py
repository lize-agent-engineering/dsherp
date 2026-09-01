"""Create the empty daily Site once; real company setup stays in the native wizard."""
from pathlib import Path
import subprocess

SITE='dsherp-daily.localhost'
ROOT=Path('/home/frappe/frappe-bench')
sites=ROOT/'sites'
if (sites/SITE).exists():raise SystemExit('Daily Site already exists; inspect instead of retrying')
db_root_password=Path('/run/secrets/db_root_password').read_text().strip()
admin_password=Path('/run/secrets/daily_admin_password').read_text().strip()

def run(arguments):
    try:
        subprocess.run(arguments,cwd=ROOT,check=True)
    finally:
        bench_log=ROOT/'logs'/'bench.log'
        if bench_log.exists():
            redacted=bench_log.read_text(errors='replace')
            for secret in (db_root_password,admin_password):
                redacted=redacted.replace(secret, "[redacted]")
            bench_log.write_text(redacted)

run(['bench','new-site',SITE,'--db-host','db','--db-root-username','root',
    '--db-root-password',db_root_password,
    '--admin-password',admin_password,
    '--mariadb-user-host-login-scope','%','--install-app','erpnext'])
run(['bench','--site',SITE,'install-app','dsherp_bridge'])
print('Daily Site created without company or synthetic business fixtures.')
