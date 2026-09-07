"""Unknown action stock effects must fail before any durable intent exists.

The last two tests leave real durable state on the Site: a synthetic submittable DocType created
by DDL (with its policy row, its rows and its table), a temporary actor, a conversation, a draft
Stock Entry, and permlevel Property Setters that change the cached meta of two shipped DocTypes.
A host-side timeout kills the docker client, not the interpreter inside the container, so the
in-script `finally` may never run and an orphan `tab...` table would survive every other sweep.
So the identifiers are minted here on the host and registered with the residue registry BEFORE
the script runs; the in-script cleanup stays exactly as it was, as the happy path.
"""
import uuid

from site_exec import run_site_script

SITE = 'dsherp-validation.localhost'
# Idempotent: the sweep deletes the permlevel Property Setter rows, this drops the meta those rows
# poisoned so the next reader of Stock Entry sees the shipped permissions again.
CLEAR_STOCK_META = r'''
frappe.clear_cache(doctype='Stock Entry')
frappe.clear_cache(doctype='Stock Entry Detail')
print('cleared')
'''


def test_action_impact_uses_exact_stock_and_no_stock_registries():
    # Rollback-only: this body commits nothing, so there is nothing to register.
    run_site_script(SITE, r'''
from dsherp_bridge.stock_impact import action_impact,validate_impact_read_access
try:
    canonical={'kind':'none','entries':[]}
    for doctype in (
        'Sales Order','Work Order','Purchase Order','Subcontracting Order',
    ):
        assert action_impact(frappe.new_doc(doctype),'submit')==canonical,doctype
    unknown=frappe.new_doc('ToDo')
    try:
        validate_impact_read_access(unknown,'Administrator')
        raise AssertionError('unknown DocType passed impact field validation')
    except frappe.ValidationError as error:
        assert 'ToDo' in str(error) and '库存影响尚未实现' in str(error),error
finally:
    frappe.db.rollback()
''', timeout=30)


