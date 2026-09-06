"""The grant a background executor acts under lives beside the run, not in it (R7), and an
executor with no grant on file is refused wherever SSO is enforced (R6). Real Site."""
import json
import subprocess


def _run(script, timeout=180):
    return subprocess.run(["docker", "exec", "-i", "dsherp-validation-backend-1",
                           "/home/frappe/frappe-bench/env/bin/python", "-"],
                          input=script, text=True, capture_output=True, timeout=timeout)


def test_the_grant_is_cached_for_the_run_never_stored_in_it_and_dropped_when_it_ends():
    script = r'''
import os,uuid,json,frappe
from frappe.utils.password import encrypt
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha','binding_version':'1','enterprise_version':'v1'}
sso.identity_for_token=lambda token:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
doc=api.send_message('Grant placement probe',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={}
out['column_present']='platform_grant' in frappe.db.get_table_columns('DS Model Run')
out['cached']=grants.of(run)==grant
out['row_mentions_token']='synthetic-token' in json.dumps(frappe.db.get_value('DS Model Run',run,'*',as_dict=True),default=str)
frappe.session.data.pop('dsherp_platform_grant',None)
frappe.conf.dsherp_runtime_user=actor
claim=execution.claim_run('a'*64);frappe.db.commit()
out['claimed']=bool(claim)
frappe.set_user('Guest')
execution.finish_run(run_id=claim['run_id'],capability=claim['capability'],status='Failed',error='probe end');frappe.db.commit()
out['cached_after_finish']=grants.of(run)
print(json.dumps(out))
frappe.set_user('Administrator')
frappe.db.delete('DS Run Event',{'run':run})
frappe.db.sql('delete from `tabDS Model Run` where name=%s',(run,))
frappe.db.sql('delete from `tabDS Conversation` where name=%s',(doc['id'],))
frappe.db.commit();frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out['column_present'] is False and out['cached'] is True, out
    assert out['row_mentions_token'] is False, out
    assert out['claimed'] is True and out['cached_after_finish'] is None, out


def test_an_executor_without_a_grant_is_refused_where_sso_is_enforced_and_the_run_fails_closed():
    script = r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
frappe.session.data.pop('dsherp_platform_grant',None)
doc=api.send_message('No-grant probe',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={'cached':grants.of(run)}
# this Site allows password login (dev); make the executor see the production rule
sso._password_login_disabled=lambda:True
frappe.conf.dsherp_runtime_user=actor
claim=execution.claim_run('a'*64);frappe.db.commit()
out['claim']=claim
out['status']=frappe.db.get_value('DS Model Run',run,'status')
out['error']=frappe.db.get_value('DS Model Run',run,'error')
print(json.dumps(out,ensure_ascii=False))
frappe.set_user('Administrator')
frappe.db.delete('DS Run Event',{'run':run})
frappe.db.sql('delete from `tabDS Model Run` where name=%s',(run,))
frappe.db.sql('delete from `tabDS Conversation` where name=%s',(doc['id'],))
frappe.db.commit();frappe.destroy()
'''
    result = _run(script)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out['cached'] is None and out['claim'] is None, out
    assert out['status'] == 'Failed', out
