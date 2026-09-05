"""Run-scoped read capabilities. The model cannot select its Site or actor."""
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
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
# 判据是"provider 是否不可用"，不是"是否 5xx"：换错 key、限流、配额耗尽同样让每条
# 运行必败，熔断必须打开。由本轮输入造成的失败（上下文超长、请求非法、空响应）不算。
PROVIDER_FAILURE_ERROR_CLASSES=('TRANSPORT','TIMEOUT','SERVER','AUTH','RATE_LIMIT','QUOTA_EXCEEDED')
# 积压最严重时清扫最长，而清扫排在领取之前：不封顶会让 claim 越慢越领不到，形成正反馈。
SWEEP_LIMIT=50
# 任何一条只有执行者才写得出的事件都算确认；只有 queued/claimed 说明没人接手。
EXECUTOR_CONTACT_KINDS=('runtime_started','lease_renewed','model_call_reserved','tool_call','tool_error','model_error','needs_input')


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


# The run capability endpoints answer anything that can address the Site, so the
# source allowlist and the per-run quota are what keep them a single run's private door.
CAPABILITY_RATE_PER_SECOND=20


def _remote_address():
    # Deliberately the socket peer, never X-Forwarded-For: a run container talks to
    # gunicorn directly, and any client could set the forwarded header itself.
    request=getattr(frappe.local,'request',None)
    return getattr(request,'remote_addr',None) if request is not None else None


def _source_allowed(address):
    allowed=frappe.conf.get('dsherp_agent_sources')
    if not allowed:return True
    if not isinstance(allowed,list):frappe.throw('dsherp_agent_sources 必须是网段列表')
    if not address:return False
    try:candidate=ipaddress.ip_address(address)
    except ValueError:return False
    for entry in allowed:
        try:network=ipaddress.ip_network(entry,strict=False)
        except ValueError:frappe.throw('dsherp_agent_sources 含无效网段')
        if candidate.version==network.version and candidate in network:return True
    return False


def _capability_guard(run_id,endpoint):
    """Every use of a run capability is placed and counted before it is honoured."""
    address=_remote_address()
    if not _source_allowed(address):
        events.record_safely(run_id,'capability_denied',{'endpoint':endpoint,'reason':'source','source':address or ''})
        raise frappe.PermissionError('运行凭据只能从 Agent 网络使用')
    cache=frappe.cache()
    limit=frappe.conf.get('dsherp_capability_rate_per_second') or CAPABILITY_RATE_PER_SECOND
    if type(limit) is not int or limit<1:frappe.throw('dsherp_capability_rate_per_second 无效')
    window=cache.make_key(f'dsherp_capability:{run_id}:{int(time.time())}')
    used=cache.incrby(window,1)
    cache.expire(window,5)
    total=cache.make_key(f'dsherp_capability_calls:{run_id}')
    cache.incrby(total,1)
    cache.expire(total,3600)
    if used>limit:
        events.record_safely(run_id,'capability_denied',{'endpoint':endpoint,'reason':'rate',
                                                        'source':address or '','used':int(used)})
        raise frappe.PermissionError('运行凭据调用过于频繁')
    return address


def _capability_calls(run_id):
    value=frappe.cache().get_value(f'dsherp_capability_calls:{run_id}')
    try:return int(value)
    except (TypeError,ValueError):return None


def _run(run_id,capability):
    run=frappe.get_doc('DS Model Run',run_id,for_update=True)
    if (run.status not in ('Running','Cancelling','NeedsInput') or not run.capability_hash
        or not isinstance(capability,str) or get_datetime(run.expires_at)<=now_datetime()
        or not hmac.compare_digest(run.capability_hash,hashlib.sha256(capability.encode()).hexdigest())):
        raise frappe.PermissionError('运行凭据失效')
    return run


def _set_worker_heartbeat(now):
    value=now.isoformat()
    frappe.cache().set_value('dsherp_worker_heartbeat',value,expires_in_sec=3600)
    return value