def test_unknown_submittable_doctype_cannot_propose_an_unverified_stock_impact(residue):
    # The tag is minted on the host, not in the script, because the registry has to know the
    # DocType name, the actor and the conversation title before the script can create them.
    tag = uuid.uuid4().hex
    doctype = 'DS Unknown Impact ' + tag[:10]
    actor = 'unknown-impact-' + tag + '@example.invalid'
    residue.doctype(SITE, doctype)  # its rows, its DS Doctype Policy row, the DocType, then DROP TABLE
    residue.user(SITE, actor)
    residue.doc(SITE, 'DS Conversation', {'title': 'Unknown stock impact ' + tag})
    run_site_script(SITE, r'''
from dsherp_bridge.operations import propose_action

tag=TAG
doctype='DS Unknown Impact '+tag[:10]
actor='unknown-impact-'+tag+'@example.invalid'
conversation=None
record=None
policy=None
proposal_names=[]
execution_names=[]
try:
    assert not frappe.db.exists('DocType',doctype)
    assert not frappe.db.table_exists(doctype)
    frappe.get_doc({
        'doctype':'DocType','name':doctype,'module':'DSHERP Bridge','custom':1,
        'autoname':'hash','is_submittable':1,
        'fields':[{'fieldname':'subject','label':'Subject','fieldtype':'Data','reqd':1}],
        'permissions':[{
            'role':'Sales User','read':1,'write':1,'create':1,'submit':1,'cancel':1,
        }],
    }).insert()
    policy=frappe.get_doc({
        'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':doctype,'enabled':1,
        'allow_read':1,'allow_create':0,'allow_update':0,'allow_submit':1,
        'allow_cancel':1,'allow_fill':0,
    }).insert().name
    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Unknown impact actor',
        'enabled':1,'send_welcome_email':0,'roles':[{'role':'Sales User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Unknown stock impact '+tag,
    }).insert(ignore_permissions=True).name
    record=frappe.get_doc({'doctype':doctype,'subject':'Must remain draft'}).insert().name
    frappe.db.commit()

    document=frappe.get_doc(doctype,record)
    document.check_permission('read');document.check_permission('submit')
    proposals_before=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
    executions_before=frappe.db.count('DS Execution Record')
    try:
        propose_action(conversation,doctype,record,'submit',str(document.modified))
        raise AssertionError('unknown stock impact produced a none proposal')
    except frappe.ValidationError as error:
        message=str(error)
        assert doctype in message,message
        assert '库存影响尚未实现' in message,message
        assert '不能安全确认' in message,message
    assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==proposals_before==0
    assert frappe.db.count('DS Execution Record')==executions_before
    assert frappe.db.get_value(doctype,record,'docstatus')==0
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        proposal_names=frappe.get_all(
            'DS Operation Proposal',filters={'conversation':conversation},pluck='name'
        )
        for proposal_name in proposal_names:
            for execution_name in frappe.get_all(
                'DS Execution Record',filters={'proposal':proposal_name},pluck='name'
            ):
                execution_names.append(execution_name)
                frappe.db.delete('DS Execution Record',{'name':execution_name})
            frappe.db.delete('DS Operation Proposal',{'name':proposal_name})
        for run_name in frappe.get_all(
            'DS Model Run',filters={'conversation':conversation},pluck='name'
        ):
            frappe.db.delete('DS Run Event',{'run':run_name})
            frappe.db.delete('DS Model Run',{'name':run_name})
    if record and frappe.db.exists(doctype,record):
        document=frappe.get_doc(doctype,record)
        if document.docstatus==1:document.cancel()
        frappe.delete_doc(doctype,record,force=True)
    if policy and frappe.db.exists('DS Doctype Policy',policy):
        frappe.delete_doc('DS Doctype Policy',policy,force=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,force=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,force=True)
    if frappe.db.exists('DocType',doctype):frappe.delete_doc('DocType',doctype,force=True)
    frappe.db.commit()
    if frappe.db.table_exists(doctype):
        frappe.db.sql_ddl('DROP TABLE `tab'+doctype+'`')
    assert not frappe.db.exists('DocType',doctype)
    assert not frappe.db.table_exists(doctype)
    assert not frappe.db.exists('User',actor)
    assert not frappe.db.exists('DS Doctype Policy',{'target_doctype':doctype})
    if conversation:
        assert not frappe.db.exists('DS Conversation',conversation)
        assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==0
        assert frappe.db.count('DS Model Run',{'conversation':conversation})==0
    assert all(not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names)
    assert all(not frappe.db.exists('DS Execution Record',name) for name in execution_names)
'''.replace('TAG', repr(tag)), timeout=60)


