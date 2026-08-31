"""Mapped-document proposals freeze drafts and never write before confirmation."""
import subprocess


def test_make_freezes_mapped_result_and_rejects_drift():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import run_revision
from dsherp_bridge.operations import confirm,verify_execution

tag=uuid.uuid4().hex
actor='make-'+tag+'@example.invalid'
route_name='sales_order_to_delivery_note'
method_path='erpnext.selling.doctype.sales_order.sales_order.make_delivery_note'
policy_name=None;routes_before=None;delivery_count_before=None
order_name=None;conversation_names=[];run_names=[];proposal_names=[];target_names=[]

def new_run():
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic make '+tag}).insert(ignore_permissions=True)
    capability=uuid.uuid4().hex
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
        'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':run_revision(actor,'operation'),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
    conversation_names.append(conversation.name);run_names.append(run.name)
    return {'run_id':run.name,'capability':capability},run.name

def make_arguments(version):
    return {'source_doctype':'Sales Order','source_name':order_name,'source_version':version,'route':route_name}

try:
    frappe.set_user('Administrator')
    delivery_count_before=frappe.db.count('Delivery Note')
    policy_name=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Sales Order'},'name')
    assert policy_name
    policy=frappe.get_doc('DS Doctype Policy',policy_name)
    assert policy.enabled and policy.allow_read
    routes_before=[{key:row.get(key) for key in ('route_name','method_path','target_doctype')} for row in policy.routes]
    assert not any(row['route_name']==route_name for row in routes_before),routes_before
    policy.append('routes',{'route_name':route_name,'method_path':method_path,'target_doctype':'Delivery Note'})
    policy.save();frappe.db.commit()

    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic make actor','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Sales User'}]}).insert()
    frappe.set_user(actor)
    template=frappe.get_doc('Sales Order','SAL-ORD-2026-00001')
    order=frappe.copy_doc(template)
    order.po_no='DSHERP-MAKE-'+tag
    order.insert();order.submit();order_name=order.name;frappe.db.commit()
    assert order.docstatus==1

    # The exact source read and version must exist in the same run. Invalid and
    # forged calls create neither a proposal nor a mapped target.
    cap,_=new_run();frappe.set_user('Guest')
    rejected_before=frappe.db.count('DS Operation Proposal')
    try:run_tool(**cap,tool='erp_propose_make',arguments=make_arguments(str(order.modified)));raise AssertionError('unread source accepted')
    except frappe.ValidationError:pass
    assert frappe.db.count('DS Operation Proposal')==rejected_before
    read=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Sales Order','name':order_name})
    try:run_tool(**cap,tool='erp_propose_make',arguments=make_arguments('wrong-version'));raise AssertionError('mismatched source version accepted')
    except frappe.ValidationError:pass
    try:run_tool(**cap,tool='erp_propose_make',arguments={**make_arguments(str(read['modified'])),'options':{}});raise AssertionError('make options accepted')
    except frappe.ValidationError:pass
    assert frappe.db.count('DS Operation Proposal')==rejected_before
    assert frappe.db.count('Delivery Note')==delivery_count_before

    # Freeze the full real mapped draft, including scalar fields and children,
    # without writing a Delivery Note.
    cap,drift_run=new_run();frappe.set_user('Guest')
    read=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Sales Order','name':order_name})
    proposal=run_tool(**cap,tool='erp_propose_make',arguments=make_arguments(str(read['modified'])))
    proposal_names.append(proposal['id'])
    assert proposal['proposal_type']=='make' and proposal['action']=='make'
    assert proposal['source_doctype']=='Sales Order' and proposal['source_name']==order_name
    assert proposal['source_version']==str(read['modified'])
    assert proposal['route']==route_name and proposal['method_path']==method_path
    assert proposal['target_doctype']=='Delivery Note'
    assert proposal['target']['doctype']=='Delivery Note' and proposal['target']['customer']==template.customer
    assert proposal['target']['items'] and proposal['target']['items'][0]['against_sales_order']==order_name
    assert 'name' not in proposal['target'] and 'name' not in proposal['target']['items'][0]
    stored=json.loads(frappe.db.get_value('DS Operation Proposal',proposal['id'],'payload'))
    assert stored['proposal_type']=='make' and stored['target']['ignore_pricing_rule']==0
    assert all(stored['target'][key]==value for key,value in proposal['target'].items())
    assert frappe.db.count('Delivery Note')==delivery_count_before
    frappe.db.set_value('DS Model Run',drift_run,'status','Succeeded');frappe.db.commit()

    # po_no is a native allow_on_submit field and is mapped into Delivery Note.
    frappe.set_user(actor);changed=frappe.get_doc('Sales Order',order_name)
    changed.po_no='DSHERP-MAKE-CHANGED-'+tag;changed.save();frappe.db.commit()
    failed=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert failed['status']=='Failed' and '重新提出' in failed['error'],failed
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    assert frappe.db.count('Delivery Note')==delivery_count_before
    assert failed['status']!='Unknown'

    # A fresh source read creates a control proposal whose frozen target is
    # inserted once under the mapped business user.
    cap,control_run=new_run();frappe.set_user('Guest')
    read=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Sales Order','name':order_name})
    control=run_tool(**cap,tool='erp_propose_make',arguments=make_arguments(str(read['modified'])))
    proposal_names.append(control['id'])
    assert frappe.db.count('Delivery Note')==delivery_count_before
    frappe.db.set_value('DS Model Run',control_run,'status','Succeeded');frappe.db.commit()
    frappe.set_user(actor)
    succeeded=confirm(control['id'],control['digest'],uuid.uuid4().hex)
    assert succeeded['status']=='Succeeded' and succeeded['doctype']=='Delivery Note',succeeded
    target_names.append(succeeded['name'])
    assert frappe.db.count('Delivery Note')==delivery_count_before+1
    saved=frappe.get_doc('Delivery Note',succeeded['name'])
    assert saved.docstatus==0 and saved.modified_by==actor
    assert str(saved.modified)==succeeded['version']
    assert saved.items[0].against_sales_order==order_name
    again=confirm(control['id'],control['digest'],uuid.uuid4().hex)
    assert again==succeeded
    assert frappe.db.count('Delivery Note')==delivery_count_before+1
    assert frappe.db.count('DS Execution Record',{'proposal':control['id']})==1
    verified=verify_execution(control['id'])
    assert verified['execution']==succeeded and verified['observed']['name']==succeeded['name']
    assert verified['matches_proposal'] is True
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if order_name:
        target_names.extend(frappe.get_all('Delivery Note Item',filters={'against_sales_order':order_name},pluck='parent'))
    for name in sorted(set(filter(None,target_names))):
        if frappe.db.exists('Delivery Note',name):
            doc=frappe.get_doc('Delivery Note',name)
            if doc.docstatus==1:doc.cancel()
            frappe.delete_doc('Delivery Note',name,ignore_permissions=True)
    for proposal_id in sorted(set(proposal_names + (frappe.get_all('DS Operation Proposal',filters={'conversation':['in',conversation_names]},pluck='name') if conversation_names else []))):
        for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
            frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
    for run_id in run_names:
        if frappe.db.exists('DS Model Run',run_id):frappe.delete_doc('DS Model Run',run_id,ignore_permissions=True)
    for conversation in conversation_names:
        if frappe.db.exists('DS Conversation',conversation):frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if order_name and frappe.db.exists('Sales Order',order_name):
        doc=frappe.get_doc('Sales Order',order_name)
        if doc.docstatus==1:doc.cancel()
        frappe.delete_doc('Sales Order',order_name,ignore_permissions=True)
    if policy_name and routes_before is not None and frappe.db.exists('DS Doctype Policy',policy_name):
        policy=frappe.get_doc('DS Doctype Policy',policy_name);policy.set('routes',routes_before);policy.save()
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
    if policy_name is not None and routes_before is not None and delivery_count_before is not None:
        os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
        try:
            frappe.set_user('Administrator')
            routes_after=[{key:row.get(key) for key in ('route_name','method_path','target_doctype')}
                for row in frappe.get_doc('DS Doctype Policy',policy_name).routes]
            assert routes_after==routes_before,(routes_before,routes_after)
            assert frappe.db.count('Delivery Note')==delivery_count_before
            assert not frappe.db.exists('User',actor)
            assert not any(frappe.db.exists('Sales Order',name) for name in [order_name] if name)
            assert all(not frappe.db.exists('DS Conversation',name) for name in conversation_names)
            assert all(not frappe.db.exists('DS Model Run',name) for name in run_names)
        finally:frappe.destroy()
'''
    result=subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=90,
    )
    assert result.returncode==0,result.stderr
