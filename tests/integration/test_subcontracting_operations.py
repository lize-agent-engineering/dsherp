"""A confirmed enterprise-supplied subcontracting chain moves real synthetic stock."""
import json
import subprocess


# Production break caught: a missing or untrusted subcontracting route cannot complete
# PO -> SCO -> supplied Stock Entry -> Subcontracting Receipt through the HITL boundary.
def test_supplied_material_subcontracting_chain():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_days,add_to_date,flt,now_datetime,nowdate

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()

from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import run_revision
from dsherp_bridge import operations
from dsherp_bridge.operations import confirm,verify_execution
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import SubcontractingOrder
from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import SubcontractingReceipt

tag=uuid.uuid4().hex
actor='subcontract-'+tag+'@example.invalid'
supplier='DSHERP 制造测试合成供应商'
service_item='DSHERP-MFG-SYN-SERVICE'
finished_item='DSHERP-MFG-SYN-FG'
raw_item='DSHERP-MFG-SYN-RM'
bom_name='BOM-DSHERP-MFG-SYN-FG-001'
purchase_qty=1
required_raw_qty=2
routes={
    'po_to_sco':(
        'purchase_order_to_subcontracting_order',
        'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
        'Subcontracting Order',
    ),
    'sco_to_supply':(
        'subcontracting_order_to_supply_stock_entry',
        'erpnext.controllers.subcontracting_controller.make_rm_stock_entry',
        'Stock Entry',
    ),
    'sco_to_receipt':(
        'subcontracting_order_to_subcontracting_receipt',
        'erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order.make_subcontracting_receipt',
        'Subcontracting Receipt',
    ),
}
conversation=None
run_names=[]
proposal_names=[]
execution_names=[]
purchase_order_names=[]
subcontracting_order_names=[]
stock_entry_names=[]
receipt_names=[]
purchase_order=None
subcontracting_order=None
supply_stock_entry=None
subcontracting_receipt=None
warehouses={}
pre_bins={}
delete_linked_ledger_entries_before=None
evidence={}
sco_before_insert_local='before_insert' in SubcontractingOrder.__dict__
sco_before_insert_original=getattr(SubcontractingOrder,'before_insert',None)
scr_before_insert_local='before_insert' in SubcontractingReceipt.__dict__
scr_before_insert_original=getattr(SubcontractingReceipt,'before_insert',None)
scr_validate_local='validate' in SubcontractingReceipt.__dict__
scr_validate_original=getattr(SubcontractingReceipt,'validate',None)
generate_hash_original=frappe.generate_hash


def restore_before_insert(target_class,was_local,original):
    if was_local:
        target_class.before_insert=original
    elif 'before_insert' in target_class.__dict__:
        delattr(target_class,'before_insert')


def restore_scr_validate():
    if scr_validate_local:
        SubcontractingReceipt.validate=scr_validate_original
    elif 'validate' in SubcontractingReceipt.__dict__:
        delattr(SubcontractingReceipt,'validate')


def exactly_one(values,label):
    assert len(values)==1,(label,values)
    return values[0]


def quantity(item_code,warehouse):
    return flt(frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'actual_qty'
    ) or 0)


def stock_snapshot():
    return {
        'raw_at_source':quantity(raw_item,warehouses['raw']),
        'raw_at_supplier':quantity(raw_item,warehouses['subcontracting']),
        'finished':quantity(finished_item,warehouses['finished']),
    }


def site_document_counts():
    return {
        'purchase_orders':frappe.db.count('Purchase Order'),
        'subcontracting_orders':frappe.db.count('Subcontracting Order'),
        'stock_entries':frappe.db.count('Stock Entry'),
        'subcontracting_receipts':frappe.db.count('Subcontracting Receipt'),
    }


def naming_series_snapshot():
    return [dict(row) for row in frappe.db.sql(
        'SELECT name,current FROM `tabSeries` ORDER BY name', as_dict=True
    )]


def new_run():
    capability=uuid.uuid4().hex
    frappe.set_user(actor)
    run=frappe.get_doc({
        'doctype':'DS Model Run','conversation':conversation,'domain':'operation',
        'status':'Running',
        'page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':run_revision(actor,'operation'),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]',
    }).insert(ignore_permissions=True)
    run_names.append(run.name)
    return {'run_id':run.name,'capability':capability},run.name


def succeed_run(run_name):
    frappe.set_user(actor)
    frappe.db.set_value('DS Model Run',run_name,'status','Succeeded')
    frappe.db.commit()


def confirm_once(proposal,run_name):
    proposal_names.append(proposal['id'])
    succeed_run(run_name)
    frappe.set_user(actor)
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    if result.get('execution_id'):
        execution_names.append(result['execution_id'])
    return result


def schema_field_map(cap,doctype,tables=None):
    """Every field of one DocType, in order, following the schema cursor.

    read_schema pages since the tool-output slice, so a single call is only the first page
    of a wide DocType such as Purchase Order. The first page is still the one whose version
    pins a create proposal, so it is returned alongside the complete map.
    """
    first=None;collected={};after=None
    for _page in range(40):
        arguments={'doctype':doctype}
        if tables is not None:
            arguments['tables']=tables
        if after:
            arguments['after_fieldname']=after
        page=run_tool(**cap,tool='erp_read_schema',arguments=arguments)
        if first is None:
            first=page
        collected.update({field['fieldname']:field for field in page['fields']})
        after=page.get('next_after_fieldname')
        if not after:
            break
    assert len(collected)==first['total_fields'],(len(collected),first['total_fields'])
    return first,collected


def child_column_map(cap,doctype,table,fields):
    """Every column of one child table, expanded on purpose.

    Child tables are named but not inlined by default now, so the columns come from an
    explicit `tables=[table]` read. `fields` is the ordered map from schema_field_map: the
    field just before the table puts the expansion at the head of its own page, and a wide
    child table such as Purchase Order Item pages its columns behind `child_after`.
    """
    order=list(fields)
    index=order.index(table)
    assert index,(doctype,table,'no page can start at the first field of a DocType')
    before=order[index-1]
    columns={};cursor=None;entry=None
    for _page in range(40):
        arguments={'doctype':doctype,'tables':[table],'after_fieldname':before}
        if cursor:
            arguments['child_after']={table:cursor}
        page=run_tool(**cap,tool='erp_read_schema',arguments=arguments)
        entry=next(field for field in page['fields'] if field['fieldname']==table)
        assert entry['fields'],entry
        columns.update({column['fieldname']:column for column in entry['fields']})
        cursor=entry.get('columns_truncated',{}).get('next_after_child_fieldname')
        if not cursor:
            break
    assert len(columns)==entry['total_columns'],(len(columns),entry['total_columns'])
    return columns


