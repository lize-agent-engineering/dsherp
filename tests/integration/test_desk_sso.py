"""Native business identity validation before OAuth may establish a session."""
import subprocess


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
state=create_oauth_state('/app')
sso.callback('code',state)
assert frappe.session.user==info['email']
assert frappe.response['location']=='/app'
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
