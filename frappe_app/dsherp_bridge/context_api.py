"""Current-user business Site conversations. No business writes or model execution here."""
import hashlib
import json
import uuid

import frappe


class ConversationConflict(frappe.ValidationError):
    http_status_code = 409


def _user():
    user = frappe.session.user
    if user in ('Guest', 'Administrator') or not frappe.db.get_value('User', user, 'enabled'):
        raise frappe.PermissionError('需要当前业务用户身份')
    return user


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _conflict(message):
    raise ConversationConflict(message)


def _context(value, check_version=True):
    if isinstance(value, str):
        value = json.loads(value)
    allowed = {'schema_version','route','page_type','reason','doctype','name','version','server_version','dirty','filters','selected','unsaved'}
    if not isinstance(value, dict) or set(value)-allowed or value.get('schema_version') != 1:
        frappe.throw('页面上下文参数无效')
    if len(_json(value).encode()) > 32768:
        frappe.throw('页面上下文超过预算')
    value = json.loads(_json(value))
    route = value.get('route')
    if not isinstance(route, list) or not all(isinstance(part,str) for part in route):
        frappe.throw('页面路由无效')
    kind = value.get('page_type')
    if kind == 'unknown':
        if set(value)-{'schema_version','route','page_type','reason'}:
            frappe.throw('未知页面不得夹带业务字段')
        return value
    doctype = value.get('doctype')
    if doctype not in ('Item','Customer','Sales Order'):
        raise frappe.PermissionError('当前查询领域不支持该业务对象')
    frappe.has_permission(doctype, 'read', throw=True)
    meta = frappe.get_meta(doctype)
    permitted = set(meta.get_permitted_fieldnames(user=frappe.session.user, permission_type='read'))
    if kind == 'form':
        name = value.get('name')
        if route != ['Form',doctype,name] or not isinstance(name,str):
            frappe.throw('表单路由与目标不一致')
        doc = frappe.get_doc(doctype,name)
        doc.check_permission('read')
        if check_version:
            # A stale page is useful context, not authorization for a write.
            # Keep its version and independently stamp current saved facts.
            # This overwrites any client-supplied server_version.
            value['server_version'] = str(doc.modified)
            if not value.get('version'):value['version'] = str(doc.modified)
        unsaved = value.get('unsaved',{})
        if not isinstance(unsaved,dict):
            frappe.throw('未保存字段无效')
        for field, content in unsaved.items():
            definition=meta.get_field(field)
            if definition and definition.fieldtype=='Table':
                if definition.permlevel and definition.permlevel not in doc.get_permlevel_access('read'):
                    raise frappe.PermissionError('无权提供该子表：'+field)
                if not isinstance(content,list):frappe.throw('子表必须明确选择行和列')
                child_meta=frappe.get_meta(definition.options)
                child_fields=set(child_meta.get_permitted_fieldnames(parenttype=doctype,user=frappe.session.user,permission_type='read'))
                saved_names={row.name for row in doc.get(field)}
                seen=set()
                for row in content:
                    if not isinstance(row,dict) or not isinstance(row.get('name'),str) or len(row)<2:
                        frappe.throw('子表行与列无效')
                    row_name=row['name']
                    if row_name in seen or (row_name not in saved_names and not row_name.startswith('new-')):
                        frappe.throw('子表行不属于当前记录')
                    seen.add(row_name)
                    for column,cell in row.items():
                        if column=='name':continue
                        if column not in child_fields:raise frappe.PermissionError('无权提供该子表字段：'+column)
                        if isinstance(cell,(dict,list)):frappe.throw('子表列必须是标量')
                continue
            if field not in permitted:
                raise frappe.PermissionError('无权提供该字段：'+field)
            if not meta.get_field(field) or isinstance(content,(dict,list)):
                frappe.throw('当前未保存字段须为明确选择的标量字段')
    elif kind == 'list':
        if 'server_version' in value:frappe.throw('列表上下文不包含单据服务器版本')
        if len(route)<2 or route[:2] != ['List',doctype]:
            frappe.throw('列表路由无效')
        filters = value.get('filters',[])
        selected = value.get('selected',[])
        if not isinstance(filters,list) or not isinstance(selected,list):
            frappe.throw('列表上下文无效')
        for item in filters:
            if not isinstance(item,list) or len(item)!=4 or item[0]!=doctype or item[1] not in permitted:
                raise frappe.PermissionError('筛选字段不在当前读取权限内')
        for name in selected:
            if not isinstance(name,str):frappe.throw('选中记录名称无效')
            frappe.get_doc(doctype,name).check_permission('read')
        if 'unsaved' in value:frappe.throw('列表不能携带未保存字段')
    else:
        frappe.throw('未知页面类型')
    return value


