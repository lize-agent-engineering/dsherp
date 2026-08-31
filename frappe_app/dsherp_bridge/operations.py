"""Business operation proposals. Native metadata/permissions remain authoritative."""
import hashlib
import json
import frappe
from frappe.utils import add_to_date, now_datetime, get_system_timezone
from zoneinfo import ZoneInfo
from dsherp_bridge.context_api import _conversation, _user, _json


_TRANSIENT_IDENTITY_FIELDS={'name','owner','creation','modified','modified_by','parent','parentfield','parenttype',
                            '_user_tags','_comments','_assign','_liked_by'}


def _authorization_revision(user, grant=None):
    from dsherp_bridge.context_permissions import revision
    identity = None
    if grant:
        from dsherp_bridge.sso import validate_grant
        identity = validate_grant(grant, user)
    return hashlib.sha256(_json([revision(user), identity]).encode()).hexdigest()


@frappe.whitelist(methods=['POST'])
def confirm(proposal_id, digest, request_id):
    user = _user()
    grant = frappe.session.data.get('dsherp_platform_grant')
    if grant:
        from dsherp_bridge.sso import validate_grant
        validate_grant(grant, user)
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        frappe.throw('请求标识无效')
    # End the authentication snapshot before taking locks. Metadata loading can
    # open another snapshot, so the deduplication lookup below is also a locking
    # (current) read rather than a repeatable-read lookup.
    frappe.db.rollback()
    proposal = frappe.get_doc('DS Operation Proposal', proposal_id, for_update=True)
    public=get_proposal(proposal_id)
    if not isinstance(digest, str) or digest != proposal.digest:
        frappe.throw('确认内容不匹配，请重新核对提案')
    existing = frappe.db.get_value('DS Execution Record', {'proposal': proposal_id}, 'name', for_update=True)
    if existing:
        return public['execution']
    if proposal.model_run and frappe.db.get_value('DS Model Run',proposal.model_run,'status',for_update=True)!='Succeeded':
        frappe.throw('提案生成运行尚未成功结束，请核实运行记录')
    if proposal.status != 'Pending':
        frappe.throw('提案已结束，请核对执行记录')
    execution = frappe.get_doc({'doctype': 'DS Execution Record', 'proposal': proposal_id,
        'request_id': request_id, 'status': 'Running'}).insert(ignore_permissions=True)
    # Durable intent precedes any business method. An interrupted invocation is
    # never retried automatically, even with a different HTTP request identifier.
    frappe.db.commit()
    try:
        proposal = frappe.get_doc('DS Operation Proposal', proposal_id, for_update=True)
        _user()
        if grant:
            validate_grant(grant, user)
        if proposal.expires_at <= now_datetime():
            frappe.throw('确认已过期，请重新提出操作')
        payload = json.loads(proposal.payload)
        if payload['authorization_revision'] != _authorization_revision(user, grant):
            frappe.throw('权限或企业成员关系已变化，请重新提出操作')
        values = {change['field']: change['after'] for change in payload['changes']}
        if payload['action']=='make':
            source=frappe.get_doc(payload['source_doctype'],payload['source_name'],for_update=True)
            source.check_permission('read')
            if str(source.modified)!=payload['source_version']:
                frappe.throw('来源记录版本或 mapped 结果已变化，请重新提出操作')
            from dsherp_bridge.doctype_policy import resolve_route
            route=resolve_route(payload['source_doctype'],payload['route'])
            if any(route[key]!=payload[value] for key,value in (
                    ('route_name','route'),('method_path','method_path'),('target_doctype','target_doctype'))):
                frappe.throw('make 路由已变化，请重新提出操作')
            if _mapped_target(source.name,route,frozen=payload['target'])!=payload['target']:
                frappe.throw('来源记录版本或 mapped 结果已变化，请重新提出操作')
            doc=frappe.get_doc(json.loads(_json(payload['target'])))
            doc.insert()
        elif payload['action'] in ('update','fill'):
            doc = frappe.get_doc(payload['doctype'], payload['name'], for_update=True)
            if str(doc.modified) != payload['version']:
                frappe.throw('记录版本已变化，请重新提出操作')
            frozen=[{key:value for key,value in change.items() if key!='form_before'} for change in payload['changes']]
            if update_diff(doc, values,include_unchanged=payload['action']=='fill',action=payload['action']) != frozen:
                frappe.throw('字段或权限已变化，请重新提出操作')
            if payload['action']=='fill':
                result={'status':'Authorized','target':'browser-draft','doctype':doc.doctype,'name':doc.name,'version':str(doc.modified),'values':values}
            else:
                _apply_values(doc,values)
                doc.save()
        elif payload['action']=='create':
            if str(frappe.get_meta(payload['doctype']).modified)!=payload['version']:
                frappe.throw('业务结构已变化，请重新提出操作')
            if create_diff(payload['doctype'],values)!=payload['changes']:
                frappe.throw('字段或权限已变化，请重新提出操作')
            doc=frappe.get_doc({'doctype':payload['doctype']})
            _apply_values(doc,values)
            doc.insert()  # Native naming, defaults, validation and permissions.
        elif payload['action'] in ('submit','cancel'):
            doc=frappe.get_doc(payload['doctype'],payload['name'],for_update=True)
            if str(doc.modified)!=payload['version']:
                frappe.throw('记录版本已变化，请重新提出操作')
            if action_diff(doc,payload['action'])!=payload['changes']:
                frappe.throw('单据状态已变化，请重新提出操作')
            if payload['action']=='submit':doc.submit()
            else:doc.cancel()
        else:
            frappe.throw('不支持的业务动作')
        if payload['action']!='fill':
            saved = frappe.get_doc(doc.doctype, doc.name)
            saved.check_permission('read')
            if payload['action']=='make':
                actual=_public_make_target(saved,_canonicalize_mapped_target(saved),user)
            else:
                actual = _actual_values(saved,values)
                if actual != values:
                    frappe.throw('保存后字段与确认内容不一致，已停止执行')
            result = {'status': 'Succeeded', 'doctype': doc.doctype, 'name': doc.name,
                'version': str(saved.modified), 'values': actual}
    except Exception as error:
        # Native business validation failures are recorded, not HTTP-success
        # claims. Unexpected failures remain unknown; no blind replay.
        frappe.db.rollback()
        known = isinstance(error, (frappe.ValidationError, frappe.PermissionError))
        result = {'status': 'Failed' if known else 'Unknown',
            'error': ('当前用户无权执行该操作' if isinstance(error, frappe.PermissionError)
                      else str(error) if known else '执行结果尚未核实，请查看业务记录；不会自动重试')}
    execution = frappe.get_doc('DS Execution Record', execution.name, for_update=True)
    execution.status = result['status']
    execution.result = _json(result)
    execution.save(ignore_permissions=True)
    proposal = frappe.get_doc('DS Operation Proposal', proposal_id)
    proposal.status = result['status']
    proposal.save(ignore_permissions=True)
    # The successful native save and its evidence commit in one DB transaction.
    # A commit transport error propagates; the next request reads, never replays.
    frappe.db.commit()
    return _execution_result(execution)


