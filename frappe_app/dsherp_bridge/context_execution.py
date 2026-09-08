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
from dsherp_bridge import grants
from dsherp_bridge import context_permissions

# The key set is strict on purpose: an argument the tool does not have is a mistake worth
# telling the model about, not one to drop silently. Paging and shaping arguments are part of
# the set and are filled in with their defaults below, so a call that omits them still matches.
TOOLS={'erp_read_schema':(erp.read_schema,{'doctype','tables','after_fieldname','child_after'}),
       'erp_read_record':(erp.read_record,{'doctype','name','fields','children','include_empty','after_idx'}),
       'erp_search_records':(erp.search_records,{'doctype','query','filters','fields','after_name'})}
# What each read tool assumes when the model does not say. `read_record`'s defaults are the
# lean ones (slice 0: not expanding child tables is -51% on a Sales Order).
TOOL_DEFAULTS={'erp_read_schema':{'tables':None,'after_fieldname':None,'child_after':None},
               'erp_read_record':{'fields':None,'children':None,'include_empty':False,'after_idx':None},
               'erp_search_records':{'query':'','filters':None,'fields':None,'after_name':None}}
# 判据是"provider 是否不可用"，不是"是否 5xx"；词表与判定在 provider_failures.py（纯 Python，
# 单元测试直接按行为验证），这里只负责取出该运行的 model_error 事件逐条计数。
from dsherp_bridge.provider_failures import count_provider_failures


def _provider_failures(run_name):
    classes=frappe.get_all('DS Run Event',filters={'run':run_name,'kind':'model_error'},pluck='error_class',limit_page_length=0)
    return count_provider_failures(classes)
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
        from dsherp_bridge import grants
        from dsherp_bridge.sso import validate_grant,sso_enforced
        grant=grants.of(run.name)
        if grant:
            identity=validate_grant(grant,run.owner)
        elif sso_enforced():
            # Every business session on this Site comes through the platform; an executor
            # acting for a member with no authorization on file is not acting for anyone (R6).
            raise frappe.PermissionError('运行缺少企业平台授权，不能继续执行')
        yield identity
    finally:
        frappe.set_user(original)


def grounds(sources,tool,identity,*,version,key):
    """Whether this run already read exactly what the proposal claims to act on.

    Matched on the **identity** keys only - the doctype, and the record name where there is
    one - never on the whole argument dict. Reads now carry shaping and paging arguments
    (`fields`, `children`, `after_idx`, `after_fieldname`), and `_run_tool` fills in their
    defaults before recording the source, so a whole-dict comparison against a two-key
    literal matches nothing: every proposal would be refused immediately after reading its
    own target. Sources recorded before those arguments existed are audit facts and still
    match, for the same reason.

    Freshness is not weakened by this: it is carried by `version`, compared below against the
    `modified` the read itself returned. A partial read is still a read of that record at
    that version.
    """
    for source in sources:
        if source['tool']!=tool:continue
        args=source['arguments']
        if any(args.get(field)!=value for field,value in identity.items()):continue
        recorded=source.get(key)
        if isinstance(recorded,dict):
            recorded=recorded.get(identity.get('name'))
        if recorded==version:return True
    return False


def authorize_sources(sources):
    for source in sources:
        if source['tool']=='erp_read_configuration':
            from dsherp_bridge.configuration_tools import authorize_source
            authorize_source(source)
            continue
        args=source['arguments'];doctype=args['doctype']
        # The policy gate first, exactly as a fresh read would face it. This used to come for
        # free because the visible set was rebuilt by calling erp.read_schema, which
        # authorizes on the way in; computing the set from metadata instead is faster and
        # page-free, but it would silently drop the gate - a source could be replayed against
        # a DocType whose policy has since been removed or disabled.
        erp._authorize(doctype)
        # Straight from the metadata, not through erp.read_schema. That tool now answers in
        # 16KB pages and no longer inlines child tables, so a visible set built from one page
        # would refuse the very read that produced this source - a permission error about
        # history, raised on something that just happened. Replaying an authorization is also
        # the wrong place to pay for paging: this is the same permitted-field computation the
        # tool does internally, without the shaping.
        meta=frappe.get_meta(doctype)
        visible=erp._readable_fields(meta,frappe.session.user)|{'name','modified'}
        visible|={field.fieldname for field in meta.fields
                  if field.fieldtype=='Table'
                  and field.permlevel in meta.get_permlevel_access('read',user=frappe.session.user)}
        if meta.is_submittable:
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
            definition=meta.get_field(table)
            if definition is None or definition.fieldtype!='Table' or table not in visible:
                raise frappe.PermissionError('历史结果的明细字段权限已改变')
            readable=erp._readable_fields(frappe.get_meta(definition.options),frappe.session.user,
                                          parenttype=doctype)|{'name','idx'}
            if set(columns)-readable:
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
        _refuse(run_id,'capability_denied',{'endpoint':endpoint,'reason':'source','source':address or ''},
                frappe.PermissionError('运行凭据只能从 Agent 网络使用'))
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
        _refuse(run_id,'capability_denied',{'endpoint':endpoint,'reason':'rate','source':address or '','used':int(used)},
                frappe.PermissionError('运行凭据调用过于频繁'))
    return address


