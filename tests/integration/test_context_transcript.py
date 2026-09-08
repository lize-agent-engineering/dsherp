"""Session detail must place tool reads and proposals on their own run."""
import subprocess


def test_session_detail_attributes_tool_reads_and_proposals_to_their_run():
    script=r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import revision
from dsherp_bridge import context_api as api
conversation=None;actor=None
try:
    frappe.set_user('Administrator')
    actor='transcript-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic transcript','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'},{'role':'Sales User'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Transcript order'}).insert(ignore_permissions=True)
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation','status':'Running',
        'question':'read then propose','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':revision(actor),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    item=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    cap={'run_id':run.name,'capability':capability}
    frappe.set_user('Guest')
    run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':item.name})
    proposal=run_tool(**cap,tool='erp_propose_update',arguments={'doctype':'Item','name':item.name,
        'values':{'item_name':'Proposed for transcript'},'version':str(item.modified)})
    frappe.db.commit()
    frappe.set_user(actor)
    session=api.get_session(conversation.name)
    message=session['messages'][0]
    assert message['id']==run.name, message['id']
    sources=message.get('sources')
    assert isinstance(sources,list) and sources, 'message must carry its authorized tool reads'
    assert sources[0]['tool']=='erp_read_record', sources[0]
    # run_tool 记录来源前会用默认值补齐缺省参数，所以 arguments 是补齐后的形状；
    # 判据不变：这条来源必须精确指向刚读过的那个 doctype 与那一条记录，且是精简默认读法
    # （没点名字段、没展开子表、没要空值、没续读游标）。
    assert sources[0]['arguments']=={'doctype':'Item','name':item.name,'fields':None,'children':None,
        'include_empty':False,'after_idx':None}, sources[0]
    assert item.name in sources[0]['records'], sources[0]
    assert session['proposals'][0]['id']==proposal['id']
    assert session['proposals'][0].get('model_run')==run.name, 'proposal must name the run that produced it'
    print('OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for name in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation.name},pluck='name'):
            frappe.db.delete('DS Operation Proposal',{'name':name})
        for name in frappe.get_all('DS Model Run',filters={'conversation':conversation.name},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':name})
            frappe.db.delete('DS Model Run',{'name':name})
        frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    if actor and frappe.db.exists('User',actor):
        frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit()
    assert not actor or not frappe.db.exists('User',actor), 'synthetic transcript user leaked'
    frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
                          input=script,text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stderr
