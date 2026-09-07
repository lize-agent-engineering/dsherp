"""Rejected/Expired proposal exits; no paid worker or business writes."""
import subprocess


def test_reject_and_expire_are_terminal_without_execution():
    script=r'''
import os,uuid,frappe
from datetime import timedelta
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as events
from dsherp_bridge.operations import confirm,expire_proposals,propose_update,reject
tag=uuid.uuid4().hex
actor='exit-'+tag+'@example.invalid'
other='exit-other-'+tag+'@example.invalid'
conversation=None
parked=[]
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic exit owner','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.get_doc({'doctype':'User','email':other,'first_name':'Synthetic exit other','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic proposal exits'}).insert(ignore_permissions=True).name
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation,'status':'Running','page_context':'{"schema_version":1,"page_type":"unknown","route":[]}','sources':'[]'}).insert(ignore_permissions=True)
    item=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    pending=propose_update(conversation,'Item',item.name,{'item_name':'Reject this proposal'},str(item.modified),model_run=run.name)
    clock=now_datetime
    from dsherp_bridge import operations
    operations.now_datetime=lambda:clock()-timedelta(minutes=11)
    expired=propose_update(conversation,'Item',item.name,{'item_name':'Expire this proposal'},str(item.modified),model_run=run.name)
    operations.now_datetime=clock
    frappe.db.set_value('DS Model Run',run.name,'status','Succeeded')
    frappe.db.commit()
    request_id='reject-'+tag
    rejected=reject(pending['id'],pending['digest'],request_id)
    assert rejected['status']=='Rejected',rejected
    stored=frappe.db.get_value('DS Operation Proposal',pending['id'],['status','rejected_by','rejected_request_id'],as_dict=True)
    assert stored.status=='Rejected' and stored.rejected_by==actor and stored.rejected_request_id==request_id,stored
    rejected_events=[e for e in events.list_events(run.name) if e['kind']=='proposal_rejected']
    assert [e['payload'] for e in rejected_events]==[{'proposal':pending['id']}],rejected_events
    again=reject(pending['id'],pending['digest'],request_id)
    assert again['status']=='Rejected',again
    assert [e['kind'] for e in events.list_events(run.name) if e['kind']=='proposal_rejected']==['proposal_rejected']
    try:
        reject(pending['id'],pending['digest'],'other-'+tag)
        raise AssertionError('different request_id rejected a non-pending proposal')
    except frappe.ValidationError:pass
    frappe.set_user(other)
    try:
        reject(pending['id'],pending['digest'],'foreign-'+tag)
        raise AssertionError('foreign owner rejected proposal')
    except frappe.PermissionError:pass
    frappe.set_user(actor)
    try:
        confirm(pending['id'],pending['digest'],'confirm-rejected-'+tag)
        raise AssertionError('confirmed rejected proposal')
    except frappe.ValidationError as error:
        assert str(error)=='提案已被拒绝，请重新提出操作',error
    assert frappe.db.count('DS Execution Record',{'proposal':pending['id']})==0
    frappe.set_user('Administrator')
    for name in frappe.get_all('DS Operation Proposal',filters={'status':'Pending','expires_at':['<=',now_datetime()]},pluck='name'):
        if name==expired['id']:continue
        parked.append((name,frappe.db.get_value('DS Operation Proposal',name,'expires_at')))
        frappe.db.set_value('DS Operation Proposal',name,'expires_at',add_to_date(now_datetime(),minutes=60))
    frappe.db.commit();frappe.set_user(actor)
    assert expire_proposals()==1
    assert frappe.db.get_value('DS Operation Proposal',expired['id'],'status')=='Expired'
    assert frappe.db.get_value('DS Operation Proposal',pending['id'],'status')=='Rejected'
    expired_events=[e for e in events.list_events(run.name) if e['kind']=='proposal_expired']
    assert [e['payload'] for e in expired_events]==[{'proposal':expired['id']}],expired_events
    assert expire_proposals()==0
    assert [e['kind'] for e in events.list_events(run.name) if e['kind']=='proposal_expired']==['proposal_expired']
    try:
        confirm(expired['id'],expired['digest'],'confirm-expired-'+tag)
        raise AssertionError('confirmed expired proposal')
    except frappe.ValidationError as error:
        assert str(error)=='提案已过期，请重新提出操作',error
    assert frappe.db.count('DS Execution Record',{'proposal':expired['id']})==0
    try:
        reject(expired['id'],expired['digest'],'reject-expired-'+tag)
        raise AssertionError('rejected a non-pending proposal')
    except frappe.ValidationError:pass
    print('PROPOSAL_EXITS_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for name,expires_at in parked:
        if frappe.db.exists('DS Operation Proposal',name):
            frappe.db.set_value('DS Operation Proposal',name,'expires_at',expires_at)
    if conversation:
        for proposal_id in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution_id in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
                frappe.db.delete('DS Execution Record',{'name':execution_id})
            frappe.db.delete('DS Operation Proposal',{'name':proposal_id})
        for run_id in frappe.get_all('DS Model Run',filters={'conversation':conversation},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':run_id})
            frappe.db.delete('DS Model Run',{'name':run_id})
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    for user in (actor,other):
        if frappe.db.exists('User',user):frappe.delete_doc('User',user,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