def _capability_calls(run_id):
    # Written by a raw INCRBY, so it must be read raw: get_value would try to unpickle it.
    cache=frappe.cache()
    value=cache.get(cache.make_key(f'dsherp_capability_calls:{run_id}'))
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
    # A stable backup window is opened by the host CLI; the worker's own hold file closes the
    # door a tick later, so the site itself must refuse in between. Queued runs are frozen,
    # not failed: the window ends in minutes and the user's question is still valid.
    # The gate is an epoch second set by the host CLI for the length of its window, so a
    # command killed mid-window cannot hold the Site's claims for ever.
    if int(frappe.conf.get('dsherp_hold_until') or 0) > time.time():
        _set_worker_heartbeat(now_datetime())
        return None
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
        grants.drop(name)
    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now]},pluck='name',order_by='creation asc, name asc',limit_page_length=SWEEP_LIMIT):
        # 没有任何一条执行者写入的事件，说明这次领取的响应从未到达 worker：
        # 这条运行从来没被执行过，说"已过期"会把用户引向完全无关的原因。
        contacted=frappe.db.count('DS Run Event',{'run':name,'kind':['in',EXECUTOR_CONTACT_KINDS]})
        error='运行已过期，未自动重试' if contacted else '助手未能启动本次运行，请重试'
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':error,'capability_hash':''})
        events.record_safely(name,'expired',{'reason':'lease_expired' if contacted else 'claim_unacked'})
        grants.drop(name)
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
    # 清扫封顶后不再保证每条过期行都已判失败，候选必须自己排除过期行，否则积压超过
    # 上限时第 51 条起的过期请求会被领走执行（复核 批评 4）。
    candidates=frappe.get_all('DS Model Run',filters={'status':'Queued'},
        or_filters=[['queue_expires_at','is','not set'],['queue_expires_at','>',now]],
        fields=['name','owner','domain'],order_by='creation asc, name asc',limit_page_length=0)
    candidates=[row for row in candidates if row.owner not in busy_owners]
    if not candidates:return None
    plan=run_budget(candidates[0].domain)
    if len(active)>=plan['site_concurrency']:return None
    run=frappe.get_doc('DS Model Run',candidates[0].name,for_update=True)
    if run.status!='Queued':return None
    if run.queue_expires_at and get_datetime(run.queue_expires_at)<=now:return None
    try:
        with _actor(run) as identity:
            conversation=conversations._conversation(run.conversation)
            conversations._public(conversation)
            permission_revision=context_permissions.run_revision(run.owner,run.domain)
    except (frappe.PermissionError,frappe.DoesNotExistError):
        error='当前用户已无法读取会话来源'
        frappe.db.set_value('DS Model Run',run.name,{'status':'Failed','error':error,'capability_hash':''})
        events.record_safely(run.name,'finished',{'status':'Failed','error':error})
        grants.drop(run.name)
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
        or type(input_bytes) is not int or input_bytes<=0
        or type(max_output_tokens) is not int or max_output_tokens<=0):
        # Still a plain refusal: a mismatched provider or a nonsensical size is a broken
        # runtime, not a budget that ran out.
        frappe.throw('模型请求配置或输入预算不符')
    if input_bytes>plan['model_max_input_bytes_per_call']:
        _over_budget(run,'model_max_input_bytes_per_call',input_bytes,
                     plan['model_max_input_bytes_per_call'])
    if max_output_tokens>plan['model_max_output_tokens_per_call']:
        _over_budget(run,'model_max_output_tokens_per_call',max_output_tokens,
                     plan['model_max_output_tokens_per_call'])
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._public(conversations._conversation(run.conversation))
    if (not isinstance(claimed_budget,dict) or set(claimed_budget)!=set(plan)
        or any(type(claimed_budget[key]) is not type(plan[key]) or claimed_budget[key]!=plan[key] for key in plan)):
        frappe.throw('领取预算与当前站点配置不一致')
    if frappe.db.count('DS Run Event',{'run':run.name,'kind':'loop_detected'}):
        # How a loop actually stops. `run_tool` runs inside an HTTP request and cannot kill a
        # container that is still going; poisoning the next model authorization is the
        # mechanism this repository already uses to end a run (model-guard sets disabled and
        # the container exits). See the deviation table.
        #
        # Raised, not `_refuse`d: the fact `finish_run` rules on is the `loop_detected` event
        # that is already on the stream. Writing a `budget_exceeded` event here would put a
        # second, differently-worded reason next to it and label the run's most important
        # event with a limit name that is not a limit.
        raise frappe.ValidationError(LOOP_MESSAGE)
    calls=run.model_calls or 0
    total_input=(run.model_input_bytes or 0)+input_bytes
    total_output=(run.model_output_tokens_reserved or 0)+max_output_tokens
    if calls>=plan['model_max_calls']:
        _over_budget(run,'model_max_calls',calls,plan['model_max_calls'])
    if total_input>plan['model_max_input_bytes_total']:
        _over_budget(run,'model_max_input_bytes_total',total_input,plan['model_max_input_bytes_total'])
    if total_output>plan['model_max_output_tokens_total']:
        _over_budget(run,'model_max_output_tokens_total',total_output,plan['model_max_output_tokens_total'])
    # Reserve before provider dispatch; uncertain/failed calls are not refunded.
    frappe.db.set_value('DS Model Run',run.name,{'model_calls':calls+1,
        'model_input_bytes':total_input,'model_output_tokens_reserved':total_output})
    events.record_safely(run.name,'model_call_reserved',{'call_index':calls+1,'input_bytes':input_bytes,
        'max_output_tokens':max_output_tokens,'purpose':purpose,'provider':provider,'model':model})
    return {'allowed':True}


