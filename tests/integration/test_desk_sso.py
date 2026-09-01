"""Native business identity validation before OAuth may establish a session."""
import subprocess
import httpx
from urllib.parse import urlparse,parse_qs
from html.parser import HTMLParser
from test_platform_identity import platform_client,platform_operator
import json


class ConsentForm(HTMLParser):
    def __init__(self):super().__init__();self.action=None;self.csrf=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form' and 'frappe.integrations.oauth2.approve' in attrs.get('action',''):self.action=attrs['action']
        if tag=='input' and attrs.get('name')=='csrf_token':self.csrf=attrs.get('value')


def test_oauth_transport_errors_do_not_escape_with_sensitive_frames():
    script=r'''
import os,frappe,traceback
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import sso
class Flow:
    def get_auth_session(self,**kwargs):raise ValueError('SYNTHETIC-SENSITIVE-DETAIL')
sso.get_oauth2_flow=lambda provider:Flow()
sso.get_redirect_uri=lambda provider:'http://localhost/callback'
try:sso.exchange('invalid');raise AssertionError('exchange failure ignored')
except frappe.PermissionError:
    assert 'SYNTHETIC-SENSITIVE-DETAIL' not in traceback.format_exc()
frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_real_native_oauth_code_exchange_logs_into_bound_business_user():
    with httpx.Client(base_url='http://localhost:18082',trust_env=False,timeout=30) as business,platform_client() as platform:
        start=business.get('/api/method/dsherp_bridge.sso.start')
        assert start.status_code==302
        target=urlparse(start.headers['location'])
        authorization=platform.get(target.path+'?'+target.query)
        if authorization.status_code==200:
            form=ConsentForm();form.feed(authorization.text)
            assert form.action and form.csrf,'Native OAuth consent form missing'
            authorization=platform.post(form.action,data={'csrf_token':form.csrf})
        assert authorization.status_code==302,authorization.text
        callback=urlparse(authorization.headers['location'])
        assert callback.netloc=='localhost:18082'
        result=business.get(callback.path+'?'+callback.query)
        assert result.status_code==302,result.text
        assert result.headers['location']=='/desk/dsherp-agent'
        identity=business.get('/api/method/frappe.auth.get_logged_user')
        assert identity.status_code==200,identity.text
        assert identity.json()['message']=='dsherp-reader@example.invalid'
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:business.get('/api/method/dsherp_bridge.context_api.list_sessions'),range(4)))
        assert all(response.status_code==200 for response in responses),[response.status_code for response in responses]
        assert business.get('/api/method/dsherp_bridge.context_api.list_sessions').status_code==200
        assert business.get(callback.path+'?'+callback.query).status_code==403
        with platform_operator() as operator:
            records=operator.get('/api/resource/DS Membership',params={'filters':json.dumps({'enterprise':'alpha','platform_user':'member@example.invalid'})}).json()['data']
            path='/api/resource/DS Membership/'+records[0]['name']
            try:
                assert operator.put(path,json={'enabled':0}).status_code==200
                assert business.get('/api/method/frappe.auth.get_logged_user').status_code==403
                assert business.get('/api/method/dsherp_bridge.context_api.list_sessions').status_code==403
            finally:
                assert operator.put(path,json={'enabled':1}).status_code==200
        logout=business.post('/api/method/logout')
        assert logout.status_code==200,logout.text


def test_configured_native_oauth_start_uses_exact_business_callback():
    with httpx.Client(base_url='http://127.0.0.1:18082',trust_env=False,timeout=15) as client:
        response=client.get('/api/method/dsherp_bridge.sso.start')
        assert response.status_code==302,response.text
        location=urlparse(response.headers['location'])
        assert location.netloc=='platform.localhost:18083'
        args=parse_qs(location.query)
        assert args['redirect_uri']==['http://localhost:18082/api/method/dsherp_bridge.sso.callback']
        assert args['state'] and args['client_id']


def test_background_run_keeps_sso_revocation_without_browser_session():
    script=r'''