def _execution_result(execution):
    if execution.status == 'Running':
        return {'execution_id': execution.name, 'status': 'Unknown',
            'error': '执行已开始，结果尚未核实；不会重复执行'}
    result = json.loads(execution.result)
    return {**result, 'execution_id': execution.name}


@frappe.whitelist(methods=['GET'])
def verify_execution(proposal_id):
    proposal=get_proposal(proposal_id)
    execution=proposal.get('execution')
    name=proposal['name']
    if proposal['action'] in ('create','make'):name=execution.get('name') if execution else None
    result={'execution':execution,'observed':None,'matches_proposal':None,
            'note':'当前字段一致也不能单独证明本次执行成功；不会重试操作或改写执行记录。'}
    if not name:
        result['note']='未记录确定的业务记录名称，不能据列表猜测创建成功；不会自动重试。'
        return result
    doc=frappe.get_doc(proposal['doctype'],name)
    doc.check_permission('read')
    if proposal['action']=='make':
        values=_public_make_target(doc,_canonicalize_mapped_target(doc),frappe.session.user)
        result['observed']={'doctype':doc.doctype,'name':doc.name,'version':str(doc.modified),'values':values}
        result['matches_proposal']=bool(execution and execution.get('status')=='Succeeded' and values==execution.get('values'))
        return result
    values={};matches=True
    for change in proposal['changes']:
        field=change['field'];expected=change['after']
        if isinstance(expected,list):
            rows=doc.get(field) or []
            columns={'name'}|{key for row in expected for key in row}
            values[field]=[{key:row.get(key) for key in sorted(columns)} for row in rows]
            matches=matches and len(rows)==len(expected) and all(
                json.loads(_json({key:row.get(key) for key in requested}))==requested
                for row,requested in zip(rows,expected))
        else:
            values[field]=doc.get(field)
            matches=matches and json.loads(_json(values[field]))==expected
    result['observed']={'doctype':doc.doctype,'name':doc.name,'version':str(doc.modified),'values':json.loads(_json(values))}
    result['matches_proposal']=matches
    if proposal['action']=='fill':result['note']='这里只核实服务器已保存内容，不能证明浏览器草稿是否已填入；不会重新填入。'
    return result