# Two audiences, two sentences. BUDGET/LOOP/TIME_MESSAGE end up in `DS Model Run.error`,
# which a **person** reads after the fact, so they say what happened and what to do next time.
# LOOP_REFUSAL is handed to the **model** at the moment it repeats itself, so it names the two
# ways out it actually has right now.
BUDGET_MESSAGE='本轮模型调用预算已用尽，运行已停止；请把问题拆小后重试'
LOOP_MESSAGE='本轮因同一工具同参数连续 3 次调用被停止；请换一个问法或补充信息'
LOOP_REFUSAL='同一工具同参数已连续调用 3 次，已停止重复；请改变参数，或用 erp_request_input 向用户说明'
TIME_MESSAGE='本轮已达运行时长上限并停止'


def _over_budget(run,limit,used,allowed):
    """Record that this run hit a limit, then refuse the call.

    Through `_refuse`, and **the run stays `Running`**. Writing a terminal status here would
    step on three things at once: an HTTP request rolls the write back when the refusal
    propagates; `_run`'s status whitelist would then make the worker's own `finish_run` throw
    "运行凭据失效"; and that `finish_run` sits inside `context_worker`'s except block, whose
    `poll_once` only swallows 5xx - a 403 would take down the worker loop.

    So the fact is persisted and the status decision is left to the next `finish_run`, which
    reads the facts the server itself wrote. Same shape as every other refusal here.
    """
    _refuse(run.name,'budget_exceeded',{'limit':limit,'used':int(used),'allowed':int(allowed)},
            frappe.ValidationError(BUDGET_MESSAGE))


class RefusalNotPersisted(Exception):
    """The server refused a call but could not persist the fact finish_run depends on.
    Surfaced as an infrastructure error (HTTP 500, transient for the runner) instead of the
    refusal itself: a clean 403 would claim a fact that does not exist."""
    def __init__(self,refusal,cause):
        super().__init__(f'拒绝事实无法持久化（{type(cause).__name__}）；原拒绝：{refusal}')
        self.refusal=refusal
        self.__cause__=cause


