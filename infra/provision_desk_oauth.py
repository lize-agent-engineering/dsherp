"""Configure native OAuth for local synthetic alpha; secrets stay in process memory."""
import json
import subprocess

PYTHON='/home/frappe/frappe-bench/env/bin/python'
CALLBACK='http://localhost:18082/api/method/dsherp_bridge.sso.callback'


def execute(container,site,body):
    script="import os,json,frappe\nos.chdir('/home/frappe/frappe-bench/sites')\nfrappe.init(site="+repr(site)+");frappe.connect();frappe.set_user('Administrator')\n"+body
    result=subprocess.run(['docker','exec','-i',container,PYTHON,'-'],input=script,text=True,capture_output=True,timeout=45)
    if result.returncode:raise RuntimeError('Native OAuth configuration failed in '+container+'; inspect the exact stage, do not regenerate blindly')
    return result.stdout


def main():
    platform='dsherp-validation-platform-backend-1';business='dsherp-validation-backend-1'
    execute(platform,'dsherp-platform.localhost',"assert not frappe.db.exists('OAuth Client',{'app_name':'DSHERP alpha Desk'})\nfrappe.destroy()")
    execute(business,'dsherp-validation.localhost',"assert not frappe.db.exists('Social Login Key','dsherp_platform')\nassert not frappe.conf.get('dsherp_platform_oauth')\nfrappe.destroy()")
    credentials=json.loads(execute(platform,'dsherp-platform.localhost',f"""
import secrets
doc=frappe.get_doc({{'doctype':'OAuth Client','app_name':'DSHERP alpha Desk','client_secret':secrets.token_urlsafe(32),'scopes':'openid','redirect_uris':{CALLBACK!r},'default_redirect_uri':{CALLBACK!r},'grant_type':'Authorization Code','response_type':'Code','skip_authorization':0}}).insert()
from frappe.installer import update_site_config
endpoints=frappe.conf.get('dsherp_desk_sites') or {{}}
endpoints['dsherp-validation.localhost']='http://localhost:18082/api/method/dsherp_bridge.sso.start'
update_site_config('dsherp_desk_sites',endpoints)
frappe.db.commit()
print(json.dumps({{'client_id':doc.client_id,'client_secret':doc.client_secret}}))
frappe.destroy()
"""))
    print('Platform native OAuth Client created; configuring alpha Social Login Key.')
    execute(business,'dsherp-validation.localhost',f"""
credentials=json.loads({json.dumps(credentials)!r})
doc=frappe.get_doc({{'doctype':'Social Login Key','provider_name':'DSHERP Platform','social_login_provider':'Custom','enable_social_login':0,'sign_ups':'Deny','client_id':credentials['client_id'],'client_secret':credentials['client_secret'],'base_url':'http://dsherp-validation-platform-backend-1:8000','authorize_url':'http://platform.localhost:18083/api/method/frappe.integrations.oauth2.authorize','access_token_url':'http://dsherp-validation-platform-backend-1:8000/api/method/frappe.integrations.oauth2.get_token','api_endpoint':'http://dsherp-validation-platform-backend-1:8000/api/method/dsherp_platform.api.desk_identity','redirect_url':{CALLBACK!r},'auth_url_data':json.dumps({{'response_type':'code','scope':'openid'}}),'user_id_property':'sub'}}).insert()
from frappe.installer import update_site_config
update_site_config('dsherp_platform_oauth',{{'provider':doc.name,'enterprise':'alpha','platform_site':'dsherp-platform.localhost'}})
frappe.db.commit();frappe.clear_cache();frappe.destroy()
""")
    print('Alpha native OAuth configuration saved; custom start endpoint only, no automatic signup.')


if __name__=='__main__':main()
