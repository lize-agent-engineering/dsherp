"""Restricted batch search uses real alpha permissions and leaves no records."""
import subprocess


def test_filtered_search_reads_bins_and_records_only_successful_sources():
    script=r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,flt,now_datetime

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()

from dsherp_bridge.context_execution import authorize_sources,run_tool
from dsherp_bridge.context_permissions import run_revision

tag=uuid.uuid4().hex
actor='query-bins-'+tag+'@example.invalid'
conversation=None
runs=[]
raw_item='DSHERP-MFG-SYN-RM'
finished_item='DSHERP-MFG-SYN-FG'
field_permlevels_before={}

def new_run(domain):
    capability=uuid.uuid4().hex
    frappe.set_user(actor)
    run=frappe.get_doc({
        'doctype':'DS Model Run','conversation':conversation,'domain':domain,
        'status':'Running','page_context':json.dumps({
            'schema_version':1,'page_type':'unknown','route':[],
        }),
        'permission_revision':run_revision(actor,domain),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]',
    }).insert(ignore_permissions=True)
    runs.append(run.name)
    return {'run_id':run.name,'capability':capability}

def sources(cap):
    return json.loads(frappe.db.get_value('DS Model Run',cap['run_id'],'sources'))

try:
    frappe.set_user('Administrator')
    policy=frappe.db.get_value(
        'DS Doctype Policy',{'target_doctype':'Bin'},['enabled','allow_read'],as_dict=True,
    )
    assert policy and policy.enabled and policy.allow_read,policy
    assert frappe.db.get_value(
        'DocPerm',{'parent':'Bin','role':'Stock User'},'read'
    )==1
    company=frappe.get_all('Company',pluck='name')
    assert company==['DSHERP 原生验收测试公司'],company
    def warehouse(label):
        names=frappe.get_all('Warehouse',filters={
            'warehouse_name':label,'company':company[0],'is_group':0,
        },pluck='name')
        assert len(names)==1,(label,names)
        return names[0]
    raw_warehouse=warehouse('DSHERP 制造测试合成原料仓')
    finished_warehouse=warehouse('DSHERP 制造测试合成成品仓')
    expected={
        (row.item_code,row.warehouse):(flt(row.actual_qty),flt(row.projected_qty))
        for row in frappe.get_all('Bin',filters={
            'item_code':['in',[raw_item,finished_item]],
            'warehouse':['in',[raw_warehouse,finished_warehouse]],
        },fields=['item_code','warehouse','actual_qty','projected_qty'])
    }
    assert set(expected)=={
        (raw_item,raw_warehouse),(finished_item,finished_warehouse),
    },expected
    assert not frappe.db.exists('User',actor)
    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic Bin query actor',
        'enabled':1,'send_welcome_email':0,'roles':[{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic filtered search '+tag,
    }).insert(ignore_permissions=True).name

    query_cap=new_run('query')
    frappe.set_user('Guest')
    single=run_tool(**query_cap,tool='erp_search_records',arguments={
        'doctype':'Bin',
        'filters':{'item_code':raw_item,'warehouse':['=',raw_warehouse]},
        'fields':['item_code','warehouse','actual_qty','projected_qty'],
    })
    assert len(single)==1,single
    assert set(single[0])=={
        'name','modified','item_code','warehouse','actual_qty','projected_qty',
    },single
    assert (flt(single[0]['actual_qty']),flt(single[0]['projected_qty']))==expected[
        (raw_item,raw_warehouse)
    ]
    first_source=sources(query_cap)[-1]
    assert first_source['fields']==[
        'name','modified','item_code','warehouse','actual_qty','projected_qty',
    ],first_source
    assert first_source['records']==[single[0]['name']]
    assert first_source['record_versions']=={
        single[0]['name']:str(single[0]['modified']),
    }
    assert first_source['match_fields']==['item_code','warehouse'],first_source

    filter_only=run_tool(**query_cap,tool='erp_search_records',arguments={
        'doctype':'Bin',
        'filters':{'projected_qty':['=',expected[(raw_item,raw_warehouse)][1]]},
        'fields':['item_code'],
    })
    assert filter_only,filter_only
    filter_only_source=sources(query_cap)[-1]
    assert filter_only_source['fields']==['name','modified','item_code'],filter_only_source
    assert filter_only_source['match_fields']==['projected_qty'],filter_only_source
    assert 'projected_qty' not in filter_only_source['fields']

    titled=run_tool(**query_cap,tool='erp_search_records',arguments={
        'doctype':'Item','query':raw_item,
    })
    assert any(row['name']==raw_item for row in titled),titled
    title_source=sources(query_cap)[-1]
    assert title_source['fields']==['name','modified'],title_source
    assert title_source['match_fields']==['name','item_name'],title_source
    legacy_title_source={**title_source,
        'arguments':{'doctype':'Item','query':raw_item},
        'fields':['item_name'],
    }
    legacy_title_source.pop('match_fields')
    frappe.set_user(actor)
    authorize_sources([legacy_title_source])

    operation_cap=new_run('operation')
    frappe.set_user('Guest')
    batch=run_tool(**operation_cap,tool='erp_search_records',arguments={
        'doctype':'Bin','query':'',
        'filters':{
            'item_code':['in',[raw_item,finished_item]],
            'warehouse':['in',[raw_warehouse,finished_warehouse]],
        },
        'fields':['item_code','warehouse','actual_qty','projected_qty'],
    })
    assert [row['name'] for row in batch]==sorted(row['name'] for row in batch),batch
    assert {
        (row['item_code'],row['warehouse']):(flt(row['actual_qty']),flt(row['projected_qty']))
        for row in batch
    }==expected,batch
    successful_source_count=len(sources(operation_cap))

    invalid_arguments=[
        {'doctype':'Bin','filters':{'item_code':['!=',raw_item]},'fields':['item_code']},
        {'doctype':'Bin','filters':{'owner':actor},'fields':['item_code']},
        {'doctype':'Bin','filters':{'item_code':['in',[]]},'fields':['item_code']},
        {'doctype':'Bin','filters':{'item_code':['in',[str(i) for i in range(101)]]},'fields':['item_code']},
        {'doctype':'Bin','filters':{str(i):i for i in range(21)},'fields':['item_code']},
        {'doctype':'Bin','filters':{'item_code':raw_item},'fields':['item_code','item_code']},
        {'doctype':'Bin','filters':{'item_code':raw_item},'fields':['item_code']*21},
        {'doctype':'Bin','filters':{'item_code':raw_item},'fields':['owner']},
        {'doctype':'Bin','filters':{'item_code':raw_item},'fields':{}},
        {'doctype':'Bin','filters':{'item_code':raw_item},'fields':['item_code'],'order_by':'modified desc'},
    ]
    for arguments in invalid_arguments:
        try:
            run_tool(**operation_cap,tool='erp_search_records',arguments=arguments)
            raise AssertionError(('invalid filtered search allowed',arguments))
        except frappe.ValidationError as error:
            assert str(error),arguments
        assert len(sources(operation_cap))==successful_source_count,arguments

    # Empty query remains compatible even for a DocType without a title field.
    old_shape=run_tool(**operation_cap,tool='erp_search_records',arguments={
        'doctype':'Bin',
    })
    assert len(old_shape)<=20
    assert all(set(row)=={'name','modified'} for row in old_shape),old_shape
    assert sources(operation_cap)[-1]['match_fields']==[]

    for governance in ('DS Doctype Policy','DS Doctype Policy Route'):
        before=len(sources(operation_cap))
        try:
            run_tool(**operation_cap,tool='erp_search_records',arguments={
                'doctype':governance,'filters':{'name':['in',['x']]},'fields':['name'],
            })
            raise AssertionError(('governance search allowed',governance))
        except frappe.PermissionError:pass
        assert len(sources(operation_cap))==before

    # Filter-only and title match fields remain authorization dependencies even
    # though neither one is returned in fields.
    frappe.set_user('Administrator')
    for doctype,field,source in (
        ('Bin','projected_qty',filter_only_source),
        ('Item','item_name',title_source),
    ):
        field_permlevels_before[(doctype,field)]=frappe.db.get_value(
            'DocField',{'parent':doctype,'fieldname':field},'permlevel'
        )
        assert field_permlevels_before[(doctype,field)]==0,(doctype,field)
        frappe.db.set_value(
            'DocField',{'parent':doctype,'fieldname':field},'permlevel',1
        )
        frappe.clear_cache(doctype=doctype)
        frappe.set_user(actor)
        try:
            authorize_sources([source])
            raise AssertionError(('match field permission change preserved source',field))
        except frappe.PermissionError as error:
            assert '匹配字段权限已改变' in str(error),error
        frappe.set_user('Administrator')
        frappe.db.set_value(
            'DocField',{'parent':doctype,'fieldname':field},
            'permlevel',field_permlevels_before[(doctype,field)],
        )
        frappe.clear_cache(doctype=doctype)
finally:
    frappe.db.rollback()
    frappe.set_user('Administrator')
    for (doctype,field),permlevel in field_permlevels_before.items():
        frappe.db.set_value(
            'DocField',{'parent':doctype,'fieldname':field},'permlevel',permlevel,
        )
        frappe.clear_cache(doctype=doctype)
    if conversation:
        for run_name in frappe.get_all(
            'DS Model Run',filters={'conversation':conversation},pluck='name'
        ):
            frappe.delete_doc('DS Model Run',run_name,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor)
    frappe.db.commit()
    assert not frappe.db.exists('User',actor)
    if conversation:
        assert not frappe.db.exists('DS Conversation',conversation)
        assert not frappe.get_all('DS Model Run',filters={'conversation':conversation})
    frappe.destroy()
'''
    result=subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1',
         '/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=60,
    )
    assert result.returncode==0,result.stderr