def _refuse(run_name,kind,payload,refusal,error_class=None):
    """Persist the refusal fact, then raise the refusal.

    In an HTTP request the handler rolls back everything the request wrote once the
    refusal propagates, so the request's partial work is discarded first (the handler
    would discard it anyway) and the event is committed on its own. Outside a request
    (in-process callers such as integration scripts) the event joins the caller's
    transaction. The write is not best-effort: this event is the fact finish_run judges
    completion by, so a failure to persist it is raised, not logged."""
    in_request=getattr(frappe.local,'request',None) is not None
    try:
        if in_request:frappe.db.rollback()
        events.record(run_name,kind,payload,error_class=error_class or type(refusal).__name__)
        if in_request:frappe.db.commit()
    except Exception as error:
        if in_request:
            try:frappe.db.rollback()
            except Exception:pass
        raise RefusalNotPersisted(refusal,error) from error
    raise refusal


def _call_key(tool,arguments):
    """What makes two calls the same call, in the shape the events already store.

    `context_events.sanitize` and no other: that is the function whose output is in the
    payloads being compared against, truncation marker included.
    """
    return conversations._json([tool,events.sanitize(arguments if isinstance(arguments,dict) else {})])


def _loop_guard(run,tool,arguments):
    """Stop a run that is asking the same thing over and over.

    Reads both `tool_call` **and** `tool_refused`: a refused call writes only the latter, so
    looking at `tool_call` alone would miss the most typical loop of all - a model resending
    the very call the server just rejected.
    """
    from dsherp_bridge import loop_guard
    key=_call_key(tool,arguments)
    if not loop_guard.comparable(key):
        return
    rows=frappe.get_all('DS Run Event',
        filters={'run':run.name,'kind':['in',('tool_call','tool_refused')]},
        fields=['kind','payload'],order_by='seq desc',limit_page_length=loop_guard.LOOP_LIMIT-1)
    previous=[]
    for row in reversed(rows):
        try:payload=json.loads(row['payload'] or '{}')
        except ValueError:payload={}
        previous.append(_call_key(payload.get('tool'),payload.get('arguments') or {}))
    if loop_guard.repeats(previous,key):
        _refuse(run.name,'loop_detected',{'tool':tool,'repeats':loop_guard.LOOP_LIMIT},
                frappe.ValidationError(LOOP_REFUSAL))


def _message_reason():
    """The refusal text when the exception itself carries none.

    `frappe.throw` puts its message on the exception, but `has_permission(throw=True)` and a
    few other native refusals raise a bare `PermissionError` and leave the wording in
    `message_log`. Without this the event records an empty reason, and neither a person nor
    an evaluation can tell which check refused."""
    for entry in reversed(getattr(frappe.local,'message_log',None) or []):
        if isinstance(entry,dict):
            message=entry.get('message')
        else:
            try:message=json.loads(entry).get('message')
            except (TypeError,ValueError):message=str(entry)
        if isinstance(message,str) and message.strip():
            return frappe.utils.strip_html(message).strip()[:200]
    return ''


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    _capability_guard(run_id,'run_tool')
    run=_run(run_id,capability)
    started=time.perf_counter()
    _loop_guard(run,tool,arguments)
    try:
        result=_run_tool(run,tool,arguments)
    except (frappe.PermissionError,frappe.ValidationError,frappe.DoesNotExistError) as error:
        # `arguments` too: a refusal that does not say what was asked cannot be told apart
        # from another refusal of the same tool, which is what slice 6's loop detection has
        # to do - and what an evaluation needs to see that the refusal was about this call.
        _refuse(run.name,'tool_refused',{'tool':tool,
            'arguments':arguments if isinstance(arguments,dict) else {'raw':str(arguments)[:200]},
            'reason':str(error)[:200] or _message_reason(),
            'source':_remote_address() or ''},error)
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
                if not grounds(sources,'erp_read_record',
                               {'doctype':arguments['source_doctype'],'name':arguments['source_name']},
                               version=arguments['source_version'],key='record_versions'):
                    frappe.throw('请先读取确切来源及当前版本，再提出 make 操作')
                from dsherp_bridge.operations import propose_make as propose
            elif tool in ('erp_propose_update','erp_propose_action','erp_propose_fill'):
                if not grounds(sources,'erp_read_record',
                               {'doctype':arguments['doctype'],'name':arguments['name']},
                               version=arguments['version'],key='record_versions'):
                    frappe.throw('请先读取确切目标及当前版本，再提出操作')
                if tool=='erp_propose_action':
                    from dsherp_bridge.operations import propose_action as propose
                elif tool=='erp_propose_fill':
                    from dsherp_bridge.operations import propose_fill as propose
                else:
                    from dsherp_bridge.operations import propose_update as propose
            else:
                if not grounds(sources,'erp_read_schema',{'doctype':arguments['doctype']},
                               version=arguments['version'],key='schema_version'):
                    frappe.throw('请先读取当前业务结构，再提出创建操作')
                from dsherp_bridge.operations import propose_create as propose
            from dsherp_bridge import grants
            return propose(run.conversation,**arguments,grant=grants.of(run.name),model_run=run.name)
    if tool not in TOOLS:frappe.throw('未知工具')
    if isinstance(arguments,str):arguments=json.loads(arguments)
    function,keys=TOOLS[tool]
    if not isinstance(arguments,dict) or not set(arguments)<=keys:frappe.throw('工具参数无效')
    if not isinstance(arguments.get('doctype'),str):frappe.throw('工具参数无效')
    if 'name' in keys and not isinstance(arguments.get('name',''),str):frappe.throw('工具参数无效')
    if 'query' in arguments and not isinstance(arguments['query'],str):frappe.throw('工具参数无效')
    arguments={**TOOL_DEFAULTS[tool],**arguments}
    if set(arguments)!=keys:frappe.throw('工具参数无效')
    with _actor(run):
        context_permissions.require_revision(run)
        conversations._context(run.page_context,check_version=False)
        result=function(**arguments)
        fields=[];records=[]
        if tool=='erp_read_schema':fields=[f['fieldname'] for f in result['fields']]
        elif tool=='erp_read_record':
            # Counted-but-unexpanded child tables are named here too: the model was told they
            # exist, so the authorization replay has to cover them as read.
            seen=set(result['fields'])|set(result.get('child_tables') or {})
            fields=[key for key in seen if frappe.get_meta(arguments['doctype']).get_field(key)]
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
            # Only the tables actually expanded have columns to authorize; the counted ones
            # revealed nothing but their existence and row count.
            source['child_fields']={field:sorted({column for row in rows for column in row})
                                    for field,rows in result['fields'].items() if isinstance(rows,list)}
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


