"""One-time local alpha runtime identity, with no ERP business roles."""
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'.runtime'/'context-worker.json'
SCRIPT=r'''
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect();frappe.set_user('Administrator')
from frappe.core.doctype.user.user import generate_keys
from frappe.installer import update_site_config
user='dsherp-context-runtime@example.invalid'
assert not frappe.db.exists('User',user),'Service user exists; inspect before retry'
assert not frappe.conf.get('dsherp_runtime_user'),'Runtime binding already exists'
frappe.get_doc({'doctype':'User','email':user,'first_name':'DSHERP Context Runtime','enabled':1,'user_type':'System User','send_welcome_email':0,'roles':[]}).insert()
keys=generate_keys(user)
frappe.set_user(user)
assert not frappe.has_permission('Item','read')
assert not frappe.has_permission('Customer','read')
frappe.set_user('Administrator');frappe.db.commit()
update_site_config('dsherp_runtime_user',user)
print(json.dumps({'base_url':'http://127.0.0.1:18081','business_url':'http://dsherp-validation-backend-1:8000','site':'dsherp-validation.localhost',**keys}))
frappe.destroy()
'''


def main():
    if TARGET.exists():raise RuntimeError('Runtime profile exists; refusing credential rotation')
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=SCRIPT,text=True,capture_output=True,timeout=40)
    if result.returncode:raise RuntimeError(result.stderr)
    profile=json.loads(result.stdout)
    fd=os.open(TARGET,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as file:json.dump(profile,file)
    print('Local alpha runtime identity provisioned; Item/Customer read permissions absent.')


if __name__=='__main__':main()