@frappe.whitelist(methods=['POST'])
def worker_heartbeat():
    user=conversations._user()
    if user!=frappe.conf.get('dsherp_runtime_user'):
        raise frappe.PermissionError('需要站点指定的运行服务身份')
    return {'heartbeat':_set_worker_heartbeat(now_datetime())}


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
    _set_worker_heartbeat(now)
    queued_expiry_filters=[['status','=','Queued'],['queue_expires_at','is','set'],['queue_expires_at','<=',now]]
    expired_names=frappe.get_all('DS Model Run',filters=queued_expiry_filters,pluck='name',order_by='creation asc, name asc',limit_page_length=SWEEP_LIMIT)
    for name in expired_names:
        expired=frappe.get_doc('DS Model Run',name,for_update=True)
        if (expired.status!='Queued' or not expired.queue_expires_at
            or get_datetime(expired.queue_expires_at)>now):continue
        error='系统繁忙，排队超时，请稍后重试'
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':error})
        events.record_safely(name,'expired',{'reason':'queue_expired'})
        events.record_safely(name,'finished',{'status':'Failed','error':'queue_expired'})
    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now]},pluck='name',order_by='creation asc, name asc',limit_page_length=SWEEP_LIMIT):
        # 没有任何一条执行者写入的事件，说明这次领取的响应从未到达 worker：
        # 这条运行从来没被执行过，说"已过期"会把用户引向完全无关的原因。
        contacted=frappe.db.count('DS Run Event',{'run':name,'kind':['in',EXECUTOR_CONTACT_KINDS]})
        error='运行已过期，未自动重试' if contacted else '助手未能启动本次运行，请重试'
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':error,'capability_hash':''})
        events.record_safely(name,'expired',{'reason':'lease_expired' if contacted else 'claim_unacked'})
    # 执行者在交还会话前死掉时，问题本身仍然有效：只收回凭据，不把用户的问题作废。
    for name in frappe.get_all('DS Model Run',filters={'status':'NeedsInput','capability_hash':['!=',''],'expires_at':['<=',now]},
                               pluck='name',order_by='creation asc, name asc',limit_page_length=SWEEP_LIMIT):
        frappe.db.set_value('DS Model Run',name,{'capability_hash':''})
        events.record_safely(name,'expired',{'reason':'needs_input_executor_lost'})
    # NeedsInput 在执行者回写前仍持有 capability 与同一个 native session：此时它就是
    # 在飞运行，放第二个执行者进来会让两个容器抢同一个会话目录（终审第 4 项）。
    active=frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']]},fields=['owner'],limit_page_length=0)
    active+=frappe.get_all('DS Model Run',filters={'status':'NeedsInput','capability_hash':['!=','']},fields=['owner'],limit_page_length=0)
    busy_owners={row.owner for row in active}
    candidates=frappe.get_all('DS Model Run',filters={'status':'Queued'},fields=['name','owner','domain'],order_by='creation asc, name asc',limit_page_length=0)
    candidates=[row for row in candidates if row.owner not in busy_owners]
    if not candidates:return None
    plan=run_budget(candidates[0].domain)
    if len(active)>=plan['site_concurrency']:return None
    run=frappe.get_doc('DS Model Run',candidates[0].name,for_update=True)
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
    revision_material=[permission_revision,runtime_revision,domain,plan['provider'],plan['model']]
    if identity:revision_material.append(identity)
    combined_revision=hashlib.sha256(conversations._json(revision_material).encode()).hexdigest()
    if conversation.runtime_revision!=combined_revision:
        conversation.runtime_session=uuid.uuid4().hex
        frappe.db.set_value('DS Conversation',conversation.name,{'runtime_session':conversation.runtime_session,'runtime_revision':combined_revision})
    frappe.db.set_value('DS Model Run',run.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now,seconds=plan['claim_ack_seconds']),'permission_revision':permission_revision,'runtime_revision':runtime_revision})
    events.record_safely(run.name,'claimed',{'domain':domain,'permission_revision':permission_revision,
        'runtime_revision':runtime_revision,'native_session_id':conversation.runtime_session,
        'provider':plan['provider'],'model':plan['model']})
    return {'run_id':run.name,'session_id':run.conversation,'native_session_id':conversation.runtime_session,
            'permission_revision':permission_revision,
            'runtime_revision':runtime_revision,'domain':domain,
            'scope_id':hashlib.sha256(json.dumps([frappe.local.site,run.owner,run.conversation,domain,conversation.runtime_session],separators=(',',':')).encode()).hexdigest(),
            'question':run.question,'context':json.loads(run.page_context),'capability':capability,
            'budget':plan,'site':frappe.local.site}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_status(run_id,capability):
    _capability_guard(run_id,'run_status')
    run=frappe.db.get_value('DS Model Run',run_id,
        ['name','status','capability_hash','expires_at','domain','needs_input'],as_dict=True)
    if (not run or run.status not in ('Running','Cancelling','NeedsInput') or not run.capability_hash
        or not run.expires_at or not isinstance(capability,str)
        or not hmac.compare_digest(run.capability_hash,hashlib.sha256(capability.encode()).hexdigest())):
        raise frappe.PermissionError('运行凭据失效')
    from dsherp_bridge.run_budget import budget
    plan=budget(run.domain);now=now_datetime()
    remaining=int((get_datetime(run.expires_at)-now).total_seconds())
    if remaining<=0:raise frappe.PermissionError('运行凭据失效')
    if run.status in ('Running','Cancelling') and remaining<plan['lease_renew_below_seconds']:
        new_expiry=add_to_date(now,seconds=plan['lease_seconds'])
        frappe.db.set_value('DS Model Run',run.name,'expires_at',new_expiry)
        events.record_safely(run.name,'lease_renewed',{'expires_at':str(new_expiry)})
        remaining=plan['lease_seconds']
    return {'run_id':run.name,'status':run.status,'needs_input':run.needs_input or '',
            'lease_remaining_seconds':remaining}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def reserve_model_call(run_id,capability,input_bytes,max_output_tokens,provider,model,purpose,runtime_revision,domain='query',claimed_budget=None):
    _capability_guard(run_id,'reserve_model_call')
    run=_run(run_id,capability)
    if run.status!='Running':raise frappe.PermissionError('运行正在取消')
    if runtime_revision!=run.runtime_revision:raise frappe.PermissionError('模型配置与领取的运行不一致')
    if domain!=run.domain:raise frappe.PermissionError('模型领域与领取的运行不一致')
    from dsherp_bridge.run_budget import budget as run_budget
    plan=run_budget(run.domain)
    if (provider!=plan['provider'] or model!=plan['model']
        or purpose not in ('conversation','compaction','session-title')
        or type(input_bytes) is not int or not 0<input_bytes<=plan['model_max_input_bytes_per_call']
        or type(max_output_tokens) is not int or not 0<max_output_tokens<=plan['model_max_output_tokens_per_call']):
        frappe.throw('模型请求配置或输入预算不符')
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._public(conversations._conversation(run.conversation))
    if (not isinstance(claimed_budget,dict) or set(claimed_budget)!=set(plan)
        or any(type(claimed_budget[key]) is not type(plan[key]) or claimed_budget[key]!=plan[key] for key in plan)):
        frappe.throw('领取预算与当前站点配置不一致')
    calls=run.model_calls or 0
    total_input=(run.model_input_bytes or 0)+input_bytes
    total_output=(run.model_output_tokens_reserved or 0)+max_output_tokens
    if (calls>=plan['model_max_calls'] or total_input>plan['model_max_input_bytes_total']
        or total_output>plan['model_max_output_tokens_total']):
        frappe.throw('本轮模型调用预算已用尽')
    # Reserve before provider dispatch; uncertain/failed calls are not refunded.
    frappe.db.set_value('DS Model Run',run.name,{'model_calls':calls+1,
        'model_input_bytes':total_input,'model_output_tokens_reserved':total_output})
    events.record_safely(run.name,'model_call_reserved',{'call_index':calls+1,'input_bytes':input_bytes,
        'max_output_tokens':max_output_tokens,'purpose':purpose,'provider':provider,'model':model})
    return {'allowed':True}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    _capability_guard(run_id,'run_tool')
    run=_run(run_id,capability)
    started=time.perf_counter()
    result=_run_tool(run,tool,arguments)
    summary=_tool_summary(tool,result)
    events.record_safely(run.name,'tool_call',{'tool':tool,
        'arguments':arguments if isinstance(arguments,dict) else {'raw':str(arguments)[:200]},
        'duration_ms':int((time.perf_counter()-started)*1000),'result':summary,
        'source':_remote_address() or ''})
    return result


