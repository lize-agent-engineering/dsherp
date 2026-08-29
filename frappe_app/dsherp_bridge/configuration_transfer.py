"""Data-only handoff to one configured preview Site; no business credentials."""
import hashlib
import json
from urllib.parse import urlsplit
import requests
import frappe
from dsherp_bridge.context_api import _user,_json
from dsherp_bridge.configuration import check_bundle,check_authorization,get_bundle,_propose_bundle
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.configuration_transport import seal,open_envelope


def _peer(direction):
    peer=frappe.conf.get('dsherp_configuration_'+direction)
    if not isinstance(peer,dict) or not all(isinstance(peer.get(key),str) and peer[key] for key in ('site','url','secret')):
        frappe.throw('尚未配置可信隔离预览交接')
    url=urlsplit(peer['url'])
    if peer['site']==frappe.local.site or url.scheme not in ('http','https') or not url.netloc or url.username or url.password or url.query or url.fragment:
        frappe.throw('配置交接站点绑定无效')
    return peer


def _decode(envelope,peer,purpose):
    if isinstance(envelope,str):envelope=json.loads(envelope)
    try:return open_envelope(envelope,peer['secret'],purpose)
    except ValueError:raise frappe.PermissionError('配置交接签名或时效无效')


def _request(peer,purpose,payload):
    with requests.Session() as client:
        client.trust_env=False
        response=client.post(peer['url'].rstrip('/')+'/api/method/dsherp_bridge.configuration_transfer.'+purpose+'_transfer',
            headers={'X-Frappe-Site-Name':peer['site']},json={'envelope':seal(payload,peer['secret'],purpose+'-request')},
            timeout=15,allow_redirects=False)
    if response.status_code!=200:raise frappe.PermissionError('配置交接源站未授权或不可用')
    body=response.json()
    if 'message' not in body:frappe.throw('配置交接响应不完整')
    return _decode(body['message'],peer,purpose+'-response')


@frappe.whitelist(methods=['POST'])
def prepare_transfer(bundle_id,digest,request_id):
    user=_user();peer=_peer('preview')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:frappe.throw('请求标识无效')
    request_key=hashlib.sha256((user+'\0'+request_id).encode()).hexdigest()
    frappe.db.rollback();frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    bundle=check_bundle(bundle_id,digest)
    if not bundle['execution_ready']:frappe.throw('来源运行尚未成功完成，不能交接配置')
    public=peer.get('public_url');url=urlsplit(public or '')
    if url.scheme not in ('http','https') or not url.netloc or url.username or url.password or url.query or url.fragment:
        frappe.throw('隔离预览入口配置无效')
    payload={'source_site':frappe.local.site,'preview_site':peer['site'],'actor':user,
        'bundle_digest':bundle['digest'],'package_digest':freeze_bundle(bundle['package'])['digest']}
    existing=frappe.db.get_value('DS Configuration Transfer',{'request_id':request_key},'name',for_update=True)
    if existing:
        transfer=frappe.get_doc('DS Configuration Transfer',existing)
        if transfer.owner!=user or transfer.bundle!=bundle_id:frappe.throw('请求标识已用于其他配置交接')
        return {'id':transfer.name,'preview_url':public.rstrip('/')+'/app/dsherp-configuration-preview/'+transfer.name}
    transfer=frappe.get_doc({'doctype':'DS Configuration Transfer','request_id':request_key,'bundle':bundle_id,'payload':_json(payload),
        'platform_grant':frappe.session.data.get('dsherp_platform_grant')}).insert(ignore_permissions=True)
    return {'id':transfer.name,'preview_url':public.rstrip('/')+'/app/dsherp-configuration-preview/'+transfer.name}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def export_transfer(envelope):
    peer=_peer('preview');request=_decode(envelope,peer,'export-request')
    keys={'transfer_id','actor','source_site','preview_site'}
    if not isinstance(request,dict) or set(request)!=keys or not all(isinstance(value,str) for value in request.values()):
        raise frappe.PermissionError('配置交接请求无效')
    transfer=frappe.get_doc('DS Configuration Transfer',request['transfer_id'])
    payload=json.loads(transfer.payload)
    if (request['source_site']!=frappe.local.site or request['preview_site']!=peer['site']
        or request['actor']!=transfer.owner or any(request[key]!=payload[key] for key in keys-{'transfer_id'})):
        raise frappe.PermissionError('配置交接身份或站点不匹配')
    original=frappe.session.user
    try:
        frappe.set_user(transfer.owner)
        bundle=check_authorization(transfer.bundle,grant=transfer.platform_grant)
        if not bundle['execution_ready']:frappe.throw('来源运行尚未成功完成')
        if bundle['digest']!=payload['bundle_digest']:frappe.throw('配置交接内容已变化')
        return seal({**payload,'transfer_id':transfer.name,'package':bundle['package']},peer['secret'],'export-response')
    finally:frappe.set_user(original)


def _source(transfer_id):
    user=_user();peer=_peer('source')
    if not isinstance(transfer_id,str) or not 1<=len(transfer_id)<=140:frappe.throw('配置交接标识无效')
    request={'transfer_id':transfer_id,'source_site':peer['site'],'preview_site':frappe.local.site,'actor':user}
    artifact=_request(peer,'export',request)
    if (not isinstance(artifact,dict) or set(artifact)!=set(request)|{'bundle_digest','package_digest','package'}
        or any(artifact[key]!=value for key,value in request.items())):
        raise frappe.PermissionError('配置交接内容不属于当前用户或站点')
    try:frozen=freeze_bundle(artifact['package'])
    except ValueError as error:frappe.throw(str(error))
    if frozen['digest']!=artifact['package_digest']:frappe.throw('配置交接包摘要不匹配')
    return artifact


@frappe.whitelist(methods=['POST'])
def accept_transfer(transfer_id):
    user=_user()
    if not frappe.conf.get('dsherp_preview'):frappe.throw('配置交接只能在隔离预览站点接收')
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    artifact=_source(transfer_id)
    identity=hashlib.sha256(_json([artifact['source_site'],transfer_id]).encode()).hexdigest()
    existing=frappe.db.get_value('DS Configuration Bundle',{'source_transfer':identity},'name')
    if existing:
        bundle=get_bundle(existing)
        return {'session_id':frappe.db.get_value('DS Configuration Bundle',existing,'conversation'),'bundle':bundle}
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'隔离配置预览'}).insert(ignore_permissions=True)
    origin={key:artifact[key] for key in ('source_site','preview_site','actor','transfer_id','bundle_digest','package_digest')}
    bundle=_propose_bundle(conversation.name,artifact['package'],origin=origin,source_transfer=identity)
    return {'session_id':conversation.name,'bundle':bundle}


def check_origin(origin,package):
    artifact=_source(origin['transfer_id'])
    if any(artifact[key]!=value for key,value in origin.items()) or artifact['package_digest']!=freeze_bundle(package)['digest']:
        raise frappe.PermissionError('配置来源绑定已变化')