def propose_make_after_rejecting_options(source_doctype,source_name,route_key):
    cap,run_name=new_run()
    frappe.set_user('Guest')
    source=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':source_doctype,'name':source_name,
    })
    before_counts=site_document_counts()
    target_doctype=routes[route_key][2]
    target_names=frappe.get_all(target_doctype,pluck='name',order_by='name asc')
    naming_series=naming_series_snapshot()
    source_state=frappe.db.get_value(
        source_doctype,source_name,['docstatus','modified','status'],as_dict=True
    )
    before_proposals=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
    try:
        run_tool(**cap,tool='erp_propose_make',arguments={
            'source_doctype':source_doctype,'source_name':source_name,
            'source_version':str(source['modified']),'route':routes[route_key][0],
            'options':{},
        })
        raise AssertionError('extra make options were accepted for '+routes[route_key][0])
    except frappe.ValidationError as error:
        assert str(error)=='操作提案参数无效',error
    assert site_document_counts()==before_counts
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==before_proposals
    proposal=run_tool(**cap,tool='erp_propose_make',arguments={
        'source_doctype':source_doctype,'source_name':source_name,
        'source_version':str(source['modified']),'route':routes[route_key][0],
    })
    assert site_document_counts()==before_counts
    assert frappe.get_all(target_doctype,pluck='name',order_by='name asc')==target_names
    assert naming_series_snapshot()==naming_series
    assert frappe.db.get_value(
        source_doctype,source_name,['docstatus','modified','status'],as_dict=True
    )==source_state
    assert 'name' not in proposal['target']
    return proposal,run_name


def reject_supplied_item_insert_drift(source_doctype,source_name,route_key,kind):
    proposal,run_name=propose_make_after_rejecting_options(
        source_doctype,source_name,route_key
    )
    target_doctype=routes[route_key][2]
    assert target_doctype in ('Subcontracting Order','Subcontracting Receipt')
    assert len(proposal['target']['supplied_items'])==1,proposal['target']
    target_class=(SubcontractingOrder if target_doctype=='Subcontracting Order'
                  else SubcontractingReceipt)
    was_local=(sco_before_insert_local if target_class is SubcontractingOrder
               else scr_before_insert_local)
    original=(sco_before_insert_original if target_class is SubcontractingOrder
              else scr_before_insert_original)
    target_count=frappe.db.count(target_doctype)

    def drift_after_generation(self):
        if original is not None:
            original(self)
        generate=self.create_raw_materials_supplied_or_received

        def generate_then_drift(raw_material_table='supplied_items'):
            generate(raw_material_table)
            rows=self.get('supplied_items')
            assert len(rows)==1,rows
            if kind=='rows':
                duplicate={key:value for key,value in rows[0].as_dict().items()
                           if key not in ('name','parent','parentfield','parenttype')}
                self.append('supplied_items',duplicate)
            elif kind=='rm_item':
                rows[0].rm_item_code=finished_item
            elif kind=='qty':
                field=('required_qty' if target_doctype=='Subcontracting Order'
                       else 'consumed_qty')
                rows[0].set(field,flt(rows[0].get(field))+1)
            elif target_doctype=='Subcontracting Order':
                rows[0].reserve_warehouse=warehouses['finished']
            else:
                self.supplier_warehouse=warehouses['raw']

        self.create_raw_materials_supplied_or_received=generate_then_drift

    target_class.before_insert=drift_after_generation
    try:
        rejected=confirm_once(proposal,run_name)
    finally:
        restore_before_insert(target_class,was_local,original)
    assert rejected['status']=='Failed' and '保存后内容与确认内容不一致' in rejected['error'],rejected
    assert rejected['status']!='Unknown'
    assert frappe.db.count('DS Execution Record',{'proposal':proposal['id']})==1
    assert frappe.db.count(target_doctype)==target_count


def assert_direct_create_denied(doctype):
    cap,_run_name=new_run()
    frappe.set_user('Guest')
    schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':doctype})
    before_counts=site_document_counts()
    before_proposals=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
    try:
        run_tool(**cap,tool='erp_propose_create',arguments={
            'doctype':doctype,'values':{},'version':str(schema['modified']),
        })
        raise AssertionError('direct '+doctype+' create was accepted')
    except frappe.PermissionError as error:
        assert str(error)==(
            'DS DocType 策略未允许 '+doctype+' 的 create 操作'
        ),error
    assert site_document_counts()==before_counts
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==before_proposals


