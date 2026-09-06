"""Platform session identity and explicit per-member ERP read delegation."""
import hashlib
import json

import frappe
import requests
from contextlib import contextmanager
from frappe.utils import now_datetime

from dsherp_platform import business_credentials as credential_policy


# What makes a binding what it is. Deliberately not the row's modified time: the credential
# the platform borrows is stored on the same row, and renewing it every twelve hours must not
# read as "the membership changed" and end every session that member has (S9).
BINDING_FIELDS = ('enterprise','platform_user','erp_user','enabled')


def binding_version(member):
    material=json.dumps([str(member.get(field)) for field in BINDING_FIELDS],separators=(',',':'))
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class CredentialStale(frappe.PermissionError):
    """The borrowed business credential is past its window, revoked, or refused.

    Distinguished from a permission decision because the login flow is what repairs it:
    a member arriving through SSO is on their way to handing over a fresh one."""


def _user():
    user = frappe.session.user
    if ('dsherp_platform' not in frappe.get_installed_apps() or user == 'Guest'
            or not frappe.db.get_value('User', user, 'enabled')):
        raise frappe.PermissionError('Platform login required')
    return user


@frappe.whitelist(methods=['GET'])
def context():
    user = _user()
    memberships = frappe.get_all('DS Membership', filters={'platform_user':user,'enabled':1}, pluck='enterprise',order_by='enterprise asc')
    enterprises = []
    for name in memberships:
        enterprise=frappe.get_doc('DS Enterprise',name)
        enterprises.append({'id':enterprise.name,'label':enterprise.title,'status':enterprise.status})
    return {'user':user,'enterprises':enterprises,'platform_admin':'System Manager' in frappe.get_roles(user)}


def _binding(enterprise,fresh=True):
    """The membership and enterprise this member may read through, credential included.

    `fresh=False` is for the one call that exists to replace an expired credential; every
    other path refuses a credential whose window has passed rather than trying it (S2)."""
    user = _user()
    if user == 'Administrator':
        raise frappe.PermissionError('Use an ordinary platform member for business reads')
    name=frappe.db.get_value('DS Membership',{'enterprise':enterprise,'platform_user':user,'enabled':1},'name')
    if not name:
        raise frappe.PermissionError('Active enterprise membership required')
    member=frappe.get_doc('DS Membership',name)
    target=frappe.get_doc('DS Enterprise',enterprise)
    if target.status != 'Ready':
        raise frappe.PermissionError('Enterprise is not ready')
    allowed=frappe.conf.get('dsherp_business_sites')
    if not isinstance(allowed,dict) or allowed.get(target.site) != target.base_url:
        raise frappe.PermissionError('Business endpoint is not configured')
    if not member.erp_user or member.erp_user in ('Guest','Administrator'):
        raise frappe.PermissionError('Ordinary business user binding required')
    if fresh and not credential_policy.usable({'expires_at':member.credential_expires_at},now_datetime()):
        # The platform borrows the credential; it does not own one that never dies.
        raise CredentialStale('企业业务凭据已过期，请重新登录企业')
    return member,target


@contextmanager
def _business(enterprise):
    member,target=_binding(enterprise)
    with requests.Session() as client:
        client.trust_env=False
        client.headers.update({'X-Frappe-Site-Name':target.site,'Authorization':f'token {member.api_key}:{member.get_password("api_secret")}'})
        def get(path,params=None):
            try:
                response=client.get(target.base_url+path,params=params,timeout=10,allow_redirects=False)
            except requests.RequestException:
                frappe.throw('Business service unavailable')
            if response.status_code == 401:
                # Not a permission decision: the borrowed credential is stale or revoked.
                raise CredentialStale('业务凭据已失效，请重新登录企业')
            if response.status_code == 403:
                raise frappe.PermissionError('Business permission denied')
            if response.status_code != 200:
                frappe.throw(f'Business read failed (HTTP {response.status_code})')
            return response.json()['message']
        actual=get('/api/method/frappe.auth.get_logged_user')
        if actual != member.erp_user:
            raise frappe.PermissionError('Bound business identity mismatch')
        # Recheck revocation after the identity round trip; no authorization cache.
        frappe.db.rollback()  # End the read snapshot before checking current membership.
        current,current_target=_binding(enterprise)
        if (binding_version(current) != binding_version(member) or current_target.modified != target.modified):
            raise frappe.PermissionError('Enterprise binding changed; start a new request')
        yield member,target,get