def test_registered_stock_impact_checks_parent_and_child_field_access_before_values(residue):
    tag = uuid.uuid4().hex
    actor = 'impact-permission-' + tag + '@example.invalid'
    # The setters carry no tag of their own; the script asserts none of them exist before it
    # starts, so "every permlevel setter on these two fields" is this test's own residue.
    residue.doc(SITE, 'Property Setter', {'doc_type': ['in', ['Stock Entry', 'Stock Entry Detail']],
                                          'field_name': ['in', ['items', 's_warehouse']],
                                          'property': 'permlevel'})
    residue.restore(SITE, 'stock entry meta cache', CLEAR_STOCK_META)
    residue.doc(SITE, 'DS Conversation', {'title': 'Impact field permission ' + tag})
    residue.doc(SITE, 'Stock Entry', {'owner': actor})
    residue.user(SITE, actor)
    run_site_script(SITE, r'''
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import nowdate
from dsherp_bridge.operations import propose_action

tag=TAG
actor='impact-permission-'+tag+'@example.invalid'
conversation=None
stock_entry=None
setter_names=[]
proposal_names=[]
execution_names=[]
cases=(
    ('Stock Entry','items','无权读取库存影响字段'),
    ('Stock Entry Detail','s_warehouse','无权读取库存影响明细字段'),
)

def clear_impact_meta():
    frappe.clear_cache(doctype='Stock Entry')
    frappe.clear_cache(doctype='Stock Entry Detail')

def remove_setter(setter_name):
    frappe.set_user('Administrator')
    if frappe.db.exists('Property Setter',setter_name):
        frappe.delete_doc('Property Setter',setter_name,force=True)
    frappe.db.commit();clear_impact_meta()

try:
    assert not frappe.get_all('Property Setter',filters={
        'doc_type':['in',['Stock Entry','Stock Entry Detail']],
        'field_name':['in',['items','s_warehouse']],'property':'permlevel',
    })
    company=frappe.get_all('Company',pluck='name')
    assert len(company)==1,company
    warehouses={}
    for key,label in (
        ('source','DSHERP 制造测试合成原料仓'),
        ('target','DSHERP 制造测试合成在制仓'),
    ):
        names=frappe.get_all('Warehouse',filters={
            'warehouse_name':label,'company':company[0],'is_group':0,
        },pluck='name')
        assert len(names)==1,(key,names)
        warehouses[key]=names[0]
    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Impact permission actor',
        'enabled':1,'send_welcome_email':0,'roles':[{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Impact field permission '+tag,
    }).insert(ignore_permissions=True).name
    stock_entry=frappe.get_doc({
        'doctype':'Stock Entry','stock_entry_type':'Material Transfer',
        'purpose':'Material Transfer','company':company[0],
        'posting_date':str(nowdate()),'items':[{
            'item_code':'DSHERP-MFG-SYN-RM','qty':1,
            's_warehouse':warehouses['source'],'t_warehouse':warehouses['target'],
            'basic_rate':1,
        }],
    }).insert().name
    frappe.db.commit()
    executions_before=frappe.db.count('DS Execution Record')

    for target_doctype,fieldname,expected_error in cases:
        frappe.set_user('Administrator')
        setter=make_property_setter(
            target_doctype,fieldname,'permlevel',1,'Int',for_doctype=False,
        )
        setter_names.append(setter.name)
        frappe.db.commit();clear_impact_meta()
        frappe.set_user(actor)
        document=frappe.get_doc('Stock Entry',stock_entry)
        document.check_permission('read');document.check_permission('submit')
        proposals_before=frappe.db.count(
            'DS Operation Proposal',{'conversation':conversation}
        )
        try:
            propose_action(
                conversation,'Stock Entry',stock_entry,'submit',str(document.modified)
            )
            raise AssertionError(fieldname+' hidden stock value produced a proposal')
        except frappe.PermissionError as error:
            assert str(error)==expected_error,error
        assert frappe.db.count(
            'DS Operation Proposal',{'conversation':conversation}
        )==proposals_before
        assert frappe.db.get_value('Stock Entry',stock_entry,'docstatus')==0
        remove_setter(setter.name)
    assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==0
    assert frappe.db.count('DS Execution Record')==executions_before
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    for setter_name in setter_names:
        if frappe.db.exists('Property Setter',setter_name):
            frappe.delete_doc('Property Setter',setter_name,force=True)
    frappe.db.commit();clear_impact_meta()
    if conversation:
        proposal_names=frappe.get_all(
            'DS Operation Proposal',filters={'conversation':conversation},pluck='name'
        )
        for proposal_name in proposal_names:
            for execution_name in frappe.get_all(
                'DS Execution Record',filters={'proposal':proposal_name},pluck='name'
            ):
                execution_names.append(execution_name)
                frappe.db.delete('DS Execution Record',{'name':execution_name})
            frappe.db.delete('DS Operation Proposal',{'name':proposal_name})
        for run_name in frappe.get_all(
            'DS Model Run',filters={'conversation':conversation},pluck='name'
        ):
            frappe.db.delete('DS Run Event',{'run':run_name})
            frappe.db.delete('DS Model Run',{'name':run_name})
    if stock_entry and frappe.db.exists('Stock Entry',stock_entry):
        document=frappe.get_doc('Stock Entry',stock_entry)
        if document.docstatus==1:document.cancel()
        frappe.delete_doc('Stock Entry',stock_entry,force=True)
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,force=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor,force=True)
    frappe.db.commit();clear_impact_meta()
    assert not frappe.get_all('Property Setter',filters={
        'name':['in',setter_names],
    })
    if stock_entry:assert not frappe.db.exists('Stock Entry',stock_entry)
    if conversation:
        assert not frappe.db.exists('DS Conversation',conversation)
        assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==0
        assert frappe.db.count('DS Model Run',{'conversation':conversation})==0
    assert not frappe.db.exists('User',actor)
    assert all(not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names)
    assert all(not frappe.db.exists('DS Execution Record',name) for name in execution_names)
'''.replace('TAG', repr(tag)), timeout=80)