try:
    frappe.set_user('Administrator')
    roles={row.name for row in frappe.get_all(
        'Role',filters={'name':['in',['Purchase User','Stock User']]},fields=['name'],
    )}
    assert roles=={'Purchase User','Stock User'},roles
    purchase_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Purchase Order','role':'Purchase User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    sco_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Subcontracting Order','role':'Purchase User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    stock_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Stock Entry','role':'Stock User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    receipt_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Subcontracting Receipt','role':'Stock User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    for permission in (purchase_perm,sco_perm,stock_perm,receipt_perm):
        assert permission and all(permission.get(field) for field in (
            'read','write','create','submit','cancel'
        )),permission

    policies={row.target_doctype:row for row in frappe.get_all(
        'DS Doctype Policy',fields=['target_doctype','allow_read','allow_create','allow_update',
        'allow_submit','allow_cancel','allow_fill'],
    )}
    assert set(('Purchase Order','Subcontracting Order','Stock Entry',
                'Subcontracting Receipt'))<=set(policies),policies
    for target in ('Subcontracting Order','Subcontracting Receipt'):
        policy=policies[target]
        assert policy.allow_read and policy.allow_submit,policy
        assert not policy.allow_create and not policy.allow_update,policy
        assert not policy.allow_cancel and not policy.allow_fill,policy
    purchase_routes=frappe.get_all(
        'DS Doctype Policy Route',filters={'parent':'Purchase Order'},
        fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc',
    )
    assert purchase_routes==[
        {
            'route_name':'purchase_order_to_purchase_receipt',
            'method_path':'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',
            'target_doctype':'Purchase Receipt',
        },
        {
            'route_name':routes['po_to_sco'][0],
            'method_path':routes['po_to_sco'][1],
            'target_doctype':routes['po_to_sco'][2],
        },
    ],purchase_routes
    sco_routes=frappe.get_all(
        'DS Doctype Policy Route',filters={'parent':'Subcontracting Order'},
        fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc',
    )
    assert sco_routes==[
        {
            'route_name':routes['sco_to_supply'][0],
            'method_path':routes['sco_to_supply'][1],
            'target_doctype':routes['sco_to_supply'][2],
        },
        {
            'route_name':routes['sco_to_receipt'][0],
            'method_path':routes['sco_to_receipt'][1],
            'target_doctype':routes['sco_to_receipt'][2],
        },
    ],sco_routes

    company=exactly_one(frappe.get_all('Company',pluck='name'),'Company')
    warehouse_labels={
        'raw':'DSHERP 制造测试合成原料仓',
        'finished':'DSHERP 制造测试合成成品仓',
        'subcontracting':'DSHERP 制造测试合成委外仓',
    }
    for key,label in warehouse_labels.items():
        warehouses[key]=exactly_one(frappe.get_all('Warehouse',filters={
            'warehouse_name':label,'company':company,'is_group':0,
        },pluck='name'),label)
    assert frappe.db.exists('Supplier',supplier)
    bom=frappe.get_doc('BOM',bom_name)
    assert bom.docstatus==1 and bom.item==finished_item and flt(bom.quantity)==1
    assert len(bom.items)==1 and bom.items[0].item_code==raw_item
    assert flt(bom.items[0].qty)==required_raw_qty
    service=frappe.get_doc('Item',service_item)
    assert not service.is_stock_item and service.is_purchase_item and service.stock_uom=='Nos'
    assert frappe.db.get_value('Item',finished_item,'is_sub_contracted_item')==1
    pre_bins=stock_snapshot()
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )
    initial_document_counts=site_document_counts()

    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic Subcontract actor',
        'enabled':1,'send_welcome_email':0,
        'roles':[{'role':'Purchase User'},{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic Subcontract chain '+tag,
    }).insert(ignore_permissions=True).name

    # Policy, not native DocPerm, forbids direct SCO creation and writes nothing.
    assert_direct_create_denied('Subcontracting Order')
    assert_direct_create_denied('Subcontracting Receipt')

    # A subcontracted PO proposal is schema-sourced and remains write-free.
    cap,po_create_run=new_run()
    frappe.set_user('Guest')
    po_schema,po_fields=schema_field_map(cap,'Purchase Order')
    # The child table is named, not inlined: the default schema read says which DocType its
    # rows are, and the columns come from a read that asks for that table by name.
    assert 'fields' not in po_fields['items'],po_fields['items']
    assert po_fields['items']['rows_of']=='Purchase Order Item',po_fields['items']
    po_item_fields=child_column_map(cap,'Purchase Order','items',po_fields)
    assert po_fields['supplier_warehouse']['options']=='Warehouse'
    assert po_fields['is_subcontracted']['fieldtype']=='Check'
    assert po_fields['set_reserve_warehouse']['options']=='Warehouse'
    for fieldname in ('fg_item','fg_item_qty'):
        assert fieldname in po_item_fields,po_item_fields
    actual_supplier=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Supplier','name':supplier,
    })
    actual_service=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Item','name':service_item,
    })
    actual_finished=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Item','name':finished_item,
    })
    for warehouse in warehouses.values():
        actual=run_tool(**cap,tool='erp_read_record',arguments={
            'doctype':'Warehouse','name':warehouse,
        })
        assert actual['fields']['company']==company
    schedule_date=str(add_days(nowdate(),7))
    po_values={
        'naming_series':po_fields['naming_series']['options'],
        'supplier':actual_supplier['name'],'transaction_date':str(nowdate()),
        'schedule_date':schedule_date,'company':company,'currency':'CNY',
        'conversion_rate':1,'is_subcontracted':1,
        'supplier_warehouse':warehouses['subcontracting'],
        'set_reserve_warehouse':warehouses['raw'],
        'set_warehouse':warehouses['finished'],
        'items':[{
            'item_code':actual_service['name'],'schedule_date':schedule_date,
            'qty':purchase_qty,'rate':5,'warehouse':warehouses['finished'],
            'uom':'Nos','conversion_factor':1,'fg_item':actual_finished['name'],
            'fg_item_qty':purchase_qty,
        }],
    }
    po_counts=site_document_counts()
    po_proposal=run_tool(**cap,tool='erp_propose_create',arguments={
        'doctype':'Purchase Order','values':po_values,'version':str(po_schema['modified']),
    })
    assert site_document_counts()==po_counts
    po_created=confirm_once(po_proposal,po_create_run)
    assert po_created['status']=='Succeeded',po_created
    purchase_order=po_created['name']
    purchase_order_names.append(purchase_order)
    po=frappe.get_doc('Purchase Order',purchase_order)
    assert po.docstatus==0 and po.owner==actor and po.is_subcontracted
    assert po.supplier_warehouse==warehouses['subcontracting']
    assert po.set_reserve_warehouse==warehouses['raw']
    assert len(po.items)==1 and po.items[0].fg_item==finished_item

    cap,po_submit_run=new_run()
    frappe.set_user('Guest')
    po_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,
    })
    po_submit_counts=site_document_counts()
    po_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Purchase Order','name':purchase_order,'action':'submit',
        'version':str(po_read['modified']),
    })
    assert po_submit['impact']=={'kind':'none','entries':[]},po_submit
    assert site_document_counts()==po_submit_counts
    assert frappe.db.get_value('Purchase Order',purchase_order,'docstatus')==0
    assert confirm_once(po_submit,po_submit_run)['status']=='Succeeded'
    assert frappe.db.get_value('Purchase Order',purchase_order,'docstatus')==1

    sco_make,sco_make_run=propose_make_after_rejecting_options(
        'Purchase Order',purchase_order,'po_to_sco'
    )
    assert sco_make['target']['doctype']=='Subcontracting Order'
    assert sco_make['target']['purchase_order']==purchase_order
    assert sco_make['target']['supplier_warehouse']==warehouses['subcontracting']
    assert len(sco_make['target']['supplied_items'])==1
    sco_supply=sco_make['target']['supplied_items'][0]
    assert sco_supply['rm_item_code']==raw_item,sco_supply
    assert sco_supply['main_item_code']==finished_item,sco_supply
    assert flt(sco_supply['required_qty'])==required_raw_qty,sco_supply
    assert sco_supply['reserve_warehouse']==warehouses['raw'],sco_supply
    assert len(sco_supply['reference_name'])==20,sco_supply
    sco_payload=json.loads(frappe.db.get_value(
        'DS Operation Proposal',sco_make['id'],'payload'
    ))
    assert len(sco_payload['target']['supplied_items'])==1
    assert sco_payload['confirmation_target']['supplied_items']==sco_make['target']['supplied_items']
    for drift_kind in ('rows','rm_item','qty','warehouse'):
        reject_supplied_item_insert_drift(
            'Purchase Order',purchase_order,'po_to_sco',drift_kind
        )
    sco_created=confirm_once(sco_make,sco_make_run)
    assert sco_created['status']=='Succeeded',sco_created
    subcontracting_order=sco_created['name']
    subcontracting_order_names.append(subcontracting_order)
    sco=frappe.get_doc('Subcontracting Order',subcontracting_order)
    assert sco.docstatus==0 and sco.owner==actor and sco.purchase_order==purchase_order
    assert len(sco.items)==1 and sco.items[0].item_code==finished_item
    assert flt(sco.items[0].qty)==purchase_qty and sco.items[0].bom==bom_name
    assert len(sco.supplied_items)==1
    assert sco.supplied_items[0].rm_item_code==raw_item
    assert flt(sco.supplied_items[0].required_qty)==required_raw_qty
    assert sco.supplied_items[0].reserve_warehouse==warehouses['raw']
    assert confirm(sco_make['id'],sco_make['digest'],uuid.uuid4().hex)==sco_created
    assert frappe.db.count('DS Execution Record',{'proposal':sco_make['id']})==1

    # The pure proposal allocator must reject a duplicate from this proposal
    # and an exact child-table collision before accepting the next candidate.
    existing_item_reference=sco.items[0].name
    repeated_reference='r'*20
    unique_reference='u'*20
    hash_lengths=[]
    hash_candidates=iter([
        repeated_reference,repeated_reference,existing_item_reference,unique_reference,
    ])
    series_before_reference_probe=naming_series_snapshot()
    child_count_before_reference_probe=frappe.db.count('Subcontracting Order Item')
    reference_probe=frappe.get_doc({
        'doctype':'Subcontracting Order',
        'items':[
            {'doctype':'Subcontracting Order Item'},
            {'doctype':'Subcontracting Order Item'},
        ],
    })
    def sequenced_hash(length=10):
        hash_lengths.append(length)
        return next(hash_candidates)
    frappe.generate_hash=sequenced_hash
    try:
        operations._subcontracting_item_references(reference_probe)
    finally:
        frappe.generate_hash=generate_hash_original
    assert [row.name for row in reference_probe.items]==[
        repeated_reference,unique_reference,
    ]
    assert hash_lengths==[20,20,20,20],hash_lengths
    assert frappe.db.count('Subcontracting Order Item')==child_count_before_reference_probe
    assert naming_series_snapshot()==series_before_reference_probe

    # The public proposal path performs the same exact-table collision check,
    # exposes only the eventual frozen reference, and stays read-only.
    public_hash_lengths=[]
    public_hash_candidates=iter([existing_item_reference,'v'*20])
    def public_sequenced_hash(length=10):
        if length!=20:
            return generate_hash_original(length=length)
        public_hash_lengths.append(length)
        return next(public_hash_candidates)
    frappe.generate_hash=public_sequenced_hash
    try:
        unique_reference_proposal,unique_reference_run=propose_make_after_rejecting_options(
            'Purchase Order',purchase_order,'po_to_sco'
        )
    finally:
        frappe.generate_hash=generate_hash_original
    unique_reference_rows=unique_reference_proposal['target']['supplied_items']
    assert public_hash_lengths==[20,20],public_hash_lengths
    assert len({row['reference_name'] for row in unique_reference_rows}) \
        ==len(unique_reference_rows)
    assert all(len(row['reference_name'])==20 for row in unique_reference_rows)
    assert unique_reference_rows[0]['reference_name']=='v'*20
    unique_reference_payload=json.loads(frappe.db.get_value(
        'DS Operation Proposal',unique_reference_proposal['id'],'payload'
    ))
    assert unique_reference_payload['target']['supplied_items']==unique_reference_rows
    assert unique_reference_payload['confirmation_target']['supplied_items']==unique_reference_rows

    # A bounded allocator failure is explicit and still cannot consume Series
    # or create either a proposal target or a proposal record.
    exhausted_counts=site_document_counts()
    exhausted_series=naming_series_snapshot()
    exhausted_proposals=frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )
    exhausted_calls=[]
    def exhausted_hash(length=10):
        if length!=20:
            return generate_hash_original(length=length)
        exhausted_calls.append(length)
        return existing_item_reference
    frappe.generate_hash=exhausted_hash
    try:
        try:
            propose_make_after_rejecting_options(
                'Purchase Order',purchase_order,'po_to_sco'
            )
            raise AssertionError('exhausted references did not fast-fail')
        except frappe.ValidationError as error:
            assert str(error)=='无法生成唯一的委外供料行引用，请重新提出操作',error
    finally:
        frappe.generate_hash=generate_hash_original
    assert exhausted_calls and len(exhausted_calls)<=10,exhausted_calls
    assert all(length==20 for length in exhausted_calls),exhausted_calls
    assert site_document_counts()==exhausted_counts
    assert naming_series_snapshot()==exhausted_series
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==exhausted_proposals

    # Occupying the frozen item reference after proposal but before confirmation
    # must fail before target insert, never fall through to Unknown.
    occupied_proposal,occupied_run=propose_make_after_rejecting_options(
        'Purchase Order',purchase_order,'po_to_sco'
    )
    occupied_reference=occupied_proposal['target']['supplied_items'][0]['reference_name']
    occupied_item=frappe.copy_doc(sco.items[0])
    occupied_item.name=occupied_reference
    occupied_item.parent=subcontracting_order
    occupied_item.parenttype='Subcontracting Order'
    occupied_item.parentfield='items'
    occupied_item.idx=99
    occupied_item.docstatus=0
    frappe.set_user('Administrator')
    occupied_item.db_insert()
    frappe.db.commit()
    occupied_target_count=frappe.db.count('Subcontracting Order')
    try:
        occupied_result=confirm_once(occupied_proposal,occupied_run)
    finally:
        frappe.set_user('Administrator')
        if frappe.db.exists('Subcontracting Order Item',occupied_reference):
            frappe.delete_doc(
                'Subcontracting Order Item',occupied_reference,ignore_permissions=True
            )
            frappe.db.commit()
        frappe.set_user(actor)
    assert occupied_result['status']=='Failed',occupied_result
    assert occupied_result['error']=='委外供料行引用已被占用，请重新提出操作',occupied_result
    assert frappe.db.count('Subcontracting Order')==occupied_target_count
    assert frappe.db.count(
        'DS Execution Record',{'proposal':occupied_proposal['id']}
    )==1

    # A collision introduced by before_insert models the narrow race between
    # the occupancy check and child insert. DuplicateEntry remains a known
    # Failed outcome and the competing row and target draft both roll back.
    race_proposal,race_run=propose_make_after_rejecting_options(
        'Purchase Order',purchase_order,'po_to_sco'
    )
    race_reference=race_proposal['target']['supplied_items'][0]['reference_name']
    race_target_count=frappe.db.count('Subcontracting Order')
    def race_before_insert(self):
        if sco_before_insert_original:
            sco_before_insert_original(self)
        competing_item=frappe.copy_doc(sco.items[0])
        competing_item.name=self.items[0].name
        competing_item.parent=subcontracting_order
        competing_item.parenttype='Subcontracting Order'
        competing_item.parentfield='items'
        competing_item.idx=99
        competing_item.docstatus=0
        competing_item.db_insert()
        raise frappe.DuplicateEntryError(
            competing_item.doctype,competing_item.name,RuntimeError('insert race')
        )
    SubcontractingOrder.before_insert=race_before_insert
    try:
        race_result=confirm_once(race_proposal,race_run)
    finally:
        restore_before_insert(
            SubcontractingOrder,sco_before_insert_local,sco_before_insert_original
        )
    assert race_result['status']=='Failed',race_result
    assert race_result['error']=='委外供料行引用已被占用，请重新提出操作',race_result
    assert frappe.db.count('Subcontracting Order')==race_target_count
    assert not frappe.db.exists('Subcontracting Order Item',race_reference)
    assert frappe.db.count(
        'DS Execution Record',{'proposal':race_proposal['id']}
    )==1

    supplied_row=sco.supplied_items[0]
    original_required_qty=flt(supplied_row.required_qty)
    frappe.set_user('Administrator')
    frappe.db.set_value('Subcontracting Order Supplied Item',supplied_row.name,
                        'required_qty',original_required_qty+1,update_modified=False)
    frappe.db.commit();frappe.set_user(actor)
    sco_verify=verify_execution(sco_make['id'])
    assert flt(
        sco_verify['observed']['values']['supplied_items'][0]['required_qty']
    )==original_required_qty+1
    assert sco_verify['matches_proposal'] is False
    frappe.set_user('Administrator')
    frappe.db.set_value('Subcontracting Order Supplied Item',supplied_row.name,
                        'required_qty',original_required_qty,update_modified=False)
    frappe.db.commit();frappe.set_user(actor)
    sco=frappe.get_doc('Subcontracting Order',subcontracting_order)

    cap,sco_submit_run=new_run()
    frappe.set_user('Guest')
    sco_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Subcontracting Order','name':subcontracting_order,
    })
    sco_submit_counts=site_document_counts()
    sco_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Subcontracting Order','name':subcontracting_order,'action':'submit',
        'version':str(sco_read['modified']),
    })
    assert sco_submit['impact']=={'kind':'none','entries':[]},sco_submit
    assert site_document_counts()==sco_submit_counts
    assert confirm_once(sco_submit,sco_submit_run)['status']=='Succeeded'

    supply_make,supply_make_run=propose_make_after_rejecting_options(
        'Subcontracting Order',subcontracting_order,'sco_to_supply'
    )
    assert supply_make['target']['doctype']=='Stock Entry'
    assert supply_make['target']['purpose']=='Send to Subcontractor'
    assert supply_make['target']['subcontracting_order']==subcontracting_order
    supply_created=confirm_once(supply_make,supply_make_run)
    assert supply_created['status']=='Succeeded',supply_created
    supply_stock_entry=supply_created['name']
    stock_entry_names.append(supply_stock_entry)
    supply_doc=frappe.get_doc('Stock Entry',supply_stock_entry)
    assert supply_doc.docstatus==0 and supply_doc.owner==actor
    assert len(supply_doc.items)==1
    assert supply_doc.items[0].item_code==raw_item
    assert supply_doc.items[0].s_warehouse==warehouses['raw']
    assert supply_doc.items[0].t_warehouse==warehouses['subcontracting']
    assert flt(supply_doc.items[0].qty)==required_raw_qty

    cap,supply_submit_run=new_run()
    frappe.set_user('Guest')
    supply_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Stock Entry','name':supply_stock_entry,
    })
    supply_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Stock Entry','name':supply_stock_entry,'action':'submit',
        'version':str(supply_read['modified']),
    })
    assert supply_submit['impact']=={'kind':'stock','entries':[
        {'item_code':raw_item,'quantity':-required_raw_qty,'uom':'Nos','warehouse':warehouses['raw']},
        {'item_code':raw_item,'quantity':required_raw_qty,'uom':'Nos','warehouse':warehouses['subcontracting']},
    ]},supply_submit
    supply_submitted=confirm_once(supply_submit,supply_submit_run)
    assert supply_submitted['status']=='Succeeded',supply_submitted
    after_supply=stock_snapshot()
    assert after_supply=={
        'raw_at_source':pre_bins['raw_at_source']-required_raw_qty,
        'raw_at_supplier':pre_bins['raw_at_supplier']+required_raw_qty,
        'finished':pre_bins['finished'],
    },after_supply
    supply_ledgers=frappe.get_all(
        'Stock Ledger Entry',filters={
            'voucher_type':'Stock Entry','voucher_no':supply_stock_entry,
        },fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )
    assert supply_ledgers==[
        {
            'voucher_type':'Stock Entry','voucher_no':supply_stock_entry,
            'item_code':raw_item,'warehouse':warehouses['raw'],
            'actual_qty':-required_raw_qty,
        },
        {
            'voucher_type':'Stock Entry','voucher_no':supply_stock_entry,
            'item_code':raw_item,'warehouse':warehouses['subcontracting'],
            'actual_qty':required_raw_qty,
        },
    ],supply_ledgers

    receipt_make,receipt_make_run=propose_make_after_rejecting_options(
        'Subcontracting Order',subcontracting_order,'sco_to_receipt'
    )
    assert receipt_make['target']['doctype']=='Subcontracting Receipt'
    assert receipt_make['target']['supplier']==supplier
    assert receipt_make['target']['supplier_warehouse']==warehouses['subcontracting']
    assert len(receipt_make['target']['items'])==1
    assert receipt_make['target']['items'][0]['subcontracting_order']==subcontracting_order
    assert receipt_make['target']['items'][0]['item_code']==finished_item
    assert len(receipt_make['target']['supplied_items'])==1
    receipt_supply=receipt_make['target']['supplied_items'][0]
    assert receipt_supply['rm_item_code']==raw_item
    assert receipt_supply['main_item_code']==finished_item
    assert flt(receipt_supply['required_qty'])==required_raw_qty
    assert flt(receipt_supply['consumed_qty'])==required_raw_qty
    assert receipt_supply['subcontracting_order']==subcontracting_order
    assert len(receipt_supply['reference_name'])==20,receipt_supply
    receipt_payload=json.loads(frappe.db.get_value(
        'DS Operation Proposal',receipt_make['id'],'payload'
    ))
    assert len(receipt_payload['target']['supplied_items'])==1
    assert receipt_payload['confirmation_target']['supplied_items']==receipt_make['target']['supplied_items']
    for drift_kind in ('rows','rm_item','qty','warehouse'):
        reject_supplied_item_insert_drift(
            'Subcontracting Order',subcontracting_order,'sco_to_receipt',drift_kind
        )
    receipt_created=confirm_once(receipt_make,receipt_make_run)
    assert receipt_created['status']=='Succeeded',receipt_created
    subcontracting_receipt=receipt_created['name']
    receipt_names.append(subcontracting_receipt)
    receipt_doc=frappe.get_doc('Subcontracting Receipt',subcontracting_receipt)
    assert receipt_doc.docstatus==0 and receipt_doc.owner==actor
    assert confirm(receipt_make['id'],receipt_make['digest'],uuid.uuid4().hex)==receipt_created
    assert frappe.db.count('DS Execution Record',{'proposal':receipt_make['id']})==1
    receipt_supplied_row=receipt_doc.supplied_items[0]
    original_consumed_qty=flt(receipt_supplied_row.consumed_qty)
    frappe.set_user('Administrator')
    frappe.db.set_value('Subcontracting Receipt Supplied Item',receipt_supplied_row.name,
                        'consumed_qty',original_consumed_qty+1,update_modified=False)
    frappe.db.commit();frappe.set_user(actor)
    receipt_verify=verify_execution(receipt_make['id'])
    assert flt(
        receipt_verify['observed']['values']['supplied_items'][0]['consumed_qty']
    )==original_consumed_qty+1
    assert receipt_verify['matches_proposal'] is False
    frappe.set_user('Administrator')
    frappe.db.set_value('Subcontracting Receipt Supplied Item',receipt_supplied_row.name,
                        'consumed_qty',original_consumed_qty,update_modified=False)
    frappe.db.commit();frappe.set_user(actor)
    receipt_doc=frappe.get_doc('Subcontracting Receipt',subcontracting_receipt)

    frappe.set_user('Administrator')
    receipt_doc.db_set('is_return',1,update_modified=False);frappe.db.commit()
    try:
        cap,_=new_run();frappe.set_user('Guest')
        unsupported_read=run_tool(**cap,tool='erp_read_record',arguments={
            'doctype':'Subcontracting Receipt','name':subcontracting_receipt,
        })
        proposals_before=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
        sle_before=frappe.db.count('Stock Ledger Entry');bins_before=stock_snapshot()
        try:
            run_tool(**cap,tool='erp_propose_action',arguments={
                'doctype':'Subcontracting Receipt','name':subcontracting_receipt,'action':'submit',
                'version':str(unsupported_read['modified']),
            })
            raise AssertionError('unsupported Subcontracting Receipt return impact was proposed')
        except frappe.ValidationError as error:
            assert '库存影响无法确定' in str(error) and '暂不支持' in str(error),error
        assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==proposals_before
        assert frappe.db.count('Stock Ledger Entry')==sle_before
        assert stock_snapshot()==bins_before
    finally:
        frappe.set_user('Administrator')
        receipt_doc.db_set('is_return',0,update_modified=False);frappe.db.commit()

    # Each confirmation is its own committed business step. A native SCR
    # validation failure must not undo or replay the already-succeeded supply SE.
    cap,failed_receipt_run=new_run()
    frappe.set_user('Guest')
    failed_receipt_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Subcontracting Receipt','name':subcontracting_receipt,
    })
    failed_receipt_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Subcontracting Receipt','name':subcontracting_receipt,'action':'submit',
        'version':str(failed_receipt_read['modified']),
    })
    validation_calls=[]
    def reject_receipt_validation(self):
        validation_calls.append(self.name)
        raise frappe.ValidationError('合成委外收货提交校验失败')
    SubcontractingReceipt.validate=reject_receipt_validation
    try:
        failed_receipt=confirm_once(failed_receipt_submit,failed_receipt_run)
    finally:
        restore_scr_validate()
    assert failed_receipt['status']=='Failed',failed_receipt
    assert '合成委外收货提交校验失败' in failed_receipt['error'],failed_receipt
    assert validation_calls==[subcontracting_receipt],validation_calls
    assert frappe.db.get_value('DS Execution Record',supply_submitted['execution_id'],'status')=='Succeeded'
    assert frappe.get_doc('Stock Entry',supply_stock_entry).docstatus==1
    assert frappe.get_doc('Subcontracting Receipt',subcontracting_receipt).docstatus==0
    assert stock_snapshot()==after_supply
    assert not frappe.db.exists('Stock Ledger Entry',{
        'voucher_type':'Subcontracting Receipt','voucher_no':subcontracting_receipt,
    })
    repeated_failure=confirm(
        failed_receipt_submit['id'],failed_receipt_submit['digest'],uuid.uuid4().hex
    )
    assert repeated_failure==failed_receipt
    assert validation_calls==[subcontracting_receipt],'failed SCR was automatically retried'
    assert frappe.db.count('DS Execution Record',{'proposal':failed_receipt_submit['id']})==1

    cap,receipt_submit_run=new_run()
    frappe.set_user('Guest')
    receipt_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Subcontracting Receipt','name':subcontracting_receipt,
    })
    receipt_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Subcontracting Receipt','name':subcontracting_receipt,'action':'submit',
        'version':str(receipt_read['modified']),
    })
    assert receipt_submit['impact']=={'kind':'stock','entries':[
        {'item_code':finished_item,'quantity':purchase_qty,'uom':'Nos','warehouse':warehouses['finished']},
        {'item_code':raw_item,'quantity':-required_raw_qty,'uom':'Nos','warehouse':warehouses['subcontracting']},
    ]},receipt_submit
    assert confirm_once(receipt_submit,receipt_submit_run)['status']=='Succeeded'
    after_receipt=stock_snapshot()
    assert after_receipt=={
        'raw_at_source':pre_bins['raw_at_source']-required_raw_qty,
        'raw_at_supplier':pre_bins['raw_at_supplier'],
        'finished':pre_bins['finished']+purchase_qty,
    },after_receipt
    receipt_ledgers=frappe.get_all(
        'Stock Ledger Entry',filters={
            'voucher_type':'Subcontracting Receipt','voucher_no':subcontracting_receipt,
        },fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )
    assert receipt_ledgers==[
        {
            'voucher_type':'Subcontracting Receipt','voucher_no':subcontracting_receipt,
            'item_code':finished_item,'warehouse':warehouses['finished'],
            'actual_qty':purchase_qty,
        },
        {
            'voucher_type':'Subcontracting Receipt','voucher_no':subcontracting_receipt,
            'item_code':raw_item,'warehouse':warehouses['subcontracting'],
            'actual_qty':-required_raw_qty,
        },
    ],receipt_ledgers

    cap,outcome_run=new_run()
    frappe.set_user('Guest')
    po_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,
    })
    sco_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Subcontracting Order','name':subcontracting_order,
    })
    # per_received is 0 here and has to survive the empty-field filter: only None and ''
    # are absences, a zero is an answer.
    assert flt(po_outcome['fields']['per_received'])==0,po_outcome
    assert po_outcome['fields']['status']=='To Receive and Bill',po_outcome
    # The same single line, counted by the default read and expanded by the one that asks.
    assert 'items' not in po_outcome['fields'],po_outcome
    assert po_outcome['child_tables']['items']=={
        'child_doctype':'Purchase Order Item','rows':1,
    },po_outcome
    po_outcome_items=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,'children':['items'],
    })['fields']['items']
    assert len(po_outcome_items)==1,po_outcome_items
    assert flt(po_outcome_items[0]['subcontracted_qty'])==purchase_qty,po_outcome_items
    assert flt(sco_outcome['fields']['per_received'])==100,sco_outcome
    assert sco_outcome['fields']['status']=='Completed',sco_outcome

    evidence={
        'roles':sorted(roles),'site_document_baseline':initial_document_counts,
        'purchase_order':purchase_order,'subcontracting_order':subcontracting_order,
        'supply_stock_entry':supply_stock_entry,
        'subcontracting_receipt':subcontracting_receipt,
        'warehouses':warehouses,'bin_before':pre_bins,
        'bin_after_supply':after_supply,'bin_after_receipt':after_receipt,
        'po_per_received':flt(po_outcome['fields']['per_received']),
        'po_status':po_outcome['fields']['status'],
        'po_subcontracted_quantity':flt(po_outcome_items[0]['subcontracted_qty']),
        'sco_per_received':flt(sco_outcome['fields']['per_received']),
        'sco_status':sco_outcome['fields']['status'],
        'supply_stock_ledger_rows':supply_ledgers,
        'receipt_stock_ledger_rows':receipt_ledgers,
        'delete_linked_ledger_entries_before':delete_linked_ledger_entries_before,
    }
