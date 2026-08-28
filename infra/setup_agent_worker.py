"""Create only the isolated platform worker; never print credentials."""
import json
from pathlib import Path
import subprocess

path=Path('.runtime/platform-users.json')
profiles=json.loads(path.read_text())
if 'worker' in profiles:raise SystemExit('Worker profile already exists; refusing credential replacement')
script='''
import contextlib, json, os, secrets, sys
import frappe
with contextlib.redirect_stdout(sys.stderr):
    os.chdir('/home/frappe/frappe-bench/sites')
    frappe.init(site='dsherp-platform.localhost',sites_path='/home/frappe/frappe-bench/sites')
    frappe.connect(); frappe.set_user('Administrator')
    frappe.reload_doc('platform','doctype','ds_agent_task',force=True)
    role='DS Agent Worker'; user='agent-worker@example.invalid'
    if frappe.db.exists('User',user):raise RuntimeError('Worker already exists')
    if not frappe.db.exists('Role',role):frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':0}).insert()
    key=secrets.token_hex(12); secret=secrets.token_urlsafe(32)
    frappe.get_doc({'doctype':'User','email':user,'first_name':'Isolated agent worker','enabled':1,'user_type':'System User','send_welcome_email':0,'roles':[{'role':role}],'api_key':key,'api_secret':secret}).insert()
    frappe.db.commit();frappe.clear_cache();frappe.destroy()
print(json.dumps({'user':user,'site':'dsherp-platform.localhost','api_key':key,'api_secret':secret}))
'''
r=subprocess.run(['docker','compose','-f','infra/compose.validation.yml','exec','-T','platform-backend','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True)
if r.returncode:raise SystemExit('Worker setup failed; '+r.stderr)
profiles['worker']=json.loads(r.stdout)
path.write_text(json.dumps(profiles,indent=2));path.chmod(0o600)
worker=profiles['worker']
minimal=Path('.runtime/agent-worker.json')
minimal.write_text(json.dumps({'base_url':'http://127.0.0.1:18083','site':'platform.localhost','api_key':worker['api_key'],'api_secret':worker['api_secret']},indent=2))
minimal.chmod(0o600)
print('Dedicated worker created and private profile stored')
