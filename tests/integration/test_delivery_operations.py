"""A confirmed Sales Order delivery changes real synthetic stock exactly once."""
import json
import subprocess


def test_delivery_note_from_sales_order_updates_delivery_status():
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
actor='delivery-'+tag+'@example.invalid'
customer='DSHERP-TEST-CUSTOMER'
item_code='DSHERP-MFG-SYN-RM'
delivery_qty=2
route_name='sales_order_to_delivery_note'
method_path='erpnext.selling.doctype.sales_order.sales_order.make_delivery_note'
conversation=None
run_names=[]
proposal_names=[]
execution_names=[]
sales_order_names=[]
delivery_note_names=[]
sales_order=None
delivery_note=None
warehouse=None
pre_bin=None
fresh_bin=None
delete_linked_ledger_entries_before=None
evidence={}
fixture_ledger_names=[]
initial_document_counts=None


def quantity():
    return flt(frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'actual_qty'
    ) or 0)


def site_document_counts():
    return {
        'sales_orders':frappe.db.count('Sales Order'),
        'delivery_notes':frappe.db.count('Delivery Note'),
    }


def stock_ledger_snapshot():
    return frappe.get_all(
        'Stock Ledger Entry',
        filters={'item_code':item_code,'warehouse':warehouse},
        fields=['name','voucher_type','voucher_no','actual_qty','is_cancelled'],
        order_by='creation asc,name asc',
    )


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
        'Role',filters={'name':['in',['Sales User','Stock User']]},fields=['name']
    )}
    assert roles=={'Sales User','Stock User'},roles
    sales_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Sales Order','role':'Sales User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    delivery_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Delivery Note','role':'Stock User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    assert sales_perm and all(sales_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),sales_perm
    assert delivery_perm and all(delivery_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),delivery_perm

    policies={row.target_doctype:row for row in frappe.get_all(
        'DS Doctype Policy',fields=['target_doctype','allow_read','allow_create','allow_update',
        'allow_submit','allow_cancel','allow_fill'],
    )}
    assert 'Sales Order' in policies and 'Delivery Note' in policies,policies
    assert policies['Sales Order'].allow_read and policies['Sales Order'].allow_create
    assert policies['Sales Order'].allow_update and policies['Sales Order'].allow_submit
    assert policies['Sales Order'].allow_cancel and policies['Sales Order'].allow_fill
    assert policies['Delivery Note'].allow_read and policies['Delivery Note'].allow_submit
    assert policies['Delivery Note'].allow_cancel and not policies['Delivery Note'].allow_create
    assert not policies['Delivery Note'].allow_update and not policies['Delivery Note'].allow_fill
    routes=frappe.get_all(
        'DS Doctype Policy Route',filters={'parent':'Sales Order'},
        fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc',
    )
    assert routes==[{
        'route_name':route_name,
        'method_path':method_path,
        'target_doctype':'Delivery Note',
    }],routes

    company=frappe.get_all('Company',pluck='name')
    assert len(company)==1,company
    company=company[0]
    warehouse=frappe.get_all(
        'Warehouse',filters={
            'warehouse_name':'DSHERP 制造测试合成原料仓',
            'company':company,
            'is_group':0,
        },pluck='name',
    )
    assert len(warehouse)==1,warehouse
    warehouse=warehouse[0]
    assert frappe.db.exists('Customer',customer)
    assert frappe.db.exists('Item',item_code)
    item=frappe.get_doc('Item',item_code)
    assert item.is_stock_item and item.is_sales_item and not item.disabled
    pre_bin=quantity()
    assert pre_bin>=delivery_qty,pre_bin
    fixture_ledger_names=frappe.get_all(
        'Stock Ledger Entry',filters={
            'voucher_type':'Stock Reconciliation',
            'voucher_no':'DSHERP-MFG-SYN-OPENING-STOCK',
            'item_code':item_code,
            'warehouse':warehouse,
        },pluck='name',order_by='name asc',
    )
    assert len(fixture_ledger_names)==1,fixture_ledger_names
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )
    initial_document_counts=site_document_counts()
    assert frappe.db.count('Sales Order',{'owner':actor})==0
    assert frappe.db.count('Delivery Note',{'owner':actor})==0

    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic Delivery actor',
        'enabled':1,'send_welcome_email':0,
        'roles':[{'role':'Sales User'},{'role':'Stock User'}],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic delivery chain '+tag,
    }).insert(ignore_permissions=True).name

    # Create and submit the source Sales Order through the server-bound HITL path.
    cap,create_run=new_run()
    frappe.set_user('Guest')
    schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Sales Order'})
    fields={field['fieldname']:field for field in schema['fields']}
    item_fields={field['fieldname']:field for field in fields['items']['fields']}
    assert fields['customer']['options']=='Customer' and fields['customer']['reqd']
    assert fields['delivery_date']['fieldtype']=='Date',fields['delivery_date']
    assert item_fields['item_code']['options']=='Item',item_fields['item_code']
    assert item_fields['warehouse']['options']=='Warehouse',item_fields['warehouse']
    actual_customer=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Customer','name':customer,
    })
    actual_item=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Item','name':item_code,
    })
    actual_warehouse=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Warehouse','name':warehouse,
    })
    assert actual_warehouse['fields']['company']==company
    delivery_date=str(add_days(nowdate(),7))
    values={
        'naming_series':fields['naming_series']['options'],
        'customer':actual_customer['name'],
        'transaction_date':str(nowdate()),
        'delivery_date':delivery_date,
        'company':company,
        'currency':'CNY',
        'conversion_rate':1,
        'selling_price_list':'Standard Selling',
        'items':[{
            'item_code':actual_item['name'],
            'qty':delivery_qty,
            'rate':10,
            'warehouse':actual_warehouse['name'],
            'delivery_date':delivery_date,
            'uom':'Nos',
        }],
    }
    create_counts=site_document_counts()
    create_proposal=run_tool(**cap,tool='erp_propose_create',arguments={
        'doctype':'Sales Order','values':values,'version':str(schema['modified']),
    })
    assert site_document_counts()==create_counts
    created=confirm_once(create_proposal,create_run)
    assert created['status']=='Succeeded' and created['doctype']=='Sales Order',created
    sales_order=created['name']
    sales_order_names.append(sales_order)
    order_doc=frappe.get_doc('Sales Order',sales_order)
    assert order_doc.docstatus==0 and order_doc.owner==actor
    assert len(order_doc.items)==1 and order_doc.items[0].item_code==item_code
    assert order_doc.items[0].warehouse==warehouse

    cap,submit_order_run=new_run()
    frappe.set_user('Guest')
    order_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Sales Order','name':sales_order,
    })
    submit_counts=site_document_counts()
    submit_order=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Sales Order','name':sales_order,'action':'submit',
        'version':str(order_read['modified']),
    })
    assert submit_order['impact']=={'kind':'none','entries':[]},submit_order
    assert site_document_counts()==submit_counts
    assert frappe.db.get_value('Sales Order',sales_order,'docstatus')==0
    assert confirm_once(submit_order,submit_order_run)['status']=='Succeeded'
    assert frappe.db.get_value('Sales Order',sales_order,'docstatus')==1

    # Public options are forbidden before proposal creation or mapped-document writes.
    cap,make_run=new_run()
    frappe.set_user('Guest')
    submitted_order=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Sales Order','name':sales_order,
    })
    rejected_proposals=frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )
    rejected_notes=frappe.db.count('Delivery Note',{'owner':actor})
    rejected_counts=site_document_counts()
    rejected_sle_count=frappe.db.count('Stock Ledger Entry')
    rejected_sle_snapshot=stock_ledger_snapshot()
    rejected_bin=quantity()
    try:
        run_tool(**cap,tool='erp_propose_make',arguments={
            'source_doctype':'Sales Order','source_name':sales_order,
            'source_version':str(submitted_order['modified']),'route':route_name,
            'options':{},
        })
        raise AssertionError('extra make options were accepted')
    except frappe.ValidationError as error:
        assert str(error)=='操作提案参数无效',error
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==rejected_proposals
    assert frappe.db.count('Delivery Note',{'owner':actor})==rejected_notes
    assert site_document_counts()==rejected_counts
    assert frappe.db.count('Stock Ledger Entry')==rejected_sle_count
    assert stock_ledger_snapshot()==rejected_sle_snapshot
    assert quantity()==rejected_bin

    make_proposal=run_tool(**cap,tool='erp_propose_make',arguments={
        'source_doctype':'Sales Order','source_name':sales_order,
        'source_version':str(submitted_order['modified']),'route':route_name,
    })
    assert make_proposal['target']['doctype']=='Delivery Note'
    assert make_proposal['target']['customer']==customer
    assert len(make_proposal['target']['items'])==1
    assert make_proposal['target']['items'][0]['against_sales_order']==sales_order
    assert make_proposal['target']['items'][0]['item_code']==item_code
    assert flt(make_proposal['target']['items'][0]['qty'])==delivery_qty
    assert make_proposal['target']['items'][0]['warehouse']==warehouse
    assert site_document_counts()==rejected_counts
    note_created=confirm_once(make_proposal,make_run)
    assert note_created['status']=='Succeeded',note_created
    delivery_note=note_created['name']
    delivery_note_names.append(delivery_note)
    note_doc=frappe.get_doc('Delivery Note',delivery_note)
    assert note_doc.docstatus==0 and note_doc.owner==actor
    assert note_doc.items[0].against_sales_order==sales_order

    def reject_unsupported_note_shape(target,fieldname,value):
        frappe.set_user('Administrator')
        saved=target.get(fieldname)
        target.db_set(fieldname,value,update_modified=False);frappe.db.commit()
        try:
            cap,_=new_run();frappe.set_user('Guest')
            read=run_tool(**cap,tool='erp_read_record',arguments={
                'doctype':'Delivery Note','name':delivery_note,
            })
            proposals_before=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
            sle_before=frappe.db.count('Stock Ledger Entry')
            bin_before=quantity()
            try:
                run_tool(**cap,tool='erp_propose_action',arguments={
                    'doctype':'Delivery Note','name':delivery_note,'action':'submit',
                    'version':str(read['modified']),
                })
                raise AssertionError('unsupported Delivery Note impact was proposed')
            except frappe.ValidationError as error:
                assert '库存影响无法确定' in str(error) and '暂不支持' in str(error),error
            assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==proposals_before
            assert frappe.db.count('Stock Ledger Entry')==sle_before
            assert quantity()==bin_before
        finally:
            frappe.set_user('Administrator')
            target.db_set(fieldname,saved,update_modified=False);frappe.db.commit()

    reject_unsupported_note_shape(note_doc.items[0],'target_warehouse',warehouse)
    reject_unsupported_note_shape(note_doc,'is_return',1)
    frappe.set_user('Administrator')
    packed=note_doc.append('packed_items',{
        'item_code':item_code,'qty':1,'warehouse':warehouse,'parent':note_doc.name,
        'parenttype':'Delivery Note','parentfield':'packed_items',
    })
    packed.db_insert();frappe.db.commit()
    try:
        cap,_=new_run();frappe.set_user('Guest')
        packed_read=run_tool(**cap,tool='erp_read_record',arguments={
            'doctype':'Delivery Note','name':delivery_note,
        })
        proposals_before=frappe.db.count('DS Operation Proposal',{'conversation':conversation})
        sle_before=frappe.db.count('Stock Ledger Entry');bin_before=quantity()
        try:
            run_tool(**cap,tool='erp_propose_action',arguments={
                'doctype':'Delivery Note','name':delivery_note,'action':'submit',
                'version':str(packed_read['modified']),
            })
            raise AssertionError('Delivery Note packed stock impact was proposed')
        except frappe.ValidationError as error:
            assert '库存影响无法确定' in str(error) and '暂不支持' in str(error),error
        assert frappe.db.count('DS Operation Proposal',{'conversation':conversation})==proposals_before
        assert frappe.db.count('Stock Ledger Entry')==sle_before and quantity()==bin_before
    finally:
        frappe.set_user('Administrator')
        packed.delete(ignore_permissions=True);frappe.db.commit()

    # Submit changes stock once and updates the exact source order status.
    cap,submit_note_run=new_run()
    frappe.set_user('Guest')
    note_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Delivery Note','name':delivery_note,
    })
    note_submit_counts=site_document_counts()
    submit_note=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Delivery Note','name':delivery_note,'action':'submit',
        'version':str(note_read['modified']),
    })
    assert submit_note['impact']=={'kind':'stock','entries':[
        {'item_code':item_code,'quantity':-delivery_qty,'uom':'Nos','warehouse':warehouse},
    ]},submit_note
    assert site_document_counts()==note_submit_counts
    assert frappe.db.get_value('Delivery Note',delivery_note,'docstatus')==0
    submitted_note=confirm_once(submit_note,submit_note_run)
    assert submitted_note['status']=='Succeeded',submitted_note
    assert quantity()==pre_bin-delivery_qty,(pre_bin,quantity())
    order_outcome=frappe.get_doc('Sales Order',sales_order)
    assert flt(order_outcome.per_delivered)==100
    assert order_outcome.status=='To Bill',order_outcome.status
    ledger_rows=frappe.get_all(
        'Stock Ledger Entry',
        filters={'voucher_type':'Delivery Note','voucher_no':delivery_note},
        fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )
    assert ledger_rows==[{
        'voucher_type':'Delivery Note',
        'voucher_no':delivery_note,
        'item_code':item_code,
        'warehouse':warehouse,
        'actual_qty':-delivery_qty,
    }],ledger_rows
    note_count_after_submit=frappe.db.count('Delivery Note',{'owner':actor})
    bin_after_submit=quantity()
    duplicate=confirm(submit_note['id'],submit_note['digest'],uuid.uuid4().hex)
    assert duplicate==submitted_note
    assert frappe.db.count('DS Execution Record',{'proposal':submit_note['id']})==1
    assert frappe.db.count('Delivery Note',{'owner':actor})==note_count_after_submit
    assert quantity()==bin_after_submit
    assert frappe.get_all(
        'Stock Ledger Entry',
        filters={'voucher_type':'Delivery Note','voucher_no':delivery_note},
        fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )==ledger_rows

    # Delivery Note drafts are make-only; direct create is denied without writes.
    cap,direct_create_run=new_run()
    frappe.set_user('Guest')
    note_schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Delivery Note'})
    direct_proposals=frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )
    direct_notes=frappe.db.count('Delivery Note',{'owner':actor})
    direct_counts=site_document_counts()
    direct_sle_count=frappe.db.count('Stock Ledger Entry')
    direct_sle_snapshot=stock_ledger_snapshot()
    direct_bin=quantity()
    try:
        run_tool(**cap,tool='erp_propose_create',arguments={
            'doctype':'Delivery Note',
            'values':{'customer':customer,'company':company,'items':[]},
            'version':str(note_schema['modified']),
        })
        raise AssertionError('direct Delivery Note create proposal was accepted')
    except frappe.PermissionError as error:
        assert '未允许 Delivery Note 的 create 操作' in str(error),error
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==direct_proposals
    assert frappe.db.count('Delivery Note',{'owner':actor})==direct_notes
    assert site_document_counts()==direct_counts
    assert frappe.db.count('Stock Ledger Entry')==direct_sle_count
    assert stock_ledger_snapshot()==direct_sle_snapshot
    assert quantity()==direct_bin

    # Read the native outcome through the same bound tool path.
    cap,outcome_run=new_run()
    frappe.set_user('Guest')
    note_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Delivery Note','name':delivery_note,
    })
    order_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Sales Order','name':sales_order,
    })
    bin_name=frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'name'
    )
    bin_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Bin','name':bin_name,
    })
    assert note_outcome['fields']['docstatus']==1
    assert flt(order_outcome['fields']['per_delivered'])==100
    assert order_outcome['fields']['status']=='To Bill'
    assert flt(bin_outcome['fields']['actual_qty'])==pre_bin-delivery_qty

    evidence={
        'roles':sorted(roles),
        'site_document_baseline':initial_document_counts,
        'sales_order':sales_order,
        'delivery_note':delivery_note,
        'warehouse':warehouse,
        'bin_before':pre_bin,
        'bin_after':quantity(),
        'delete_linked_ledger_entries_before':delete_linked_ledger_entries_before,
        'per_delivered':flt(order_outcome['fields']['per_delivered']),
        'sales_order_status':order_outcome['fields']['status'],
        'duplicate_submit':{
            'execution_id':submitted_note['execution_id'],
            'delivery_note_count':note_count_after_submit,
            'stock_ledger_rows':len(ledger_rows),
        },
    }
