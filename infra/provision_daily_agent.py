"""Prepare daily Site OAuth and its permissionless on-demand Runtime identity."""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / '.runtime' / 'context-worker-daily.json'
PYTHON = '/home/frappe/frappe-bench/env/bin/python'
PLATFORM = 'dsherp-validation-platform-backend-1'
BUSINESS = 'dsherp-validation-backend-1'
SITE = 'dsherp-daily.localhost'
ENTERPRISE = 'daily'
CALLBACK = 'http://daily.localhost:18086/api/method/dsherp_bridge.sso.callback'


def execute(command, site, body):
    script = "import os,json,frappe\nos.chdir('/home/frappe/frappe-bench/sites')\nfrappe.init(site=" + repr(site) + ");frappe.connect();frappe.set_user('Administrator')\n" + body
    result = subprocess.run([*command, PYTHON, '-'], input=script, text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError(f'Daily Agent provisioning failed in {command[-1]}; inspect state instead of retrying')
    return result.stdout


def main():
    if PROFILE.exists():
        raise RuntimeError('Daily Runtime profile exists; inspect instead of rotating credentials')
    execute(['docker', 'exec', '-i', BUSINESS], SITE, """
assert not frappe.db.exists('Social Login Key','dsherp_platform')
assert not frappe.conf.get('dsherp_platform_oauth')
assert not frappe.conf.get('dsherp_runtime_user')
assert not frappe.db.exists('User','dsherp-context-runtime@example.invalid')
frappe.destroy()
""")
    execute(['docker', 'exec', '-i', PLATFORM], 'dsherp-platform.localhost', """
assert not frappe.db.exists('DS Enterprise','daily')
assert not frappe.db.exists('OAuth Client',{'app_name':'DSHERP daily Desk'})
frappe.destroy()
""")
    credentials = json.loads(execute(['docker', 'exec', '-i', PLATFORM], 'dsherp-platform.localhost', f"""
import secrets
enterprise=frappe.get_doc({{'doctype':'DS Enterprise','enterprise_id':'daily','title':'日常企业（待初始化）','site':{SITE!r},'base_url':'http://backend:8000','status':'Provisioning'}}).insert()
client=frappe.get_doc({{'doctype':'OAuth Client','app_name':'DSHERP daily Desk','client_secret':secrets.token_urlsafe(32),'scopes':'openid','redirect_uris':{CALLBACK!r},'default_redirect_uri':{CALLBACK!r},'grant_type':'Authorization Code','response_type':'Code','skip_authorization':0}}).insert()
from frappe.installer import update_site_config
sites=frappe.conf.get('dsherp_business_sites') or {{}};sites[{SITE!r}]='http://backend:8000';update_site_config('dsherp_business_sites',sites)
desks=frappe.conf.get('dsherp_desk_sites') or {{}};desks[{SITE!r}]='http://daily.localhost:18086/api/method/dsherp_bridge.sso.start';update_site_config('dsherp_desk_sites',desks)
frappe.db.commit();print(json.dumps({{'client_id':client.client_id,'client_secret':client.client_secret}}));frappe.destroy()
"""))
    runtime = json.loads(execute(['docker', 'exec', '-i', BUSINESS], SITE, f"""
credentials=json.loads({json.dumps(credentials)!r})
provider=frappe.get_doc({{'doctype':'Social Login Key','provider_name':'DSHERP Platform','social_login_provider':'Custom','enable_social_login':0,'sign_ups':'Deny','client_id':credentials['client_id'],'client_secret':credentials['client_secret'],'base_url':'http://dsherp-validation-platform-backend-1:8000','authorize_url':'http://platform.localhost:18083/api/method/frappe.integrations.oauth2.authorize','access_token_url':'http://dsherp-validation-platform-backend-1:8000/api/method/frappe.integrations.oauth2.get_token','api_endpoint':'http://dsherp-validation-platform-backend-1:8000/api/method/dsherp_platform.api.desk_identity','redirect_url':{CALLBACK!r},'auth_url_data':json.dumps({{'response_type':'code','scope':'openid'}}),'user_id_property':'sub'}}).insert()
from frappe.core.doctype.user.user import generate_keys
runtime_user='dsherp-context-runtime@example.invalid'
frappe.get_doc({{'doctype':'User','email':runtime_user,'first_name':'DSHERP Context Runtime','enabled':1,'user_type':'System User','send_welcome_email':0,'roles':[]}}).insert()
keys=generate_keys(runtime_user)
frappe.set_user(runtime_user)
assert not frappe.has_permission('Item','read') and not frappe.has_permission('Customer','read')
frappe.set_user('Administrator')
from frappe.installer import update_site_config
update_site_config('dsherp_platform_oauth',{{'provider':provider.name,'enterprise':'daily','platform_site':'dsherp-platform.localhost'}})
update_site_config('dsherp_runtime_user',runtime_user)
frappe.db.commit();print(json.dumps({{'base_url':'http://127.0.0.1:18081','business_url':'http://dsherp-validation-backend-1:8000','site':{SITE!r},**keys}}));frappe.destroy()
"""))
    fd = os.open(PROFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(runtime, stream)
    print('Daily OAuth and permissionless on-demand Runtime identity prepared; no member or company was created.')


if __name__ == '__main__':
    main()