import os,uuid,json,frappe
from frappe.utils.password import encrypt
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import sso,context_api as api,context_execution as execution
actor='dsherp-reader@example.invalid';frappe.set_user(actor)
frappe.conf.dsherp_platform_oauth={'provider':'dsherp','enterprise':'alpha'}
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha','binding_version':'v1','enterprise_version':'v1'}
sso.identity_for_token=lambda token:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
doc=api.send_message('Background SSO revocation',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex)
frappe.db.commit()
try:
    run=frappe.get_doc('DS Model Run',doc['active_run'])
    assert run.get('platform_grant')==grant
    assert grant not in json.dumps(doc)
    frappe.session.data.pop('dsherp_platform_grant',None)
    frappe.conf.dsherp_runtime_user=actor
    claim=execution.claim_run('a'*64);frappe.db.commit()
    assert claim and grant not in json.dumps(claim) and 'synthetic-token' not in json.dumps(claim)
    cap={'run_id':claim['run_id'],'capability':claim['capability']}
    frappe.set_user('Guest')
    sso.identity_for_token=lambda token:{**info,'binding_version':'v2'}
    for operation in (lambda:execution.run_status(**cap),lambda:execution.run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'}),lambda:execution.reserve_model_call(**cap,input_bytes=100,max_output_tokens=2048,provider='deepseek-official',model='deepseek-v4-flash',purpose='compaction',runtime_revision='a'*64)):
        try:operation();raise AssertionError('revoked background grant accepted')
        except frappe.PermissionError:pass
    assert frappe.db.get_value('DS Model Run',run.name,'model_calls')==0
    execution.finish_run(**cap,status='Failed',error='Synthetic revocation');frappe.db.commit()
    frappe.set_user(actor)
    new_info={**info,'binding_version':'v2'}
    frappe.session.data.dsherp_platform_grant=encrypt(json.dumps({'identity':new_info,'token':'new-synthetic-token'}))
    api.send_message('Renewed membership',{'schema_version':1,'page_type':'unknown','route':[]},uuid.uuid4().hex,session_id=doc['id']);frappe.db.commit()
    frappe.session.data.pop('dsherp_platform_grant',None)
    next_claim=execution.claim_run('a'*64);frappe.db.commit()
    assert next_claim['native_session_id']!=claim['native_session_id']
    assert next_claim['permission_revision']==claim['permission_revision']
    execution.finish_run(run_id=next_claim['run_id'],capability=next_claim['capability'],status='Failed',error='End synthetic run');frappe.db.commit()
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name in frappe.get_all('DS Model Run',filters={'conversation':doc['id']},pluck='name'):frappe.delete_doc('DS Model Run',name,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',doc['id'],ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_business_sso_rejects_cross_site_unbound_and_privileged_users():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.sso import validate_identity
frappe.conf.dsherp_platform_oauth={'provider':'dsherp','enterprise':'alpha'}
info={'sub':'member@example.invalid','email':'dsherp-reader@example.invalid','site':frappe.local.site,'enterprise':'alpha','binding_version':'v1','enterprise_version':'v1'}
assert validate_identity(info)=='dsherp-reader@example.invalid'
for patch in ({'site':'dsherp-beta.localhost'},{'enterprise':'beta'},{'email':'Administrator'},{'email':'Guest'},{'email':'not-provisioned@example.invalid'},{'sub':''},{'binding_version':''}):
    try:validate_identity({**info,**patch});raise AssertionError('invalid identity accepted')
    except frappe.PermissionError:pass
assert not frappe.db.exists('User','not-provisioned@example.invalid')
frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_oauth_callback_consumes_native_state_and_rechecks_session_grant():
    script=r'''
import os,frappe
from types import SimpleNamespace
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import sso
from frappe.utils.oauth import create_oauth_state
frappe.conf.dsherp_platform_oauth={'provider':'dsherp','enterprise':'alpha'}
info={'sub':'member@example.invalid','email':'dsherp-reader@example.invalid','site':frappe.local.site,'enterprise':'alpha','binding_version':'v1','enterprise_version':'v1'}
calls=[]
sso.exchange=lambda code:(calls.append(code) or (info,'synthetic-oauth-token'))
try:sso.callback('code','invalid');raise AssertionError('invalid state accepted')
except frappe.PermissionError:pass
assert not calls
frappe.local.session=frappe._dict(user='Guest',data=frappe._dict())
frappe.local.login_manager=SimpleNamespace(login_as=lambda user:frappe.set_user(user))
frappe.local.session_obj=SimpleNamespace(update=lambda force:None)
state=create_oauth_state('/desk/dsherp-agent')
sso.callback('code',state)
assert frappe.session.user==info['email']
assert frappe.response['location']=='/desk/dsherp-agent'
grant=frappe.session.data.dsherp_platform_grant
assert 'synthetic-oauth-token' not in grant
sso.identity_for_token=lambda token:info
assert sso.validate_grant(grant,info['email'])==info
frappe.local.request=SimpleNamespace(path='/api/method/dsherp_bridge.context_api.list_sessions')
sso.validate_session()
try:sso.callback('code',state);raise AssertionError('replayed state accepted')
except frappe.PermissionError:pass
assert calls==['code']
sso.identity_for_token=lambda token:{**info,'binding_version':'v2'}
try:sso.validate_grant(grant,info['email']);raise AssertionError('changed membership remained valid')
except frappe.PermissionError:pass
try:sso.validate_session();raise AssertionError('revoked session remained valid')
except frappe.PermissionError:pass
frappe.local.request.path='/api/method/dsherp_bridge.sso.start'
sso.validate_session()
frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