def _conversation(name):
    doc = frappe.get_doc('DS Conversation',name)
    if doc.owner != _user():
        raise frappe.PermissionError('会话属于其他用户')
    return doc


def _public(doc):
    runs = frappe.get_all('DS Model Run',filters={'conversation':doc.name},fields=['*'],order_by='creation asc')
    messages=[]
    active=None
    for run in runs:
        context=_context(run.page_context,check_version=False)
        from dsherp_bridge.context_execution import authorize_sources
        # Reauthorized on every read; exposing them lets the transcript show the
        # actual ERP reads in the run that made them, instead of a flat list.
        sources=json.loads(run.sources or '[]')
        authorize_sources(sources)
        messages.append({'id':run.name,'question':run.question,'answer':run.answer or '',
                         'error':run.error or '', 'status':run.status,'context':context,'domain':run.domain,
                         'sources':sources})
        if run.status in ('Queued','Running','Cancelling'):active=run.name
    from dsherp_bridge.operations import get_proposal
    proposals=[get_proposal(name) for name in frappe.get_all('DS Operation Proposal',
        filters={'conversation':doc.name},pluck='name',order_by='creation asc')]
    from dsherp_bridge.configuration_execution import get_confirmation
    bundles=frappe.get_all('DS Configuration Bundle',filters={'conversation':doc.name},pluck='name')
    configurations=[get_confirmation(name) for name in frappe.get_all('DS Configuration Confirmation',
        filters={'bundle':['in',bundles]},pluck='name',order_by='creation asc')] if bundles else []
    from dsherp_bridge.configuration import get_bundle
    return {'id':doc.name,'title':doc.title,'archived':bool(doc.archived),'messages':messages,'active_run':active,'proposals':proposals,
        'configuration_confirmations':configurations,'configuration_bundles':[get_bundle(name) for name in bundles]}


def _summary(doc):
    # Reauthorize provenance before exposing even a title or timestamp.
    _public(doc)
    return {'id':doc.name,'title':doc.title,'archived':bool(doc.archived),
            'modified':str(doc.modified),'archived_at':str(doc.archived_at) if doc.archived_at else None}


def _summaries(*, archived, query='', limit=None, offset=0):
    filters={'owner':_user(),'archived':1 if archived else 0}
    if query:
        filters['title']=['like',f'%{query}%']
    names=frappe.get_all('DS Conversation',filters=filters,pluck='name',order_by='modified desc')
    visible=[]
    for name in names:
        try:visible.append(_summary(_conversation(name)))
        except frappe.PermissionError:continue
    page=visible[offset:offset+limit] if limit is not None else visible[offset:]
    return page,len(visible)>offset+len(page)


@frappe.whitelist(methods=['GET'])
def get_session(session_id):
    return _public(_conversation(session_id))


@frappe.whitelist(methods=['GET'])
def list_sessions():
    items,has_more=_summaries(archived=False,limit=10)
    return {'items':items,'has_more':has_more}


@frappe.whitelist(methods=['GET'])
def search_sessions(query='',page=1,archived=0):
    _user()
    if not isinstance(query,str) or len(query)>100:frappe.throw('会话搜索内容无效')
    try:page=int(page);archived=int(archived)
    except (TypeError,ValueError):frappe.throw('会话分页参数无效')
    if page<1 or archived not in (0,1):frappe.throw('会话分页参数无效')
    items,has_more=_summaries(archived=bool(archived),query=query.strip(),limit=20,offset=(page-1)*20)
    return {'items':items,'page':page,'has_more':has_more}


