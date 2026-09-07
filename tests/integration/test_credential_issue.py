"""Issuing a credential writes the two columns Frappe reads and nothing else: no User save,
no controller run, no background job. The key it issues authenticates over HTTP at once."""
import json
import subprocess


def _run(script, timeout=180):
    return subprocess.run(["docker", "exec", "-i", "dsherp-validation-backend-1",
                           "/home/frappe/frappe-bench/env/bin/python", "-"],
                          input=script, text=True, capture_output=True, timeout=timeout)


def test_issuing_enqueues_nothing_runs_no_user_controller_and_the_key_works_immediately():
    script = r'''
import os,json,frappe,requests
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect();frappe.set_user('Administrator')
from dsherp_bridge import credentials
user='issue-probe-'+frappe.generate_hash(length=6)+'@example.invalid'
frappe.get_doc({'doctype':'User','email':user,'first_name':'Issue probe','enabled':1,'user_type':'System User','send_welcome_email':0}).insert(ignore_permissions=True)
frappe.db.commit()
enqueued=[];saved=[]
frappe.enqueue=lambda *a,**k:enqueued.append(a[0] if a else k.get('method'))
from frappe.core.doctype.user.user import User
original_on_update=User.on_update
User.on_update=lambda self:saved.append(self.name)
try:
    pair=credentials.issue(user,issued_for='probe');frappe.db.commit()
    with requests.Session() as client:
        client.trust_env=False
        r=client.get('http://backend:8000/api/method/frappe.auth.get_logged_user',
            headers={'X-Frappe-Site-Name':'dsherp-validation.localhost','Authorization':'token '+pair['api_key']+':'+pair['api_secret']},timeout=10)
    out={'enqueued':enqueued,'user_controller_ran':saved,'http':r.status_code,'who':r.json().get('message')}
finally:
    User.on_update=original_on_update
    frappe.db.delete('DS Business Credential',{'user':user})
    frappe.db.delete('User',{'name':user})
    frappe.db.sql('delete from `__Auth` where doctype=%s and name=%s',('User',user))
    frappe.db.commit()
print(json.dumps(out));frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out['enqueued'] == [] and out['user_controller_ran'] == [], out
    assert out['http'] == 200 and out['who'].startswith('issue-probe-'), out
