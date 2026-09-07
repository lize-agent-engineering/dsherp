"""One business user, one enabled platform binding - guaranteed by the database, not by a
read-then-write check (R2); and a binding version that counts real binding changes, not
credential renewals (R8). Against the real platform Site."""
import json
import subprocess


def _run(script, timeout=180):
    return subprocess.run(["docker", "exec", "-i", "dsherp-validation-platform-backend-1",
                           "/home/frappe/frappe-bench/env/bin/python", "-"],
                          input=script, text=True, capture_output=True, timeout=timeout)


def test_two_transactions_cannot_both_insert_an_enabled_binding_for_the_same_business_user():
    """Two independent database connections, each running the real DocType controller: the
    first commits; the second, which passed the same application-level check while the first
    was uncommitted, is refused by the unique index when it commits."""
    script = r'''
import os,json,threading,frappe
os.chdir('/home/frappe/frappe-bench/sites')
SITE='dsherp-platform.localhost'
frappe.init(site=SITE,sites_path='/home/frappe/frappe-bench/sites');frappe.connect();frappe.set_user('Administrator')
enterprise='probe-r2-'+frappe.generate_hash(length=6)
frappe.get_doc({'doctype':'DS Enterprise','enterprise_id':enterprise,'title':'R2 probe','site':'probe.invalid',
                'base_url':'http://probe.invalid','status':'Ready'}).insert(ignore_permissions=True)
users=[]
for tag in ('one','two'):
    email=f'r2-{tag}-{frappe.generate_hash(length=5)}@example.invalid'
    frappe.get_doc({'doctype':'User','email':email,'first_name':'R2 '+tag,'enabled':1,'send_welcome_email':0}).insert(ignore_permissions=True)
    users.append(email)
frappe.db.commit();frappe.destroy()

outcome={}
import time
def attempt(tag,platform_user,hold_seconds,start_after):
    time.sleep(start_after)
    frappe.init(site=SITE,sites_path='/home/frappe/frappe-bench/sites');frappe.connect();frappe.set_user('Administrator')
    try:
        doc=frappe.get_doc({'doctype':'DS Membership','enterprise':enterprise,'platform_user':platform_user,'enabled':1,
                            'erp_user':'shared-business-user@example.invalid','api_key':'k'+tag,'api_secret':'s'+tag})
        # The first transaction inserts and keeps its transaction open; the second passes the
        # same application-level check (the first row is not committed, so it sees no clash)
        # and its INSERT then waits on the unique index until the first commits.
        doc.insert(ignore_permissions=True)
        time.sleep(hold_seconds)
        frappe.db.commit()
        outcome[tag]='committed'
    except Exception as error:
        outcome[tag]=type(error).__name__
        frappe.db.rollback()
    finally:
        frappe.destroy()
threads=[threading.Thread(target=attempt,args=('one',users[0],3.0,0.0)),
         threading.Thread(target=attempt,args=('two',users[1],0.0,1.0))]
[t.start() for t in threads];[t.join(timeout=90) for t in threads]

frappe.init(site=SITE,sites_path='/home/frappe/frappe-bench/sites');frappe.connect();frappe.set_user('Administrator')
enabled=frappe.get_all('DS Membership',filters={'enterprise':enterprise,'enabled':1},pluck='platform_user')
result={'outcome':outcome,'enabled_bindings':len(enabled)}
# clean up everything the probe created
frappe.db.sql('delete from `tabDS Membership` where enterprise=%s',(enterprise,))
frappe.db.sql('delete from `tabDS Enterprise` where name=%s',(enterprise,))
for email in users:frappe.delete_doc('User',email,force=True,ignore_permissions=True)
frappe.db.commit()
result['cleaned']=not frappe.db.exists('DS Enterprise',enterprise)
print(json.dumps(result));frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out['enabled_bindings'] == 1, out
    assert out['outcome']['one'] == 'committed', out
    assert out['outcome']['two'] != 'committed', out
    assert out['cleaned'], out


def test_the_binding_version_counts_binding_changes_and_ignores_credential_renewals():
    script = r'''
import os,json,frappe
from frappe.utils import now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost',sites_path='/home/frappe/frappe-bench/sites');frappe.connect();frappe.set_user('Administrator')
enterprise='probe-r8-'+frappe.generate_hash(length=6)
frappe.get_doc({'doctype':'DS Enterprise','enterprise_id':enterprise,'title':'R8 probe','site':'probe.invalid',
                'base_url':'http://probe.invalid','status':'Ready'}).insert(ignore_permissions=True)
email=f'r8-{frappe.generate_hash(length=5)}@example.invalid'
frappe.get_doc({'doctype':'User','email':email,'first_name':'R8','enabled':1,'send_welcome_email':0}).insert(ignore_permissions=True)
doc=frappe.get_doc({'doctype':'DS Membership','enterprise':enterprise,'platform_user':email,'enabled':1,
                    'erp_user':'r8-business@example.invalid','api_key':'k','api_secret':'s'}).insert(ignore_permissions=True)
seen=[doc.binding_version]
# a credential renewal, the way accept_credential writes it
doc.api_secret='renewed';doc.credential_issued_at=now_datetime();doc.credential_version=2;doc.save(ignore_permissions=True)
seen.append(doc.binding_version)
doc.enabled=0;doc.save(ignore_permissions=True);seen.append(doc.binding_version)
doc.enabled=1;doc.save(ignore_permissions=True);seen.append(doc.binding_version)
doc.erp_user='r8-other@example.invalid';doc.save(ignore_permissions=True);seen.append(doc.binding_version)
doc.erp_user='r8-business@example.invalid';doc.save(ignore_permissions=True);seen.append(doc.binding_version)
from dsherp_platform.api import binding_version
final=binding_version(frappe.get_doc('DS Membership',doc.name))
frappe.db.rollback()
frappe.db.sql('delete from `tabDS Membership` where enterprise=%s',(enterprise,))
frappe.db.sql('delete from `tabDS Enterprise` where name=%s',(enterprise,))
if frappe.db.exists('User',email):frappe.delete_doc('User',email,force=True,ignore_permissions=True)
frappe.db.commit()
print(json.dumps({'versions':seen,'api_says':final}));frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    # insert=1, renewal keeps 1, disable=2, re-enable=3, rebind=4, rebind back=5: never the old number again
    assert out['versions'] == [1, 1, 2, 3, 4, 5], out
    assert out['api_says'] == '5', out
