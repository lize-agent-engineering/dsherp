"""Real native platform sessions and server-owned enterprise bindings."""
import httpx
import subprocess
from contextlib import contextmanager

PLATFORM = 'http://127.0.0.1:18083'

def test_desk_entry_requires_membership_and_all_ready_targets_are_configured():
    with platform_client() as client:
        expected = {
            'alpha': 'http://localhost:18082/api/method/dsherp_bridge.sso.start',
            'beta': 'http://preview.localhost:18085/api/method/dsherp_bridge.sso.start',
            'daily': 'http://daily.localhost:18086/api/method/dsherp_bridge.sso.start',
        }
        for enterprise, url in expected.items():
            response=client.get('/api/method/dsherp_platform.api.desk_entry',params={'enterprise':enterprise})
            assert response.status_code==200,response.text
            assert response.json()['message']=={'url':url}
    with platform_client('outsider') as client:
        assert client.get('/api/method/dsherp_platform.api.desk_entry',params={'enterprise':'alpha'}).status_code==403


def test_platform_login_is_native_and_guest_context_is_denied():
    with httpx.Client(base_url=PLATFORM,headers={'Host':'platform.localhost'},trust_env=False,timeout=15) as client:
        assert client.get('/login').status_code == 200
        assert client.get('/api/method/dsherp_platform.api.context').status_code == 403


@contextmanager
def platform_client(actor='member'):
    import json
    from pathlib import Path
    profiles=json.loads((Path(__file__).resolve().parents[2]/'.runtime/platform-users.json').read_text())
    profile=profiles[actor]
    client=httpx.Client(base_url=PLATFORM,headers={'Host':'platform.localhost'},trust_env=False,timeout=15)
    with client:
        login=client.post('/api/method/login',json={'usr':profile['user'],'pwd':profile['password']})
        assert login.status_code==200
        yield client


def test_native_session_lists_only_enabled_memberships_and_no_credentials():
    with platform_client() as client:
        response=client.get('/api/method/dsherp_platform.api.context')
        assert response.status_code==200
        payload=response.json()['message']
        assert payload['user']=='member@example.invalid'
        assert {x['id'] for x in payload['enterprises']}=={'alpha','beta','daily'}
        assert 'api_key' not in response.text and 'api_secret' not in response.text
        assert 'base_url' not in response.text
    with platform_client('outsider') as client:
        assert client.get('/api/method/dsherp_platform.api.context').json()['message']['enterprises']==[]

def test_desk_oauth_identity_uses_explicit_verified_business_mapping():
    endpoint='/api/method/dsherp_platform.api.desk_identity'
    with platform_client() as client:
        for enterprise,user,site in [('alpha','dsherp-reader@example.invalid','dsherp-validation.localhost'),('beta','beta-reader@example.invalid','dsherp-beta.localhost'),('daily','daily-operator@example.invalid','dsherp-daily.localhost')]:
            response=client.get(endpoint,params={'enterprise':enterprise})
            assert response.status_code==200,response.text
            data=response.json()
            assert data['email']==user and data['site']==site
            assert data['sub']=='member@example.invalid'
            assert data['enterprise']==enterprise and data['binding_version']
            assert 'api_key' not in response.text and 'api_secret' not in response.text
    with platform_client('outsider') as client:
        assert client.get(endpoint,params={'enterprise':'alpha','user':'member@example.invalid'}).status_code==403


def test_membership_read_uses_distinct_business_users_and_sites():
    with platform_client() as client:
        for enterprise,name in [('alpha','DSHERP-TEST-ITEM'),('beta','DSHERP-BETA-ITEM')]:
            response=client.get('/api/method/dsherp_platform.api.read_record',params={'enterprise':enterprise,'doctype':'Item','name':name})
            assert response.status_code==200
            assert response.json()['message']['name']==name
        response=client.get('/api/method/dsherp_platform.api.read_record',params={
            'enterprise':'daily','doctype':'Item','name':'DAILY-AGENT-ITEM'})
        assert response.status_code==200,response.text
        assert response.json()['message']['name']=='DAILY-AGENT-ITEM'
        response=client.get('/api/method/dsherp_platform.api.read_record',params={'enterprise':'beta','doctype':'Item','name':'DSHERP-TEST-ITEM'})
        assert response.status_code!=200


def test_no_member_and_scope_override_are_denied():
    with platform_client('outsider') as client:
        response=client.get('/api/method/dsherp_platform.api.read_record',params={'enterprise':'alpha','doctype':'Item','name':'DSHERP-TEST-ITEM','user':'member@example.invalid'})
        assert response.status_code==403
    with platform_client() as client:
        response=client.get('/api/method/dsherp_platform.api.read_record',params={'enterprise':'alpha','doctype':'User','name':'Administrator'})
        assert response.status_code==403
        response=client.get('/api/resource/DS Membership')
        assert response.status_code==403


@contextmanager
def platform_operator():
    import json
    from pathlib import Path
    p=json.loads((Path(__file__).resolve().parents[2]/'.runtime/platform-users.json').read_text())['operator']
    with httpx.Client(base_url=PLATFORM,headers={'Host':'platform.localhost','Authorization':f"token {p['api_key']}:{p['api_secret']}"},trust_env=False,timeout=15) as client:
        yield client