def _budget_ruling(run,error):
    """Turn a generic failure into `BudgetExceeded` when the server's own record says so.

    Each branch requires **a fact the server wrote itself**. The runner's word alone never
    moves a status: a runtime that reported a timeout it did not actually hit would otherwise
    relabel an ordinary failure as an expense.

    Returns `(status, error, reason)`. The reason is a code, not prose: it rides on the
    `finished` event so the transcript can say why the run ended without anyone parsing the
    message, and it is `''` for an ordinary failure.
    """
    kinds={row['kind'] for row in frappe.get_all('DS Run Event',
        filters={'run':run.name,'kind':['in',('budget_exceeded','loop_detected')]},
        fields=['kind'],limit_page_length=0)}
    if 'loop_detected' in kinds:
        return 'BudgetExceeded',LOOP_MESSAGE,'loop_detected'
    if 'budget_exceeded' in kinds:
        return 'BudgetExceeded',BUDGET_MESSAGE,'budget_exceeded'
    # The time budget is the one limit only the runner observes. So it is believed only when
    # the server's own clock agrees: claimed → now must actually have reached the budget.
    timed_out=frappe.get_all('DS Run Event',
        filters={'run':run.name,'kind':'runtime_failed'},fields=['payload'],limit_page_length=0)
    reported=any((json.loads(row['payload'] or '{}') or {}).get('reason')=='run_total_exceeded'
                 for row in timed_out)
    if reported:
        from dsherp_bridge.run_budget import budget as run_budget
        claimed=frappe.db.get_value('DS Run Event',{'run':run.name,'kind':'claimed'},'recorded_at')
        if claimed:
            from frappe.utils import get_datetime,now_datetime,time_diff_in_seconds
            elapsed=time_diff_in_seconds(now_datetime(),get_datetime(claimed))
            if elapsed>=run_budget(run.domain)['run_total_seconds']-5:
                return 'BudgetExceeded',TIME_MESSAGE,'run_total_exceeded'
    return 'Failed',error,''


def _usage_of(run):
    """结算这次运行的真实用量：provider 的数字来自它自己在 model_response 里报的 usage，
    时长来自服务端记的 claimed 与 finished。runner 汇总的数字一概不采信。"""
    from dsherp_bridge.usage import storable, summarise
    rows=frappe.get_all('DS Run Event',filters={'run':run},fields=['kind','payload','recorded_at','source'],
        order_by='seq asc',limit_page_length=0)
    return storable(summarise(rows))


