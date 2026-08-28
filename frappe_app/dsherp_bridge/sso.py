"""Business Desk OAuth adapter. Membership identity is supplied by the platform."""
import frappe
import json
import requests
from frappe.utils.password import encrypt,decrypt
from frappe.utils.oauth import get_oauth2_flow,get_oauth2_providers,get_redirect_uri,get_oauth2_authorize_url,consume_oauth_state


def configuration():
    config=frappe.conf.get('dsherp_platform_oauth')
    if not isinstance(config,dict) or not config.get('provider') or not config.get('enterprise'):
        raise frappe.PermissionError('平台登录尚未配置')
    return config


def validate_identity(info):
    config=configuration()
    keys=('sub','email','site','enterprise','binding_version','enterprise_version')
    if not isinstance(info,dict) or any(not isinstance(info.get(key),str) or not info[key] for key in keys):
        raise frappe.PermissionError('平台身份响应无效')
    if info['site']!=frappe.local.site or info['enterprise']!=config['enterprise']:
        raise frappe.PermissionError('平台身份不属于当前企业')
    user=info['email']
    if user in ('Guest','Administrator'):
        raise frappe.PermissionError('需要明确绑定的普通业务用户')
    record=frappe.db.get_value('User',user,['enabled','user_type'],as_dict=True)
    if not record or not record.enabled or record.user_type!='System User':
        raise frappe.PermissionError('绑定的业务用户未开通或已停用')
    return user


def identity_for_token(token):
    config=configuration()
    provider=get_oauth2_providers()[config['provider']]
    with requests.Session() as client:
        client.trust_env=False
        response=client.get(provider['api_endpoint'],params={'enterprise':config['enterprise']},
            headers={'Authorization':'Bearer '+token},timeout=15,allow_redirects=False)
        if response.status_code!=200:raise frappe.PermissionError('平台登录授权已失效')
        info=response.json()
    validate_identity(info)
    return info


def exchange(code):
    provider=configuration()['provider']
    flow=get_oauth2_flow(provider)
    with flow.get_auth_session(data={'code':code,'redirect_uri':get_redirect_uri(provider),'grant_type':'authorization_code'},
            decoder=lambda body:json.loads(body.decode()),timeout=15,allow_redirects=False) as session:
        token=session.access_token
        if not isinstance(token,str) or not token:raise frappe.PermissionError('平台授权码交换失败')
        return identity_for_token(token),token


@frappe.whitelist(allow_guest=True,methods=['GET'])
def start():
    frappe.response.update(type='redirect',location=get_oauth2_authorize_url(configuration()['provider'],'/app'))


@frappe.whitelist(allow_guest=True,methods=['GET'])
def callback(code: str,state: str):
    if consume_oauth_state(state)!='/app':raise frappe.PermissionError('登录请求已失效，请重新进入企业')
    info,token=exchange(code)
    user=validate_identity(info)
    frappe.local.login_manager.login_as(user)
    frappe.session.data.dsherp_platform_grant=encrypt(json.dumps({'identity':info,'token':token}))
    frappe.local.session_obj.update(force=True)
    frappe.db.commit()
    frappe.response.update(type='redirect',location='/app')


def validate_grant(grant,user):
    data=json.loads(decrypt(grant))
    info=identity_for_token(data['token'])
    if validate_identity(info)!=user or info!=data['identity']:
        raise frappe.PermissionError('企业成员绑定已变化，请重新登录')
    return info


def validate_session():
    grant=frappe.session.data.get('dsherp_platform_grant')
    if not grant:return
    if frappe.request.path in ('/api/method/dsherp_bridge.sso.start','/api/method/dsherp_bridge.sso.callback','/api/method/logout'):
        return
    validate_grant(grant,frappe.session.user)
