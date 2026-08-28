"""Native metadata and field permissions; no paid worker or business writes."""
import subprocess


def test_customer_native_field_permission_rejects_silently_ignored_write():
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import update_diff
try:
    frappe.set_user('Administrator')
    actor='field-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic field writer','enabled':1,'send_welcome_email':0,'roles':[{'role':'Sales User'}]}).insert()
    frappe.set_user(actor)
    customer=frappe.get_doc('Customer','DSHERP-TEST-CUSTOMER')
    customer.check_permission('write')
    field=customer.meta.get_field('custom_dsherp_restricted')
    assert field and field.permlevel==1
    assert update_diff(customer,{'customer_name':'Proposed customer name'})
    try:update_diff(customer,{'custom_dsherp_restricted':'must reject'});raise AssertionError('higher-level write accepted')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_confirm_native_save_readback_and_duplicate_request_has_one_execution():
    script=r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_update,confirm
tag=uuid.uuid4().hex
actor='execute-'+tag+'@example.invalid';item_name='DS-EXEC-'+tag;conversation=None
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic execution writer','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    item=frappe.copy_doc(frappe.get_doc('Item','DSHERP-TEST-ITEM'))
    item.item_code=item_name;item.item_name='Before confirmation';item.insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic execution'}).insert(ignore_permissions=True).name
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Confirmed native save'},str(item.modified))
    frappe.db.commit()
    assert frappe.db.get_value('Item',item.name,'item_name')=='Before confirmation'
    try:confirm(proposal['id'],'forged',tag);raise AssertionError('wrong digest accepted')
    except frappe.ValidationError:frappe.db.rollback()
    result=confirm(proposal['id'],proposal['digest'],tag)
    assert result['status']=='Succeeded',result
    assert result['values']=={'item_name':'Confirmed native save'}
    from dsherp_bridge.context_api import get_session
    assert get_session(conversation)['proposals'][0]['execution']==result
    assert frappe.db.get_value('Item',item.name,'item_name')=='Confirmed native save'
    version=frappe.db.get_value('Item',item.name,'modified')
    # A lost response is read from the execution record, not saved a second time.
    again=confirm(proposal['id'],proposal['digest'],tag)
    assert again==result
    assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
    assert frappe.db.get_value('Item',item.name,'modified')==version
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    # A proposal freezes its target/version; intervening native writes invalidate it.
    stale=propose_update(conversation,'Item',item.name,{'item_name':'Must not save'},str(version));frappe.db.commit()
    changed=frappe.get_doc('Item',item.name);changed.item_name='Manual native edit';changed.save();frappe.db.commit()
    failed=confirm(stale['id'],stale['digest'],uuid.uuid4().hex)
    assert failed['status']=='Failed',failed
    assert frappe.db.get_value('Item',item.name,'item_name')=='Manual native edit'
    assert frappe.db.count('DS Execution Record',{'proposal':stale['id']})==1
    from dsherp_bridge import operations
    from datetime import timedelta
    clock=operations.now_datetime
    operations.now_datetime=lambda:clock()-timedelta(minutes=11)
    current=frappe.get_doc('Item',item.name)
    expired=propose_update(conversation,'Item',item.name,{'item_name':'Expired change'},str(current.modified));frappe.db.commit()
    operations.now_datetime=clock
    assert confirm(expired['id'],expired['digest'],uuid.uuid4().hex)['status']=='Failed'
    assert frappe.db.get_value('Item',item.name,'item_name')=='Manual native edit'
    pending=propose_update(conversation,'Item',item.name,{'item_name':'Interrupted change'},str(current.modified));frappe.db.commit()
    # A durable intent left by a terminated process must not trigger another save.
    frappe.get_doc({'doctype':'DS Execution Record','proposal':pending['id'],'request_id':'interrupted','status':'Running'}).insert(ignore_permissions=True);frappe.db.commit()
    assert confirm(pending['id'],pending['digest'],uuid.uuid4().hex)['status']=='Unknown'
    assert frappe.db.get_value('Item',item.name,'item_name')=='Manual native edit'
    changed_permission=propose_update(conversation,'Item',item.name,{'item_name':'Old authorization'},str(current.modified));frappe.db.commit()
    frappe.set_user('Administrator')
    account=frappe.get_doc('User',actor);account.append('roles',{'role':'DSHERP Reader'});account.save();frappe.db.commit()
    frappe.set_user(actor)
    assert confirm(changed_permission['id'],changed_permission['digest'],uuid.uuid4().hex)['status']=='Failed'
    assert frappe.db.get_value('Item',item.name,'item_name')=='Manual native edit'
    # Two real authenticated HTTP requests use the same frozen proposal.
    frappe.set_user('Administrator')
    account=frappe.get_doc('User',actor);account.api_key=uuid.uuid4().hex;secret=uuid.uuid4().hex;account.api_secret=secret;account.save();frappe.db.commit()
    frappe.set_user(actor)
    concurrent=propose_update(conversation,'Item',item.name,{'item_name':'Concurrent confirmed'},str(current.modified));frappe.db.commit()
    import requests
    from concurrent.futures import ThreadPoolExecutor
    headers={'X-Frappe-Site-Name':'dsherp-validation.localhost','Authorization':'token '+account.api_key+':'+secret}
    def send(_):
        with requests.Session() as client:
            client.trust_env=False
            return client.post('http://127.0.0.1:8000/api/method/dsherp_bridge.operations.confirm',headers=headers,json={'proposal_id':concurrent['id'],'digest':concurrent['digest'],'request_id':uuid.uuid4().hex},timeout=20)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(send,range(2)))
    assert all(response.status_code==200 for response in responses),[(response.status_code,response.json().get('exc_type')) for response in responses]
    assert all(response.json()['message']['status'] in ('Succeeded','Unknown') for response in responses)
    frappe.db.rollback()
    assert frappe.db.get_value('Item',item.name,'item_name')=='Concurrent confirmed'
    assert frappe.db.count('DS Execution Record',{'proposal':concurrent['id']})==1
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal_id in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution_id in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
                frappe.delete_doc('DS Execution Record',execution_id,ignore_permissions=True)
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('Item',item_name):frappe.delete_doc('Item',item_name,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr


def test_update_diff_uses_native_permissions_and_rejects_system_fields():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import update_diff
try:
    frappe.set_user('dsherp-reader@example.invalid')
    doc=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    try:update_diff(doc,{'item_name':'Changed'});raise AssertionError('read-only writer accepted')
    except frappe.PermissionError:pass
    # Administrator is only a metadata test fixture, not a business API actor.
    frappe.set_user('Administrator')
    changes=update_diff(doc,{'item_name':'Synthetic proposed name'})
    assert changes==[{'field':'item_name','label':doc.meta.get_field('item_name').label,'before':doc.item_name,'after':'Synthetic proposed name'}]
    assert frappe.db.get_value('Item',doc.name,'item_name')==doc.item_name
    for changes in ({'docstatus':1},{'modified':'forged'},{'name':'other'},{'item_name':doc.item_name},{'item_name':{'nested':'value'}},{'unknown_field':'x'}):
        try:update_diff(doc,changes);raise AssertionError('invalid change accepted')
        except (frappe.ValidationError,frappe.PermissionError):pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_proposal_is_immutable_owner_bound_and_does_not_write_business_data():
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_update,get_proposal
try:
    frappe.set_user('Administrator')
    actor='proposal-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic proposal writer','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic proposal'}).insert(ignore_permissions=True)
    doc=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    result=propose_update(conversation.name,'Item',doc.name,{'item_name':'Proposed only'},str(doc.modified))
    assert result['status']=='Pending' and result['digest']
    assert result['changes'][0]['after']=='Proposed only'
    from dsherp_bridge.context_api import get_session
    assert get_session(conversation.name)['proposals']==[result]
    assert frappe.db.get_value('Item',doc.name,'item_name')==doc.item_name
    assert get_proposal(result['id'])==result
    try:propose_update(conversation.name,'Item',doc.name,{'item_name':'Other'},'stale');raise AssertionError('stale version accepted')
    except frappe.ValidationError:pass
    stored=frappe.get_doc('DS Operation Proposal',result['id'])
    stored.payload='{}'
    try:stored.save(ignore_permissions=True);raise AssertionError('proposal body mutable')
    except frappe.ValidationError:pass
    frappe.set_user('dsherp-reader@example.invalid')
    try:get_proposal(result['id']);raise AssertionError('other owner accepted')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