@frappe.whitelist(allow_guest=True,methods=['POST'])
def finish_run(run_id,capability,status,answer='',error=''):
    source=_capability_guard(run_id,'finish_run')
    run=_run(run_id,capability)
    # BudgetExceeded is a ruling this function makes, never a status a caller may claim:
    # the run is over budget because the server itself wrote that down, not because the
    # worker said so.
    if status not in ('Succeeded','Failed','Cancelled','NeedsInput'):frappe.throw('无效运行结束状态')
    requested_success=status=='Succeeded'
    if requested_success:
        if run.status not in ('Running','Cancelling'):raise frappe.PermissionError('运行不能成功完成')
        # 完成只由服务端自己记录的事实判定："读到了记录"（sources）或"服务端拒绝了这次工具
        # 调用"（tool_refused，由 run_tool 在拒绝时提交）。runner 回写的 tool_error 不算：它是
        # 尽力而为的观测事件，回写失败就没有，客户端自造的参数错误也能伪造一条。
        attempted=bool(json.loads(run.sources or '[]')) or bool(frappe.db.count('DS Run Event',{'run':run.name,'kind':'tool_refused'}))
        if not isinstance(answer,str) or not answer.strip() or not attempted:
            frappe.throw('成功结果必须包含实际读取或服务端记录的工具失败')
        with _actor(run):
            context_permissions.require_revision(run)
            conversations._public(conversations._conversation(run.conversation))
        if run.status=='Cancelling':status='Cancelled'
    if status=='Cancelled' and run.status!='Cancelling':frappe.throw('运行未请求取消')
    if status=='NeedsInput' and run.status!='NeedsInput':frappe.throw('运行未请求补充信息')
    reason=''
    if status=='Failed':
        status,error,reason=_budget_ruling(run,error)
    provider_failures=_provider_failures(run.name)
    sources=json.loads(run.sources or '[]')
    proposals=frappe.db.count('DS Operation Proposal',{'model_run':run.name})
    proposal_names=frappe.get_all('DS Operation Proposal',filters={'model_run':run.name},pluck='name')
    executions=frappe.db.count('DS Execution Record',{'proposal':['in',proposal_names],'status':'Succeeded'}) if proposal_names else 0
    # One line closes the capability's audit: who used it, from where, how many times.
    events.record_safely(run.name,'finished',{'status':status,'answer_chars':len(answer) if isinstance(answer,str) else 0,
        'error':(error or '')[:500],'reason':reason,'model_calls':run.model_calls or 0,'provider_failures':provider_failures,
        'proposals':proposals,'executions':executions,'sources':len(sources),
        'source':source or '','capability_calls':_capability_calls(run.name)})
    values={'status':status,'answer':answer if requested_success else '',
        # BudgetExceeded too: without it the carefully worded reason above is dropped and the
        # person sees an empty error on the most expensive kind of run there is.
        'error':error if status in ('Failed','BudgetExceeded') else '',
        'capability_hash':'','provider_failures':provider_failures}
    # `FINISHED_STATUSES`, not a fourth hand-written tuple: a run stopped by its budget spent
    # real money before it stopped, and the month's bill reads the same fields as any other
    # finished run. Leaving it out would bill it as zero — a hole nothing can fill in later.
    from dsherp_bridge.usage import FINISHED_STATUSES
    if status in FINISHED_STATUSES:
        # 结算这次运行的真实用量。必须在 finished 事件之后：duration 由「首事件 → finished」算出。
        # 不包 try/except：读本站自己的事件表失败即 500，worker 侧有 finish_outcome_unknown 与租约
        # 清扫兜底；把 run 写成终态却不记用量，会造成永远补不回的计量空洞。
        values.update(_usage_of(run.name))
    if status=='NeedsInput':values['needs_input']=answer
    if requested_success:
        flagged=executions==0 and bool(re.search(r'(已|成功)(创建|提交|保存|完成|生成|录入|执行)',answer))
        values['answer_flagged']=1 if flagged else 0
        if flagged:
            events.record_safely(run.name,'unverified_completion_claim',{'proposals':proposals,'executions':executions})
    frappe.db.set_value('DS Model Run',run.name,values)
    # The executor is done acting for the member - finished, or paused for the person, in
    # which case the next message starts a new run with a grant of its own.
    grants.drop(run.name)
    return {'run_id':run.name,'status':status,'provider_failures':provider_failures}