@frappe.whitelist(methods=['POST'])
def accept_credential(enterprise: str,site: str,api_key: str,api_secret: str,expires_at: str,version=1):
    """Take the short-lived pair the business Site issued for this member (S2).

    The member's own platform session authorises it, and the pair is proved against the
    business Site before it is stored: a credential that does not answer as the bound user
    is refused here, not discovered later on somebody's read."""
    member,target=_binding(enterprise,fresh=False)
    if site!=target.site:
        raise frappe.PermissionError('凭据来自另一个业务站')
    expires=credential_policy.moment(expires_at)
    ceiling=credential_policy.expiry(now_datetime())
    if expires is None or expires<=now_datetime() or expires>ceiling:
        raise frappe.PermissionError('凭据有效期不在允许的窗口内')
    with requests.Session() as client:
        client.trust_env=False
        client.headers.update({'X-Frappe-Site-Name':target.site,'Authorization':f'token {api_key}:{api_secret}'})
        try:
            response=client.get(target.base_url+'/api/method/frappe.auth.get_logged_user',timeout=10,allow_redirects=False)
        except requests.RequestException:
            frappe.throw('Business service unavailable')
        if response.status_code!=200 or response.json()['message']!=member.erp_user:
            raise frappe.PermissionError('凭据不属于绑定的业务用户')
    member.api_key=api_key
    member.api_secret=api_secret
    member.credential_issued_at=now_datetime()
    member.credential_expires_at=expires
    member.credential_version=int(version or 1)
    # Which business user this credential was just proved to be. A later binding edit that
    # names somebody else no longer has a proof behind it, and login says so.
    member.credential_erp_user=member.erp_user
    member.save(ignore_permissions=True)
    frappe.db.commit()
    return {'stored':True,'expires_at':str(expires),'version':member.credential_version}


def revoke_credential(member):
    """Ask the business Site to kill the credential this membership lent out.

    A disabled membership already stops platform reads; this closes the other door, the key
    itself. If the Site cannot be reached the credential still dies on its own window, and
    the caller is told rather than left believing the key is gone."""
    target=frappe.db.get_value('DS Enterprise',member.enterprise,['site','base_url'],as_dict=True)
    if not target or not member.api_key:
        return {'revoked':False,'why':'no credential on record'}
    allowed=frappe.conf.get('dsherp_business_sites')
    if not isinstance(allowed,dict) or allowed.get(target.site)!=target.base_url:
        return {'revoked':False,'why':'business endpoint is not configured'}
    try:
        with requests.Session() as client:
            client.trust_env=False
            response=client.post(target.base_url+'/api/method/dsherp_bridge.sso.revoke_credential',
                json={'user':member.erp_user},
                headers={'X-Frappe-Site-Name':target.site,
                         'Authorization':f'token {member.api_key}:{member.get_password("api_secret")}'},
                timeout=10,allow_redirects=False)
    except requests.RequestException:
        return {'revoked':False,'why':'business site unreachable'}
    return {'revoked':response.status_code==200,'status':response.status_code}


@frappe.whitelist(methods=['GET'])
def desk_entry(enterprise: str):
    """Where this member enters the business Desk.

    Deliberately does not require a live borrowed credential: this endpoint is how a member
    reaches the login that renews it, and a stale credential must not be able to block the
    only door out of being stale. It hands back a configured URL, and the business Site
    authenticates whoever arrives there itself."""
    _,target=_binding(enterprise,fresh=False)
    endpoints=frappe.conf.get('dsherp_desk_sites')
    if not isinstance(endpoints,dict) or not isinstance(endpoints.get(target.site),str):
        frappe.throw('该企业 Desk 登录尚未配置')
    return {'url':endpoints[target.site]}


@frappe.whitelist(methods=['GET'])
def desk_identity(enterprise: str):
    """OAuth user info for an explicit enterprise binding, not email inference.

    The round trip proves the bound business identity. When the borrowed credential is the
    thing that has expired, this must not become a login that cannot happen: the login is
    what replaces it. The binding is asserted instead, and the identity is proved moments
    later - `accept_credential` refuses to store a pair that does not answer as `erp_user`,
    and a callback whose delivery fails does not complete. No session is established on an
    unverified mapping either way."""
    try:
        with _business(enterprise) as (member,target,_):
            _identity_response(member,target)
            return
    except CredentialStale:
        pass
    member,target=_binding(enterprise,fresh=False)
    # The fallback stands on the last proof: this credential was shown to answer as this
    # business user. A binding edited to name somebody else has no proof behind it, and is
    # refused until a working credential shows otherwise - which is what the round trip above
    # is for. Losing that check would let a changed erp_user log in on an expired credential.
    if not member.credential_erp_user or member.credential_erp_user!=member.erp_user:
        raise frappe.PermissionError('企业绑定的业务用户已变化，需要有效凭据重新验证')
    _identity_response(member,target)


def _identity_response(member,target):
    frappe.response.update({'sub':frappe.session.user,'email':member.erp_user,
        'enterprise':target.name,'site':target.site,'binding_version':binding_version(member),
        'enterprise_version':str(target.modified)})


@frappe.whitelist(methods=['GET'])
def desk_membership(enterprise: str):
    # An established business grant already verified the credential binding.
    # Reauthorization checks its immutable versions without calling back into
    # the waiting business request and exhausting that Site's worker pool.
    member,target=_binding(enterprise)
    _identity_response(member,target)


def _read(enterprise,doctype,method,name=None,query=None):
    with _business(enterprise) as (_,__,get):
        params={'doctype':doctype}
        if name is not None:params['name']=name
        if query is not None:params['query']=query
        return get('/api/method/dsherp_bridge.api.'+method,params)


@frappe.whitelist(methods=['GET'])
def read_record(enterprise: str,doctype: str,name: str):
    return _read(enterprise,doctype,'read_record',name)


@frappe.whitelist(methods=['GET'])
def read_schema(enterprise: str,doctype: str):
    return _read(enterprise,doctype,'read_schema')
