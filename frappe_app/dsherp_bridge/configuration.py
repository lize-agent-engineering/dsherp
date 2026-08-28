"""Business-Site configuration proposals, separate from DDL execution."""
import hashlib
import json
import frappe
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.context_api import _conversation,_user,_json
from dsherp_bridge.context_permissions import revision


def _authorization_revision(user,grant):
    identity=None
    if grant:
        from dsherp_bridge.sso import validate_grant
        identity=validate_grant(grant,user)
    return hashlib.sha256(_json([revision(user,doctypes=('DocType','Custom Field','Workflow')),identity]).encode()).hexdigest()


def authorize(package):
    _user()
    if package['doctypes']:frappe.has_permission('DocType','create',throw=True)
    if package['extensions']:frappe.has_permission('Custom Field','create',throw=True)
    if package['workflows']:frappe.has_permission('Workflow','create',throw=True)
    for extension in package['extensions']:
        frappe.get_doc('DocType',extension['doctype']).check_permission('read')


def inspect_baseline(package):
    """Capture native configuration facts, never business records."""
    facts={}
    for spec in package['doctypes']:
        name=spec['name']
        if frappe.db.exists('DocType',name):frappe.throw('新应用DocType已存在：'+name)
        if not frappe.db.exists('Module Def',spec['module']):frappe.throw('原生模块不存在')
        facts[name]={'doctype':None}
    new_types={spec['name']:spec for spec in package['doctypes']}
    for spec in package['extensions']:
        name=spec['doctype'];doc=frappe.get_doc('DocType',name)
        meta=frappe.get_meta(name)
        for field in spec['fields']:
            if meta.has_field(field['fieldname']):frappe.throw('扩展字段已存在：'+field['fieldname'])
            if field.get('insert_after') and not meta.has_field(field['insert_after']):frappe.throw('布局基线字段不存在')
        facts[name]={'doctype':doc.as_dict(),
            'custom_fields':frappe.get_all('Custom Field',filters={'dt':name},fields=['*'],order_by='name'),
            'property_setters':frappe.get_all('Property Setter',filters={'doc_type':name},fields=['*'],order_by='name')}
    for spec in package['doctypes']+package['extensions']:
        for field in spec['fields']:
            if field['fieldtype'] in ('Link','Table'):
                target=field['options']
                if target in new_types:is_table=new_types[target].get('istable',0)
                else:
                    if not frappe.db.exists('DocType',target):frappe.throw('关联DocType不存在：'+target)
                    native=frappe.get_doc('DocType',target);native.check_permission('read');is_table=native.istable
                    facts['link:'+target]={'modified':str(native.modified),'istable':is_table}
                if (field['fieldtype']=='Table')!=bool(is_table):frappe.throw('关联类型与原生子表定义不一致')
        for permission in spec.get('permissions',[]):
            if not frappe.db.exists('Role',permission['role']):frappe.throw('原生角色不存在')
    for workflow in package['workflows']:
        if frappe.db.exists('Workflow',workflow['workflow_name']) or frappe.db.exists('Workflow',{'document_type':workflow['document_type']}):
            frappe.throw('不能覆盖既有工作流')
        for role in [state['allow_edit'] for state in workflow['states']]+[transition['allowed'] for transition in workflow['transitions']]:
            if not frappe.db.exists('Role',role):frappe.throw('原生工作流角色不存在')
        facts['workflow:'+workflow['workflow_name']]=None
    return hashlib.sha256(_json(facts).encode()).hexdigest()


@frappe.whitelist(methods=['POST'])
def propose_bundle(session_id,package):
    user=_user();_conversation(session_id)
    if isinstance(package,str):package=json.loads(package)
    try:frozen=freeze_bundle(package)
    except ValueError as error:frappe.throw(str(error))
    package=frozen['package'];authorize(package)
    baseline=inspect_baseline(package)
    grant=frappe.session.data.get('dsherp_platform_grant')
    payload={'site':frappe.local.site,'actor':user,'package':package,
        'authorization_revision':_authorization_revision(user,grant),'baseline':baseline}
    digest=hashlib.sha256(_json(payload).encode()).hexdigest()
    doc=frappe.get_doc({'doctype':'DS Configuration Bundle','conversation':session_id,
        'payload':_json(payload),'digest':digest,'baseline':baseline}).insert(ignore_permissions=True)
    return get_bundle(doc.name)


@frappe.whitelist(methods=['GET'])
def get_bundle(bundle_id):
    user=_user();doc=frappe.get_doc('DS Configuration Bundle',bundle_id)
    if doc.owner!=user:raise frappe.PermissionError('无权读取此配置包')
    _conversation(doc.conversation)
    payload=json.loads(doc.payload)
    if payload['site']!=frappe.local.site or payload['actor']!=user:raise frappe.PermissionError('配置包身份不匹配')
    authorize(payload['package'])
    return {'id':doc.name,'digest':doc.digest,'baseline':doc.baseline,'site':payload['site'],'package':payload['package']}


def check_bundle(bundle_id,digest):
    """Read-only revalidation; the caller must hold native configuration locks."""
    result=get_bundle(bundle_id)
    if not isinstance(digest,str) or digest!=result['digest']:frappe.throw('配置包确认摘要不匹配')
    payload=json.loads(frappe.get_doc('DS Configuration Bundle',bundle_id).payload)
    grant=frappe.session.data.get('dsherp_platform_grant')
    if payload['authorization_revision']!=_authorization_revision(_user(),grant):
        frappe.throw('配置权限或成员关系已变化，请重新提出配置')
    if inspect_baseline(result['package'])!=result['baseline']:
        frappe.throw('原生配置基线已变化，请重新预览并确认')
    return result
