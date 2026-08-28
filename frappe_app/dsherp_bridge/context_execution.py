"""Run-scoped read capabilities. The model cannot select its Site or actor."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import uuid
import re

import frappe
from frappe.utils import now_datetime,add_to_date,get_datetime
from dsherp_bridge import api as erp
from dsherp_bridge import context_api as conversations
from dsherp_bridge import context_permissions

TOOLS={'erp_read_schema':(erp.read_schema,{'doctype'}),
       'erp_read_record':(erp.read_record,{'doctype','name'}),
       'erp_search_records':(erp.search_records,{'doctype','query'})}


@contextmanager
def _actor(run):
    original=frappe.session.user
    try:
        frappe.set_user(run.owner)
        conversations._user()
        identity=None
        if run.get('platform_grant'):
            from dsherp_bridge.sso import validate_grant
            identity=validate_grant(run.platform_grant,run.owner)
        yield identity
    finally:
        frappe.set_user(original)


def authorize_sources(sources):
    for source in sources:
        args=source['arguments'];doctype=args['doctype']
        schema={f['fieldname']:f for f in erp.read_schema(doctype)['fields']}
        visible=set(schema)
        if set(source['fields'])-visible:
            raise frappe.PermissionError('历史结果的字段权限已改变')
        for table,columns in source.get('child_fields',{}).items():
            readable={field['fieldname'] for field in schema.get(table,{}).get('fields',[])}|{'name','idx'}
            if table not in visible or set(columns)-readable:
                raise frappe.PermissionError('历史结果的明细字段权限已改变')
        for name in source['records']:
            frappe.get_doc(doctype,name).check_permission('read')


def _run(run_id,capability):
    run=frappe.get_doc('DS Model Run',run_id,for_update=True)
    if (run.status not in ('Running','Cancelling') or not run.capability_hash
        or not isinstance(capability,str) or get_datetime(run.expires_at)<=now_datetime()
        or not hmac.compare_digest(run.capability_hash,hashlib.sha256(capability.encode()).hexdigest())):
        raise frappe.PermissionError('运行凭据失效')
    return run


@frappe.whitelist(methods=['POST'])
def claim_run(runtime_revision):
    user=conversations._user()
    if user!=frappe.conf.get('dsherp_runtime_user'):
        raise frappe.PermissionError('需要站点指定的运行服务身份')
    if not isinstance(runtime_revision,str) or not re.fullmatch('[a-f0-9]{64}',runtime_revision):
        frappe.throw('运行配置摘要无效')
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now_datetime()]},pluck='name'):
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':'运行已过期，未自动重试','capability_hash':''})
    if frappe.db.exists('DS Model Run',{'status':['in',['Running','Cancelling']]}):return None
    names=frappe.get_all('DS Model Run',filters={'status':'Queued'},pluck='name',order_by='creation asc',limit_page_length=1)
    if not names:return None
    run=frappe.get_doc('DS Model Run',names[0],for_update=True)
    if run.status!='Queued':return None
    try:
        with _actor(run) as identity:
            conversation=conversations._conversation(run.conversation)
            conversations._public(conversation)
            permission_revision=context_permissions.revision(run.owner)
    except (frappe.PermissionError,frappe.DoesNotExistError):
        frappe.db.set_value('DS Model Run',run.name,{'status':'Failed','error':'当前用户已无法读取会话来源','capability_hash':''})
        return None
    capability=secrets.token_urlsafe(32)
    domain=run.domain
    if domain not in ('query','operation'):frappe.throw('未知业务领域')
    combined_revision=hashlib.sha256((permission_revision+runtime_revision+domain).encode()).hexdigest()
    if identity:
        combined_revision=hashlib.sha256((combined_revision+conversations._json(identity)).encode()).hexdigest()
    if conversation.runtime_revision!=combined_revision:
        conversation.runtime_session=uuid.uuid4().hex
        frappe.db.set_value('DS Conversation',conversation.name,{'runtime_session':conversation.runtime_session,'runtime_revision':combined_revision})
    frappe.db.set_value('DS Model Run',run.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),seconds=180),'permission_revision':permission_revision,'runtime_revision':runtime_revision})
    return {'run_id':run.name,'session_id':run.conversation,'native_session_id':conversation.runtime_session,
            'permission_revision':permission_revision,
            'runtime_revision':runtime_revision,'domain':domain,
            'scope_id':hashlib.sha256(json.dumps([frappe.local.site,run.owner,run.conversation,domain,conversation.runtime_session],separators=(',',':')).encode()).hexdigest(),
            'question':run.question,'context':json.loads(run.page_context),'capability':capability}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_status(run_id,capability):
    run=_run(run_id,capability)
    if run.status=='Running':
        with _actor(run):
            context_permissions.require_revision(run)
            conversations._public(conversations._conversation(run.conversation))
    return {'run_id':run.name,'status':run.status}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def reserve_model_call(run_id,capability,input_bytes,max_output_tokens,provider,model,purpose,runtime_revision,domain='query'):
    run=_run(run_id,capability)
    if run.status!='Running':raise frappe.PermissionError('运行正在取消')
    if runtime_revision!=run.runtime_revision:raise frappe.PermissionError('模型配置与领取的运行不一致')
    if domain!=run.domain:raise frappe.PermissionError('模型领域与领取的运行不一致')
    if (provider!='deepseek-official' or model!='deepseek-v4-flash'
        or purpose not in ('conversation','compaction','session-title')
        or type(input_bytes) is not int or not 0<input_bytes<=131072
        or type(max_output_tokens) is not int or not 0<max_output_tokens<=(3072 if run.domain=='operation' else 2048)):
        frappe.throw('模型请求配置或输入预算不符')
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._public(conversations._conversation(run.conversation))
    calls=run.model_calls or 0
    total_input=(run.model_input_bytes or 0)+input_bytes
    total_output=(run.model_output_tokens_reserved or 0)+max_output_tokens
    if calls>=8 or total_input>524288 or total_output>16384:
        frappe.throw('本轮模型调用预算已用尽')
    # Reserve before provider dispatch; uncertain/failed calls are not refunded.
    frappe.db.set_value('DS Model Run',run.name,{'model_calls':calls+1,
        'model_input_bytes':total_input,'model_output_tokens_reserved':total_output})
    return {'allowed':True}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    run=_run(run_id,capability)
    if run.status!='Running':raise frappe.PermissionError('运行正在取消')
    if tool in ('erp_propose_update','erp_propose_create','erp_propose_action'):
        if run.domain!='operation':raise frappe.PermissionError('当前领域不能提出业务操作')
        if isinstance(arguments,str):arguments=json.loads(arguments)
        keys=({'doctype','name','action','version'} if tool=='erp_propose_action'
              else {'doctype','values','version'}|({'name'} if tool=='erp_propose_update' else set()))
        if (not isinstance(arguments,dict) or set(arguments)!=keys
            or not all(isinstance(arguments[key],str) for key in keys-{'values'})
            or ('values' in keys and not isinstance(arguments['values'],dict))):
            frappe.throw('操作提案参数无效')
        with _actor(run):
            context_permissions.require_revision(run)
            sources=json.loads(run.sources or '[]')
            authorize_sources(sources)
            if tool in ('erp_propose_update','erp_propose_action'):
                if not any(source['tool']=='erp_read_record' and source['arguments']=={'doctype':arguments['doctype'],'name':arguments['name']}
                           and source.get('record_versions',{}).get(arguments['name'])==arguments['version'] for source in sources):
                    frappe.throw('请先读取确切目标及当前版本，再提出操作')
                if tool=='erp_propose_action':
                    from dsherp_bridge.operations import propose_action as propose
                else:
                    from dsherp_bridge.operations import propose_update as propose
            else:
                if not any(source['tool']=='erp_read_schema' and source['arguments']=={'doctype':arguments['doctype']}
                           and source.get('schema_version')==arguments['version'] for source in sources):
                    frappe.throw('请先读取当前业务结构，再提出创建操作')
                from dsherp_bridge.operations import propose_create as propose
            return propose(run.conversation,**arguments,grant=run.platform_grant,model_run=run.name)
    if tool not in TOOLS:frappe.throw('未知工具')
    if isinstance(arguments,str):arguments=json.loads(arguments)
    if tool=='erp_search_records' and isinstance(arguments,dict):arguments={'query':'',**arguments}
    function,keys=TOOLS[tool]
    if not isinstance(arguments,dict) or set(arguments)!=keys or not all(isinstance(v,str) for v in arguments.values()):
        frappe.throw('工具参数无效')
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._context(run.page_context,check_version=False)
        result=function(**arguments)
        fields=[];records=[]
        if tool=='erp_read_schema':fields=[f['fieldname'] for f in result['fields']]
        elif tool=='erp_read_record':
            fields=[key for key in result['fields'] if frappe.get_meta(arguments['doctype']).get_field(key)]
            records=[result['name']]
        else:
            records=[r['name'] for r in result]
            title=frappe.get_meta(arguments['doctype']).title_field
            visible={f['fieldname'] for f in erp.read_schema(arguments['doctype'])['fields']}
            if title in visible:fields=[title]
        source={'tool':tool,'arguments':arguments,'fields':fields,'records':records}
        if tool=='erp_read_schema':
            source['child_fields']={field['fieldname']:[child['fieldname'] for child in field['fields']] for field in result['fields'] if 'fields' in field}
        elif tool=='erp_read_record':
            source['child_fields']={field:sorted({column for row in rows for column in row}) for field,rows in result['fields'].items() if isinstance(rows,list)}
        if tool=='erp_read_schema':
            source['schema_version']=str(result['modified'])
        elif tool=='erp_read_record':
            source['record_versions']={result['name']:str(result['modified'])}
        else:
            source['record_versions']={r['name']:str(r['modified']) for r in result}
        authorize_sources([source])
    sources=json.loads(run.sources or '[]');sources.append(source)
    frappe.db.set_value('DS Model Run',run.name,'sources',conversations._json(sources))
    return result


@frappe.whitelist(allow_guest=True,methods=['POST'])
def finish_run(run_id,capability,status,answer='',error=''):
    run=_run(run_id,capability)
    if status not in ('Succeeded','Failed','Cancelled'):frappe.throw('无效运行结束状态')
    if status=='Succeeded':
        if run.status!='Running':raise frappe.PermissionError('取消中的运行不能成功完成')
        if not isinstance(answer,str) or not answer.strip() or not json.loads(run.sources or '[]'):
            frappe.throw('成功结果必须包含实际读取及回答')
        with _actor(run):
            context_permissions.require_revision(run)
            conversations._public(conversations._conversation(run.conversation))
    if status=='Cancelled' and run.status!='Cancelling':frappe.throw('运行未请求取消')
    frappe.db.set_value('DS Model Run',run.name,{'status':status,'answer':answer if status=='Succeeded' else '',
        'error':error if status=='Failed' else '', 'capability_hash':''})
    return {'run_id':run.name,'status':status}
