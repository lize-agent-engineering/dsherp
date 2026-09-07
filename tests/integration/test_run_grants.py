"""The grant a background executor acts under lives beside the run, not in it (R7), and an
executor with no grant on file is refused wherever SSO is enforced (R6). Real Site."""
import uuid

from site_exec import run_site_json

SITE = 'dsherp-validation.localhost'
ACTOR = 'dsherp-reader@example.invalid'
CONTEXT = "{'schema_version':1,'page_type':'unknown','route':[]}"


def test_the_grant_is_cached_for_the_run_never_stored_in_it_and_dropped_when_it_ends(residue):
    # send_message titles the conversation with the question's first 100 characters, so a uuid tag
    # in the question is what identifies this test's own rows before the script creates them.
    question = f'Grant placement probe {uuid.uuid4().hex}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    out = run_site_json(SITE, r'''
from frappe.utils.password import encrypt
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor=ACTOR;frappe.set_user(actor)
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha','binding_version':'1','enterprise_version':'v1'}
sso.identity_for_token=lambda token:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
doc=api.send_message(QUESTION,CONTEXT,uuid.uuid4().hex)
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
'''.replace('ACTOR', repr(ACTOR)).replace('QUESTION', repr(question)).replace('CONTEXT', CONTEXT), timeout=180)
    assert out['column_present'] is False and out['cached'] is True, out
    assert out['row_mentions_token'] is False, out
    assert out['claimed'] is True and out['cached_after_finish'] is None, out


def test_an_executor_without_a_grant_is_refused_where_sso_is_enforced_and_the_run_fails_closed(residue):
    question = f'No-grant probe {uuid.uuid4().hex}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': question})
    out = run_site_json(SITE, r'''
from dsherp_bridge import sso,context_api as api,context_execution as execution,grants
actor=ACTOR;frappe.set_user(actor)
frappe.session.data.pop('dsherp_platform_grant',None)
doc=api.send_message(QUESTION,CONTEXT,uuid.uuid4().hex)
run=doc['active_run'];frappe.db.commit()
out={'cached':grants.of(run)}
# this Site allows password login (dev); the production switch is the site_config key
frappe.conf.dsherp_sso_required=1
frappe.conf.dsherp_runtime_user=actor
claim=execution.claim_run('a'*64);frappe.db.commit()
out['claim']=claim
out['status']=frappe.db.get_value('DS Model Run',run,'status')
out['error']=frappe.db.get_value('DS Model Run',run,'error')
print(json.dumps(out,ensure_ascii=False))
'''.replace('ACTOR', repr(ACTOR)).replace('QUESTION', repr(question)).replace('CONTEXT', CONTEXT), timeout=180)
    assert out['cached'] is None and out['claim'] is None, out
    assert out['status'] == 'Failed', out