finally:
    frappe.generate_hash=generate_hash_original
    restore_scr_validate()
    restore_before_insert(SubcontractingOrder,sco_before_insert_local,sco_before_insert_original)
    restore_before_insert(SubcontractingReceipt,scr_before_insert_local,scr_before_insert_original)
    frappe.db.rollback()
    frappe.set_user('Administrator')
    purchase_order_names.extend(frappe.get_all(
        'Purchase Order',filters={'owner':actor},pluck='name'
    ))
    subcontracting_order_names.extend(frappe.get_all(
        'Subcontracting Order',filters={'owner':actor},pluck='name'
    ))
    stock_entry_names.extend(frappe.get_all(
        'Stock Entry',filters={'owner':actor},pluck='name'
    ))
    receipt_names.extend(frappe.get_all(
        'Subcontracting Receipt',filters={'owner':actor},pluck='name'
    ))
    purchase_order_names=list(dict.fromkeys(filter(None,purchase_order_names)))
    subcontracting_order_names=list(dict.fromkeys(filter(None,subcontracting_order_names)))
    stock_entry_names=list(dict.fromkeys(filter(None,stock_entry_names)))
    receipt_names=list(dict.fromkeys(filter(None,receipt_names)))
    receipt_docs=[frappe.get_doc('Subcontracting Receipt',name) for name in receipt_names
                  if frappe.db.exists('Subcontracting Receipt',name)]
    stock_docs=[frappe.get_doc('Stock Entry',name) for name in stock_entry_names
                if frappe.db.exists('Stock Entry',name)]
    for doc in receipt_docs:
        if doc.docstatus==1:
            doc.cancel()
    for doc in stock_docs:
        if doc.docstatus==1:
            doc.cancel()
    from frappe.tests.utils import change_settings
    with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
        for doc in receipt_docs:
            if frappe.db.exists('Subcontracting Receipt',doc.name):
                frappe.delete_doc('Subcontracting Receipt',doc.name,ignore_permissions=True)
        for doc in stock_docs:
            if frappe.db.exists('Stock Entry',doc.name):
                frappe.delete_doc('Stock Entry',doc.name,ignore_permissions=True)
    for name in subcontracting_order_names:
        if frappe.db.exists('Subcontracting Order',name):
            doc=frappe.get_doc('Subcontracting Order',name)
            if doc.docstatus==1:
                doc.cancel()
            frappe.delete_doc('Subcontracting Order',name,ignore_permissions=True)
    for name in purchase_order_names:
        if frappe.db.exists('Purchase Order',name):
            doc=frappe.get_doc('Purchase Order',name)
            if doc.docstatus==1:
                doc.cancel()
            frappe.delete_doc('Purchase Order',name,ignore_permissions=True)
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
            frappe.db.delete('DS Execution Record',{'name':execution_id})
    for proposal_id in proposal_names:
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.db.delete('DS Operation Proposal',{'name':proposal_id})
    for run_name in run_names:
        if frappe.db.exists('DS Model Run',run_name):
            frappe.db.delete('DS Run Event',{'run':run_name})
            frappe.db.delete('DS Model Run',{'name':run_name})
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('User',actor):
        frappe.delete_doc('User',actor,ignore_permissions=True)
    frappe.db.commit()
    frappe.destroy()

    os.chdir('/home/frappe/frappe-bench/sites')
    frappe.init(site='dsherp-validation.localhost')
    frappe.connect()
    try:
        frappe.set_user('Administrator')
        fresh_bins=stock_snapshot() if warehouses and pre_bins else pre_bins
        if pre_bins:
            assert fresh_bins==pre_bins,(pre_bins,fresh_bins)
        assert frappe.db.get_value('Item',finished_item,'is_sub_contracted_item')==1
        assert frappe.db.exists('Item',service_item)
        assert not frappe.db.exists('User',actor)
        assert frappe.db.count('Purchase Order',{'owner':actor})==0
        assert frappe.db.count('Subcontracting Order',{'owner':actor})==0
        assert frappe.db.count('Stock Entry',{'owner':actor})==0
        assert frappe.db.count('Subcontracting Receipt',{'owner':actor})==0
        assert all(not frappe.db.exists('Purchase Order',name) for name in purchase_order_names)
        assert all(not frappe.db.exists('Subcontracting Order',name)
                   for name in subcontracting_order_names)
        assert all(not frappe.db.exists('Stock Entry',name) for name in stock_entry_names)
        assert all(not frappe.db.exists('Subcontracting Receipt',name) for name in receipt_names)
        assert frappe.db.count('Stock Ledger Entry',{
            'voucher_type':['in',['Stock Entry','Subcontracting Receipt']],
            'voucher_no':['in',[*stock_entry_names,*receipt_names]],
        })==0
        if delete_linked_ledger_entries_before is not None:
            assert int(frappe.db.get_single_value(
                'Accounts Settings','delete_linked_ledger_entries'
            ) or 0)==delete_linked_ledger_entries_before
        assert all(not frappe.db.exists('DS Execution Record',name) for name in execution_names)
        assert all(not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names)
        assert all(not frappe.db.exists('DS Model Run',name) for name in run_names)
        if conversation:
            assert not frappe.db.exists('DS Conversation',conversation)
        if evidence:
            evidence['bin_cleanup']=fresh_bins
            evidence['fresh_residual']={
                'purchase_orders':sum(bool(frappe.db.exists('Purchase Order',name))
                                      for name in purchase_order_names),
                'subcontracting_orders':sum(
                    bool(frappe.db.exists('Subcontracting Order',name))
                    for name in subcontracting_order_names
                ),
                'stock_entries':sum(bool(frappe.db.exists('Stock Entry',name))
                                    for name in stock_entry_names),
                'subcontracting_receipts':sum(
                    bool(frappe.db.exists('Subcontracting Receipt',name))
                    for name in receipt_names
                ),
                'actor_purchase_orders':frappe.db.count('Purchase Order',{'owner':actor}),
                'actor_subcontracting_orders':frappe.db.count(
                    'Subcontracting Order',{'owner':actor}
                ),
                'actor_stock_entries':frappe.db.count('Stock Entry',{'owner':actor}),
                'actor_subcontracting_receipts':frappe.db.count(
                    'Subcontracting Receipt',{'owner':actor}
                ),
                'user':int(bool(frappe.db.exists('User',actor))),
                'runs':sum(bool(frappe.db.exists('DS Model Run',name)) for name in run_names),
                'proposals':sum(bool(frappe.db.exists('DS Operation Proposal',name))
                                for name in proposal_names),
                'executions':sum(bool(frappe.db.exists('DS Execution Record',name))
                                 for name in execution_names),
                'stock_ledger_entries':frappe.db.count('Stock Ledger Entry',{
                    'voucher_type':['in',['Stock Entry','Subcontracting Receipt']],
                    'voucher_no':['in',[*stock_entry_names,*receipt_names]],
                }),
                'fixture_finished_good_flag':int(frappe.db.get_value(
                    'Item',finished_item,'is_sub_contracted_item'
                ) or 0),
                'fixture_service_item':int(bool(frappe.db.exists('Item',service_item))),
                'delete_linked_ledger_entries':int(frappe.db.get_single_value(
                    'Accounts Settings','delete_linked_ledger_entries'
                ) or 0),
            }
        assert ('before_insert' in SubcontractingOrder.__dict__)==sco_before_insert_local
        assert getattr(SubcontractingOrder,'before_insert',None) is sco_before_insert_original
        assert ('before_insert' in SubcontractingReceipt.__dict__)==scr_before_insert_local
        assert getattr(SubcontractingReceipt,'before_insert',None) is scr_before_insert_original
        assert frappe.generate_hash is generate_hash_original
    finally:
        frappe.destroy()

print(json.dumps(evidence,ensure_ascii=False,sort_keys=True))
'''
    result = subprocess.run(
        [
            "docker", "exec", "-i", "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python", "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["bin_before"] == evidence["bin_cleanup"]
    assert (
        evidence["fresh_residual"]["delete_linked_ledger_entries"]
        == evidence["delete_linked_ledger_entries_before"]
    )
    assert {
        key: value
        for key, value in evidence["fresh_residual"].items()
        if key != "delete_linked_ledger_entries"
    } == {
        "purchase_orders": 0,
        "subcontracting_orders": 0,
        "stock_entries": 0,
        "subcontracting_receipts": 0,
        "actor_purchase_orders": 0,
        "actor_subcontracting_orders": 0,
        "actor_stock_entries": 0,
        "actor_subcontracting_receipts": 0,
        "user": 0,
        "runs": 0,
        "proposals": 0,
        "executions": 0,
        "stock_ledger_entries": 0,
        "fixture_finished_good_flag": 1,
        "fixture_service_item": 1,
    }
