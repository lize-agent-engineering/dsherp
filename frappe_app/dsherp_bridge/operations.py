"""Business operation proposals. Native metadata/permissions remain authoritative."""
import hashlib
import json
import frappe
from frappe.utils import add_to_date, now_datetime, get_system_timezone
from zoneinfo import ZoneInfo
from dsherp_bridge.context_api import _conversation, _user, _json


def propose_update(session_id, doctype, name, values, version):
    user = _user()
    conversation = _conversation(session_id)
    doc = frappe.get_doc(doctype, name)
    changes = update_diff(doc, values)
    if str(doc.modified) != version:
        frappe.throw('记录版本已变化，请重新提出操作')
    expires = add_to_date(now_datetime(), minutes=10)
    payload = _json({'site': frappe.local.site, 'actor': user, 'action': 'update',
        'doctype': doctype, 'name': name, 'version': version, 'changes': changes})
    digest = hashlib.sha256(_json([conversation.name, payload, str(expires)]).encode()).hexdigest()
    proposal = frappe.get_doc({'doctype': 'DS Operation Proposal', 'conversation': conversation.name,
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
    return {**payload, 'id': proposal.name, 'digest': proposal.digest,
        'expires_at': proposal.expires_at.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(), 'status': proposal.status}


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
