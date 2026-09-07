"""Business Desk OAuth adapter. Membership identity is supplied by the platform."""
import frappe
import hashlib
import hmac
import json
import requests
from frappe.utils.password import encrypt,decrypt
from dsherp_bridge import credentials
from frappe.utils.oauth import get_oauth2_flow,get_oauth2_providers,get_redirect_uri,get_oauth2_authorize_url,consume_oauth_state
from urllib.parse import urlsplit,urlunsplit


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


def identity_for_token(token,verify_business=False):
    config=configuration()
    provider=get_oauth2_providers()[config['provider']]
    endpoint=provider['api_endpoint']
    if not verify_business:
        parts=urlsplit(endpoint)
        endpoint=urlunsplit((parts.scheme,parts.netloc,'/api/method/dsherp_platform.api.desk_membership','',''))
    with requests.Session() as client:
        client.trust_env=False
        response=client.get(endpoint,params={'enterprise':config['enterprise']},
            headers={'Authorization':'Bearer '+token,'X-Frappe-Site-Name':config['platform_site']},timeout=15,allow_redirects=False)
        if response.status_code!=200:raise frappe.PermissionError('平台登录授权已失效')
        info=response.json()
    validate_identity(info)
    return info


def exchange(code):
    try:
        return _exchange(code)
    except (requests.RequestException,ValueError,KeyError):
        pass
    # Raise outside the handler: no chained provider frames containing credentials.
    raise frappe.PermissionError('平台授权码交换失败，请重新登录')


def _exchange(code):
    provider=configuration()['provider']
    flow=get_oauth2_flow(provider)
    with flow.get_auth_session(data={'code':code,'redirect_uri':get_redirect_uri(provider),'grant_type':'authorization_code'},
            headers={'X-Frappe-Site-Name':configuration()['platform_site']},
            decoder=lambda body:json.loads(body.decode()),timeout=15,allow_redirects=False) as session:
        token=session.access_token
        if not isinstance(token,str) or not token:raise frappe.PermissionError('平台授权码交换失败')
        return identity_for_token(token,verify_business=True),token


@frappe.whitelist(allow_guest=True,methods=['GET'])
def start():
    frappe.response.update(type='redirect',location=get_oauth2_authorize_url(configuration()['provider'],'/desk/dsherp-agent'))


@frappe.whitelist(allow_guest=True,methods=['GET'])
def callback(code: str,state: str):
    if consume_oauth_state(state)!='/desk/dsherp-agent':raise frappe.PermissionError('登录请求已失效，请重新进入企业')
    info,token=exchange(code)
    user=validate_identity(info)
    # The platform identity round trip updates this User's session metadata on
    # the business Site. End the read snapshot before native login_as updates it.
    frappe.db.rollback()
    frappe.local.login_manager.login_as(user)
    frappe.session.data.dsherp_platform_grant=encrypt(json.dumps({'identity':info,'token':token}))
    frappe.local.session_obj.update(force=True)
    # The platform borrows a credential with an end, and gets it on every login: this Site
    # renews it only when it is near its window, so a second login never kills the first.
    pair=credentials.current(user,issued_for=info['enterprise'])
    frappe.db.commit()
    deliver_credential(info,token,pair)
    frappe.response.update(type='redirect',location='/desk/dsherp-agent')


def deliver_credential(info,token,pair):
    """Hand the platform the pair it must use, over the member's own platform token.

    A failure here is not cosmetic: without a live credential the platform cannot read this
    Site for that member, so it is reported instead of leaving a login that half works."""
    config=configuration()
    provider=get_oauth2_providers().get(config['provider'])
    if not provider or not provider.get('api_endpoint'):
        raise frappe.PermissionError('平台登录配置不完整，无法交付业务凭据')
    parts=urlsplit(provider['api_endpoint'])
    endpoint=urlunsplit((parts.scheme,parts.netloc,'/api/method/dsherp_platform.api.accept_credential','',''))
    try:
        with requests.Session() as client:
            client.trust_env=False
            response=client.post(endpoint,json={'enterprise':config['enterprise'],'site':frappe.local.site,
                'api_key':pair['api_key'],'api_secret':pair['api_secret'],
                'expires_at':pair['expires_at'],'version':pair['version']},
                headers={'Authorization':'Bearer '+token,'X-Frappe-Site-Name':config['platform_site']},
                timeout=15,allow_redirects=False)
    except requests.RequestException:
        raise frappe.PermissionError('企业平台暂时不可达，业务凭据未能交付，请稍后重新登录')
    if response.status_code!=200:
        raise frappe.PermissionError('企业平台拒绝了本次业务凭据交付，请联系企业管理员')


# The business interface; neither Administrator nor the runtime identity may hold one.
DESK_PREFIXES=('/app','/desk')
EXEMPT_PATHS=('/api/method/dsherp_bridge.sso.start','/api/method/dsherp_bridge.sso.callback','/api/method/logout')
# How long the last successful platform answer may stand in for an unreachable
# platform. Revocation itself never waits on this: a reachable platform is asked every time.
GRANT_CACHE_SECONDS=60


