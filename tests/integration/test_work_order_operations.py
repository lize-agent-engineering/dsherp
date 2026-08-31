"""A native Work Order chain moves real synthetic stock and cleans reversibly."""
import json
import subprocess


def test_work_order_chain_updates_stock():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,flt,now_datetime

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()

from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import run_revision
from dsherp_bridge.operations import confirm

tag=uuid.uuid4().hex
actor='work-order-'+tag+'@example.invalid'
bom_name='BOM-DSHERP-MFG-SYN-FG-001'
finished_item='DSHERP-MFG-SYN-FG'
raw_item='DSHERP-MFG-SYN-RM'
route_transfer='work_order_material_transfer'
route_manufacture='work_order_manufacture'
method_path='erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry'
conversation=None
run_names=[]
proposal_names=[]
execution_names=[]
stock_entry_names=[]
work_order=None
pre_bins={}
after_bins={}
fresh_bins={}
evidence={}
fixture_ledger_names=[]
delete_linked_ledger_entries_before=None


def exactly_one(values,label):
    assert len(values)==1,(label,values)
    return values[0]


def quantity(item_code,warehouse):
    return flt(frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'actual_qty'
    ) or 0)


def stock_snapshot(raw_warehouse,wip_warehouse,fg_warehouse):
    return {
        'raw':quantity(raw_item,raw_warehouse),
        'wip':quantity(raw_item,wip_warehouse),
        'finished':quantity(finished_item,fg_warehouse),
    }


