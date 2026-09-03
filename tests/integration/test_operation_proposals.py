"""Native metadata and field permissions; no paid worker or business writes."""
import subprocess


def test_run_domain_only_exposes_proposals_after_real_target_read():
    script=r'''
import os,uuid,json,hashlib,frappe
from frappe.utils import now_datetime,add_to_date
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import revision
try:
    frappe.set_user('Administrator')
    actor='tool-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic proposal tool','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'},{'role':'Sales User'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Tool proposals'}).insert(ignore_permissions=True)
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}), 'permission_revision':revision(actor),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    item=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    args={'doctype':'Item','name':item.name,'values':{'item_name':'Proposed by tool'},'version':str(item.modified)}
    cap={'run_id':run.name,'capability':capability}
    frappe.set_user('Guest')
    try:run_tool(**cap,tool='erp_propose_update',arguments=args);raise AssertionError('query domain proposed')
    except frappe.PermissionError:pass
    frappe.db.set_value('DS Model Run',run.name,'domain','operation')
    try:run_tool(**cap,tool='erp_propose_update',arguments=args);raise AssertionError('unread target proposed')
    except frappe.ValidationError:pass
    run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Item','name':item.name})
    proposal=run_tool(**cap,tool='erp_propose_update',arguments=args)
    assert proposal['status']=='Pending' and proposal['actor']==actor
    from dsherp_bridge.context_api import search_sessions
    frappe.set_user(actor)
    summary=next(item for item in search_sessions(query='Tool proposals')['items'] if item['id']==conversation.name)
    assert summary['pending_count']==1
    frappe.set_user('Guest')
    assert frappe.db.get_value('Item',item.name,'item_name')==item.item_name
    assert 'confirm' not in proposal
    try:run_tool(**cap,tool='erp_propose_fill',arguments=args);raise AssertionError('non-form fill accepted')
    except frappe.ValidationError:pass
    frappe.db.set_value('DS Model Run',run.name,'page_context',json.dumps({'schema_version':1,'page_type':'form','route':['Form','Item',item.name],'doctype':'Item','name':item.name,'version':str(item.modified)}))
    fill=run_tool(**cap,tool='erp_propose_fill',arguments=args)
    assert fill['action']=='fill' and fill['status']=='Pending'
    create={'doctype':'Item','values':{'item_code':'DS-PROPOSE-'+uuid.uuid4().hex,'item_name':'New proposed item','item_group':item.item_group,'stock_uom':item.stock_uom},'version':str(frappe.get_meta('Item').modified)}
    before=frappe.db.count('Item')
    try:run_tool(**cap,tool='erp_propose_create',arguments=create);raise AssertionError('unread schema accepted')
    except frappe.ValidationError:pass
    run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Item'})
    created=run_tool(**cap,tool='erp_propose_create',arguments=create)
    assert created['action']=='create' and created['status']=='Pending'
    assert frappe.db.count('Item')==before
    order=frappe.get_doc('Sales Order','SAL-ORD-2026-00001')
    action={'doctype':'Sales Order','name':order.name,'action':'submit','version':str(order.modified)}
    try:run_tool(**cap,tool='erp_propose_action',arguments=action);raise AssertionError('unread order accepted')
    except frappe.ValidationError:pass
    read=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Sales Order','name':order.name})
    sources=json.loads(frappe.db.get_value('DS Model Run',run.name,'sources'))
    assert 'qty' in sources[-1]['child_fields']['items']
    proposal_count=frappe.db.count('DS Operation Proposal',{'conversation':conversation.name})
    try:
        run_tool(**cap,tool='erp_propose_action',arguments={
            **action,'impact':{'kind':'stock','entries':[]},
        })
        raise AssertionError('model supplied a writable impact block')
    except frappe.ValidationError as error:
        assert str(error)=='操作提案参数无效',error
    assert frappe.db.count('DS Operation Proposal',{'conversation':conversation.name})==proposal_count
    proposed=run_tool(**cap,tool='erp_propose_action',arguments=action)
    assert proposed['action']=='submit' and proposed['status']=='Pending'
    assert proposed['impact']=={'kind':'none','entries':[]}
    assert frappe.get_doc('Sales Order',order.name).docstatus==0
    try:run_tool(**cap,tool='confirm',arguments={});raise AssertionError('model confirmed write')
    except frappe.ValidationError:pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


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
    origin=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation,'status':'Running','page_context':'{"schema_version":1,"page_type":"unknown","route":[]}','sources':'[]'}).insert(ignore_permissions=True)
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Confirmed native save'},str(item.modified),model_run=origin.name)
    frappe.db.commit()
    try:confirm(proposal['id'],proposal['digest'],tag);raise AssertionError('confirmed before model run ended')
    except frappe.ValidationError as error:assert '运行' in str(error)
    frappe.db.set_value('DS Model Run',origin.name,'status','Succeeded');frappe.db.commit()
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
    # MariaDB may deadlock the waiter after the winner commits. The waiter must
    # observe the durable execution instead of surfacing HTTP 500 or replaying.
    native_get_doc=frappe.get_doc;deadlocked=[False]
    def deadlock_waiter(doctype,*args,**kwargs):
        if doctype=='DS Operation Proposal' and kwargs.get('for_update') and not deadlocked[0]:
            deadlocked[0]=True
            raise frappe.QueryDeadlockError('Synthetic locked-row snapshot')
        return native_get_doc(doctype,*args,**kwargs)
    frappe.get_doc=deadlock_waiter
    try:
        try:
            confirm(proposal['id'],'forged',uuid.uuid4().hex)
            raise AssertionError('deadlock recovery bypassed the frozen digest')
        except frappe.ValidationError:pass
        deadlocked[0]=False
        assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
    finally:
        frappe.get_doc=native_get_doc
    timed_out=[False]
    def timeout_waiter(doctype,*args,**kwargs):
        if doctype=='DS Operation Proposal' and kwargs.get('for_update') and not timed_out[0]:
            timed_out[0]=True
            raise frappe.QueryTimeoutError('Synthetic locked-row timeout')
        return native_get_doc(doctype,*args,**kwargs)
    frappe.get_doc=timeout_waiter
    try:
        assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
    finally:
        frappe.get_doc=native_get_doc
    frappe.set_user('dsherp-reader@example.invalid');foreign_timed_out=[False]
    def foreign_timeout_waiter(doctype,*args,**kwargs):
        if doctype=='DS Operation Proposal' and kwargs.get('for_update') and not foreign_timed_out[0]:
            foreign_timed_out[0]=True
            raise frappe.QueryTimeoutError('Synthetic foreign locked-row timeout')
        return native_get_doc(doctype,*args,**kwargs)
    frappe.get_doc=foreign_timeout_waiter
    try:
        try:
            confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
            raise AssertionError('foreign deadlock recovery exposed execution')
        except frappe.PermissionError:pass
    finally:
        frappe.get_doc=native_get_doc;frappe.set_user(actor)
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
        for run_id in frappe.get_all('DS Model Run',filters={'conversation':conversation},pluck='name'):
            frappe.db.delete('DS Run Event',{'run':run_id})
            frappe.delete_doc('DS Model Run',run_id,ignore_permissions=True)
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


def test_stock_action_proposal_carries_impact_summary():
    script=r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import flt,nowdate
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import operations
from dsherp_bridge.operations import confirm,get_proposal,propose_action

tag=uuid.uuid4().hex
actor='impact-'+tag+'@example.invalid'
conversation=None
stock_entry=None
proposal_names=[]
execution_names=[]
warehouses={}
pre_bins={}
delete_linked_ledger_entries_before=None

def quantity(warehouse):
    return flt(frappe.db.get_value('Bin',{
        'item_code':'DSHERP-MFG-SYN-RM','warehouse':warehouse,
    },'actual_qty') or 0)

try:
    frappe.set_user('Administrator')
    company=frappe.get_all('Company',pluck='name')
    assert len(company)==1,company
    for key,label in (
        ('source','DSHERP 制造测试合成原料仓'),
        ('target','DSHERP 制造测试合成在制仓'),
    ):
        matches=frappe.get_all('Warehouse',filters={
            'warehouse_name':label,'company':company[0],'is_group':0,
        },pluck='name')
        assert len(matches)==1,(key,matches)
        warehouses[key]=matches[0]
    pre_bins={key:quantity(name) for key,name in warehouses.items()}
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )
    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic stock impact actor',
        'enabled':1,'send_welcome_email':0,'roles':[{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic stock impact '+tag,
    }).insert(ignore_permissions=True).name
    stock_entry=frappe.get_doc({
        'doctype':'Stock Entry','stock_entry_type':'Material Transfer','purpose':'Material Transfer',
        'company':company[0],'posting_date':str(nowdate()),'items':[
            {'item_code':'DSHERP-MFG-SYN-RM','qty':1,'s_warehouse':warehouses['source'],'t_warehouse':warehouses['target'],'basic_rate':1},
            {'item_code':'DSHERP-MFG-SYN-RM','qty':2,'s_warehouse':warehouses['source'],'t_warehouse':warehouses['target'],'basic_rate':1},
        ],
    }).insert()
    frappe.db.commit()
    entry_name=stock_entry.name
    entry_version=str(stock_entry.modified)
    expected={'kind':'stock','entries':[
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':-3,'uom':'Nos','warehouse':warehouses['source']},
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':3,'uom':'Nos','warehouse':warehouses['target']},
    ]}
    proposal=propose_action(conversation,'Stock Entry',entry_name,'submit',entry_version)
    proposal_names.append(proposal['id'])
    assert proposal.get('impact')==expected,proposal
    assert frappe.db.get_value('Stock Entry',entry_name,'docstatus')==0
    assert str(frappe.db.get_value('Stock Entry',entry_name,'modified'))==entry_version
    assert frappe.db.count('Stock Ledger Entry',{'voucher_type':'Stock Entry','voucher_no':entry_name})==0
    assert {key:quantity(name) for key,name in warehouses.items()}==pre_bins
    stored=json.loads(frappe.db.get_value('DS Operation Proposal',proposal['id'],'payload'))
    assert stored['impact']==expected and get_proposal(proposal['id'])['impact']==expected
    assert frappe.db.get_value('DS Operation Proposal',proposal['id'],'digest')==proposal['digest']
    frappe.db.commit()

    # Historical frozen item/warehouse values are no longer readable after role revocation.
    frappe.set_user('Administrator')
    account=frappe.get_doc('User',actor)
    account.set('roles',[row for row in account.roles if row.role!='Stock User'])
    account.save();frappe.db.commit()
    frappe.set_user(actor)
    try:get_proposal(proposal['id']);raise AssertionError('revoked stock impact remained readable')
    except frappe.PermissionError:pass
    frappe.set_user('Administrator')
    account=frappe.get_doc('User',actor)
    account.append('roles',{'role':'Stock User'});account.save();frappe.db.commit()
    frappe.set_user(actor)
    assert get_proposal(proposal['id'])['impact']==expected
    proposal=propose_action(conversation,'Stock Entry',entry_name,'submit',entry_version)
    proposal_names.append(proposal['id']);frappe.db.commit()

    # A native source edit changes both version and movement; the frozen proposal cannot execute.
    changed=frappe.get_doc('Stock Entry',entry_name)
    changed.items[0].qty=2
    changed.save();frappe.db.commit()
    stale=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    execution_names.append(stale['execution_id'])
    assert stale['status']=='Failed' and '记录版本已变化' in stale['error'],stale
    assert frappe.db.get_value('Stock Entry',entry_name,'docstatus')==0

    # Even with the same source version, a changed registry result invalidates the frozen impact.
    current=frappe.get_doc('Stock Entry',entry_name)
    drift=propose_action(conversation,'Stock Entry',entry_name,'submit',str(current.modified))
    proposal_names.append(drift['id']);frappe.db.commit()
    original=operations.action_impact
    try:
        operations.action_impact=lambda doc,action:{'kind':'stock','entries':[
            {**drift['impact']['entries'][0],'quantity':-99},
            drift['impact']['entries'][1],
        ]}
        rejected=confirm(drift['id'],drift['digest'],uuid.uuid4().hex)
    finally:
        operations.action_impact=original
    execution_names.append(rejected['execution_id'])
    assert rejected['status']=='Failed' and '库存影响已变化' in rejected['error'],rejected
    assert frappe.db.get_value('Stock Entry',entry_name,'docstatus')==0

    current=frappe.get_doc('Stock Entry',entry_name)
    submit=propose_action(conversation,'Stock Entry',entry_name,'submit',str(current.modified))
    proposal_names.append(submit['id']);frappe.db.commit()
    submitted=confirm(submit['id'],submit['digest'],uuid.uuid4().hex)
    execution_names.append(submitted['execution_id'])
    assert submitted['status']=='Succeeded',submitted
    assert frappe.db.get_value('Stock Entry',entry_name,'docstatus')==1
    submitted_impact=submit['impact']
    submitted_doc=frappe.get_doc('Stock Entry',entry_name)
    cancel=propose_action(conversation,'Stock Entry',entry_name,'cancel',str(submitted_doc.modified))
    proposal_names.append(cancel['id'])
    assert cancel['impact']=={'kind':'stock','entries':[
        {**entry,'quantity':-entry['quantity']} for entry in submitted_impact['entries']
    ]},cancel
    frappe.db.commit()
    cancelled=confirm(cancel['id'],cancel['digest'],uuid.uuid4().hex)
    execution_names.append(cancelled['execution_id'])
    assert cancelled['status']=='Succeeded',cancelled
    assert frappe.db.get_value('Stock Entry',entry_name,'docstatus')==2
    assert {key:quantity(name) for key,name in warehouses.items()}==pre_bins
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        proposal_names.extend(frappe.get_all(
            'DS Operation Proposal',filters={'conversation':conversation},pluck='name'
        ))
    proposal_names=list(dict.fromkeys(filter(None,proposal_names)))
    for proposal_id in proposal_names:
        execution_names.extend(frappe.get_all(
            'DS Execution Record',filters={'proposal':proposal_id},pluck='name'
        ))
    execution_names=list(dict.fromkeys(filter(None,execution_names)))
    for execution_id in execution_names:
        if frappe.db.exists('DS Execution Record',execution_id):
            frappe.delete_doc('DS Execution Record',execution_id,ignore_permissions=True)
    for proposal_id in proposal_names:
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
    if stock_entry and frappe.db.exists('Stock Entry',stock_entry.name):
        doc=frappe.get_doc('Stock Entry',stock_entry.name)
        if doc.docstatus==1:doc.cancel()
        from frappe.tests.utils import change_settings
        with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
            frappe.delete_doc('Stock Entry',doc.name,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()

    os.chdir('/home/frappe/frappe-bench/sites')
    frappe.init(site='dsherp-validation.localhost');frappe.connect()
    try:
        frappe.set_user('Administrator')
        assert not frappe.db.exists('User',actor)
        if stock_entry:assert not frappe.db.exists('Stock Entry',stock_entry.name)
        assert all(not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names)
        assert all(not frappe.db.exists('DS Execution Record',name) for name in execution_names)
        if stock_entry:
            assert frappe.db.count('Stock Ledger Entry',{
                'voucher_type':'Stock Entry','voucher_no':stock_entry.name,
            })==0
        if warehouses:
            assert {key:quantity(name) for key,name in warehouses.items()}==pre_bins
        if delete_linked_ledger_entries_before is not None:
            assert int(frappe.db.get_single_value(
                'Accounts Settings','delete_linked_ledger_entries'
            ) or 0)==delete_linked_ledger_entries_before
    finally:frappe.destroy()
'''
    result=subprocess.run([
        'docker','exec','-i','dsherp-validation-backend-1',
        '/home/frappe/frappe-bench/env/bin/python','-',
    ],input=script,text=True,capture_output=True,timeout=120)
    assert result.returncode==0,result.stderr


