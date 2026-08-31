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
from dsherp_bridge.operations import confirm

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


def propose_make_after_rejecting_options(source_doctype,source_name,route_key):
    cap,run_name=new_run()
    frappe.set_user('Guest')
    source=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':source_doctype,'name':source_name,
    })
    before_counts=site_document_counts()
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
    return proposal,run_name


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
    po_schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Purchase Order'})
    po_fields={field['fieldname']:field for field in po_schema['fields']}
    po_item_fields={field['fieldname']:field for field in po_fields['items']['fields']}
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
    assert confirm_once(supply_submit,supply_submit_run)['status']=='Succeeded'
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
    assert len(receipt_make['target']['items'])==1
    assert receipt_make['target']['items'][0]['subcontracting_order']==subcontracting_order
    assert receipt_make['target']['items'][0]['item_code']==finished_item
    receipt_created=confirm_once(receipt_make,receipt_make_run)
    assert receipt_created['status']=='Succeeded',receipt_created
    subcontracting_receipt=receipt_created['name']
    receipt_names.append(subcontracting_receipt)
    receipt_doc=frappe.get_doc('Subcontracting Receipt',subcontracting_receipt)
    assert receipt_doc.docstatus==0 and receipt_doc.owner==actor

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
    assert flt(po_outcome['fields']['per_received'])==0,po_outcome
    assert po_outcome['fields']['status']=='To Receive and Bill',po_outcome
    assert len(po_outcome['fields']['items'])==1,po_outcome
    assert flt(
        po_outcome['fields']['items'][0]['subcontracted_quantity']
    )==purchase_qty,po_outcome
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
        'po_subcontracted_quantity':flt(
            po_outcome['fields']['items'][0]['subcontracted_quantity']
        ),
        'sco_per_received':flt(sco_outcome['fields']['per_received']),
        'sco_status':sco_outcome['fields']['status'],
        'supply_stock_ledger_rows':supply_ledgers,
        'receipt_stock_ledger_rows':receipt_ledgers,
        'delete_linked_ledger_entries_before':delete_linked_ledger_entries_before,
    }
finally:
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
            frappe.delete_doc('DS Execution Record',execution_id,ignore_permissions=True)
    for proposal_id in proposal_names:
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.delete_doc('DS Operation Proposal',proposal_id,ignore_permissions=True)
    for run_name in run_names:
        if frappe.db.exists('DS Model Run',run_name):
            frappe.delete_doc('DS Model Run',run_name,ignore_permissions=True)
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
