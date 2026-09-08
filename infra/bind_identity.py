"""Create explicit synthetic memberships; no same-email inference or shared admin key.

The membership carries the credential window the business Site recorded when it issued the
key, not a window the platform invents. Both sides then agree about the same window, which is
what `desk_identity` checks before it will answer at all: with `credential_expires_at` empty it
calls the credential expired, and its fallback refuses because `credential_erp_user` is empty
too - so a Site built from zero could never complete an SSO round trip, and the refusal reached
the browser as a bare 403 on the callback.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path('.runtime')
ENTERPRISES = (
    ('alpha', '甲企业验收站点', 'http://backend:8000'),
    ('beta', '乙企业验收站点', 'http://beta-backend:8000'),
)
PLATFORM_USER = 'member@example.invalid'


def memberships(*profiles):
    """One binding row per business Site, carrying its recorded credential window.

    A profile without a window is refused rather than written as empty: empty window columns
    are exactly the state that made a from-zero stack unable to log in, and writing them again
    would reproduce it silently."""
    rows = []
    for (enterprise, title, base_url), profile in zip(ENTERPRISES, profiles):
        for field in ('user', 'api_key', 'api_secret', 'expires_at', 'version'):
            if profile.get(field) in (None, ''):
                raise ValueError(f'{enterprise} 的凭据档案缺少 {field}；'
                                 '开通必须走站点自己的凭据模块，它才会登记窗口')
        rows.append({
            'enterprise': enterprise, 'title': title, 'site': profile['site'], 'base_url': base_url,
            'platform_user': PLATFORM_USER, 'erp_user': profile['user'],
            'api_key': profile['api_key'], 'api_secret': profile['api_secret'],
            # The window the business Site recorded, and the user it was proved to answer as.
            'credential_issued_at': profile.get('issued_at') or profile['expires_at'],
            'credential_expires_at': profile['expires_at'],
            'credential_erp_user': profile['user'],
            'credential_version': profile['version'],
        })
    return rows


SITE_SCRIPT = '''
import os
import frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost');frappe.connect();frappe.set_user('Administrator')
from frappe.installer import update_site_config
update_site_config('dsherp_business_sites',{row['site']:row['base_url'] for row in bindings})
for row in bindings:
    if frappe.db.exists('DS Enterprise',row['enterprise']):
        raise RuntimeError('Enterprise already exists; inspect before retrying')
    frappe.get_doc({'doctype':'DS Enterprise','enterprise_id':row['enterprise'],'title':row['title'],
                    'site':row['site'],'base_url':row['base_url'],'status':'Ready'}).insert()
    frappe.get_doc({'doctype':'DS Membership','enterprise':row['enterprise'],
                    'platform_user':row['platform_user'],'enabled':1,'erp_user':row['erp_user'],
                    'api_key':row['api_key'],'api_secret':row['api_secret'],
                    'credential_issued_at':row['credential_issued_at'],
                    'credential_expires_at':row['credential_expires_at'],
                    'credential_erp_user':row['credential_erp_user'],
                    'credential_version':row['credential_version']}).insert()
frappe.db.commit();frappe.clear_cache();frappe.destroy()
print('Explicit synthetic enterprise bindings saved')
'''


def main():
    alpha = json.loads((ROOT / 'erp-reader.json').read_text())
    beta = json.loads((ROOT / 'beta-users.json').read_text())['reader']
    script = 'bindings = ' + repr(memberships(alpha, beta)) + SITE_SCRIPT
    result = subprocess.run(
        ['docker', 'compose', '-f', 'infra/compose.validation.yml', 'exec', '-T',
         'platform-backend', '/home/frappe/frappe-bench/env/bin/python', '-'],
        input=script, text=True, capture_output=True)
    if result.returncode:
        # Error frames can include request details; keep credential-bearing input private.
        print('Binding failed; no automatic retry. Error:',
              result.stderr.splitlines()[-1] if result.stderr else 'unknown')
        raise SystemExit(result.returncode)
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
