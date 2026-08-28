"""Persisted read-only tasks. Only a task capability reaches the model runtime."""
import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from dsherp_platform.api import _binding, _read, _user

ROLE = 'DS Agent Worker'


class AgentUnavailable(frappe.ValidationError):
    http_status_code = 503


def _worker():
    user = _user()
    if ROLE not in frappe.get_roles(user) or user == 'Administrator':
        raise frappe.PermissionError('Dedicated agent worker required')
    return user


def _lock(task_id):
    names = frappe.db.sql('SELECT name FROM `tabDS Agent Task` WHERE name=%s FOR UPDATE', (task_id,))
    if not names:
        raise frappe.DoesNotExistError('Task does not exist')
    return frappe.get_doc('DS Agent Task', task_id, for_update=True)


def _fingerprint(member, enterprise):
    return hashlib.sha256((member.name+'\0'+str(member.modified)+'\0'+str(enterprise.modified)).encode()).hexdigest()


def _task_binding(task):
    member, enterprise = _binding(task.enterprise)
    if task.binding_fingerprint != _fingerprint(member, enterprise):
        raise frappe.PermissionError('企业身份绑定已变更，请重新提交任务')


@contextmanager
def _actor(task):
    original = frappe.session.user
    try:
        frappe.set_user(task.owner)
        _task_binding(task)
        yield
    finally:
        frappe.set_user(original)


def _cap(task, capability):
    if (task.status != 'Running' or not task.capability_hash or not isinstance(capability,str)
            or get_datetime(task.expires_at) <= now_datetime()
            or not hmac.compare_digest(task.capability_hash, hashlib.sha256(capability.encode()).hexdigest())):
        raise frappe.PermissionError('Task capability expired or invalid')


def _expire(task):
    expired = (task.status == 'Running' and get_datetime(task.expires_at) <= now_datetime())
    abandoned = (task.status == 'Queued' and get_datetime(task.creation) <= add_to_date(now_datetime(), seconds=-180))
    if expired or abandoned:
        task = _lock(task.name)
        if ((task.status == 'Running' and get_datetime(task.expires_at) <= now_datetime())
                or (task.status == 'Queued' and get_datetime(task.creation) <= add_to_date(now_datetime(), seconds=-180))):
            frappe.db.set_value('DS Agent Task', task.name, {'status':'Failed','error':'Agent 任务已超时，请重新提交','capability_hash':''})
            frappe.db.commit()
            task = frappe.get_doc('DS Agent Task', task.name)
    return task


def _public(task):
    return {'id':task.name,'enterprise':task.enterprise,'question':task.question,
            'status':task.status,'answer':task.answer or '', 'error':task.error or '',
            'created':str(task.creation),'events':json.loads(task.events or '[]')}


def _owner(task):
    if task.owner != _user():
        raise frappe.PermissionError('Task belongs to another user')
    _task_binding(task)


@frappe.whitelist(methods=['GET','POST'])
def worker_heartbeat():
    _worker()
    frappe.cache.set_value('dsherp_agent_worker', True, expires_in_sec=45)
    return {'ready':True}


@frappe.whitelist(methods=['POST'])
def submit_task(enterprise, question, request_id):
    user = _user()
    _binding(enterprise)
    if not isinstance(question,str) or not question.strip() or len(question)>8000:
        frappe.throw('Question must contain 1–8000 characters')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
        frappe.throw('Request id is required (maximum 128 characters)')
    name=hashlib.sha256((user+'\0'+request_id).encode()).hexdigest()
    # Serialize this user's submissions without holding locks during ERP network I/O.
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    member, target = _binding(enterprise)
    if frappe.db.exists('DS Agent Task',name):
        task=frappe.get_doc('DS Agent Task',name)
        _owner(task)
        if task.enterprise != enterprise or task.question != question.strip():
            frappe.throw('Request id already used with different content')
        return _public(task)
    if not frappe.cache.get_value('dsherp_agent_worker'):
        frappe.throw('Agent 服务未启动，请启动工作进程后重试', exc=AgentUnavailable)
    task=frappe.get_doc({'doctype':'DS Agent Task','name':name,'enterprise':enterprise,
                         'question':question.strip(),'request_id':request_id,'status':'Queued','events':'[]',
                         'binding_fingerprint':_fingerprint(member,target)})
    task.insert(ignore_permissions=True, set_name=name)
    return _public(task)


@frappe.whitelist(methods=['GET'])
def list_tasks(enterprise):
    user=_user(); member,target=_binding(enterprise)
    names=frappe.get_all('DS Agent Task',filters={'owner':user,'enterprise':enterprise,'binding_fingerprint':_fingerprint(member,target)},order_by='creation desc',limit_page_length=20,pluck='name')
    return [_public(_expire(frappe.get_doc('DS Agent Task',name))) for name in names]


