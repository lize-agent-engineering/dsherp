"""Pair only the existing synthetic alpha/beta Sites; no extra services."""
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PAIR=ROOT/'.runtime/configuration-preview.json'
SCRIPT=r'''
import os,json,frappe
from frappe.installer import update_site_config
data=json.loads(DATA)
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site=data['site']);frappe.connect()
try:
    assert data['site'] in ('dsherp-validation.localhost','dsherp-beta.localhost')
    if data['site']=='dsherp-beta.localhost':
        assert frappe.conf.dsherp_preview==1 and frappe.conf.mute_emails==1
    existing=frappe.conf.get(data['setting'])
    assert not existing or existing==data['peer'],'Existing pair differs; do not replace it'
    frappe.set_user('Administrator')
    if not frappe.db.exists('User',data['user']):
        assert data['site']=='dsherp-validation.localhost'
        frappe.get_doc({'doctype':'User','email':data['user'],'first_name':'合成配置发布验收',
            'enabled':1,'send_welcome_email':0,'user_type':'System User','new_password':data['password'],
            'roles':[{'role':'System Manager'}]}).insert()
    frappe.set_user(data['user'])
    assert frappe.db.get_value('User',data['user'],'enabled')
    for doctype in ('DocType','Custom Field','Workflow'):assert frappe.has_permission(doctype,'create')
    frappe.db.commit()
    if not existing:update_site_config(data['setting'],data['peer'])
    print('Configured synthetic Site pair and verified native configuration identity')
finally:frappe.destroy()
'''


def main():
    operator=json.loads((ROOT/'.runtime/preview-operator.json').read_text())
    if operator['site']!='dsherp-beta.localhost' or operator['user']!='dsherp-preview@example.invalid':
        raise ValueError('Expected the existing synthetic preview operator')
    if PAIR.exists():pair=json.loads(PAIR.read_text())
    else:
        pair={'secret':secrets.token_urlsafe(48)}
        fd=os.open(PAIR,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as file:json.dump(pair,file)
    if not isinstance(pair.get('secret'),str) or len(pair['secret'])<48:raise ValueError('Invalid local pair key')
    targets=[('dsherp-validation-backend-1','dsherp-validation.localhost','dsherp_configuration_preview',
              {'site':'dsherp-beta.localhost','url':'http://beta-backend:8000','public_url':'http://preview.localhost:18085','secret':pair['secret']}),
             ('dsherp-validation-beta-backend-1','dsherp-beta.localhost','dsherp_configuration_source',
              {'site':'dsherp-validation.localhost','url':'http://backend:8000','secret':pair['secret']})]
    for container,site,setting,peer in targets:
        data={'site':site,'setting':setting,'peer':peer,'user':operator['user'],'password':operator['password']}
        result=subprocess.run(['docker','exec','-i',container,'/home/frappe/frappe-bench/env/bin/python','-'],
            input=SCRIPT.replace('DATA',repr(json.dumps(data))),text=True,capture_output=True,timeout=45)
        if result.returncode:raise RuntimeError('Synthetic pair setup failed; inspect configured state before retrying')
    print('Paired synthetic alpha/beta configuration preview; private key remains in local state and Site configuration.')


if __name__=='__main__':main()
