"""Create explicit synthetic memberships; no same-email inference or shared admin key."""
import json
from pathlib import Path
import subprocess

root=Path('.runtime')
alpha=json.loads((root/'erp-reader.json').read_text())
beta=json.loads((root/'beta-users.json').read_text())['reader']
bindings=[{'enterprise':'alpha','title':'甲企业验收站点','site':alpha['site'],'base_url':'http://backend:8000','credential':alpha},{'enterprise':'beta','title':'乙企业验收站点','site':beta['site'],'base_url':'http://beta-backend:8000','credential':beta}]
script='bindings = '+repr(bindings)+'''\nimport os
import frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost');frappe.connect();frappe.set_user('Administrator')
from frappe.installer import update_site_config
update_site_config('dsherp_business_sites',{row['site']:row['base_url'] for row in bindings})
for row in bindings:
    if frappe.db.exists('DS Enterprise',row['enterprise']):
        raise RuntimeError('Enterprise already exists; inspect before retrying')
    frappe.get_doc({'doctype':'DS Enterprise','enterprise_id':row['enterprise'],'title':row['title'],'site':row['site'],'base_url':row['base_url'],'status':'Ready'}).insert()
    credential=row['credential']
    frappe.get_doc({'doctype':'DS Membership','enterprise':row['enterprise'],'platform_user':'member@example.invalid','enabled':1,'erp_user':credential['user'],'api_key':credential['api_key'],'api_secret':credential['api_secret']}).insert()
frappe.db.commit();frappe.clear_cache();frappe.destroy()
print('Explicit synthetic enterprise bindings saved')
'''
result=subprocess.run(['docker','compose','-f','infra/compose.validation.yml','exec','-T','platform-backend','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True)
if result.returncode:
    # Error frames can include request details; keep credential-bearing input private.
    print('Binding failed; no automatic retry. Error:',result.stderr.splitlines()[-1] if result.stderr else 'unknown')
    raise SystemExit(result.returncode)
print(result.stdout.strip())