def _tool_summary(tool,result):
    if tool=='erp_read_schema':return {'records':0,'fields':len(result.get('fields',[]))}
    if tool=='erp_read_record':return {'records':1,'fields':len(result.get('fields',{}))}
    if tool=='erp_search_records':return {'records':len(result),'fields':len(result[0]) if result else 0}
    if tool=='erp_read_configuration':return {'exists':bool(result.get('exists'))}
    if isinstance(result,dict) and result.get('id'):return {'proposal':result['id']}
    return {'keys':sorted(result)[:20] if isinstance(result,dict) else type(result).__name__}


def _run_tool(run,tool,arguments):
    if tool=='erp_request_input':
        if run.status!='Running':raise frappe.PermissionError('运行正在取消')
        with _actor(run):
            context_permissions.require_revision(run)
        if isinstance(arguments,str):arguments=json.loads(arguments)
        question=arguments.get('question') if isinstance(arguments,dict) and set(arguments)=={'question'} else None
        if not isinstance(question,str) or not 1<=len(question.strip())<=2000:
            frappe.throw('补充信息问题必须包含 1–2000 个字符')
        question=question.strip()
        frappe.db.set_value('DS Model Run',run.name,{'status':'NeedsInput','needs_input':question})
        events.record_safely(run.name,'needs_input',{'question_chars':len(question)})
        return {'status':'NeedsInput'}
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
    _capability_guard(run_id,'record_run_event')
    run=_run(run_id,capability)
    items=json.loads(events) if isinstance(events,str) else events
    from dsherp_bridge import context_events
    return context_events.record_many(run.name,items)