def new_run():
    capability=uuid.uuid4().hex
    frappe.set_user(actor)
    run=frappe.get_doc({
        'doctype':'DS Model Run',
        'conversation':conversation,
        'domain':'operation',
        'status':'Running',
        'page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
        'permission_revision':run_revision(actor,'operation'),
        'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),minutes=3),
        'sources':'[]',
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


try:
    frappe.set_user('Administrator')
    roles={row.name for row in frappe.get_all(
        'Role',filters={'name':['in',['Manufacturing User','Stock User']]},fields=['name']
    )}
    assert roles=={'Manufacturing User','Stock User'},roles
    manufacturing_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Work Order','role':'Manufacturing User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    stock_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Stock Entry','role':'Stock User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    assert manufacturing_perm and all(manufacturing_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),manufacturing_perm
    assert stock_perm and all(stock_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),stock_perm

    policies={row.target_doctype:row for row in frappe.get_all(
        'DS Doctype Policy',fields=['target_doctype','allow_read','allow_create','allow_update',
        'allow_submit','allow_cancel','allow_fill']
    )}
    assert set(('BOM','Warehouse','Bin','Work Order','Stock Entry'))<=set(policies),policies
    assert policies['Work Order'].allow_create and policies['Work Order'].allow_update
    assert policies['Work Order'].allow_submit and policies['Work Order'].allow_cancel
    assert not policies['Work Order'].allow_fill
    assert policies['Stock Entry'].allow_submit and policies['Stock Entry'].allow_cancel
    assert not policies['Stock Entry'].allow_create and not policies['Stock Entry'].allow_update
    routes=frappe.get_all(
        'DS Doctype Policy Route',filters={'parent':'Work Order'},
        fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc',
    )
    assert routes==[
        {'route_name':route_transfer,'method_path':method_path,'target_doctype':'Stock Entry'},
        {'route_name':route_manufacture,'method_path':method_path,'target_doctype':'Stock Entry'},
    ],routes

    bom=frappe.get_doc('BOM',bom_name)
    company=bom.company
    wip_warehouse=exactly_one(frappe.get_all(
        'Warehouse',filters={
            'warehouse_name':'DSHERP 制造测试合成在制仓','company':company,'is_group':0,
        },pluck='name'),'synthetic WIP Warehouse')
    fg_warehouse=exactly_one(frappe.get_all(
        'Warehouse',filters={
            'warehouse_name':'DSHERP 制造测试合成成品仓','company':company,'is_group':0,
        },pluck='name'),'synthetic finished-goods Warehouse')
    raw_warehouse=exactly_one(frappe.get_all(
        'Warehouse',filters={
            'warehouse_name':'DSHERP 制造测试合成原料仓','company':company,'is_group':0,
        },pluck='name'),'synthetic raw-material Warehouse')
    pre_bins=stock_snapshot(raw_warehouse,wip_warehouse,fg_warehouse)
    assert pre_bins=={'raw':100.0,'wip':0.0,'finished':0.0},pre_bins
    fixture_ledger_names=frappe.get_all(
        'Stock Ledger Entry',filters={
            'voucher_type':'Stock Reconciliation',
            'voucher_no':'DSHERP-MFG-SYN-OPENING-STOCK',
            'item_code':raw_item,
            'warehouse':raw_warehouse,
        },pluck='name',order_by='name asc',
    )
    assert len(fixture_ledger_names)==1,fixture_ledger_names
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )
    assert frappe.db.count('Work Order')==0
    assert frappe.db.count('Stock Entry')==0

    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic Work Order actor',
        'enabled':1,'send_welcome_email':0,
        'roles':[{'role':'Manufacturing User'},{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic Work Order chain '+tag,
    }).insert(ignore_permissions=True).name

    # The create proposal is schema-sourced and performs no Work Order write.
    cap,create_run=new_run()
    frappe.set_user('Guest')
    schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Work Order'})
    fields={field['fieldname']:field for field in schema['fields']}
    assert fields['production_item']['options']=='Item' and fields['production_item']['reqd']
    assert fields['bom_no']['options']=='BOM' and fields['bom_no']['reqd']
    assert fields['qty']['fieldtype']=='Float' and fields['qty']['reqd']
    assert fields['source_warehouse']['options']=='Warehouse'
    assert fields['wip_warehouse']['options']=='Warehouse'
    assert fields['fg_warehouse']['options']=='Warehouse' and fields['fg_warehouse']['reqd']
    assert fields['naming_series']['options']=='MFG-WO-.YYYY.-'
    required_item_fields={
        field['fieldname']:field for field in fields['required_items']['fields']
    }
    assert required_item_fields['item_code']['options']=='Item'
    assert required_item_fields['source_warehouse']['options']=='Warehouse'
    assert required_item_fields['required_qty']['fieldtype']=='Float'
    actual_bom=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'BOM','name':bom_name})
    actual_wip=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Warehouse','name':wip_warehouse,
    })
    actual_raw=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Warehouse','name':raw_warehouse,
    })
    actual_fg=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Warehouse','name':fg_warehouse,
    })
    assert actual_bom['fields']['item']==finished_item
    assert flt(actual_bom['fields']['quantity'])==1
    raw_rows=actual_bom['fields']['items']
    assert len(raw_rows)==1 and raw_rows[0]['item_code']==raw_item,raw_rows
    assert actual_raw['fields']['company']==actual_wip['fields']['company']==company
    assert actual_fg['fields']['company']==company
    values={
        'naming_series':fields['naming_series']['options'],
        'production_item':actual_bom['fields']['item'],
        'bom_no':actual_bom['name'],
        'company':company,
        'qty':1,
        'source_warehouse':actual_raw['name'],
        'wip_warehouse':actual_wip['name'],
        'fg_warehouse':actual_fg['name'],
        'planned_start_date':str(now_datetime()),
        'required_items':[{
            'item_code':raw_rows[0]['item_code'],
            'source_warehouse':actual_raw['name'],
            'required_qty':flt(raw_rows[0]['qty']),
            'include_item_in_manufacturing':1,
        }],
    }
    work_order_before=frappe.db.count('Work Order')
    create=run_tool(**cap,tool='erp_propose_create',arguments={
        'doctype':'Work Order','values':values,'version':str(schema['modified']),
    })
    assert frappe.db.count('Work Order')==work_order_before
    created=confirm_once(create,create_run)
    assert created['status']=='Succeeded' and created['doctype']=='Work Order',created
    work_order=created['name']
    draft=frappe.get_doc('Work Order',work_order)
    assert draft.docstatus==0 and draft.modified_by==actor
    assert draft.production_item==finished_item and flt(draft.qty)==1
    assert draft.bom_no==bom_name
    assert draft.wip_warehouse==wip_warehouse and draft.fg_warehouse==fg_warehouse

    # Submit is a distinct proposal and confirmation over an exact fresh read.
    cap,submit_work_order_run=new_run()
    frappe.set_user('Guest')
    draft_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Work Order','name':work_order,
    })
    assert draft_read['fields']['docstatus']==0
    submit_work_order=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Work Order','name':work_order,'action':'submit',
        'version':str(draft_read['modified']),
    })
    assert frappe.db.get_value('Work Order',work_order,'docstatus')==0
    submitted=confirm_once(submit_work_order,submit_work_order_run)
    assert submitted['status']=='Succeeded',submitted
    assert frappe.db.get_value('Work Order',work_order,'docstatus')==1

    # Public make rejects extra options before route execution or proposal/write.
    cap,transfer_make_run=new_run()
    frappe.set_user('Guest')
    submitted_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Work Order','name':work_order,
    })
    rejected_proposals=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
    rejected_stock_entries=frappe.db.count('Stock Entry')
    try:
        run_tool(**cap,tool='erp_propose_make',arguments={
            'source_doctype':'Work Order','source_name':work_order,
            'source_version':str(submitted_read['modified']),'route':route_transfer,
            'options':{},
        })
        raise AssertionError('extra make options were accepted')
    except frappe.ValidationError as error:
        assert str(error)=='操作提案参数无效',error
    assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==rejected_proposals
    assert frappe.db.count('Stock Entry')==rejected_stock_entries

    transfer_make=run_tool(**cap,tool='erp_propose_make',arguments={
        'source_doctype':'Work Order','source_name':work_order,
        'source_version':str(submitted_read['modified']),'route':route_transfer,
    })
    assert transfer_make['target']['doctype']=='Stock Entry'
    assert transfer_make['target']['purpose']=='Material Transfer for Manufacture'
    assert transfer_make['target']['work_order']==work_order
    assert frappe.db.count('Stock Entry')==rejected_stock_entries
    transfer_created=confirm_once(transfer_make,transfer_make_run)
    assert transfer_created['status']=='Succeeded',transfer_created
    transfer_entry=transfer_created['name']
    stock_entry_names.append(transfer_entry)
    transfer_doc=frappe.get_doc('Stock Entry',transfer_entry)
    assert transfer_doc.docstatus==0 and transfer_doc.purpose=='Material Transfer for Manufacture'

    cap,transfer_submit_run=new_run()
    frappe.set_user('Guest')
    transfer_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Stock Entry','name':transfer_entry,
    })
    transfer_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Stock Entry','name':transfer_entry,'action':'submit',
        'version':str(transfer_read['modified']),
    })
    assert frappe.db.get_value('Stock Entry',transfer_entry,'docstatus')==0
    transfer_sle_before=frappe.db.count('Stock Ledger Entry',{
        'voucher_type':'Stock Entry','voucher_no':transfer_entry,
    })
    transfer_submitted=confirm_once(transfer_submit,transfer_submit_run)
    assert transfer_submitted['status']=='Succeeded',transfer_submitted
    transfer_sle_after=frappe.db.count('Stock Ledger Entry',{
        'voucher_type':'Stock Entry','voucher_no':transfer_entry,
    })
    assert transfer_sle_after>transfer_sle_before
    transfer_duplicate=confirm(transfer_submit['id'],transfer_submit['digest'],uuid.uuid4().hex)
    assert transfer_duplicate==transfer_submitted
    assert frappe.db.count('DS Execution Record',{'proposal':transfer_submit['id']})==1
    assert frappe.db.count('Stock Ledger Entry',{
        'voucher_type':'Stock Entry','voucher_no':transfer_entry,
    })==transfer_sle_after

    cap,manufacture_make_run=new_run()
    frappe.set_user('Guest')
    transferred_work_order=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Work Order','name':work_order,
    })
    manufacture_make=run_tool(**cap,tool='erp_propose_make',arguments={
        'source_doctype':'Work Order','source_name':work_order,
        'source_version':str(transferred_work_order['modified']),'route':route_manufacture,
    })
    assert manufacture_make['target']['purpose']=='Manufacture'
    assert manufacture_make['target']['work_order']==work_order
    manufacture_created=confirm_once(manufacture_make,manufacture_make_run)
    assert manufacture_created['status']=='Succeeded',manufacture_created
    manufacture_entry=manufacture_created['name']
    stock_entry_names.append(manufacture_entry)
    manufacture_doc=frappe.get_doc('Stock Entry',manufacture_entry)
    assert manufacture_doc.docstatus==0 and manufacture_doc.purpose=='Manufacture'

    cap,manufacture_submit_run=new_run()
    frappe.set_user('Guest')
    manufacture_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Stock Entry','name':manufacture_entry,
    })
    manufacture_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Stock Entry','name':manufacture_entry,'action':'submit',
        'version':str(manufacture_read['modified']),
    })
    manufacture_submitted=confirm_once(manufacture_submit,manufacture_submit_run)
    assert manufacture_submitted['status']=='Succeeded',manufacture_submitted

    after_bins=stock_snapshot(raw_warehouse,wip_warehouse,fg_warehouse)
    assert after_bins['raw']==pre_bins['raw']-2,after_bins
    assert after_bins['finished']==pre_bins['finished']+1,after_bins
    assert after_bins['wip']==pre_bins['wip'],after_bins
    ledger_rows=frappe.get_all(
        'Stock Ledger Entry',
        filters={'voucher_type':'Stock Entry','voucher_no':['in',stock_entry_names]},
        fields=['voucher_no','item_code','warehouse','actual_qty'],
        order_by='voucher_no asc,creation asc,name asc',
    )
    assert sum(flt(row.actual_qty) for row in ledger_rows
               if row.item_code==raw_item and row.warehouse==raw_warehouse)==-2
    assert sum(flt(row.actual_qty) for row in ledger_rows
               if row.item_code==raw_item and row.warehouse==wip_warehouse)==0
    assert sum(flt(row.actual_qty) for row in ledger_rows
               if row.item_code==finished_item and row.warehouse==fg_warehouse)==1

    # Read the actual Bin records and native Work Order outcome through run_tool.
    cap,outcome_run=new_run()
    frappe.set_user('Guest')
    bin_values={}
    for key,item_code,warehouse in (
        ('raw',raw_item,raw_warehouse),
        ('wip',raw_item,wip_warehouse),
        ('finished',finished_item,fg_warehouse),
    ):
        bin_name=frappe.db.get_value('Bin',{'item_code':item_code,'warehouse':warehouse},'name')
        assert bin_name,(item_code,warehouse)
        record=run_tool(**cap,tool='erp_read_record',arguments={'doctype':'Bin','name':bin_name})
        assert record['fields']['item_code']==item_code
        assert record['fields']['warehouse']==warehouse
        bin_values[key]=flt(record['fields']['actual_qty'])
    assert bin_values==after_bins
    completed=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Work Order','name':work_order,
    })
    assert completed['fields']['status']=='Completed',completed
    assert flt(completed['fields']['produced_qty'])==1,completed

    # Stock Entry drafts are make-only; schema read cannot authorize direct create.
    cap,direct_create_run=new_run()
    frappe.set_user('Guest')
    stock_schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Stock Entry'})
    direct_proposals=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
    direct_stock_entries=frappe.db.count('Stock Entry')
    try:
        run_tool(**cap,tool='erp_propose_create',arguments={
            'doctype':'Stock Entry','values':{'company':company},
            'version':str(stock_schema['modified']),
        })
        raise AssertionError('direct Stock Entry create proposal was accepted')
    except frappe.PermissionError as error:
        assert '未允许 Stock Entry 的 create 操作' in str(error),error
    assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==direct_proposals
    assert frappe.db.count('Stock Entry')==direct_stock_entries

    evidence={
        'roles':sorted(roles),
        'work_order_schema_version':str(schema['modified']),
        'work_order_fields':sorted(values),
        'warehouses':{
            'raw':raw_warehouse,'wip':wip_warehouse,'finished':fg_warehouse,
        },
        'work_order':work_order,
        'stock_entries':{
            'material_transfer':transfer_entry,'manufacture':manufacture_entry,
        },
        'bin_before':pre_bins,
        'bin_after':after_bins,
        'duplicate_submit':{
            'execution_id':transfer_submitted['execution_id'],
            'stock_ledger_rows_before':transfer_sle_before,
            'stock_ledger_rows_after':transfer_sle_after,
            'stock_ledger_rows_after_duplicate':frappe.db.count(
                'Stock Ledger Entry',{
                    'voucher_type':'Stock Entry','voucher_no':transfer_entry,
                },
            ),
        },
        'native_work_order':{
            'status':completed['fields']['status'],
            'produced_qty':flt(completed['fields']['produced_qty']),
        },
    }