@frappe.whitelist(methods=['POST'])
def rename_session(session_id,title):
    if not isinstance(title,str) or not title.strip() or len(title.strip())>100:
        frappe.throw('会话标题必须包含 1–100 个字符')
    doc=_conversation(session_id)
    doc.title=title.strip()
    doc.save(ignore_permissions=True)
    return _summary(doc)


@frappe.whitelist(methods=['POST'])
def archive_session(session_id):
    doc=_conversation(session_id)
    current=_public(doc)
    if current['active_run']:_conflict('活动运行结束前不能归档会话')
    if not doc.archived:
        from frappe.utils import now_datetime
        doc.archived=1
        doc.archived_at=now_datetime()
        doc.save(ignore_permissions=True)
    return _summary(doc)


@frappe.whitelist(methods=['POST'])
def restore_session(session_id):
    doc=_conversation(session_id)
    if doc.archived:
        doc.archived=0
        doc.archived_at=None
        doc.save(ignore_permissions=True)
    return _summary(doc)


def _page(value):
    try:value=int(value)
    except (TypeError,ValueError):frappe.throw('列表分页参数无效')
    if value<1:frappe.throw('列表分页参数无效')
    return value


def _owned_conversations():
    return frappe.get_all('DS Conversation',filters={'owner':_user()},pluck='name')


def _paged(items,page):
    page=_page(page);start=(page-1)*20
    return {'items':items[start:start+20],'page':page,'has_more':len(items)>start+20}


@frappe.whitelist(methods=['GET'])
def list_pending(page=1):
    conversations=_owned_conversations()
    if not conversations:return _paged([],page)
    items=[]
    for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':['in',conversations],'status':'Pending'},fields=['name','conversation','status','expires_at','payload','modified'],order_by='modified desc'):
        payload=json.loads(proposal.payload)
        items.append({'id':proposal.name,'session_id':proposal.conversation,'kind':'operation',
            'title':f"{payload.get('doctype','业务对象')} · {payload.get('action','待确认操作')}",
            'status':proposal.status,'expires_at':str(proposal.expires_at),'modified':str(proposal.modified)})
    bundles=frappe.get_all('DS Configuration Bundle',filters={'conversation':['in',conversations]},fields=['name','conversation'])
    bundle_sessions={item.name:item.conversation for item in bundles}
    if bundle_sessions:
        for confirmation in frappe.get_all('DS Configuration Confirmation',filters={'bundle':['in',list(bundle_sessions)],'status':'Pending'},fields=['name','bundle','status','expires_at','modified'],order_by='modified desc'):
            items.append({'id':confirmation.name,'session_id':bundle_sessions[confirmation.bundle],'kind':'configuration',
                'title':'应用配置确认','status':confirmation.status,'expires_at':str(confirmation.expires_at),'modified':str(confirmation.modified)})
    items.sort(key=lambda item:item['modified'],reverse=True)
    return _paged(items,page)


@frappe.whitelist(methods=['GET'])
def list_execution_records(page=1):
    conversations=_owned_conversations()
    if not conversations:return _paged([],page)
    proposals=frappe.get_all('DS Operation Proposal',filters={'conversation':['in',conversations]},fields=['name','conversation'])
    proposal_sessions={item.name:item.conversation for item in proposals}
    items=[]
    if proposal_sessions:
        for record in frappe.get_all('DS Execution Record',filters={'proposal':['in',list(proposal_sessions)]},fields=['name','proposal','status','modified'],order_by='modified desc'):
            items.append({'id':record.name,'session_id':proposal_sessions[record.proposal],'kind':'operation','title':'业务执行',
                'status':record.status,'modified':str(record.modified)})
    bundles=frappe.get_all('DS Configuration Bundle',filters={'conversation':['in',conversations]},fields=['name','conversation'])
    bundle_sessions={item.name:item.conversation for item in bundles}
    confirmations=frappe.get_all('DS Configuration Confirmation',filters={'bundle':['in',list(bundle_sessions)]},fields=['name','bundle']) if bundle_sessions else []
    confirmation_sessions={item.name:bundle_sessions[item.bundle] for item in confirmations}
    if confirmation_sessions:
        for record in frappe.get_all('DS Configuration Execution',filters={'confirmation':['in',list(confirmation_sessions)]},fields=['name','confirmation','status','modified'],order_by='modified desc'):
            items.append({'id':record.name,'session_id':confirmation_sessions[record.confirmation],'kind':'configuration','title':'配置执行',
                'status':record.status,'modified':str(record.modified)})
    items.sort(key=lambda item:item['modified'],reverse=True)
    return _paged(items,page)


