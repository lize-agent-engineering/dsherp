"""Platform session identity and explicit per-member ERP read delegation."""
import frappe
import requests
from contextlib import contextmanager


def _user():
    user = frappe.session.user
    if ('dsherp_platform' not in frappe.get_installed_apps() or user == 'Guest'
            or not frappe.db.get_value('User', user, 'enabled')):
        raise frappe.PermissionError('Platform login required')
    return user


@frappe.whitelist(methods=['GET'])
def context():
    user = _user()
    memberships = frappe.get_all('DS Membership', filters={'platform_user':user,'enabled':1}, pluck='enterprise')
    enterprises = []
    for name in memberships:
        enterprise=frappe.get_doc('DS Enterprise',name)
        enterprises.append({'id':enterprise.name,'label':enterprise.title,'status':enterprise.status})
    return {'user':user,'enterprises':enterprises,'platform_admin':'System Manager' in frappe.get_roles(user)}


def _binding(enterprise):
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
            if response.status_code in (401,403):
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
        if (current.modified != member.modified or current_target.modified != target.modified):
            raise frappe.PermissionError('Enterprise binding changed; start a new request')
        yield member,target,get


@frappe.whitelist(methods=['GET'])
def desk_entry(enterprise: str):
    with _business(enterprise) as (_,target,__):
        endpoints=frappe.conf.get('dsherp_desk_sites')
        if not isinstance(endpoints,dict) or not isinstance(endpoints.get(target.site),str):
            frappe.throw('该企业 Desk 登录尚未配置')
        return {'url':endpoints[target.site]}


@frappe.whitelist(methods=['GET'])
def desk_identity(enterprise: str):
    """OAuth user info for an explicit enterprise binding, not email inference."""
    with _business(enterprise) as (member,target,_):
        _identity_response(member,target)


def _identity_response(member,target):
    frappe.response.update({'sub':frappe.session.user,'email':member.erp_user,
        'enterprise':target.name,'site':target.site,'binding_version':str(member.modified),
        'enterprise_version':str(target.modified)})


@frappe.whitelist(methods=['GET'])
def desk_membership(enterprise: str):
    # An established business grant already verified the credential binding.
    # Reauthorization checks its immutable versions without calling back into
    # the waiting business request and exhausting that Site's worker pool.
    member,target=_binding(enterprise)
    _identity_response(member,target)


def _read(enterprise,doctype,method,name=None,query=None):
    if doctype not in ('Item','Customer'):
        raise frappe.PermissionError('Unsupported business object')
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