def _password_login_disabled():
    # Frappe's own switch is the primary control: it refuses inside LoginManager, before
    # a session exists, and it hides the password form. This hook only re-states it.
    return bool(frappe.get_system_settings('disable_user_pass_login'))


def sso_enforced():
    """Whether every business session on this Site must come through the platform.

    Production provisioning sets `dsherp_sso_required` in site_config and disables password
    login in System Settings. The two used to be one condition: an operator re-enabling
    password login for a day would silently have switched off the grant requirement on
    background executors and the credential window on API keys as well. The site_config
    key is the deliberate switch; the System Settings switch still counts on its own."""
    return bool(frappe.conf.get('dsherp_sso_required')) or _password_login_disabled()


def _machine_authenticated():
    """True only when the Authorization header carries the session user's own API key.

    A prefix check is not authentication: Frappe silently ignores a malformed token and
    keeps the cookie session, so a grant-less browser session could add
    `Authorization: token x` and walk past the grant requirement.
    """
    import base64
    header=frappe.get_request_header('Authorization') or ''
    scheme,_,rest=header.partition(' ')
    scheme=scheme.lower();rest=rest.strip()
    if scheme=='basic':
        try:rest=base64.b64decode(rest).decode()
        except (ValueError,UnicodeDecodeError):return False
    elif scheme!='token':return False
    key,_,secret=rest.partition(':')
    if not key or not secret:return False
    user=frappe.session.user
    stored_key=frappe.db.get_value('User',user,'api_key')
    if not stored_key or not hmac.compare_digest(stored_key,key):return False
    from frappe.utils.password import get_decrypted_password
    stored_secret=get_decrypted_password('User',user,'api_secret',raise_exception=False) or ''
    return bool(stored_secret) and hmac.compare_digest(stored_secret,secret)


@frappe.whitelist(methods=['POST'])
def revoke_credential(user=None):
    """Kill the caller's own business credential (S2).

    The platform calls this with the very credential it is giving up, so the request
    authenticates as the business user whose key is about to die. No caller may revoke
    somebody else's: a revocation is the last act of a credential, not a weapon."""
    session_user=frappe.session.user
    if user and user!=session_user:
        raise frappe.PermissionError('只能吊销自身的业务凭据')
    if session_user in _service_identities():
        raise frappe.PermissionError('运维身份的凭据由轮换命令管理')
    outcome=credentials.revoke(session_user,reason='platform revoked')
    frappe.db.commit()
    return outcome


def _service_identities():
    return {'Administrator',frappe.conf.get('dsherp_runtime_user')}


def _grant_cache_key(user,identity):
    material=[user,identity['sub'],identity['binding_version'],identity['enterprise_version']]
    return 'dsherp_grant:'+hashlib.sha256(json.dumps(material,separators=(',',':')).encode()).hexdigest()


def validate_grant(grant,user):
    data=json.loads(decrypt(grant))
    key=_grant_cache_key(user,data['identity'])
    # The platform is asked on every request, so a revoked membership stops on the
    # very next one. The cache is only a fallback for the moments the platform cannot
    # answer: a decision younger than GRANT_CACHE_SECONDS still stands, an older one
    # is a refusal, never a silent pass.
    try:
        info=identity_for_token(data['token'])
    except requests.RequestException:
        if frappe.cache().get_value(key):return data['identity']
        raise frappe.PermissionError('企业平台暂时不可达，请稍后重试')
    if validate_identity(info)!=user or info!=data['identity']:
        frappe.cache().delete_value(key)
        raise frappe.PermissionError('企业成员绑定已变化，请重新登录')
    frappe.cache().set_value(key,1,expires_in_sec=GRANT_CACHE_SECONDS)
    return info


def validate_session():
    if getattr(frappe.local,'request',None) is None:return
    path=frappe.request.path
    if path=='/api/method/login' and sso_enforced():
        raise frappe.PermissionError('本站只接受企业平台登录')
    if path in EXEMPT_PATHS:return
    user=frappe.session.user
    if user=='Guest':return
    if user in _service_identities():
        # They exist for provisioning and for the runtime, not for using the product.
        if not _machine_authenticated() and path.startswith(DESK_PREFIXES):
            raise frappe.PermissionError('运维身份不得建立业务界面会话')
        return
    grant=frappe.session.data.get('dsherp_platform_grant')
    if grant:
        validate_grant(grant,user)
        return
    # A browser session without a platform grant is exactly what SSO enforcement forbids.
    if _machine_authenticated():
        # An API key is only as good as the window it was issued in; past that, the holder
        # goes back through the platform for a new one (S2). The window is a rule about
        # credentials the platform borrows, which exist only where every session comes from
        # the platform: a Site that still allows password login (development) records the
        # windows but does not refuse on them, the same line _actor draws for grants.
        if sso_enforced():
            credentials.require(user)
        return
    raise frappe.PermissionError('需要通过企业平台登录后再访问')