finally:
    frappe.db.rollback()
    frappe.set_user('Administrator')
    sales_order_names.extend(frappe.get_all(
        'Sales Order',filters={'owner':actor},pluck='name'
    ))
    sales_order_names=list(dict.fromkeys(filter(None,sales_order_names)))
    delivery_note_names.extend(frappe.get_all(
        'Delivery Note',filters={'owner':actor},pluck='name'
    ))
    if sales_order_names:
        delivery_note_names.extend(frappe.get_all(
            'Delivery Note Item',
            filters={'against_sales_order':['in',sales_order_names]},pluck='parent',
        ))
    delivery_note_names=list(dict.fromkeys(filter(None,delivery_note_names)))
    note_docs=[]
    for name in delivery_note_names:
        if frappe.db.exists('Delivery Note',name):
            note_docs.append(frappe.get_doc('Delivery Note',name))
    for doc in note_docs:
        if doc.docstatus==1:
            doc.cancel()
    from frappe.tests.utils import change_settings
    with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
        for doc in note_docs:
            if frappe.db.exists('Delivery Note',doc.name):
                frappe.delete_doc('Delivery Note',doc.name,ignore_permissions=True)
    for name in sales_order_names:
        if frappe.db.exists('Sales Order',name):
            doc=frappe.get_doc('Sales Order',name)
            if doc.docstatus==1:
                doc.cancel()
            frappe.delete_doc('Sales Order',name,ignore_permissions=True)
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
        fresh_bin=quantity() if warehouse and pre_bin is not None else pre_bin
        if warehouse and pre_bin is not None:
            assert fresh_bin==pre_bin,(pre_bin,fresh_bin)
        assert not frappe.db.exists('User',actor)
        assert frappe.db.exists('Customer',customer)
        assert frappe.db.exists('Item',item_code)
        assert frappe.db.exists('Warehouse',warehouse)
        assert all(
            frappe.db.exists('Stock Ledger Entry',name) for name in fixture_ledger_names
        )
        if initial_document_counts is not None:
            assert site_document_counts()==initial_document_counts
        assert all(not frappe.db.exists('Sales Order',name) for name in sales_order_names)
        assert frappe.db.count('Sales Order',{'owner':actor})==0
        assert all(not frappe.db.exists('Delivery Note',name) for name in delivery_note_names)
        assert frappe.db.count('Delivery Note',{'owner':actor})==0
        assert frappe.db.count('Stock Ledger Entry',{
            'voucher_type':'Delivery Note',
            'voucher_no':['in',delivery_note_names],
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
            evidence['bin_cleanup']=fresh_bin
            evidence['fresh_residual']={
                'sales_orders':sum(
                    bool(frappe.db.exists('Sales Order',name)) for name in sales_order_names
                ),
                'delivery_notes':sum(
                    bool(frappe.db.exists('Delivery Note',name))
                    for name in delivery_note_names
                ),
                'actor_sales_orders':frappe.db.count('Sales Order',{'owner':actor}),
                'actor_delivery_notes':frappe.db.count('Delivery Note',{'owner':actor}),
                'user':int(bool(frappe.db.exists('User',actor))),
                'runs':sum(bool(frappe.db.exists('DS Model Run',name)) for name in run_names),
                'proposals':sum(
                    bool(frappe.db.exists('DS Operation Proposal',name))
                    for name in proposal_names
                ),
                'executions':sum(
                    bool(frappe.db.exists('DS Execution Record',name))
                    for name in execution_names
                ),
                'stock_ledger_entries':frappe.db.count('Stock Ledger Entry',{
                    'voucher_type':'Delivery Note',
                    'voucher_no':['in',delivery_note_names],
                }),
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
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["roles"] == ["Sales User", "Stock User"]
    assert evidence["per_delivered"] == 100
    assert evidence["sales_order_status"] == "To Bill"
    assert evidence["bin_after"] == evidence["bin_before"] - 2
    assert evidence["bin_cleanup"] == evidence["bin_before"]
    assert (
        evidence["fresh_residual"]["delete_linked_ledger_entries"]
        == evidence["delete_linked_ledger_entries_before"]
    )
    assert {
        key: value
        for key, value in evidence["fresh_residual"].items()
        if key != "delete_linked_ledger_entries"
    } == {
        "sales_orders": 0,
        "delivery_notes": 0,
        "actor_sales_orders": 0,
        "actor_delivery_notes": 0,
        "user": 0,
        "runs": 0,
        "proposals": 0,
        "executions": 0,
        "stock_ledger_entries": 0,
    }


def test_delivery_zero_write_guards_detect_a_native_stock_mutation():
    script = r'''
import json,os,uuid,frappe
from frappe.utils import flt,nowdate

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()

item_code='DSHERP-MFG-SYN-RM'
stock_entry=None
stock_entry_names=[]
warehouse=None
pre_bin=None
delete_linked_ledger_entries_before=None
evidence={}


def quantity():
    return flt(frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'actual_qty'
    ) or 0)


def stock_ledger_snapshot():
    return frappe.get_all(
        'Stock Ledger Entry',
        filters={'item_code':item_code,'warehouse':warehouse},
        fields=['name','voucher_type','voucher_no','actual_qty','is_cancelled'],
        order_by='creation asc,name asc',
    )


try:
    frappe.set_user('Administrator')
    company=frappe.get_all('Company',pluck='name')
    assert len(company)==1,company
    warehouse=frappe.get_all(
        'Warehouse',filters={
            'warehouse_name':'DSHERP 制造测试合成原料仓',
            'company':company[0],
            'is_group':0,
        },pluck='name',
    )
    assert len(warehouse)==1,warehouse
    warehouse=warehouse[0]
    pre_bin=quantity()
    pre_sle_count=frappe.db.count('Stock Ledger Entry')
    pre_sle_snapshot=stock_ledger_snapshot()
    old_guards={
        'sales_orders':frappe.db.count('Sales Order'),
        'delivery_notes':frappe.db.count('Delivery Note'),
        'proposals':frappe.db.count('DS Operation Proposal'),
    }
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )

    stock_entry=frappe.get_doc({
        'doctype':'Stock Entry',
        'stock_entry_type':'Material Receipt',
        'purpose':'Material Receipt',
        'company':company[0],
        'posting_date':str(nowdate()),
        'items':[{
            'item_code':item_code,
            'qty':1,
            't_warehouse':warehouse,
            'basic_rate':1,
        }],
    })
    stock_entry.insert()
    stock_entry_names.append(stock_entry.name)
    stock_entry.submit()
    frappe.db.commit()

    assert old_guards=={
        'sales_orders':frappe.db.count('Sales Order'),
        'delivery_notes':frappe.db.count('Delivery Note'),
        'proposals':frappe.db.count('DS Operation Proposal'),
    }
    detected=[]
    for label,guard in (
        ('site_sle',lambda: frappe.db.count('Stock Ledger Entry')==pre_sle_count),
        ('related_sle',lambda: stock_ledger_snapshot()==pre_sle_snapshot),
        ('bin',lambda: quantity()==pre_bin),
    ):
        try:
            assert guard()
        except AssertionError:
            detected.append(label)
    assert detected==['site_sle','related_sle','bin'],detected
    evidence={
        'detected':detected,
        'bin_before':pre_bin,
        'bin_mutated':quantity(),
        'temporary_stock_entry':stock_entry.name,
        'delete_linked_ledger_entries_before':delete_linked_ledger_entries_before,
    }
finally:
    frappe.db.rollback()
    frappe.set_user('Administrator')
    stock_entry_names.extend(frappe.get_all(
        'Stock Entry',filters={'name':['in',stock_entry_names]},pluck='name'
    ))
    stock_entry_names=list(dict.fromkeys(filter(None,stock_entry_names)))
    docs=[]
    for name in stock_entry_names:
        if frappe.db.exists('Stock Entry',name):
            docs.append(frappe.get_doc('Stock Entry',name))
    for doc in docs:
        if doc.docstatus==1:
            doc.cancel()
    from frappe.tests.utils import change_settings
    with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
        for doc in docs:
            if frappe.db.exists('Stock Entry',doc.name):
                frappe.delete_doc('Stock Entry',doc.name,ignore_permissions=True)
    frappe.db.commit()
    frappe.destroy()

    os.chdir('/home/frappe/frappe-bench/sites')
    frappe.init(site='dsherp-validation.localhost')
    frappe.connect()
    try:
        fresh_bin=quantity() if warehouse and pre_bin is not None else pre_bin
        assert fresh_bin==pre_bin,(pre_bin,fresh_bin)
        assert all(not frappe.db.exists('Stock Entry',name) for name in stock_entry_names)
        assert frappe.db.count('Stock Ledger Entry',{
            'voucher_type':'Stock Entry','voucher_no':['in',stock_entry_names],
        })==0
        if delete_linked_ledger_entries_before is not None:
            assert int(frappe.db.get_single_value(
                'Accounts Settings','delete_linked_ledger_entries'
            ) or 0)==delete_linked_ledger_entries_before
        if evidence:
            evidence['bin_cleanup']=fresh_bin
            evidence['fresh_stock_entries']=sum(
                bool(frappe.db.exists('Stock Entry',name)) for name in stock_entry_names
            )
            evidence['fresh_stock_ledger_entries']=frappe.db.count(
                'Stock Ledger Entry',{
                    'voucher_type':'Stock Entry','voucher_no':['in',stock_entry_names],
                },
            )
            evidence['delete_linked_ledger_entries_after']=int(
                frappe.db.get_single_value(
                    'Accounts Settings','delete_linked_ledger_entries'
                ) or 0
            )
    finally:
        frappe.destroy()

print(json.dumps(evidence,ensure_ascii=False,sort_keys=True))
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["detected"] == ["site_sle", "related_sle", "bin"]
    assert evidence["bin_mutated"] == evidence["bin_before"] + 1
    assert evidence["bin_cleanup"] == evidence["bin_before"]
    assert evidence["fresh_stock_entries"] == 0
    assert evidence["fresh_stock_ledger_entries"] == 0
    assert (
        evidence["delete_linked_ledger_entries_after"]
        == evidence["delete_linked_ledger_entries_before"]
    )
