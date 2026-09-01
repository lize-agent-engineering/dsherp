"""Explicit configuration confirmations, separate from immutable packages."""
import hashlib
import json
from zoneinfo import ZoneInfo
import frappe
from frappe.exceptions import QueryDeadlockError
from frappe.utils import add_to_date,now_datetime,get_system_timezone
from dsherp_bridge.configuration import check_bundle,get_bundle,check_authorization,workflow_masters
from dsherp_bridge.configuration_bundle import freeze_bundle
from dsherp_bridge.context_api import _user,_json
from dsherp_bridge.configuration_locks import acquire,release
from dsherp_bridge.configuration_transfer import read_receipt


@frappe.whitelist(methods=['POST'])
def prepare_preview(bundle_id,digest):
    _user()
    if not frappe.conf.get('dsherp_preview'):frappe.throw('预览应用必须在隔离预览站点执行')
    bundle=check_bundle(bundle_id,digest)
    payload={'purpose':'preview','target':frappe.local.site,'bundle_digest':bundle['digest'],
        'package_digest':freeze_bundle(bundle['package'])['digest'],'baseline':bundle['baseline'],
        'documents':_documents(bundle['package'])}
    confirmation=frappe.get_doc({'doctype':'DS Configuration Confirmation','bundle':bundle_id,
        'payload':_json(payload),'digest':hashlib.sha256(_json(payload).encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=30),'status':'Pending'}).insert(ignore_permissions=True)
    return get_confirmation(confirmation.name)


@frappe.whitelist(methods=['POST'])
def prepare_publish(transfer_id,digest):
    user=_user()
    if frappe.conf.get('dsherp_preview'):frappe.throw('发布确认必须在目标业务站点创建')
    transfer=frappe.get_doc('DS Configuration Transfer',transfer_id)
    if transfer.owner!=user:raise frappe.PermissionError('无权读取此配置交接')
    bundle=check_bundle(transfer.bundle,digest)
    if not bundle['execution_ready']:frappe.throw('来源运行尚未成功完成，不能发布')
    receipt=read_receipt(transfer)
    payload={'purpose':'publish','target':frappe.local.site,'bundle_digest':bundle['digest'],
        'package_digest':freeze_bundle(bundle['package'])['digest'],'baseline':bundle['baseline'],
        'documents':_documents(bundle['package']),'transfer_id':transfer.name,'preview_receipt':receipt}
    confirmation=frappe.get_doc({'doctype':'DS Configuration Confirmation','bundle':transfer.bundle,
        'payload':_json(payload),'digest':hashlib.sha256(_json(payload).encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=30),'status':'Pending'}).insert(ignore_permissions=True)
    return get_confirmation(confirmation.name)


def _changes(package):
    changes=[]
    for spec in package['doctypes']+package['extensions']:
        detail='；'.join(f"{field['label']} / {field['fieldname']} / {field['fieldtype']}"+
                (f" / {field['options']}" if field.get('options') else '')+
                (' / 必填' if field.get('reqd') else ' / 选填')+
                (' / 列表显示' if field.get('in_list_view') else '')+
                (f" / 位于{field['insert_after']}之后" if field.get('insert_after') else '') for field in spec['fields'])
        if 'name' in spec:
            detail+=f"；模块：{spec['module']}；原生随机编号；可提交：{bool(spec.get('is_submittable'))}；子表：{bool(spec.get('istable'))}"
            for permission in spec['permissions']:
                detail+='；权限：'+permission['role']+' / '+','.join(key for key,value in permission.items() if key!='role' and value)
        changes.append({'object':spec.get('name') or spec['doctype'],
            'action':'新增 DocType' if 'name' in spec else '新增字段',
            'detail':detail})
    for workflow in package['workflows']:
        state_detail='；'.join(f"{row['state']} / {['草稿','已提交','已取消'][int(row['doc_status'])]} / 可编辑角色：{row['allow_edit']}" for row in workflow['states'])
        changes.append({'object':workflow['workflow_name'],'action':'新应用工作流',
            'detail':'启用工作流；邮件提醒关闭；'+state_detail+'；'+
            '；'.join(f"{row['state']} → {row['action']} → {row['next_state']} ({row['allowed']}) / 申请人自行审批："+
                ('允许' if row.get('allow_self_approval') else '不允许') for row in workflow['transitions'])})
    return changes


@frappe.whitelist(methods=['GET'])
def get_confirmation(proposal_id):
    user=_user();doc=frappe.get_doc('DS Configuration Confirmation',proposal_id)
    if doc.owner!=user:raise frappe.PermissionError('无权读取此配置确认')
    bundle=get_bundle(doc.bundle);payload=json.loads(doc.payload)
    if payload['target']!=frappe.local.site:raise frappe.PermissionError('配置确认目标不匹配')
    result={'id':doc.name,'bundle_id':doc.bundle,'digest':doc.digest,'purpose':payload['purpose'],'target':payload['target'],
        'baseline':payload['baseline'],'status':doc.status,'execution_ready':bundle['execution_ready'],
        'expires_at':doc.expires_at.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(),
        'changes':bundle['changes']}
    execution=frappe.db.get_value('DS Configuration Execution',{'confirmation':doc.name},'name')
    if execution:result['execution']=_result(frappe.get_doc('DS Configuration Execution',execution))
    return result