def test_stock_impact_registry_matches_fixed_v15_rejected_stock_schema():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.stock_impact import action_impact
try:
    frappe.set_user('Administrator')
    company=frappe.get_all('Company',pluck='name')
    assert len(company)==1,company
    warehouses={}
    for key,label in (
        ('raw','DSHERP 制造测试合成原料仓'),
        ('wip','DSHERP 制造测试合成在制仓'),
        ('subcontracting','DSHERP 制造测试合成委外仓'),
        ('finished','DSHERP 制造测试合成成品仓'),
    ):
        matches=frappe.get_all('Warehouse',filters={
            'warehouse_name':label,'company':company[0],'is_group':0,
        },pluck='name')
        assert len(matches)==1,(key,matches)
        warehouses[key]=matches[0]

    pr_meta=frappe.get_meta('Purchase Receipt Item')
    assert all(pr_meta.get_field(field) for field in (
        'stock_qty','rejected_qty','conversion_factor','warehouse','rejected_warehouse'
    ))
    purchase_receipt=frappe.get_doc({
        'doctype':'Purchase Receipt','company':company[0],'items':[{
            'item_code':'DSHERP-MFG-SYN-RM','stock_qty':2,'stock_uom':'Nos',
            'warehouse':warehouses['raw'],'rejected_qty':1,'conversion_factor':2,
            'rejected_warehouse':warehouses['wip'],
        }],
    })
    assert action_impact(purchase_receipt,'submit')=={'kind':'stock','entries':[
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':2,'uom':'Nos','warehouse':warehouses['raw']},
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':2,'uom':'Nos','warehouse':warehouses['wip']},
    ]}

    scr_item_meta=frappe.get_meta('Subcontracting Receipt Item')
    scr_supplied_meta=frappe.get_meta('Subcontracting Receipt Supplied Item')
    assert not scr_item_meta.get_field('stock_qty')
    assert all(scr_item_meta.get_field(field) for field in (
        'qty','conversion_factor','stock_uom','warehouse','rejected_qty','rejected_warehouse'
    ))
    assert all(scr_supplied_meta.get_field(field) for field in (
        'rm_item_code','consumed_qty','stock_uom'
    ))
    assert not scr_supplied_meta.get_field('supplier_warehouse')
    assert frappe.get_meta('Subcontracting Receipt').get_field('supplier_warehouse')
    receipt=frappe.get_doc({
        'doctype':'Subcontracting Receipt','company':company[0],
        'supplier_warehouse':warehouses['subcontracting'],'items':[{
            'item_code':'DSHERP-MFG-SYN-FG','qty':1,'conversion_factor':2,
            'stock_uom':'Nos','warehouse':warehouses['finished'],'rejected_qty':1,
            'rejected_warehouse':warehouses['wip'],
        }],'supplied_items':[{
            'rm_item_code':'DSHERP-MFG-SYN-RM','consumed_qty':3,'stock_uom':'Nos',
        }],
    })
    assert action_impact(receipt,'submit')=={'kind':'stock','entries':[
        {'item_code':'DSHERP-MFG-SYN-FG','quantity':2,'uom':'Nos','warehouse':warehouses['wip']},
        {'item_code':'DSHERP-MFG-SYN-FG','quantity':2,'uom':'Nos','warehouse':warehouses['finished']},
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':-3,'uom':'Nos','warehouse':warehouses['subcontracting']},
    ]}
    stock_entry=frappe.get_doc({
        'doctype':'Stock Entry','company':company[0],'items':[
            {'item_code':'DSHERP-MFG-SYN-RM','transfer_qty':0.1,'stock_uom':'Nos',
             's_warehouse':warehouses['raw'],'t_warehouse':warehouses['wip']},
            {'item_code':'DSHERP-MFG-SYN-RM','transfer_qty':0.2,'stock_uom':'Nos',
             's_warehouse':warehouses['raw'],'t_warehouse':warehouses['wip']},
        ],
    })
    normalized={'kind':'stock','entries':[
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':-0.3,'uom':'Nos','warehouse':warehouses['raw']},
        {'item_code':'DSHERP-MFG-SYN-RM','quantity':0.3,'uom':'Nos','warehouse':warehouses['wip']},
    ]}
    assert action_impact(stock_entry,'submit')==normalized
    assert action_impact(stock_entry,'cancel')=={'kind':'stock','entries':[
        {**entry,'quantity':-entry['quantity']} for entry in normalized['entries']
    ]}
    zero=frappe.get_doc({
        'doctype':'Stock Entry','company':company[0],'items':[{
            'item_code':'DSHERP-MFG-SYN-RM','transfer_qty':0.3,'stock_uom':'Nos',
            's_warehouse':warehouses['raw'],'t_warehouse':warehouses['raw'],
        }],
    })
    assert action_impact(zero,'submit')=={'kind':'none','entries':[]}

    unsupported=[
        frappe.get_doc({
            'doctype':'Purchase Receipt','company':company[0],'items':[{
                'item_code':'DSHERP-MFG-SYN-RM','stock_qty':1,'stock_uom':'Nos',
                'warehouse':warehouses['wip'],'from_warehouse':warehouses['raw'],
            }],
        }),
        frappe.get_doc({
            'doctype':'Purchase Receipt','company':company[0],'is_return':1,'items':[{
                'item_code':'DSHERP-MFG-SYN-RM','stock_qty':-1,'stock_uom':'Nos',
                'warehouse':warehouses['raw'],
            }],
        }),
        frappe.get_doc({
            'doctype':'Delivery Note','company':company[0],'packed_items':[{
                'item_code':'DSHERP-MFG-SYN-RM','qty':1,'warehouse':warehouses['raw'],
            }],'items':[{
                'item_code':'DSHERP-MFG-SYN-FG','stock_qty':1,'stock_uom':'Nos',
                'warehouse':warehouses['finished'],
            }],
        }),
        frappe.get_doc({
            'doctype':'Delivery Note','company':company[0],'items':[{
                'item_code':'DSHERP-MFG-SYN-RM','stock_qty':1,'stock_uom':'Nos',
                'warehouse':warehouses['raw'],'target_warehouse':warehouses['wip'],
            }],
        }),
        frappe.get_doc({
            'doctype':'Delivery Note','company':company[0],'is_return':1,'items':[{
                'item_code':'DSHERP-MFG-SYN-RM','stock_qty':-1,'stock_uom':'Nos',
                'warehouse':warehouses['raw'],
            }],
        }),
        frappe.get_doc({
            'doctype':'Subcontracting Receipt','company':company[0],'is_return':1,
            'supplier_warehouse':warehouses['subcontracting'],'items':[{
                'item_code':'DSHERP-MFG-SYN-FG','qty':-1,'conversion_factor':1,
                'stock_uom':'Nos','warehouse':warehouses['finished'],
            }],
        }),
    ]
    for document in unsupported:
        try:action_impact(document,'submit');raise AssertionError(document.doctype+' unsupported shape accepted')
        except frappe.ValidationError as error:
            assert '库存影响无法确定' in str(error) and '暂不支持' in str(error),error

    original_get_field=receipt.meta.get_field
    receipt.meta.get_field=lambda fieldname:None if fieldname=='supplier_warehouse' else original_get_field(fieldname)
    try:action_impact(receipt,'submit');raise AssertionError('missing SCR supplier_warehouse meta accepted')
    except frappe.ValidationError as error:
        assert '库存影响无法确定' in str(error) and 'supplier_warehouse' in str(error),error
    finally:receipt.meta.get_field=original_get_field
    try:
        action_impact(frappe.get_doc({
            'doctype':'Delivery Note','company':company[0],'items':[{
                'item_code':'DSHERP-MFG-SYN-RM','stock_qty':1,'stock_uom':'Nos',
            }],
        }),'submit')
        raise AssertionError('stock impact guessed a missing warehouse')
    except frappe.ValidationError as error:
        assert '库存影响无法确定' in str(error) and '仓库' in str(error),error
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run([
        'docker','exec','-i','dsherp-validation-backend-1',
        '/home/frappe/frappe-bench/env/bin/python','-',
    ],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
