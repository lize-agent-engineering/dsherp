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
    return hashlib.sha256(_json([revision(user,doctypes=('DocType','Custom Field','Workflow','Workflow State','Workflow Action Master')),identity]).encode()).hexdigest()


def workflow_masters(package):
    states=sorted({row['state'] for workflow in package['workflows'] for row in workflow['states']})
    actions=sorted({row['action'] for workflow in package['workflows'] for row in workflow['transitions']})
    return [('Workflow State','workflow_state_name',name) for name in states]+[('Workflow Action Master','workflow_action_name',name) for name in actions]


def authorize(package):
    _user()
    if package['doctypes']:frappe.has_permission('DocType','create',throw=True)
    if package['extensions']:frappe.has_permission('Custom Field','create',throw=True)
    if package['workflows']:frappe.has_permission('Workflow','create',throw=True)
    for doctype,field,name in workflow_masters(package):
        if frappe.db.exists(doctype,name):frappe.get_doc(doctype,name).check_permission('read')
        else:frappe.has_permission(doctype,'create',throw=True)
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
    for doctype,field,name in workflow_masters(package):
        facts[doctype+':'+name]=frappe.get_doc(doctype,name).as_dict() if frappe.db.exists(doctype,name) else None
    return hashlib.sha256(_json(facts).encode()).hexdigest()


@frappe.whitelist(methods=['POST'])
def propose_bundle(session_id,package,model_run=None):
    return _propose_bundle(session_id,package,model_run=model_run)


def _propose_bundle(session_id,package,model_run=None,origin=None,source_transfer=None):
    user=_user();_conversation(session_id)
    source_run=None
    if model_run:
        source_run=_origin(model_run,session_id,user)
        if source_run.status!='Running':raise frappe.PermissionError('配置提案来源运行尚未运行或已结束')
    if isinstance(package,str):package=json.loads(package)
    try:frozen=freeze_bundle(package)
    except ValueError as error:frappe.throw(str(error))
    package=frozen['package'];authorize(package)
    baseline=inspect_baseline(package)
    if source_run:
        from dsherp_bridge import grants
        grant=grants.of(source_run.name)
    else:
        grant=frappe.session.data.get('dsherp_platform_grant')
    payload={'site':frappe.local.site,'actor':user,'package':package,
        'authorization_revision':_authorization_revision(user,grant),'baseline':baseline}
    if model_run:payload['model_run']=model_run
    if origin:payload['origin']=origin
    digest=hashlib.sha256(_json(payload).encode()).hexdigest()
    doc=frappe.get_doc({'doctype':'DS Configuration Bundle','conversation':session_id,'source_transfer':source_transfer,
        'payload':_json(payload),'digest':digest,'baseline':baseline}).insert(ignore_permissions=True)
    return get_bundle(doc.name)


def _origin(model_run,conversation,user):
    run=frappe.get_doc('DS Model Run',model_run)
    if run.owner!=user or run.conversation!=conversation or run.domain!='configuration':
        raise frappe.PermissionError('配置提案来源运行归属或领域不匹配')
    return run


@frappe.whitelist(methods=['GET'])
def get_bundle(bundle_id):
    user=_user();doc=frappe.get_doc('DS Configuration Bundle',bundle_id)
    if doc.owner!=user:raise frappe.PermissionError('无权读取此配置包')
    _conversation(doc.conversation)
    payload=json.loads(doc.payload)
    if payload['site']!=frappe.local.site or payload['actor']!=user:raise frappe.PermissionError('配置包身份不匹配')
    authorize(payload['package'])
    origin=_origin(payload['model_run'],doc.conversation,user) if payload.get('model_run') else None
    from dsherp_bridge.configuration_execution import _changes
    transfer=None
    transfer_id=frappe.db.get_value('DS Configuration Transfer',{'bundle':doc.name,'owner':user},'name',order_by='creation desc')
    if transfer_id:
        peer=frappe.conf.get('dsherp_configuration_preview') or {};public=peer.get('public_url','').rstrip('/')
        transfer={'id':transfer_id,'preview_url':public+'/desk/dsherp-configuration-preview/'+transfer_id}
    return {'id':doc.name,'digest':doc.digest,'baseline':doc.baseline,'site':payload['site'],'package':payload['package'],
        'model_run':payload.get('model_run'),
        'execution_ready':not origin or origin.status=='Succeeded','changes':_changes(payload['package']),
        'preview_available':bool(frappe.conf.get('dsherp_preview')),
        'preview_transfer_available':bool(frappe.conf.get('dsherp_configuration_preview')),'transfer':transfer}


def check_authorization(bundle_id,grant=None):
    result=get_bundle(bundle_id)
    payload=json.loads(frappe.get_doc('DS Configuration Bundle',bundle_id).payload)
    grant=grant or frappe.session.data.get('dsherp_platform_grant')
    if payload['authorization_revision']!=_authorization_revision(_user(),grant):
        frappe.throw('配置权限或成员关系已变化，请重新提出配置')
    if payload.get('origin'):
        from dsherp_bridge.configuration_transfer import check_origin
        check_origin(payload['origin'],payload['package'])
    return result


def check_bundle(bundle_id,digest):
    """Read-only revalidation; the caller must hold native configuration locks."""
    result=check_authorization(bundle_id)
    if not isinstance(digest,str) or digest!=result['digest']:frappe.throw('配置包确认摘要不匹配')
    if inspect_baseline(result['package'])!=result['baseline']:
        frappe.throw('原生配置基线已变化，请重新预览并确认')
    return result