def _documents(package):
    pending={spec['name']:spec for spec in package['doctypes']};documents=[]
    for workflow in package['workflows']:
        spec=pending[workflow['document_type']]
        field=next((field for field in spec['fields'] if field['fieldname']=='workflow_state'),None)
        if not field or field['fieldtype']!='Link' or field.get('options')!='Workflow State':
            frappe.throw('请在新应用中显式提供workflow_state关联字段，避免隐式创建字段')
        if any(state['doc_status']!='0' for state in workflow['states']) and not spec.get('is_submittable'):
            frappe.throw('提交或取消工作流要求新应用启用原生可提交设置')
    for doctype,field,name in workflow_masters(package):
        if not frappe.db.exists(doctype,name):documents.append({'doctype':doctype,'name':name,field:name})
    while pending:
        ready=[name for name,spec in pending.items() if not any(field['fieldtype'] in ('Link','Table') and field.get('options') in pending for field in spec['fields'])]
        if not ready:frappe.throw('新配置关联存在循环，当前原生创建顺序无法应用')
        for name in ready:
            spec=pending.pop(name)
            documents.append({'doctype':'DocType',**spec,'custom':1,'autoname':'hash'})
    for spec in package['extensions']:
        for field in spec['fields']:documents.append({'doctype':'Custom Field','dt':spec['doctype'],**field})
    for workflow in package['workflows']:
        documents.append({'doctype':'Workflow','name':workflow['workflow_name'],**workflow,
            'is_active':1,'send_email_alert':0,'workflow_state_field':'workflow_state'})
    return documents