@frappe.whitelist(allow_guest=True,methods=['POST'])
def finish_run(run_id,capability,status,answer='',error=''):
    source=_capability_guard(run_id,'finish_run')
    run=_run(run_id,capability)
    if status not in ('Succeeded','Failed','Cancelled','NeedsInput'):frappe.throw('无效运行结束状态')
    requested_success=status=='Succeeded'
    if requested_success:
        if run.status not in ('Running','Cancelling'):raise frappe.PermissionError('运行不能成功完成')
        # 完成仍只由外部可核验的事实判定，但"服务端拒绝了这次工具调用"和"读到了记录"
        # 一样是服务端自己记录的事实。少了它，被权限拒绝的运行既不能成功也没有出口，
        # 模型写好的解释会随租约过期一起丢掉（见 runtime-reliability-evidence 终审第 1 项）。
        attempted=bool(json.loads(run.sources or '[]')) or bool(frappe.db.count('DS Run Event',{'run':run.name,'kind':'tool_error'}))
        if not isinstance(answer,str) or not answer.strip() or not attempted:
            frappe.throw('成功结果必须包含实际读取或服务端记录的工具失败')
        with _actor(run):
            context_permissions.require_revision(run)
            conversations._public(conversations._conversation(run.conversation))
        if run.status=='Cancelling':status='Cancelled'
    if status=='Cancelled' and run.status!='Cancelling':frappe.throw('运行未请求取消')
    if status=='NeedsInput' and run.status!='NeedsInput':frappe.throw('运行未请求补充信息')
    provider_failures=frappe.db.count('DS Run Event',{
        'run':run.name,
        'kind':'model_error',
        'error_class':['in',PROVIDER_FAILURE_ERROR_CLASSES],
    })
    sources=json.loads(run.sources or '[]')
    proposals=frappe.db.count('DS Operation Proposal',{'model_run':run.name})
    proposal_names=frappe.get_all('DS Operation Proposal',filters={'model_run':run.name},pluck='name')
    executions=frappe.db.count('DS Execution Record',{'proposal':['in',proposal_names],'status':'Succeeded'}) if proposal_names else 0
    # One line closes the capability's audit: who used it, from where, how many times.
    events.record_safely(run.name,'finished',{'status':status,'answer_chars':len(answer) if isinstance(answer,str) else 0,
        'error':(error or '')[:500],'model_calls':run.model_calls or 0,'provider_failures':provider_failures,
        'proposals':proposals,'executions':executions,'sources':len(sources),
        'source':source or '','capability_calls':_capability_calls(run.name)})
    values={'status':status,'answer':answer if requested_success else '',
        'error':error if status=='Failed' else '','capability_hash':'','provider_failures':provider_failures}
    if status=='NeedsInput':values['needs_input']=answer
    if requested_success:
        flagged=executions==0 and bool(re.search(r'(已|成功)(创建|提交|保存|完成|生成|录入|执行)',answer))
        values['answer_flagged']=1 if flagged else 0
        if flagged:
            events.record_safely(run.name,'unverified_completion_claim',{'proposals':proposals,'executions':executions})
    frappe.db.set_value('DS Model Run',run.name,values)
    return {'run_id':run.name,'status':status,'provider_failures':provider_failures}
