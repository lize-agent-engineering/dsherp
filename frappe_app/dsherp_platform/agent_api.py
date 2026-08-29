"""Read-only access to historical tasks from the retired platform Agent."""
import hashlib
import json

import frappe
from dsherp_platform.api import _binding, _user


def _fingerprint(member, enterprise):
    return hashlib.sha256((member.name+'\0'+str(member.modified)+'\0'+str(enterprise.modified)).encode()).hexdigest()


def _task_binding(task):
    member, enterprise = _binding(task.enterprise)
    if task.binding_fingerprint != _fingerprint(member, enterprise):
        raise frappe.PermissionError('企业身份绑定已变更，请重新提交任务')


def _public(task):
    return {'id':task.name,'enterprise':task.enterprise,'question':task.question,
            'status':task.status,'answer':task.answer or '', 'error':task.error or '',
            'created':str(task.creation),'events':json.loads(task.events or '[]')}


def _owner(task):
    if task.owner != _user():
        raise frappe.PermissionError('Task belongs to another user')
    _task_binding(task)


@frappe.whitelist(methods=['GET'])
def list_tasks(enterprise):
    user=_user(); member,target=_binding(enterprise)
    names=frappe.get_all('DS Agent Task',filters={'owner':user,'enterprise':enterprise,'binding_fingerprint':_fingerprint(member,target)},order_by='creation desc',limit_page_length=20,pluck='name')
    return [_public(frappe.get_doc('DS Agent Task',name)) for name in names]


@frappe.whitelist(methods=['GET'])
def get_task(task_id):
    task=frappe.get_doc('DS Agent Task',task_id); _owner(task)
    return _public(task)