def _matches(actual,expected):
    if isinstance(expected,dict):return all(_matches(actual.get(key),value) for key,value in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and len(actual)>=len(expected) and all(_matches(row,wanted) for row,wanted in zip(actual,expected))
    return actual==expected


def _document_name(expected):
    return expected['dt']+'-'+expected['fieldname'] if expected['doctype']=='Custom Field' else expected['name']


def _step_id(expected):
    return _json([expected['doctype'],_document_name(expected)])


def _result(execution):
    steps=json.loads(execution.steps)
    payload=json.loads(frappe.db.get_value('DS Configuration Confirmation',execution.confirmation,'payload'))
    documents=payload['documents']
    if len(steps)>len(documents):frappe.throw('配置执行步骤与确认内容不一致')
    for index,step in enumerate(steps):
        expected_id=_step_id(documents[index])
        if step.get('step_id') not in (None,expected_id):frappe.throw('配置执行步骤标识与确认内容不一致')
        step['step_id']=expected_id
    return {'execution_id':execution.name,'status':'Unknown' if execution.status=='Running' else execution.status,
        'steps':steps,'error':execution.error or ('执行已开始，尚未核实；不会重复执行' if execution.status=='Running' else None)}


@frappe.whitelist(methods=['GET'])
def verify_execution(proposal_id):
    public=get_confirmation(proposal_id)
    confirmation=frappe.get_doc('DS Configuration Confirmation',proposal_id)
    execution_id=frappe.db.get_value('DS Configuration Execution',{'confirmation':proposal_id},'name')
    if not execution_id:
        return {'execution':None,'observations':[],'note':'尚无配置执行记录；不会据配置列表猜测或自动执行。'}
    execution=frappe.get_doc('DS Configuration Execution',execution_id)
    steps=json.loads(execution.steps);expected=json.loads(confirmation.payload)['documents'];observations=[]
    for index,wanted in enumerate(expected):
        step=steps[index] if index<len(steps) else {}
        doctype=step.get('doctype') or wanted['doctype']
        name=step.get('name') or _document_name(wanted)
        observation={'object':step.get('object') or wanted.get('name') or wanted.get('dt','')+'.'+wanted.get('fieldname',''),'doctype':doctype,'name':name}
        if not name or not frappe.db.exists(doctype,name):
            observation['state']='Missing'
        else:
            actual=frappe.get_doc(doctype,name);actual.check_permission('read')
            observation.update(state='Matches' if _matches(actual.as_dict(),wanted) else 'Differs',version=str(actual.modified))
        observations.append(observation)
    return {'execution':public.get('execution') or _result(execution),'observations':observations,
        'note':'只读核实当前原生配置；一致也不能单独证明某次响应成功，不会重试或改写执行记录。'}


@frappe.whitelist(methods=['POST'])
def confirm_preview(proposal_id,digest,request_id):
    return _confirm(proposal_id,digest,request_id,'preview')


@frappe.whitelist(methods=['POST'])
def confirm_publish(proposal_id,digest,request_id):
    return _confirm(proposal_id,digest,request_id,'publish')


def _confirm(proposal_id,digest,request_id,purpose):
    _user()
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:frappe.throw('请求标识无效')
    frappe.db.rollback()
    try:
        confirmation=frappe.get_doc('DS Configuration Confirmation',proposal_id,for_update=True)
        public=get_confirmation(proposal_id)
        if digest!=confirmation.digest:frappe.throw('配置确认摘要不匹配')
        existing=frappe.db.get_value('DS Configuration Execution',{'confirmation':proposal_id},'name',for_update=True)
    except QueryDeadlockError:
        # MariaDB 11 can reject the waiter's stale locked-row snapshot after the
        # winner commits.  Observe the winner's durable intent; never execute
        # the configuration a second time.
        frappe.db.rollback()
        public=get_confirmation(proposal_id)
        if digest!=public['digest']:frappe.throw('配置确认摘要不匹配')
        existing=frappe.db.get_value('DS Configuration Execution',{'confirmation':proposal_id},'name')
        if not existing:raise
        return public['execution']
    if existing:return _result(frappe.get_doc('DS Configuration Execution',existing,for_update=True))
    if not public['execution_ready']:frappe.throw('来源运行尚未成功完成，不能应用此配置')
    if confirmation.status!='Pending' or confirmation.expires_at<=now_datetime():frappe.throw('配置确认已结束或过期')
    if purpose=='preview' and not all(frappe.conf.get(key) for key in ('dsherp_preview','mute_emails','disable_scheduler','pause_scheduler')):
        frappe.throw('隔离预览设置不完整，停止应用')
    payload=json.loads(confirmation.payload)
    if payload['purpose']!=purpose or payload['target']!=frappe.local.site:frappe.throw('不是当前站点和用途的配置确认')
    transfer=None
    if purpose=='publish':
        if frappe.conf.get('dsherp_preview'):frappe.throw('隔离预览站点不能执行目标发布')
        transfer=frappe.get_doc('DS Configuration Transfer',payload['transfer_id'])
        if transfer.owner!=frappe.session.user or read_receipt(transfer)!=payload['preview_receipt']:
            frappe.throw('隔离预览回执已变化，请重新确认发布')
    package=get_bundle(confirmation.bundle)['package']
    targets=[doc['name'] for doc in package['doctypes']]+[doc['doctype'] for doc in package['extensions']]
    targets += [field['options'] for doc in package['doctypes']+package['extensions'] for field in doc['fields'] if field['fieldtype'] in ('Link','Table')]
    if package['workflows']:targets+=['Workflow State','Workflow Action Master']
    try:
        acquire(targets)
        check_bundle(confirmation.bundle,payload['bundle_digest'])
        documents=payload['documents']
        if documents!=_documents(package):frappe.throw('配置执行内容已变化，请重新确认')
        execution=frappe.get_doc({'doctype':'DS Configuration Execution','confirmation':proposal_id,
            'request_id':request_id,'status':'Running','steps':'[]'}).insert(ignore_permissions=True)
        confirmation.status='Running';confirmation.save(ignore_permissions=True)
        frappe.db.commit()  # Durable intent, before native DDL can commit itself.
        steps=[]
        for expected in documents:
            step={'step_id':_step_id(expected),'object':expected.get('name') or expected['dt']+'.'+expected['fieldname'],'status':'Running'}
            steps.append(step);execution.steps=_json(steps);execution.save(ignore_permissions=True);frappe.db.commit()
            started=False
            try:
                check_authorization(confirmation.bundle)
                if transfer and read_receipt(transfer)!=payload['preview_receipt']:
                    frappe.throw('隔离预览结果已变化，停止发布')
                if expected['doctype']=='Workflow':
                    if frappe.db.count(expected['document_type']):frappe.throw('新应用已有业务记录，停止工作流应用以避免隐式回填')
                    if frappe.db.exists('Workflow',{'document_type':expected['document_type']}):frappe.throw('工作流基线已变化，不能覆盖')
                started=True
                native=frappe.get_doc(json.loads(_json(expected))).insert()
                saved=frappe.get_doc(native.doctype,native.name);saved.check_permission('read')
                if not _matches(saved.as_dict(),expected):frappe.throw('原生配置回读与确认内容不一致')
                step.update(status='Succeeded',doctype=saved.doctype,name=saved.name,version=str(saved.modified))
                execution.steps=_json(steps);execution.save(ignore_permissions=True);frappe.db.commit()
            except Exception as error:
                frappe.db.rollback()
                step['status']='Unknown' if started else 'Failed'
                step['error_type']=type(error).__name__
                execution=frappe.get_doc('DS Configuration Execution',execution.name,for_update=True)
                execution.status='Partial' if any(row['status']=='Succeeded' for row in steps) else step['status']
                execution.error='配置执行已停止，请核实逐项结果；DDL不保证回滚，不会整包重跑'
                execution.steps=_json(steps);execution.save(ignore_permissions=True)
                confirmation=frappe.get_doc('DS Configuration Confirmation',proposal_id,for_update=True)
                confirmation.status=execution.status;confirmation.save(ignore_permissions=True);frappe.db.commit()
                return _result(execution)
        execution.status='Succeeded';execution.save(ignore_permissions=True)
        confirmation=frappe.get_doc('DS Configuration Confirmation',proposal_id,for_update=True)
        confirmation.status='Succeeded';confirmation.save(ignore_permissions=True);frappe.db.commit()
        return _result(execution)
    finally:release()