@frappe.whitelist(methods=['GET'])
def get_task(task_id):
    task=frappe.get_doc('DS Agent Task',task_id); _owner(task)
    return _public(_expire(task))


@frappe.whitelist(methods=['POST'])
def claim_task():
    _worker()
    frappe.response['message'] = None
    frappe.db.rollback()  # Authentication reads must not retain a pre-lock snapshot.
    # One worker slot, serialized in the platform database across HTTP processes.
    frappe.db.sql('SELECT name FROM `tabRole` WHERE name=%s FOR UPDATE',(ROLE,))
    expired=frappe.get_all('DS Agent Task',filters={'status':'Running','expires_at':['<=',now_datetime()]},pluck='name')
    for name in expired:
        frappe.db.set_value('DS Agent Task',name,{'status':'Failed','error':'Agent 执行凭据已过期，请重新提交','capability_hash':''})
    if frappe.db.exists('DS Agent Task',{'status':'Running'}):
        return None
    names=frappe.get_all('DS Agent Task',filters={'status':'Queued'},order_by='creation asc',limit_page_length=1,pluck='name')
    if not names:return None
    task=_lock(names[0])
    try:
        with _actor(task):pass
    except frappe.PermissionError:
        frappe.db.set_value('DS Agent Task',task.name,{'status':'Failed','error':'企业成员权限已撤销，任务无法继续'})
        return None
    capability=secrets.token_urlsafe(32)
    frappe.db.set_value('DS Agent Task',task.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),'expires_at':add_to_date(now_datetime(),seconds=180)})
    return {'task_id':task.name,'question':task.question,'capability':capability}


@frappe.whitelist(allow_guest=True, methods=['POST'])
def task_tool(task_id, capability, tool, arguments):
    frappe.db.rollback()
    task=_lock(task_id); _cap(task,capability)
    specs={'erp_read_schema':('read_schema',{'doctype'}),'erp_read_record':('read_record',{'doctype','name'}),'erp_search_records':('search_records',{'doctype','query'})}
    if tool not in specs:frappe.throw('Unsupported tool')
    if isinstance(arguments,str):arguments=json.loads(arguments)
    method,keys=specs[tool]
    if tool == 'erp_search_records' and isinstance(arguments,dict):
        arguments = {'query':'', **arguments}
    if not isinstance(arguments,dict) or set(arguments)!=keys or any(not isinstance(v,str) for v in arguments.values()):
        frappe.throw('Invalid tool arguments')
    with _actor(task):pass
    events=json.loads(task.events or '[]')
    if any(e['status']=='Running' for e in events):frappe.throw('A tool is already running')
    if len(events)>=20:frappe.throw('Task tool limit exceeded')
    events.append({'tool':tool,'arguments':arguments,'status':'Running'})
    frappe.db.set_value('DS Agent Task',task_id,'events',json.dumps(events))
    frappe.db.commit()
    try:
        with _actor(task):
            result=_read(task.enterprise,arguments['doctype'],method,arguments.get('name'),query=arguments.get('query'))
        frappe.db.rollback()  # Recheck membership from a fresh snapshot after network I/O.
        task=_lock(task_id); _cap(task,capability)
        with _actor(task):pass
        events[-1].update(status='Succeeded',result=result)
        frappe.db.set_value('DS Agent Task',task_id,'events',json.dumps(events))
        frappe.db.commit()
        return result
    except Exception:
        frappe.db.rollback()
        task=_lock(task_id)
        events[-1].update(status='Failed',error='ERP 读取失败或权限已撤销')
        frappe.db.set_value('DS Agent Task',task_id,'events',json.dumps(events))
        frappe.db.commit()
        raise


@frappe.whitelist(allow_guest=True, methods=['POST'])
def finish_task(task_id, capability, status, answer='', error=''):
    frappe.db.rollback()
    task=_lock(task_id); _cap(task,capability)
    # Failed completion only records task metadata; revoked actors cannot publish results.
    if status == 'Succeeded':
        with _actor(task):pass
    elif answer:
        frappe.throw('Failed tasks cannot publish an answer')
    events=json.loads(task.events or '[]')
    if status not in ('Succeeded','Failed') or any(e['status']=='Running' for e in events):
        frappe.throw('Invalid task completion')
    if status=='Succeeded' and (not answer.strip() or not any(e['status']=='Succeeded' for e in events)):
        frappe.throw('Success requires an actual ERP read and answer')
    if len(answer)>32000 or len(error)>4000:frappe.throw('Task output too long')
    frappe.db.set_value('DS Agent Task',task_id,{'status':status,'answer':answer,'error':error,'capability_hash':''})
    return task_id