def propose_update(session_id, doctype, name, values, version, grant=None, model_run=None):
    doc = frappe.get_doc(doctype, name)
    changes = update_diff(doc, values)
    if str(doc.modified) != version:
        frappe.throw('记录版本已变化，请重新提出操作')
    return _propose(session_id,doctype,name,'update',changes,version,grant,model_run)


def propose_create(session_id, doctype, values, version, grant=None, model_run=None):
    _user()
    changes=create_diff(doctype,values)
    if str(frappe.get_meta(doctype).modified)!=version:
        frappe.throw('业务结构已变化，请重新读取后提出操作')
    return _propose(session_id,doctype,None,'create',changes,version,grant,model_run)


def _canonicalize_mapped_target(target):
    as_dict=getattr(target,'as_dict',None)
    if callable(as_dict):target=as_dict()
    if not isinstance(target,dict):frappe.throw('mapped 方法未返回目标单据')
    def clean(value):
        if isinstance(value,dict):
            return {key:clean(item) for key,item in value.items()
                    if key not in _TRANSIENT_IDENTITY_FIELDS and not key.startswith('__')}
        if isinstance(value,list):return [clean(item) for item in value]
        return value
    return json.loads(_json(clean(target)))


def _mapped_target(source_name,route,frozen=None):
    from dsherp_bridge.make_adapters import get_make_adapter
    adapter=get_make_adapter(route.get('source_doctype'),route.get('route_name'),
                             route.get('method_path'),route.get('target_doctype'))
    target=_canonicalize_mapped_target(adapter(source_name))
    if target.get('doctype')!=route['target_doctype']:
        frappe.throw('make 路由目标与 mapped 结果不一致')
    # ERPNext mapped stock documents default posting_time from the current
    # clock even when set_posting_time is false. Preserve that complete frozen
    # field on confirmation so elapsed milliseconds alone are not business drift.
    if (frozen and not target.get('set_posting_time') and 'posting_time' in target
        and frozen.get('doctype')==target.get('doctype') and 'posting_time' in frozen):
        target['posting_time']=frozen['posting_time']
    return target


def _make_changes(target):
    meta=frappe.get_meta(target['doctype'])
    return [{'field':field,'label':meta.get_field(field).label,'before':None,'after':value}
            for field,value in target.items() if meta.get_field(field)]


def _public_make_target(doc,target,user):
    doc.check_permission('read')
    readable=set(doc.meta.get_permitted_fieldnames(user=user,permission_type='read'))
    levels=doc.get_permlevel_access('read')
    readable.update(field.fieldname for field in doc.meta.fields if field.fieldtype=='Table' and field.permlevel in levels)
    public={key:value for key,value in target.items() if key in ('doctype','docstatus','idx')}
    for field,value in target.items():
        if field in ('doctype','docstatus','idx'):continue
        definition=doc.meta.get_field(field)
        if not definition or field not in readable:continue
        if definition.fieldtype!='Table':
            public[field]=value;continue
        child_readable=set(frappe.get_meta(definition.options).get_permitted_fieldnames(
            parenttype=doc.doctype,user=user,permission_type='read'))|{'doctype','docstatus','idx'}
        public[field]=[{key:item for key,item in row.items() if key in child_readable} for row in value]
    return public


