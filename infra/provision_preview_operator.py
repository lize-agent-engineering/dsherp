"""One synthetic native configuration identity, only on the isolated beta Site."""
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'.runtime/preview-operator.json'
SCRIPT=r'''
import os,json,secrets,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
try:
    assert frappe.conf.dsherp_preview==1 and frappe.conf.mute_emails==1
    frappe.set_user('Administrator')
    user='dsherp-preview@example.invalid'
    assert not frappe.db.exists('User',user),'Synthetic preview operator already exists'
    password=secrets.token_urlsafe(32)
    frappe.get_doc({'doctype':'User','email':user,'first_name':'合成预览配置验收',
        'enabled':1,'send_welcome_email':0,'user_type':'System User','new_password':password,
        'roles':[{'role':'System Manager'}]}).insert()
    frappe.set_user(user)
    for doctype in ('DocType','Custom Field','Workflow'):assert frappe.has_permission(doctype,'create')
    frappe.db.commit()
    print(json.dumps({'user':user,'password':password,'site':'dsherp-beta.localhost'}))
finally:frappe.destroy()
'''


def main():
    if TARGET.exists():raise RuntimeError('Preview profile already exists; do not reset credentials')
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=SCRIPT,text=True,capture_output=True,timeout=60)
    if result.returncode:raise RuntimeError('Synthetic preview identity provisioning failed; no credentials printed')
    profile=json.loads(result.stdout)
    fd=os.open(TARGET,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as file:json.dump(profile,file,ensure_ascii=False)
    print('Created isolated synthetic preview operator; private profile saved.')


if __name__=='__main__':main()
