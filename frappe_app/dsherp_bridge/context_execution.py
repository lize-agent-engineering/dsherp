"""Run-scoped read capabilities. The model cannot select its Site or actor."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets

import frappe
from frappe.utils import now_datetime,add_to_date,get_datetime
from dsherp_bridge import api as erp
from dsherp_bridge import context_api as conversations

TOOLS={'erp_read_schema':(erp.read_schema,{'doctype'}),
       'erp_read_record':(erp.read_record,{'doctype','name'}),
       'erp_search_records':(erp.search_records,{'doctype','query'})}


@contextmanager
def _actor(run):
    original=frappe.session.user
    try:
        frappe.set_user(run.owner)
        conversations._user()
        yield
    finally:
        frappe.set_user(original)


def authorize_sources(sources):
    for source in sources:
        args=source['arguments'];doctype=args['doctype']
        visible={f['fieldname'] for f in erp.read_schema(doctype)['fields']}
        if set(source['fields'])-visible:
            raise frappe.PermissionError('历史结果的字段权限已改变')
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
def claim_run():
    user=conversations._user()
    if user!=frappe.conf.get('dsherp_runtime_user'):
        raise frappe.PermissionError('需要站点指定的运行服务身份')
    frappe.db.rollback()
    frappe.db.sql('SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE',(user,))
    for name in frappe.get_all('DS Model Run',filters={'status':['in',['Running','Cancelling']], 'expires_at':['<=',now_datetime()]},pluck='name'):
        frappe.db.set_value('DS Model Run',name,{'status':'Failed','error':'运行已过期，未自动重试','capability_hash':''})
    if frappe.db.exists('DS Model Run',{'status':['in',['Running','Cancelling']]}):return None
    names=frappe.get_all('DS Model Run',filters={'status':'Queued'},pluck='name',order_by='creation asc',limit_page_length=1)
    if not names:return None
    run=frappe.get_doc('DS Model Run',names[0],for_update=True)
    if run.status!='Queued':return None
    try:
        with _actor(run):
            conversation=conversations._conversation(run.conversation)
            conversations._public(conversation)
    except (frappe.PermissionError,frappe.DoesNotExistError):
        frappe.db.set_value('DS Model Run',run.name,{'status':'Failed','error':'当前用户已无法读取会话来源','capability_hash':''})
        return None
    capability=secrets.token_urlsafe(32)
    frappe.db.set_value('DS Model Run',run.name,{'status':'Running','capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),seconds=180)})
    return {'run_id':run.name,'session_id':run.conversation,'native_session_id':conversation.runtime_session,
            'question':run.question,'context':json.loads(run.page_context),'capability':capability}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_status(run_id,capability):
    run=_run(run_id,capability)
    if run.status=='Running':
        with _actor(run):
            conversations._public(conversations._conversation(run.conversation))
    return {'run_id':run.name,'status':run.status}


@frappe.whitelist(allow_guest=True,methods=['POST'])
def run_tool(run_id,capability,tool,arguments):
    run=_run(run_id,capability)
    if run.status!='Running':raise frappe.PermissionError('运行正在取消')
    if tool not in TOOLS:frappe.throw('未知工具')
    if isinstance(arguments,str):arguments=json.loads(arguments)
    if tool=='erp_search_records' and isinstance(arguments,dict):arguments={'query':'',**arguments}
    function,keys=TOOLS[tool]
    if not isinstance(arguments,dict) or set(arguments)!=keys or not all(isinstance(v,str) for v in arguments.values()):
        frappe.throw('工具参数无效')
    with _actor(run):
        conversations._context(run.page_context,check_version=False)
        result=function(**arguments)
        fields=[];records=[]
        if tool=='erp_read_schema':fields=[f['fieldname'] for f in result['fields']]
        elif tool=='erp_read_record':
            fields=[key for key in result['fields'] if frappe.get_meta(arguments['doctype']).get_field(key)]
            records=[result['name']]
        else:
            records=[r['name'] for r in result]
            title=frappe.get_meta(arguments['doctype']).title_field
            visible={f['fieldname'] for f in erp.read_schema(arguments['doctype'])['fields']}
            if title in visible:fields=[title]
        source={'tool':tool,'arguments':arguments,'fields':fields,'records':records}
        authorize_sources([source])
    sources=json.loads(run.sources or '[]');sources.append(source)
    frappe.db.set_value('DS Model Run',run.name,'sources',conversations._json(sources))
    return result


@frappe.whitelist(allow_guest=True,methods=['POST'])
def finish_run(run_id,capability,status,answer='',error=''):
    run=_run(run_id,capability)
    if status not in ('Succeeded','Failed','Cancelled'):frappe.throw('无效运行结束状态')
    if status=='Succeeded':
        if run.status!='Running':raise frappe.PermissionError('取消中的运行不能成功完成')
        if not isinstance(answer,str) or not answer.strip() or not json.loads(run.sources or '[]'):
            frappe.throw('成功结果必须包含实际读取及回答')
        with _actor(run):
            conversations._public(conversations._conversation(run.conversation))
    if status=='Cancelled' and run.status!='Cancelling':frappe.throw('运行未请求取消')
    frappe.db.set_value('DS Model Run',run.name,{'status':status,'answer':answer if status=='Succeeded' else '',
        'error':error if status=='Failed' else '', 'capability_hash':''})
    return {'run_id':run.name,'status':status}