def propose_make(session_id,source_doctype,source_name,source_version,route,grant=None,model_run=None):
    user=_user();conversation=_conversation(session_id)
    if model_run:
        origin=frappe.get_doc('DS Model Run',model_run)
        if origin.owner!=user or origin.conversation!=conversation.name or origin.status!='Running':
            raise frappe.PermissionError('提案运行归属或状态不匹配')
    source=frappe.get_doc(source_doctype,source_name);source.check_permission('read')
    if str(source.modified)!=source_version:frappe.throw('来源记录版本已变化，请重新读取后提出操作')
    from dsherp_bridge.doctype_policy import resolve_route
    resolved=resolve_route(source_doctype,route)
    target=_mapped_target(source_name,resolved)
    target_doc=frappe.get_doc(target);target_doc.check_permission('create');target_doc.check_permission('read')
    expires=add_to_date(now_datetime(),minutes=10)
    authorization=_authorization_revision(user,grant or frappe.session.data.get('dsherp_platform_grant'))
    payload=_json({'site':frappe.local.site,'actor':user,'action':'make','proposal_type':'make',
        'authorization_revision':authorization,'doctype':resolved['target_doctype'],'name':None,'version':source_version,
        'source_doctype':source_doctype,'source_name':source_name,'source_version':source_version,
        'route':resolved['route_name'],'method_path':resolved['method_path'],'target_doctype':resolved['target_doctype'],
        'target':target,'changes':_make_changes(target)})
    digest=hashlib.sha256(_json([conversation.name,payload,str(expires)]).encode()).hexdigest()
    proposal=frappe.get_doc({'doctype':'DS Operation Proposal','conversation':conversation.name,'model_run':model_run,
        'payload':payload,'digest':digest,'expires_at':expires,'status':'Pending'}).insert(ignore_permissions=True)
    return get_proposal(proposal.name)


def propose_fill(session_id,doctype,name,values,version,grant=None,model_run=None):
    doc=frappe.get_doc(doctype,name)
    changes=update_diff(doc,values,include_unchanged=True,action='fill')
    for change in changes:
        if isinstance(change['after'],list):
            if [row.get('name') for row in change['after']]!=[row['name'] for row in change['before']]:
                frappe.throw('填入保留现有子表行及顺序；增删行请另提业务修改')
    if str(doc.modified)!=version:frappe.throw('记录版本已变化，请重新提出建议')
    if model_run:
        context=json.loads(frappe.get_doc('DS Model Run',model_run).page_context)
        if context.get('page_type')!='form' or context.get('doctype')!=doctype or context.get('name')!=name:
            frappe.throw('填入目标必须是本次消息的当前表单')
        for change in changes:
            if change['field'] in context.get('unsaved',{}):
                change['form_before']=context['unsaved'][change['field']]
    return _propose(session_id,doctype,name,'fill',changes,version,grant,model_run)


def propose_action(session_id,doctype,name,action,version,grant=None,model_run=None):
    _user()
    doc=frappe.get_doc(doctype,name)
    changes=action_diff(doc,action)
    if str(doc.modified)!=version:frappe.throw('记录版本已变化，请重新提出操作')
    return _propose(session_id,doctype,name,action,changes,version,grant,model_run)


def action_diff(doc,action):
    if action not in ('submit','cancel'):
        raise frappe.PermissionError('当前领域不支持该状态操作')
    from dsherp_bridge.doctype_policy import require_action
    require_action(doc.doctype, action)
    doc.check_permission('read')
    doc.check_permission(action)
    before,after=(0,1) if action=='submit' else (1,2)
    if doc.docstatus!=before:frappe.throw('单据状态不支持该操作')
    return [{'field':'docstatus','label':'单据状态','before':before,'after':after}]


def _propose(session_id,doctype,name,action,changes,version,grant,model_run):
    user = _user()
    conversation = _conversation(session_id)
    if model_run:
        origin=frappe.get_doc('DS Model Run',model_run)
        if origin.owner!=user or origin.conversation!=conversation.name or origin.status!='Running':
            raise frappe.PermissionError('提案运行归属或状态不匹配')
    expires = add_to_date(now_datetime(), minutes=10)
    authorization = _authorization_revision(user, grant or frappe.session.data.get('dsherp_platform_grant'))
    payload = _json({'site': frappe.local.site, 'actor': user, 'action': action, 'authorization_revision': authorization,
        'doctype': doctype, 'name': name, 'version': version, 'changes': changes})
    digest = hashlib.sha256(_json([conversation.name, payload, str(expires)]).encode()).hexdigest()
    proposal = frappe.get_doc({'doctype': 'DS Operation Proposal', 'conversation': conversation.name,'model_run':model_run,
        'payload': payload, 'digest': digest, 'expires_at': expires, 'status': 'Pending'}).insert(ignore_permissions=True)
    return get_proposal(proposal.name)


@frappe.whitelist(methods=['GET'])
def get_proposal(proposal_id):
    user = _user()
    proposal = frappe.get_doc('DS Operation Proposal', proposal_id)
    if proposal.owner != user:
        raise frappe.PermissionError('操作提案属于其他用户')
    _conversation(proposal.conversation)
    payload = json.loads(proposal.payload)
    if payload['site'] != frappe.local.site or payload['actor'] != user:
        raise frappe.PermissionError('操作提案身份不匹配')
    execution = frappe.db.get_value('DS Execution Record', {'proposal': proposal_id}, 'name')
    outcome=_execution_result(frappe.get_doc('DS Execution Record',execution)) if execution else None
    if payload['action']=='make' and not (outcome and outcome['status']=='Succeeded'):
        doc=frappe.get_doc(payload['target'])
    elif payload['action']=='create' and not (outcome and outcome['status']=='Succeeded'):
        doc=frappe.get_doc({'doctype':payload['doctype'],**json.loads(_json({change['field']:change['after'] for change in payload['changes']}))})
    else:
        doc = frappe.get_doc(payload['doctype'], outcome['name'] if payload['action'] in ('create','make') else payload['name'])
    doc.check_permission('read')
    if payload['action']=='make':
        public_target=_public_make_target(doc,payload['target'],user)
        if outcome and outcome['status']=='Succeeded':
            outcome={**outcome,'values':_public_make_target(doc,outcome.get('values',{}),user)}
    # Recheck access before exposing saved before/after values, even after success.
    readable = set(doc.meta.get_permitted_fieldnames(user=user, permission_type='read'))
    read_levels=doc.get_permlevel_access('read')
    readable.update(field.fieldname for field in doc.meta.fields if field.fieldtype=='Table' and field.permlevel in read_levels)
    if payload['action'] in ('submit','cancel'):readable.add('docstatus')
    if payload['action']!='make' and any(change['field'] not in readable for change in payload['changes']):
        raise frappe.PermissionError('无权读取提案字段')
    for change in ([] if payload['action']=='make' else payload['changes']):
        definition=doc.meta.get_field(change['field'])
        if definition and definition.fieldtype=='Table':
            child_readable=set(frappe.get_meta(definition.options).get_permitted_fieldnames(parenttype=doc.doctype,user=user,permission_type='read'))|{'name','doctype','docstatus','idx'}
            if any(key not in child_readable for rows in (change['before'],change['after'],change.get('form_before')) if rows for row in rows for key in row):
                raise frappe.PermissionError('无权读取提案明细字段')
    public_payload={**payload}
    if payload['action']=='make':
        public_payload['target']=public_target
        public_payload['changes']=_make_changes(public_target)
    result = {**public_payload, 'id': proposal.name, 'digest': proposal.digest, 'model_run': proposal.model_run,
        'expires_at': proposal.expires_at.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(), 'status': proposal.status}
    result['execution_ready']=not proposal.model_run or frappe.db.get_value('DS Model Run',proposal.model_run,'status')=='Succeeded'
    if outcome:
        result['execution'] = outcome
    return result


def update_diff(doc, values,include_unchanged=False,action='update'):
    """Validate explicit scalar edits without mutating the document or database."""
    from dsherp_bridge.doctype_policy import require_action
    require_action(doc.doctype, action)
    doc.check_permission('read')
    doc.check_permission('write')
    if doc.docstatus != 0:
        frappe.throw('当前操作只支持草稿记录')
    return _field_changes(doc,values,include_unchanged=include_unchanged)


def create_diff(doctype,values):
    from dsherp_bridge.doctype_policy import require_action
    require_action(doctype, 'create')
    doc=frappe.get_doc({'doctype':doctype})
    doc.check_permission('create')
    doc.check_permission('read')
    changes=_field_changes(doc,values,creating=True)
    _apply_values(doc,values)
    doc.check_permission('create')
    return changes


def _field_changes(doc,values,creating=False,include_unchanged=False):
    if not isinstance(values, dict) or not values:
        frappe.throw('必须提供明确的修改字段')
    permitted = set(doc.meta.get_permitted_fieldnames(user=frappe.session.user, permission_type='write'))
    levels = doc.get_permlevel_access('write')
    permitted.update(field.fieldname for field in doc.meta.fields if field.fieldtype=='Table' and field.permlevel in levels)
    changes = []
    for field, value in values.items():
        definition = doc.meta.get_field(field)
        if not definition or field in ('name', 'docstatus', 'owner', 'creation', 'modified', 'modified_by'):
            frappe.throw('不能修改系统字段或不存在的字段：' + field)
        if (field not in permitted and not (creating and not definition.permlevel)) or (definition.permlevel and definition.permlevel not in levels):
            raise frappe.PermissionError('无权修改该字段：' + field)
        if definition.fieldtype=='Table' and not definition.read_only:
            changes.append(_table_change(doc,definition,value,creating))
            continue
        if definition.read_only or definition.fieldtype not in (
            'Data', 'Small Text', 'Text', 'Long Text', 'Text Editor', 'Select',
            'Link', 'Dynamic Link', 'Int', 'Float', 'Currency', 'Percent',
            'Check', 'Date', 'Datetime', 'Time', 'Duration', 'Autocomplete',
        ):
            frappe.throw('该字段不支持直接修改：' + field)
        if isinstance(value, (dict, list)):
            frappe.throw('修改值必须是标量')
        before=json.loads(_json(doc.get(field)))
        if creating or include_unchanged or before != value:
            changes.append({'field': field, 'label': definition.label, 'before': None if creating else before, 'after': value})
    if not changes:
        frappe.throw('没有实际修改')
    return json.loads(_json(changes))


def _table_change(doc,definition,rows,creating):
    # The list freezes the resulting row set and order. Named rows are patches;
    # omitted rows are removals, unnamed rows are additions.
    if not isinstance(rows,list) or not rows or len(rows)>100:
        frappe.throw('业务明细必须包含1–100行')
    child=frappe.get_meta(definition.options)
    writable=set(child.get_permitted_fieldnames(parenttype=doc.doctype,permission_type='write'))
    readable=set(child.get_permitted_fieldnames(parenttype=doc.doctype,permission_type='read'))
    saved={row.name:row for row in doc.get(definition.fieldname) or []}
    seen=set();columns=set()
    for row in rows:
        if not isinstance(row,dict) or not row:frappe.throw('业务明细行无效')
        name=row.get('name')
        if name is not None:
            if creating or not isinstance(name,str) or name not in saved or name in seen:
                frappe.throw('明细行不属于当前订单或重复')
            seen.add(name)
        for key,value in row.items():
            if key=='name':continue
            field=child.get_field(key)
            if (not field or key not in writable or key not in readable or field.read_only
                or field.fieldtype in ('Table','Table MultiSelect','Password','Attach','Attach Image')
                or isinstance(value,(dict,list))):
                raise frappe.PermissionError('无权直接修改该明细字段：'+key)
            columns.add(key)
    columns.update(field.fieldname for field in child.fields if field.reqd and field.fieldname in readable)
    before=[] if creating else [{'name':row.name,**{key:row.get(key) for key in sorted(columns)}} for row in saved.values()]
    return {'field':definition.fieldname,'label':definition.label,'before':None if creating else before,'after':rows,
            'columns':{key:child.get_field(key).label for key in sorted(columns)}}


def _apply_values(doc,values):
    for field,value in values.items():
        definition=doc.meta.get_field(field)
        if definition and definition.fieldtype=='Table':
            saved={row.name:row for row in doc.get(field) or []}
            merged=[{**saved[row['name']].as_dict(),**row} if row.get('name') in saved else dict(row) for row in value]
            # Native form payloads use JSON dates consistently; mixing loaded
            # date objects with new-row strings breaks ERPNext date comparisons.
            doc.set(field,json.loads(_json(merged)))
        else:doc.set(field,value)


def _actual_values(doc,values):
    actual={}
    for field,expected in values.items():
        definition=doc.meta.get_field(field)
        if definition and definition.fieldtype=='Table':
            rows=doc.get(field) or []
            if len(rows)!=len(expected):frappe.throw('保存后的明细行数与确认内容不一致')
            actual[field]=[{key:row.get(key) for key in requested} for row,requested in zip(rows,expected)]
        else:actual[field]=doc.get(field)
    return json.loads(_json(actual))