@frappe.whitelist(methods=['GET'])
def list_configuration_records(page=1):
    conversations=_owned_conversations()
    if not conversations:return _paged([],page)
    items=[]
    for bundle in frappe.get_all('DS Configuration Bundle',filters={'conversation':['in',conversations]},fields=['name','conversation','baseline','modified'],order_by='modified desc'):
        latest=frappe.get_all('DS Configuration Confirmation',filters={'bundle':bundle.name},fields=['status'],order_by='modified desc',limit_page_length=1)
        items.append({'id':bundle.name,'session_id':bundle.conversation,'kind':'configuration','title':'应用配置包',
            'summary':f'基线 {bundle.baseline}','status':latest[0].status if latest else 'Draft','modified':str(bundle.modified)})
    return _paged(items,page)


@frappe.whitelist(methods=['POST'])
def send_message(question, context, request_id, session_id=None, domain='query'):
    user=_user()
    if domain not in ('query','operation','configuration'):frappe.throw('未知业务领域')
    grant=frappe.session.data.get('dsherp_platform_grant')
    if grant:
        from dsherp_bridge.sso import validate_grant
        validate_grant(grant,user)
    if not isinstance(question,str) or not question.strip() or len(question)>8000:
        frappe.throw('问题必须包含 1–8000 个字符')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
        frappe.throw('请求标识无效')
    raw_context=json.loads(context) if isinstance(context,str) else context
    digest=hashlib.sha256(_json([session_id,question.strip(),raw_context,domain]).encode()).hexdigest()
    run_id=hashlib.sha256((user+'\0'+request_id).encode()).hexdigest()
    # The existing native User row serializes submissions without another lock service.
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    _user()
    if frappe.db.exists('DS Model Run',run_id):
        run=frappe.get_doc('DS Model Run',run_id)
        if run.owner!=user:raise frappe.PermissionError('运行归属无效')
        if run.request_digest!=digest:frappe.throw('请求标识已用于其他内容')
        return _public(_conversation(run.conversation))
    snapshot=_context(raw_context)
    if session_id:
        doc=_conversation(session_id)
        current=_public(doc)
        if doc.archived:_conflict('归档会话为只读，请先恢复')
        if current['active_run']:_conflict('本会话仍有运行，请等待或取消')
    else:
        doc=frappe.get_doc({'doctype':'DS Conversation','title':question.strip()[:100],
                            'runtime_session':uuid.uuid4().hex}).insert(ignore_permissions=True)
    frappe.get_doc({'doctype':'DS Model Run','name':run_id,'conversation':doc.name,
        'platform_grant':grant,'domain':domain,
        'request_id':request_id,'request_digest':digest,'question':question.strip(),
        'page_context':_json(snapshot),'status':'Queued','sources':'[]'}).insert(ignore_permissions=True,set_name=run_id)
    return _public(doc)


@frappe.whitelist(methods=['POST'])
def cancel_run(session_id,run_id,request_id):
    user=_user()
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:frappe.throw('取消请求标识无效')
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    doc=_conversation(session_id)
    run=frappe.get_doc('DS Model Run',run_id,for_update=True)
    if run.conversation!=doc.name or run.owner!=user:raise frappe.PermissionError('运行不属于此会话')
    if run.status in ('Queued','Running'):
        run.status='Cancelled' if run.status=='Queued' else 'Cancelling'
        run.cancel_request_id=request_id
        run.save(ignore_permissions=True)
    return _public(doc)
