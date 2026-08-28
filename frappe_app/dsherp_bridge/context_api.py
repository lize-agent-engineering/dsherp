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
        authorize_sources(json.loads(run.sources or '[]'))
        messages.append({'id':run.name,'question':run.question,'answer':run.answer or '',
                         'error':run.error or '', 'status':run.status,'context':context,'domain':run.domain})
        if run.status in ('Queued','Running','Cancelling'):active=run.name
    from dsherp_bridge.operations import get_proposal
    proposals=[get_proposal(name) for name in frappe.get_all('DS Operation Proposal',
        filters={'conversation':doc.name},pluck='name',order_by='creation asc')]
    return {'id':doc.name,'title':doc.title,'messages':messages,'active_run':active,'proposals':proposals}


@frappe.whitelist(methods=['GET'])
def get_session(session_id):
    return _public(_conversation(session_id))


@frappe.whitelist(methods=['GET'])
def list_sessions():
    user=_user()
    names=frappe.get_all('DS Conversation',filters={'owner':user},pluck='name',order_by='modified desc',limit_page_length=20)
    result=[]
    for name in names:
        doc=_conversation(name)
        # Reauthorize provenance before exposing even the conversation title.
        try:_public(doc)
        except frappe.PermissionError:continue
        result.append({'id':name,'title':doc.title})
    return result


@frappe.whitelist(methods=['POST'])
def send_message(question, context, request_id, session_id=None, domain='query'):
    user=_user()
    if domain not in ('query','operation'):frappe.throw('未知业务领域')
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