finally:
    frappe.db.rollback()
    frappe.set_user('Administrator')
    if work_order:
        stock_entry_names.extend(frappe.get_all(
            'Stock Entry',filters={'work_order':work_order},pluck='name'
        ))
    stock_entry_names=list(dict.fromkeys(filter(None,stock_entry_names)))
    stock_docs=[]
    for name in stock_entry_names:
        if frappe.db.exists('Stock Entry',name):
            stock_docs.append(frappe.get_doc('Stock Entry',name))
    stock_docs.sort(key=lambda doc:(
        0 if doc.purpose=='Manufacture' else 1,
        doc.creation,
    ))
    for doc in stock_docs:
        if doc.docstatus==1:
            doc.cancel()
    # This fixed ERPNext version retains cancelled ledgers by default. Its own
    # tests temporarily enable the native voucher on_trash cascade, then restore
    # the setting, so test vouchers can be deleted without direct ledger writes.
    from frappe.tests.utils import change_settings
    with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
        for doc in stock_docs:
            if frappe.db.exists('Stock Entry',doc.name):
                frappe.delete_doc('Stock Entry',doc.name,ignore_permissions=True)
    if work_order and frappe.db.exists('Work Order',work_order):
        doc=frappe.get_doc('Work Order',work_order)
        if doc.docstatus==1:
            doc.cancel()
        frappe.delete_doc('Work Order',work_order,ignore_permissions=True)
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
        fresh_bins=stock_snapshot(raw_warehouse,wip_warehouse,fg_warehouse)
        assert fresh_bins==pre_bins,(pre_bins,fresh_bins)
        assert not frappe.db.exists('User',actor)
        if work_order:
            assert not frappe.db.exists('Work Order',work_order)
        assert all(not frappe.db.exists('Stock Entry',name) for name in stock_entry_names)
        assert frappe.db.count('Stock Ledger Entry',{
            'voucher_type':'Stock Entry',
            'voucher_no':['in',stock_entry_names],
            'item_code':['in',[raw_item,finished_item]],
        })==0
        assert frappe.get_all(
            'Stock Ledger Entry',filters={
                'voucher_type':'Stock Reconciliation',
                'voucher_no':'DSHERP-MFG-SYN-OPENING-STOCK',
                'item_code':raw_item,
                'warehouse':raw_warehouse,
            },pluck='name',order_by='name asc',
        )==fixture_ledger_names
        assert int(frappe.db.get_single_value(
            'Accounts Settings','delete_linked_ledger_entries'
        ) or 0)==delete_linked_ledger_entries_before
        assert all(not frappe.db.exists('DS Execution Record',name) for name in execution_names)
        assert all(not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names)
        assert all(not frappe.db.exists('DS Model Run',name) for name in run_names)
        if conversation:
            assert not frappe.db.exists('DS Conversation',conversation)
        work_order_policy=frappe.get_doc('DS Doctype Policy','Work Order')
        assert [row.route_name for row in work_order_policy.routes]==[
            route_transfer,route_manufacture,
        ]
        evidence['bin_cleanup']=fresh_bins
        evidence['fresh_residual']={
            'work_order':int(bool(
                work_order and frappe.db.exists('Work Order',work_order)
            )),
            'stock_entries':sum(
                bool(frappe.db.exists('Stock Entry',name)) for name in stock_entry_names
            ),
            'user':int(bool(frappe.db.exists('User',actor))),
            'runs':sum(bool(frappe.db.exists('DS Model Run',name)) for name in run_names),
            'proposals':sum(
                bool(frappe.db.exists('DS Operation Proposal',name)) for name in proposal_names
            ),
            'executions':sum(
                bool(frappe.db.exists('DS Execution Record',name)) for name in execution_names
            ),
            'stock_ledger_entries':frappe.db.count('Stock Ledger Entry',{
                'voucher_type':'Stock Entry',
                'voucher_no':['in',stock_entry_names],
                'item_code':['in',[raw_item,finished_item]],
            }),
            'fixture_stock_ledger_entries':len(fixture_ledger_names),
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
            'docker','exec','-i','dsherp-validation-backend-1',
            '/home/frappe/frappe-bench/env/bin/python','-',
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['bin_before'] == evidence['bin_cleanup']
    assert evidence['fresh_residual'] == {
        'work_order':0,
        'stock_entries':0,
        'user':0,
        'runs':0,
        'proposals':0,
        'executions':0,
        'stock_ledger_entries':0,
        'fixture_stock_ledger_entries':1,
        'delete_linked_ledger_entries':0,
    }