def test_member_revocation_applies_to_existing_session_and_binding_changes_fail_closed():
    import json
    with platform_client() as member, platform_operator() as operator:
        entries=operator.get('/api/resource/DS Membership',params={'filters':json.dumps({'enterprise':'alpha','platform_user':'member@example.invalid'})}).json()['data']
        path='/api/resource/DS Membership/'+entries[0]['name']
        original=operator.get(path).json()['data']
        params={'enterprise':'alpha','doctype':'Item','name':'DSHERP-TEST-ITEM'}
        try:
            assert operator.put(path,json={'enabled':0}).status_code==200
            assert member.get('/api/method/dsherp_platform.api.read_record',params=params).status_code==403
            assert member.get('/api/method/dsherp_platform.api.desk_identity',params={'enterprise':'alpha'}).status_code==403
            assert 'alpha' not in {x['id'] for x in member.get('/api/method/dsherp_platform.api.context').json()['message']['enterprises']}
            assert operator.put(path,json={'enabled':1,'erp_user':'wrong-user@example.invalid'}).status_code==200
            assert member.get('/api/method/dsherp_platform.api.read_record',params=params).status_code==403
            assert member.get('/api/method/dsherp_platform.api.desk_identity',params={'enterprise':'alpha'}).status_code==403
        finally:
            assert operator.put(path,json={'enabled':original['enabled'],'erp_user':original['erp_user']}).status_code==200


def test_real_field_and_record_permissions_are_preserved_through_platform():
    with platform_client() as client:
        schema=client.get('/api/method/dsherp_platform.api.read_schema',params={'enterprise':'alpha','doctype':'Customer'})
        assert schema.status_code==200
        assert 'custom_dsherp_restricted' not in {x['fieldname'] for x in schema.json()['message']['fields']}
        assert client.get('/api/method/dsherp_platform.api.read_record',params={'enterprise':'alpha','doctype':'Customer','name':'DSHERP-TEST-OTHER-CUSTOMER'}).status_code==403


def test_platform_cookie_does_not_authenticate_to_business_site():
    with platform_client() as member:
        with httpx.Client(base_url='http://127.0.0.1:18082',trust_env=False,timeout=15) as business:
            response=business.get('/api/method/frappe.auth.get_logged_user',headers={'Cookie':'; '.join(f'{c.name}={c.value}' for c in member.cookies.jar)})
            assert response.status_code==403


def test_platform_member_can_open_native_workbench_page():
    with platform_client() as client:
        response = client.get('/api/method/frappe.desk.desk_page.getpage', params={'name': 'dsherp-home'})
        assert response.status_code == 200
        assert response.json()['docs'][0]['name'] == 'dsherp-home'


def test_existing_membership_identity_cannot_be_changed():
    import json
    with platform_operator() as operator:
        entries = operator.get('/api/resource/DS Membership', params={'filters':json.dumps({'enterprise':'alpha','platform_user':'member@example.invalid'})}).json()['data']
        path = '/api/resource/DS Membership/' + entries[0]['name']
        try:
            response = operator.put(path, json={'enterprise':'beta'})
            assert response.status_code != 200
        finally:
            operator.put(path, json={'enterprise':'alpha'})


def test_platform_requires_its_own_cookie_hostname():
    with httpx.Client(base_url=PLATFORM, trust_env=False, timeout=15) as client:
        assert client.get('/login').status_code == 421


def test_retired_platform_realtime_endpoint_stays_closed():
    with platform_client() as client:
        params = {'EIO':4, 'transport':'polling'}
        headers = {'Origin':'http://platform.localhost:18083'}
        assert client.get('/socket.io/', params=params, headers=headers).status_code == 404


def test_platform_supplier_reads_reach_real_policy_and_native_permission_gates():
    script = r'''
import os,uuid,frappe
from contextlib import contextmanager
from unittest.mock import patch
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import api as bridge_api
from dsherp_platform.api import read_record,read_schema

actor='platform-supplier-'+uuid.uuid4().hex+'@example.invalid'
try:
    frappe.set_user('Administrator')
    policy=frappe.get_doc('DS Doctype Policy','Supplier')
    assert {
        'enabled':policy.enabled,
        'allow_read':policy.allow_read,
        'allow_create':policy.allow_create,
        'allow_update':policy.allow_update,
        'allow_submit':policy.allow_submit,
        'allow_cancel':policy.allow_cancel,
        'allow_fill':policy.allow_fill,
        'routes':len(policy.routes),
    }=={
        'enabled':1,'allow_read':1,'allow_create':1,'allow_update':1,
        'allow_submit':0,'allow_cancel':0,'allow_fill':0,'routes':0,
    }
    native=frappe.db.get_value('DocPerm',{'parent':'Supplier','role':'Purchase Master Manager'},'read')
    assert native,'Supplier native read permission fixture is missing'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Platform Supplier read','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Purchase Master Manager'}]}).insert()
    supplier=frappe.get_doc('Supplier','DSHERP 制造测试合成供应商')
    calls=[]

    @contextmanager
    def business(_enterprise):
        def get(path,params=None):
            calls.append(path)
            return getattr(bridge_api,path.rsplit('.',1)[-1])(**params)
        yield None,None,get

    frappe.set_user(actor)
    with patch('dsherp_platform.api._business',business):
        record=read_record('alpha','Supplier',supplier.name)
        schema=read_schema('alpha','Supplier')
        assert record['name']==supplier.name and schema['doctype']=='Supplier'
        assert calls==['/api/method/dsherp_bridge.api.read_record','/api/method/dsherp_bridge.api.read_schema']

        frappe.set_user('Administrator');policy.enabled=0;policy.save();frappe.set_user(actor)
        try:read_schema('alpha','Supplier');raise AssertionError('disabled Supplier policy was allowed')
        except frappe.PermissionError as error:assert 'DS DocType 策略未启用' in str(error),error

        frappe.set_user('Administrator');frappe.delete_doc('DS Doctype Policy','Supplier');frappe.set_user(actor)
        try:read_record('alpha','Supplier',supplier.name);raise AssertionError('missing Supplier policy was allowed')
        except frappe.PermissionError as error:assert '缺少 DS DocType 策略' in str(error),error
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr
