"""Business operation proposals. Native metadata/permissions remain authoritative."""
import hashlib
import json
import frappe
from frappe.utils import add_to_date, now_datetime, get_system_timezone
from zoneinfo import ZoneInfo
from dsherp_bridge.context_api import _conversation, _user, _json


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
    get_proposal(proposal_id)
    if not isinstance(digest, str) or digest != proposal.digest:
        frappe.throw('确认内容不匹配，请重新核对提案')
    existing = frappe.db.get_value('DS Execution Record', {'proposal': proposal_id}, 'name', for_update=True)
    if existing:
        return _execution_result(frappe.get_doc('DS Execution Record', existing, for_update=True))
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
        if payload['action'] != 'update':
            frappe.throw('不支持的业务动作')
        doc = frappe.get_doc(payload['doctype'], payload['name'], for_update=True)
        if str(doc.modified) != payload['version']:
            frappe.throw('记录版本已变化，请重新提出操作')
        values = {change['field']: change['after'] for change in payload['changes']}
        if update_diff(doc, values) != payload['changes']:
            frappe.throw('字段或权限已变化，请重新提出操作')
        doc.update(values)
        doc.save()  # Native validation, permissions, versioning and hooks.
        saved = frappe.get_doc(doc.doctype, doc.name)
        saved.check_permission('read')
        actual = {field: saved.get(field) for field in values}
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


def propose_update(session_id, doctype, name, values, version, grant=None, model_run=None):
    user = _user()
    conversation = _conversation(session_id)
    if model_run:
        origin=frappe.get_doc('DS Model Run',model_run)
        if origin.owner!=user or origin.conversation!=conversation.name or origin.status!='Running':
            raise frappe.PermissionError('提案运行归属或状态不匹配')
    doc = frappe.get_doc(doctype, name)
    changes = update_diff(doc, values)
    if str(doc.modified) != version:
        frappe.throw('记录版本已变化，请重新提出操作')
    expires = add_to_date(now_datetime(), minutes=10)
    authorization = _authorization_revision(user, grant or frappe.session.data.get('dsherp_platform_grant'))
    payload = _json({'site': frappe.local.site, 'actor': user, 'action': 'update', 'authorization_revision': authorization,
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
    doc = frappe.get_doc(payload['doctype'], payload['name'])
    doc.check_permission('read')
    # Recheck access before exposing saved before/after values, even after success.
    readable = set(doc.meta.get_permitted_fieldnames(user=user, permission_type='read'))
    if any(change['field'] not in readable for change in payload['changes']):
        raise frappe.PermissionError('无权读取提案字段')
    result = {**payload, 'id': proposal.name, 'digest': proposal.digest,
        'expires_at': proposal.expires_at.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(), 'status': proposal.status}
    result['execution_ready']=not proposal.model_run or frappe.db.get_value('DS Model Run',proposal.model_run,'status')=='Succeeded'
    execution = frappe.db.get_value('DS Execution Record', {'proposal': proposal_id}, 'name')
    if execution:
        result['execution'] = _execution_result(frappe.get_doc('DS Execution Record', execution))
    return result


def update_diff(doc, values):
    """Validate explicit scalar edits without mutating the document or database."""
    if doc.doctype not in ('Item', 'Customer'):
        raise frappe.PermissionError('当前操作领域不支持该业务对象')
    doc.check_permission('read')
    doc.check_permission('write')
    if doc.docstatus != 0:
        frappe.throw('当前操作只支持草稿记录')
    if not isinstance(values, dict) or not values:
        frappe.throw('必须提供明确的修改字段')
    permitted = set(doc.meta.get_permitted_fieldnames(user=frappe.session.user, permission_type='write'))
    levels = doc.get_permlevel_access('write')
    changes = []
    for field, value in values.items():
        definition = doc.meta.get_field(field)
        if not definition or field in ('name', 'docstatus', 'owner', 'creation', 'modified', 'modified_by'):
            frappe.throw('不能修改系统字段或不存在的字段：' + field)
        if field not in permitted or (definition.permlevel and definition.permlevel not in levels):
            raise frappe.PermissionError('无权修改该字段：' + field)
        if definition.read_only or definition.fieldtype not in (
            'Data', 'Small Text', 'Text', 'Long Text', 'Text Editor', 'Select',
            'Link', 'Dynamic Link', 'Int', 'Float', 'Currency', 'Percent',
            'Check', 'Date', 'Datetime', 'Time', 'Duration', 'Autocomplete',
        ):
            frappe.throw('该字段不支持直接修改：' + field)
        if isinstance(value, (dict, list)):
            frappe.throw('修改值必须是标量')
        if doc.get(field) != value:
            changes.append({'field': field, 'label': definition.label, 'before': doc.get(field), 'after': value})
    if not changes:
        frappe.throw('没有实际修改')
    return changes
