"""Run-scoped read capabilities. The model cannot select its Site or actor."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import time
import uuid
import re

import frappe
from frappe.utils import now_datetime,add_to_date,get_datetime
from dsherp_bridge import api as erp
from dsherp_bridge import context_api as conversations
from dsherp_bridge import context_events as events
from dsherp_bridge import context_permissions

TOOLS={'erp_read_schema':(erp.read_schema,{'doctype'}),
       'erp_read_record':(erp.read_record,{'doctype','name'}),
       'erp_search_records':(erp.search_records,{'doctype','query','filters','fields'})}
MAX_OPERATION_MODEL_CALLS=10
MAX_OPERATION_OUTPUT_TOKENS_RESERVED=30720


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
        if source['tool']=='erp_read_configuration':
            from dsherp_bridge.configuration_tools import authorize_source
            authorize_source(source)
            continue
        args=source['arguments'];doctype=args['doctype']
        schema={f['fieldname']:f for f in erp.read_schema(doctype)['fields']}
        visible=set(schema)|{'name','modified'}
        if frappe.get_meta(doctype).is_submittable:
            visible.add('docstatus')
        if set(source['fields'])-visible:
            raise frappe.PermissionError('历史结果的字段权限已改变')
        # Sources written before match_fields existed retain their prior fields-based
        # authorization. Every new search source carries the key, including [].
        if 'match_fields' in source:
            match_fields=source['match_fields']
            if (not isinstance(match_fields,list)
                or any(not isinstance(field,str) for field in match_fields)
                or set(match_fields)-visible):
                raise frappe.PermissionError('历史结果的匹配字段权限已改变')
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
    from dsherp_bridge.run_budget import budget as run_budget
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    now=now_datetime()
    frappe.cache().set_value('dsherp_worker_heartbeat',now.isoformat(),expires_in_sec=3600)
    queued_expiry_filters=[['status','=','Queued'],['queue_expires_at','is','set'],['queue_expires_at','<=',now]]
    expired_names=frappe.get_all('DS Model Run',filters=queued_expiry_filters,pluck='name',order_by='creation asc, name asc',limit_page_length=0)
    for name in expired_names:
        expired=frappe.get_doc('DS Model Run',name,for_update=True)
        if (expired.status!='Queued' or not expired.queue_expires_at
            or get_datetime(expired.queue_expires_at)>now):continue
        error='系统繁忙，排队超时，请稍后重试'
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':error})
        events.record_safely(name,'expired',{'reason':'queue_expired'})
        events.record_safely(name,'finished',{'status':'Failed','error':'queue_expired'})
    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now]},pluck='name',order_by='creation asc, name asc'):
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':'运行已过期，未自动重试','capability_hash':''})
        events.record_safely(name,'expired',{'reason':'lease_expired'})
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']]},fields=['owner'],limit_page_length=0)
    busy_owners={row.owner for row in active}
    candidates=frappe.get_all('DS Model Run',filters={'status':'Queued'},fields=['name','owner','domain'],order_by='creation asc, name asc',limit_page_length=0)
    names=[row.name for row in candidates if row.owner not in busy_owners]
    if not names:return None
    if len(active)>=run_budget(candidates[0].domain)['site_concurrency']:return None
    run=frappe.get_doc('DS Model Run',names[0],for_update=True)
    if run.status!='Queued':return None
    try:
        with _actor(run) as identity:
            conversation=conversations._conversation(run.conversation)
            conversations._public(conversation)
            permission_revision=context_permissions.run_revision(run.owner,run.domain)
    except (frappe.PermissionError,frappe.DoesNotExistError):
        error='当前用户已无法读取会话来源'
        frappe.db.set_value('DS Model Run',run.name,{'status':'Failed','error':error,'capability_hash':''})
        events.record_safely(run.name,'finished',{'status':'Failed','error':error})
        return None
    capability=secrets.token_urlsafe(32)
    domain=run.domain
    plan=run_budget(domain)
    combined_revision=hashlib.sha256((permission_revision+runtime_revision+domain).encode()).hexdigest()
    if identity:
        combined_revision=hashlib.sha256((combined_revision+conversations._json(identity)).encode()).hexdigest()
    if conversation.runtime_revision!=combined_revision:
        conversation.runtime_session=uuid.uuid4().hex
        frappe.db.set_value('DS Conversation',conversation.name,{'runtime_session':conversation.runtime_session,'runtime_revision':combined_revision})
    frappe.db.set_value('DS Model Run',run.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now,seconds=plan['lease_seconds']),'permission_revision':permission_revision,'runtime_revision':runtime_revision})
    events.record_safely(run.name,'claimed',{'domain':domain,'permission_revision':permission_revision,
        'runtime_revision':runtime_revision,'native_session_id':conversation.runtime_session})
    return {'run_id':run.name,'session_id':run.conversation,'native_session_id':conversation.runtime_session,
            'permission_revision':permission_revision,
            'runtime_revision':runtime_revision,'domain':domain,
            'scope_id':hashlib.sha256(json.dumps([frappe.local.site,run.owner,run.conversation,domain,conversation.runtime_session],separators=(',',':')).encode()).hexdigest(),
            'question':run.question,'context':json.loads(run.page_context),'capability':capability,
            'budget':plan,'site':frappe.local.site}


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
        or type(max_output_tokens) is not int or not 0<max_output_tokens<=(3072 if run.domain in ('operation','configuration') else 2048)):
        frappe.throw('模型请求配置或输入预算不符')
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._public(conversations._conversation(run.conversation))
    calls=run.model_calls or 0
    total_input=(run.model_input_bytes or 0)+input_bytes
    total_output=(run.model_output_tokens_reserved or 0)+max_output_tokens
    max_calls=MAX_OPERATION_MODEL_CALLS if run.domain=='operation' else 8
    max_output=MAX_OPERATION_OUTPUT_TOKENS_RESERVED if run.domain=='operation' else 16384
    if calls>=max_calls or total_input>524288 or total_output>max_output:
        frappe.throw('本轮模型调用预算已用尽')
    # Reserve before provider dispatch; uncertain/failed calls are not refunded.
    frappe.db.set_value('DS Model Run',run.name,{'model_calls':calls+1,
        'model_input_bytes':total_input,'model_output_tokens_reserved':total_output})
    events.record_safely(run.name,'model_call_reserved',{'call_index':calls+1,'input_bytes':input_bytes,
        'max_output_tokens':max_output_tokens,'purpose':purpose,'model':model})
    return {'allowed':True}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    run=_run(run_id,capability)
    started=time.perf_counter()
    result=_run_tool(run,tool,arguments)
    summary=_tool_summary(tool,result)
    events.record_safely(run.name,'tool_call',{'tool':tool,
        'arguments':arguments if isinstance(arguments,dict) else {'raw':str(arguments)[:200]},
        'duration_ms':int((time.perf_counter()-started)*1000),'result':summary})
    return result


def _tool_summary(tool,result):
    if tool=='erp_read_schema':return {'records':0,'fields':len(result.get('fields',[]))}
    if tool=='erp_read_record':return {'records':1,'fields':len(result.get('fields',{}))}
    if tool=='erp_search_records':return {'records':len(result),'fields':len(result[0]) if result else 0}
    if tool=='erp_read_configuration':return {'exists':bool(result.get('exists'))}
    if isinstance(result,dict) and result.get('id'):return {'proposal':result['id']}
    return {'keys':sorted(result)[:20] if isinstance(result,dict) else type(result).__name__}


def _run_tool(run,tool,arguments):
    if run.status!='Running':raise frappe.PermissionError('运行正在取消')
    if run.domain=='configuration':
        from dsherp_bridge.configuration_tools import read_configuration
        from dsherp_bridge.configuration import propose_bundle
        from dsherp_bridge.configuration_bundle import freeze_bundle
        if isinstance(arguments,str):arguments=json.loads(arguments)
        if tool not in ('erp_read_configuration','erp_propose_configuration'):
            raise frappe.PermissionError('配置领域不允许此工具')
        key='doctype' if tool=='erp_read_configuration' else 'package'
        if not isinstance(arguments,dict) or set(arguments)!={key}:frappe.throw('配置工具参数无效')
        with _actor(run):
            context_permissions.require_revision(run)
            sources=json.loads(run.sources or '[]');authorize_sources(sources)
            if tool=='erp_read_configuration':
                result=read_configuration(**arguments)
                sources.append({'tool':tool,'arguments':arguments,'modules':result['modules'],'roles':result['roles'],
                    'exists':result['exists'],'version':result.get('modified'),'configuration_revision':result['configuration_revision']})
                frappe.db.set_value('DS Model Run',run.name,'sources',conversations._json(sources))
                return result
            try:package=freeze_bundle(arguments['package'])['package']
            except ValueError as error:frappe.throw(str(error))
            targets={row['name'] for row in package['doctypes']}|{row['doctype'] for row in package['extensions']}
            if not targets.issubset({source['arguments']['doctype'] for source in sources if source['tool']=='erp_read_configuration'}):
                frappe.throw('请先读取确切配置目标，再提出配置')
            latest={source['arguments']['doctype']:source for source in sources if source['tool']=='erp_read_configuration'}
            for target in targets:
                if latest[target]['configuration_revision']!=read_configuration(target)['configuration_revision']:
                    frappe.throw('配置来源版本已变化，请重新读取后提出配置')
            return propose_bundle(run.conversation,package,model_run=run.name)
    if tool in ('erp_propose_update','erp_propose_create','erp_propose_action','erp_propose_fill','erp_propose_make'):
        if run.domain!='operation':raise frappe.PermissionError('当前领域不能提出业务操作')
        if isinstance(arguments,str):arguments=json.loads(arguments)
        keys=({'source_doctype','source_name','source_version','route'} if tool=='erp_propose_make'
              else {'doctype','name','action','version'} if tool=='erp_propose_action'
              else {'doctype','values','version'}|({'name'} if tool in ('erp_propose_update','erp_propose_fill') else set()))
        if (not isinstance(arguments,dict) or set(arguments)!=keys
            or not all(isinstance(arguments[key],str) for key in keys-{'values'})
            or ('values' in keys and not isinstance(arguments['values'],dict))):
            frappe.throw('操作提案参数无效')
        with _actor(run):
            context_permissions.require_revision(run)
            sources=json.loads(run.sources or '[]')
            authorize_sources(sources)
            if tool=='erp_propose_make':
                if not any(source['tool']=='erp_read_record'
                           and source['arguments']=={'doctype':arguments['source_doctype'],'name':arguments['source_name']}
                           and source.get('record_versions',{}).get(arguments['source_name'])==arguments['source_version']
                           for source in sources):
                    frappe.throw('请先读取确切来源及当前版本，再提出 make 操作')
                from dsherp_bridge.operations import propose_make as propose
            elif tool in ('erp_propose_update','erp_propose_action','erp_propose_fill'):
                if not any(source['tool']=='erp_read_record' and source['arguments']=={'doctype':arguments['doctype'],'name':arguments['name']}
                           and source.get('record_versions',{}).get(arguments['name'])==arguments['version'] for source in sources):
                    frappe.throw('请先读取确切目标及当前版本，再提出操作')
                if tool=='erp_propose_action':
                    from dsherp_bridge.operations import propose_action as propose
                elif tool=='erp_propose_fill':
                    from dsherp_bridge.operations import propose_fill as propose
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
    if tool=='erp_search_records' and isinstance(arguments,dict):
        if (not set(arguments)<={'doctype','query','filters','fields'}
            or not isinstance(arguments.get('doctype'),str)
            or ('query' in arguments and not isinstance(arguments['query'],str))):
            frappe.throw('工具参数无效')
        arguments={'query':'','filters':None,'fields':None,**arguments}
    function,keys=TOOLS[tool]
    if (not isinstance(arguments,dict) or set(arguments)!=keys
        or (tool!='erp_search_records' and not all(isinstance(v,str) for v in arguments.values()))):
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
            if result:
                fields=list(result[0])
            elif arguments['filters'] is not None:
                fields=['name','modified',*(field for field in (arguments['fields'] or [])
                    if field not in ('name','modified'))]
            else:
                fields=['name','modified']
        source={'tool':tool,'arguments':arguments,'fields':fields,'records':records}
        if tool=='erp_search_records':
            meta=frappe.get_meta(arguments['doctype'])
            source['match_fields']=erp._search_match_fields(
                meta,erp._searchable_fields(meta),arguments['query'],arguments['filters'],
            )
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
def record_run_event(run_id,capability,events):
    run=_run(run_id,capability)
    items=json.loads(events) if isinstance(events,str) else events
    from dsherp_bridge import context_events
    return context_events.record_many(run.name,items)


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
    events.record_safely(run.name,'finished',{'status':status,'answer_chars':len(answer) if isinstance(answer,str) else 0,
        'error':(error or '')[:500],'model_calls':run.model_calls or 0})
    frappe.db.set_value('DS Model Run',run.name,{'status':status,'answer':answer if status=='Succeeded' else '',
        'error':error if status=='Failed' else '', 'capability_hash':''})
    return {'run_id':run.name,'status':status}
