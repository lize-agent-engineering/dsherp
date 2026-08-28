"""Synthetic alpha UI acceptance fixture; no platform or production identities."""
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'.runtime/context-writer.json'
SCRIPT=r'''
import os,json,secrets,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect();frappe.set_user('Administrator')
user='dsherp-writer@example.invalid';item_name='DSHERP-HITL-ITEM'
assert not frappe.db.exists('User',user),'Synthetic writer already exists'
assert not frappe.db.exists('Item',item_name),'Synthetic Item already exists'
password=secrets.token_urlsafe(32)
frappe.get_doc({'doctype':'User','email':user,'first_name':'业务操作验收','enabled':1,
    'send_welcome_email':0,'user_type':'System User','new_password':password,
    'roles':[{'role':'Item Manager'},{'role':'Sales User'}]}).insert()
item=frappe.copy_doc(frappe.get_doc('Item','DSHERP-TEST-ITEM'))
item.item_code=item_name;item.item_name='HITL 确认前物料';item.insert()
frappe.set_user(user)
item.check_permission('read');item.check_permission('write')
assert not frappe.has_permission('User','write',doc=frappe.get_doc('User','Administrator'))
frappe.db.commit()
print(json.dumps({'user':user,'password':password,'item':item.name,'baseline':str(item.modified)},ensure_ascii=False))
frappe.destroy()
'''


def main():
    if TARGET.exists():raise RuntimeError('Acceptance profile already exists; inspect it instead of resetting credentials')
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=SCRIPT,text=True,capture_output=True,timeout=40)
    if result.returncode:
        kind=result.stderr.strip().splitlines()[-1].split(':')[0]
        raise RuntimeError('Synthetic writer provisioning failed ('+kind+'); no credentials printed')
    profile=json.loads(result.stdout)
    fd=os.open(TARGET,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as file:json.dump(profile,file,ensure_ascii=False)
    print('Created ordinary synthetic writer and isolated HITL Item; private local profile saved.')


if __name__=='__main__':main()
