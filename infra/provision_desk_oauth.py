"""Configure native OAuth for the local synthetic alpha and beta Sites."""
import json
import subprocess


PYTHON = '/home/frappe/frappe-bench/env/bin/python'
PLATFORM = 'dsherp-validation-platform-backend-1'
PLATFORM_SITE = 'dsherp-platform.localhost'
TARGETS = (
    {
        'enterprise': 'alpha',
        'app_name': 'DSHERP alpha Desk',
        'container': 'dsherp-validation-backend-1',
        'site': 'dsherp-validation.localhost',
        'callback': 'http://localhost:18082/api/method/dsherp_bridge.sso.callback',
        'desk_url': 'http://localhost:18082/api/method/dsherp_bridge.sso.start',
    },
    {
        'enterprise': 'beta',
        'app_name': 'DSHERP beta Desk',
        'container': 'dsherp-validation-beta-backend-1',
        'site': 'dsherp-beta.localhost',
        'callback': 'http://preview.localhost:18085/api/method/dsherp_bridge.sso.callback',
        'desk_url': 'http://preview.localhost:18085/api/method/dsherp_bridge.sso.start',
    },
)


def execute(container, site, body):
    script = (
        "import os,json,frappe\n"
        "os.chdir('/home/frappe/frappe-bench/sites')\n"
        f"frappe.init(site={site!r});frappe.connect();frappe.set_user('Administrator')\n"
        + body
    )
    result = subprocess.run(
        ['docker', 'exec', '-i', container, PYTHON, '-'],
        input=script,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(
            f'Native OAuth configuration failed for {site}; inspect the exact stage, do not regenerate blindly'
        )
    return result.stdout


def main():
    target_data = json.dumps(TARGETS)
    execute(PLATFORM, PLATFORM_SITE, f"""
targets=json.loads({target_data!r})
for target in targets:
    assert not frappe.db.exists('OAuth Client',{{'app_name':target['app_name']}})
frappe.destroy()
""")
    for target in TARGETS:
        execute(target['container'], target['site'], """
assert not frappe.db.exists('Social Login Key','dsherp_platform')
assert not frappe.conf.get('dsherp_platform_oauth')
frappe.destroy()
""")

    credentials = json.loads(execute(PLATFORM, PLATFORM_SITE, f"""
import secrets
targets=json.loads({target_data!r})
credentials={{}}
for target in targets:
    client=frappe.get_doc({{'doctype':'OAuth Client','app_name':target['app_name'],
        'client_secret':secrets.token_urlsafe(32),'scopes':'openid',
        'redirect_uris':target['callback'],'default_redirect_uri':target['callback'],
        'grant_type':'Authorization Code','response_type':'Code','skip_authorization':0,
        'allowed_roles':[{{'role':'DSHERP Member'}}]}}).insert()
    credentials[target['enterprise']]={{'client_id':client.client_id,'client_secret':client.client_secret}}
from frappe.installer import update_site_config
endpoints=frappe.conf.get('dsherp_desk_sites') or {{}}
for target in targets:endpoints[target['site']]=target['desk_url']
update_site_config('dsherp_desk_sites',endpoints)
frappe.db.commit();print(json.dumps(credentials));frappe.destroy()
"""))

    for target in TARGETS:
        execute(target['container'], target['site'], f"""
credentials=json.loads({json.dumps(credentials[target['enterprise']])!r})
target=json.loads({json.dumps(target)!r})
provider=frappe.get_doc({{'doctype':'Social Login Key','provider_name':'DSHERP Platform',
    'social_login_provider':'Custom','enable_social_login':0,'sign_ups':'Deny',
    'client_id':credentials['client_id'],'client_secret':credentials['client_secret'],
    'base_url':'http://dsherp-validation-platform-backend-1:8000',
    'authorize_url':'http://platform.localhost:18083/api/method/frappe.integrations.oauth2.authorize',
    'access_token_url':'http://dsherp-validation-platform-backend-1:8000/api/method/frappe.integrations.oauth2.get_token',
    'api_endpoint':'http://dsherp-validation-platform-backend-1:8000/api/method/dsherp_platform.api.desk_identity',
    'redirect_url':target['callback'],
    'auth_url_data':json.dumps({{'response_type':'code','scope':'openid'}}),
    'user_id_property':'sub'}}).insert()
from frappe.installer import update_site_config
update_site_config('dsherp_platform_oauth',{{'provider':provider.name,
    'enterprise':target['enterprise'],'platform_site':'dsherp-platform.localhost'}})
frappe.db.commit();frappe.clear_cache();frappe.destroy()
""")
    print('Alpha and beta native OAuth configured; custom start endpoints only, no automatic signup.')


if __name__ == '__main__':
    main()
