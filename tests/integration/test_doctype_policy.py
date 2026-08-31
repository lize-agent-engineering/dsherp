"""Native DocType policies gate tools and rotate pending authorization."""
import subprocess


def test_new_supplier_policy_controls_real_context_read_and_create_proposal():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import revision

tag=uuid.uuid4().hex;actor='supplier-policy-'+tag+'@example.invalid'
try:
    frappe.set_user('Administrator')
    meta=frappe.get_meta('Supplier')
    assert not meta.issingle and not meta.is_submittable and meta.title_field=='supplier_name'
    native=frappe.db.get_value('DocPerm',{'parent':'Supplier','role':'Purchase Master Manager'},
        ['read','write','create'],as_dict=True)
    assert native and native.read and native.write and native.create,native
    supplier=frappe.get_doc('Supplier','DSHERP 制造测试合成供应商')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic Supplier policy','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Purchase Master Manager'}]}).insert()

    existing=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Supplier'},'name')
    assert not existing,'Supplier policy belongs to a later manufacturing segment'

    def new_run():
        capability=uuid.uuid4().hex
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
            'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
            'permission_revision':revision(actor),
            'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
        return {'run_id':run.name,'capability':capability}

    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic Supplier policy'}).insert(ignore_permissions=True)
    missing=new_run();frappe.set_user('Guest')
    try:
        run_tool(**missing,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
        raise AssertionError('missing policy allowed Supplier read')
    except frappe.PermissionError as error:
        assert '缺少 DS DocType 策略' in str(error),error

    frappe.set_user('Administrator')
    policy=frappe.get_doc({'doctype':'DS Doctype Policy','target_doctype':'Supplier','enabled':0,
        'allow_read':1,'allow_create':1,'allow_update':0,'allow_submit':0,'allow_cancel':0,
        'allow_fill':0,'company_scope':None,'routes':[]}).insert()
    frappe.set_user(actor);disabled=new_run();frappe.set_user('Guest')
    try:
        run_tool(**disabled,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
        raise AssertionError('disabled policy allowed Supplier read')
    except frappe.PermissionError as error:
        assert 'DS DocType 策略未启用' in str(error),error

    frappe.set_user('Administrator');policy.enabled=1;policy.allow_create=0;policy.save()
    frappe.set_user(actor);denied=new_run();frappe.set_user('Guest')
    record=run_tool(**denied,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
    assert record['name']==supplier.name and record['doctype']=='Supplier'
    schema=run_tool(**denied,tool='erp_read_schema',arguments={'doctype':'Supplier'})
    values={'supplier_name':'DSHERP 瞬时策略供应商 '+tag,
        'supplier_group':supplier.supplier_group,'supplier_type':supplier.supplier_type}
    create={'doctype':'Supplier','values':values,'version':str(schema['modified'])}
    try:
        run_tool(**denied,tool='erp_propose_create',arguments=create)
        raise AssertionError('allow_create=0 accepted Supplier create proposal')
    except frappe.PermissionError as error:
        assert '未允许 Supplier 的 create 操作' in str(error),error

    frappe.set_user('Administrator');policy.allow_create=1;policy.save()
    frappe.set_user(actor);allowed=new_run();frappe.set_user('Guest')
    schema=run_tool(**allowed,tool='erp_read_schema',arguments={'doctype':'Supplier'})
    create['version']=str(schema['modified'])
    before=frappe.db.count('Supplier')
    proposal=run_tool(**allowed,tool='erp_propose_create',arguments=create)
    assert proposal['doctype']=='Supplier' and proposal['action']=='create' and proposal['status']=='Pending'
    assert frappe.db.count('Supplier')==before
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_policy_rows_gate_tool_doctypes():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_execution
from dsherp_bridge.context_permissions import run_revision

actor='policy-gate-'+uuid.uuid4().hex+'@example.invalid'
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic policy gate','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic policy gate'}).insert(ignore_permissions=True)

    def new_run():
        capability=uuid.uuid4().hex
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
            'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
            'permission_revision':run_revision(actor,'operation'),
            'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
        return {'run_id':run.name,'capability':capability}

    frappe.set_user('Administrator')
    policy_name=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Item'},'name')
    policy=(frappe.get_doc('DS Doctype Policy',policy_name) if policy_name else
        frappe.get_doc({'doctype':'DS Doctype Policy','target_doctype':'Item'}).insert())
    policy.enabled=0;policy.allow_read=0;policy.allow_create=0;policy.allow_update=0
    policy.allow_submit=0;policy.allow_cancel=0;policy.allow_fill=0;policy.save()
    frappe.set_user(actor)
    try:
        frappe.get_doc('DS Doctype Policy',policy.name).check_permission('read')
        raise AssertionError('business user read governance policy')
    except frappe.PermissionError:
        pass
    disabled=new_run()
    try:
        context_execution.run_tool(**disabled,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
        raise AssertionError('disabled policy allowed read')
    except frappe.PermissionError as error:
        assert '策略' in str(error),error

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.enabled=1;policy.allow_read=1;policy.save();frappe.set_user(actor)
    read_only=new_run()
    item=context_execution.run_tool(**read_only,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    arguments={'doctype':'Item','name':item['name'],'values':{'item_name':'Policy proposal only'},'version':str(item['modified'])}
    try:
        context_execution.run_tool(**read_only,tool='erp_propose_update',arguments=arguments)
        raise AssertionError('read-only policy allowed update proposal')
    except frappe.PermissionError as error:
        assert '策略' in str(error),error

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.allow_update=1;policy.save();frappe.set_user(actor)
    allowed=new_run()
    item=context_execution.run_tool(**allowed,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    arguments={'doctype':'Item','name':item['name'],'values':{'item_name':'Policy proposal only'},'version':str(item['modified'])}
    proposal=context_execution.run_tool(**allowed,tool='erp_propose_update',arguments=arguments)
    assert proposal['status']=='Pending'
    assert frappe.db.get_value('Item',item['name'],'item_name')!='Policy proposal only'
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_policy_change_rotates_revision():
    script = r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_permissions import revision
from dsherp_bridge.operations import propose_update,confirm

tag=uuid.uuid4().hex;actor='policy-revision-'+tag+'@example.invalid';conversation=None;proposal_id=None
policy_name=None;original_policy=None
item_name='DSHERP-TEST-ITEM';before_name=None
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic policy revision','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    policy_name=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Item'},'name')
    if policy_name:
        policy=frappe.get_doc('DS Doctype Policy',policy_name)
        original_policy={field:policy.get(field) for field in ('enabled','allow_read','allow_create','allow_update',
            'allow_submit','allow_cancel','allow_fill','company_scope')}
        original_policy['routes']=[{key:row.get(key) for key in ('route_name','method_path','target_doctype')} for row in policy.routes]
    else:
        policy=frappe.get_doc({'doctype':'DS Doctype Policy','target_doctype':'Item'}).insert();policy_name=policy.name
    policy.enabled=1;policy.allow_read=1;policy.allow_create=0;policy.allow_update=1
    policy.allow_submit=0;policy.allow_cancel=0;policy.allow_fill=0;policy.company_scope=None
    policy.set('routes',[{'route_name':'delivery_note',
        'method_path':'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note',
        'target_doctype':'Delivery Note'}]);policy.save()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic policy revision'}).insert(ignore_permissions=True).name
    item=frappe.get_doc('Item',item_name);before_name=item.item_name
    before_revision=revision(actor)
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Must remain only proposed'},str(item.modified))
    proposal_id=proposal['id'];frappe.db.commit()

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.routes[0].route_name='delivery_note_changed';policy.save();frappe.db.commit()
    frappe.set_user(actor)
    assert revision(actor)!=before_revision
    result=confirm(proposal_id,proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Failed',result
    assert frappe.db.get_value('Item',item_name,'item_name')==before_name
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if proposal_id:
        for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
            frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if policy_name and frappe.db.exists('DS Doctype Policy',policy_name):
        if original_policy:
            policy=frappe.get_doc('DS Doctype Policy',policy_name)
            for field,value in original_policy.items():
                if field!='routes':policy.set(field,value)
            policy.set('routes',original_policy['routes']);policy.save()
        else:frappe.delete_doc('DS Doctype Policy',policy_name)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_disabled_policy_creation_rotates_revision_before_confirmation():
    script = r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_permissions import revision
from dsherp_bridge.operations import propose_update,confirm

tag=uuid.uuid4().hex;actor='disabled-policy-'+tag+'@example.invalid';conversation=None;proposal_id=None
item_name='DS-POLICY-'+tag
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic disabled policy revision','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    item=frappe.copy_doc(frappe.get_doc('Item','DSHERP-TEST-ITEM'))
    item.item_code=item_name;item.item_name='Before disabled policy';item.insert()
    item_policy=frappe.get_doc('DS Doctype Policy','Item')
    assert item_policy.enabled and item_policy.allow_read and item_policy.allow_update
    assert not frappe.db.exists('DS Doctype Policy','Supplier')
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic disabled policy revision'}).insert(ignore_permissions=True).name
    item=frappe.get_doc('Item',item_name)
    before_revision=revision(actor)
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Disabled policy must invalidate'},str(item.modified))
    proposal_id=proposal['id'];frappe.db.commit()

    frappe.set_user('Administrator')
    policy=frappe.get_doc({'doctype':'DS Doctype Policy','target_doctype':'Supplier','enabled':0,
        'allow_read':0,'allow_create':0,'allow_update':0,'allow_submit':0,'allow_cancel':0,'allow_fill':0}).insert()
    frappe.db.commit();frappe.set_user(actor)
    after_revision=revision(actor)
    assert after_revision!=before_revision,'disabled policy creation did not rotate authorization revision'
    result=confirm(proposal_id,proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Failed',result
    assert '权限或企业成员关系已变化' in result['error'],result
    assert frappe.db.get_value('Item',item_name,'item_name')=='Before disabled policy'
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if proposal_id:
        for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
            frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('DS Doctype Policy','Supplier'):
        frappe.delete_doc('DS Doctype Policy','Supplier')
    if frappe.db.exists('Item',item_name):frappe.delete_doc('Item',item_name,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr
