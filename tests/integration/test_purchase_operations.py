"""A confirmed Purchase Order receipt changes real synthetic stock exactly once."""
import json
import subprocess


def test_purchase_order_to_receipt_updates_stock():
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
actor='purchase-'+tag+'@example.invalid'
supplier_name='DSHERP-T2.3-SYN-SUPPLIER-'+tag
item_code='DSHERP-MFG-SYN-RM'
route_name='purchase_order_to_purchase_receipt'
method_path='erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt'
purchase_qty=4
conversation=None
run_names=[]
proposal_names=[]
execution_names=[]
purchase_order_names=[]
purchase_receipt_names=[]
purchase_order=None
purchase_receipt=None
warehouse=None
pre_bin=None
fresh_bin=None
delete_linked_ledger_entries_before=None
evidence={}


def quantity():
    return flt(frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'actual_qty'
    ) or 0)


def site_document_counts():
    return {
        'suppliers':frappe.db.count('Supplier'),
        'purchase_orders':frappe.db.count('Purchase Order'),
        'purchase_receipts':frappe.db.count('Purchase Receipt'),
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
        'Role',filters={'name':['in',['Purchase User','Stock User','Purchase Master Manager']]},
        fields=['name'],
    )}
    assert roles=={'Purchase User','Stock User','Purchase Master Manager'},roles
    purchase_order_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Purchase Order','role':'Purchase User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    purchase_receipt_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Purchase Receipt','role':'Stock User'},
        ['read','write','create','submit','cancel'],as_dict=True,
    )
    supplier_perm=frappe.db.get_value(
        'DocPerm',{'parent':'Supplier','role':'Purchase Master Manager'},
        ['read','write','create'],as_dict=True,
    )
    assert purchase_order_perm and all(purchase_order_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),purchase_order_perm
    assert purchase_receipt_perm and all(purchase_receipt_perm.get(field) for field in (
        'read','write','create','submit','cancel'
    )),purchase_receipt_perm
    assert supplier_perm and all(supplier_perm.get(field) for field in (
        'read','write','create'
    )),supplier_perm

    policies={row.target_doctype:row for row in frappe.get_all(
        'DS Doctype Policy',fields=['target_doctype','allow_read','allow_create','allow_update',
        'allow_submit','allow_cancel','allow_fill'],
    )}
    assert set(('Supplier','Purchase Order','Purchase Receipt'))<=set(policies),policies
    assert policies['Supplier'].allow_read and policies['Supplier'].allow_create
    assert policies['Supplier'].allow_update and not policies['Supplier'].allow_submit
    assert not policies['Supplier'].allow_cancel and not policies['Supplier'].allow_fill
    assert policies['Purchase Order'].allow_read and policies['Purchase Order'].allow_create
    assert policies['Purchase Order'].allow_update and policies['Purchase Order'].allow_submit
    assert policies['Purchase Order'].allow_cancel and not policies['Purchase Order'].allow_fill
    assert policies['Purchase Receipt'].allow_read and policies['Purchase Receipt'].allow_submit
    assert policies['Purchase Receipt'].allow_cancel and not policies['Purchase Receipt'].allow_create
    assert not policies['Purchase Receipt'].allow_update and not policies['Purchase Receipt'].allow_fill
    routes=frappe.get_all(
        'DS Doctype Policy Route',filters={'parent':'Purchase Order'},
        fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc',
    )
    assert routes==[
        {
            'route_name':route_name,'method_path':method_path,
            'target_doctype':'Purchase Receipt',
        },
        {
            'route_name':'purchase_order_to_subcontracting_order',
            'method_path':'erpnext.buying.doctype.purchase_order.purchase_order.make_subcontracting_order',
            'target_doctype':'Subcontracting Order',
        },
    ],routes

    company=frappe.get_all('Company',pluck='name')
    assert company==['DSHERP 原生验收测试公司'],company
    company=company[0]
    warehouses=frappe.get_all('Warehouse',filters={
        'warehouse_name':'DSHERP 制造测试合成原料仓',
        'company':company,
        'is_group':0,
    },pluck='name')
    assert len(warehouses)==1,warehouses
    warehouse=warehouses[0]
    item=frappe.get_doc('Item',item_code)
    assert item.is_stock_item and item.stock_uom=='Nos',item.as_dict()
    pre_bin=quantity()
    delete_linked_ledger_entries_before=int(
        frappe.db.get_single_value('Accounts Settings','delete_linked_ledger_entries') or 0
    )
    assert not frappe.db.exists('Supplier',supplier_name)
    assert frappe.db.count('Purchase Order',{'owner':actor})==0
    assert frappe.db.count('Purchase Receipt',{'owner':actor})==0
    initial_document_counts=site_document_counts()

    frappe.get_doc({
        'doctype':'User','email':actor,'first_name':'Synthetic Purchase actor',
        'enabled':1,'send_welcome_email':0,
        'roles':[
            {'role':'Purchase User'},
            {'role':'Stock User'},
            {'role':'Purchase Master Manager'},
        ],
    }).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({
        'doctype':'DS Conversation','title':'Synthetic Purchase chain '+tag,
    }).insert(ignore_permissions=True).name

    # Supplier create is schema-sourced and the proposal itself writes nothing.
    cap,supplier_run=new_run()
    frappe.set_user('Guest')
    supplier_schema=run_tool(**cap,tool='erp_read_schema',arguments={'doctype':'Supplier'})
    supplier_fields={field['fieldname']:field for field in supplier_schema['fields']}
    assert supplier_fields['supplier_name']['reqd']
    assert supplier_fields['supplier_type']['options']=='Company\nIndividual\nPartnership'
    supplier_values={
        'supplier_name':supplier_name,
        'supplier_group':'原材料',
        'supplier_type':'Company',
        'country':'China',
    }
    supplier_documents_before=site_document_counts()
    supplier_proposal=run_tool(**cap,tool='erp_propose_create',arguments={
        'doctype':'Supplier','values':supplier_values,
        'version':str(supplier_schema['modified']),
    })
    assert site_document_counts()==supplier_documents_before
    assert not frappe.db.exists('Supplier',supplier_name)
    supplier_created=confirm_once(supplier_proposal,supplier_run)
    assert supplier_created['status']=='Succeeded',supplier_created
    assert supplier_created['name']==supplier_name,supplier_created
    supplier=frappe.get_doc('Supplier',supplier_name)
    assert supplier.owner==actor and supplier.supplier_group=='原材料'

    # Purchase Order creation and submit are separate confirmed operations.
    cap,purchase_order_create_run=new_run()
    frappe.set_user('Guest')
    purchase_order_schema=run_tool(
        **cap,tool='erp_read_schema',arguments={'doctype':'Purchase Order'}
    )
    purchase_order_fields={field['fieldname']:field for field in purchase_order_schema['fields']}
    assert purchase_order_fields['supplier']['options']=='Supplier'
    assert purchase_order_fields['company']['options']=='Company'
    assert purchase_order_fields['set_warehouse']['options']=='Warehouse'
    item_fields={field['fieldname']:field for field in purchase_order_fields['items']['fields']}
    assert item_fields['item_code']['options']=='Item'
    assert item_fields['warehouse']['options']=='Warehouse'
    assert item_fields['qty']['fieldtype']=='Float'
    actual_supplier=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Supplier','name':supplier_name,
    })
    actual_item=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Item','name':item_code,
    })
    actual_warehouse=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Warehouse','name':warehouse,
    })
    assert actual_supplier['fields']['supplier_group']=='原材料'
    assert actual_item['fields']['stock_uom']=='Nos'
    assert actual_warehouse['fields']['company']==company
    schedule_date=str(add_days(nowdate(),7))
    purchase_order_values={
        'naming_series':purchase_order_fields['naming_series']['options'],
        'supplier':actual_supplier['name'],
        'transaction_date':str(nowdate()),
        'schedule_date':schedule_date,
        'company':company,
        'currency':'CNY',
        'conversion_rate':1,
        'set_warehouse':actual_warehouse['name'],
        'items':[{
            'item_code':actual_item['name'],
            'schedule_date':schedule_date,
            'qty':purchase_qty,
            'rate':7,
            'warehouse':actual_warehouse['name'],
            'uom':'Nos',
            'conversion_factor':1,
        }],
    }
    purchase_order_documents_before=site_document_counts()
    purchase_order_proposal=run_tool(**cap,tool='erp_propose_create',arguments={
        'doctype':'Purchase Order','values':purchase_order_values,
        'version':str(purchase_order_schema['modified']),
    })
    assert site_document_counts()==purchase_order_documents_before
    assert frappe.db.count('Purchase Order',{'owner':actor})==0
    purchase_order_created=confirm_once(purchase_order_proposal,purchase_order_create_run)
    assert purchase_order_created['status']=='Succeeded',purchase_order_created
    purchase_order=purchase_order_created['name']
    purchase_order_names.append(purchase_order)
    purchase_order_doc=frappe.get_doc('Purchase Order',purchase_order)
    assert purchase_order_doc.docstatus==0 and purchase_order_doc.owner==actor
    assert purchase_order_doc.supplier==supplier_name
    assert len(purchase_order_doc.items)==1
    assert purchase_order_doc.items[0].item_code==item_code
    assert flt(purchase_order_doc.items[0].qty)==purchase_qty
    assert purchase_order_doc.items[0].warehouse==warehouse

    cap,purchase_order_submit_run=new_run()
    frappe.set_user('Guest')
    purchase_order_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,
    })
    purchase_order_submit_documents_before=site_document_counts()
    purchase_order_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Purchase Order','name':purchase_order,'action':'submit',
        'version':str(purchase_order_read['modified']),
    })
    assert purchase_order_submit['impact']=={'kind':'none','entries':[]},purchase_order_submit
    assert site_document_counts()==purchase_order_submit_documents_before
    assert frappe.db.get_value('Purchase Order',purchase_order,'docstatus')==0
    purchase_order_submitted=confirm_once(purchase_order_submit,purchase_order_submit_run)
    assert purchase_order_submitted['status']=='Succeeded',purchase_order_submitted
    assert frappe.db.get_value('Purchase Order',purchase_order,'docstatus')==1

    # An unexpected public option is rejected before mapping or proposal creation.
    cap,purchase_receipt_make_run=new_run()
    frappe.set_user('Guest')
    submitted_order=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,
    })
    rejected_proposals=frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )
    rejected_receipts=frappe.db.count('Purchase Receipt',{'owner':actor})
    rejected_documents_before=site_document_counts()
    try:
        run_tool(**cap,tool='erp_propose_make',arguments={
            'source_doctype':'Purchase Order','source_name':purchase_order,
            'source_version':str(submitted_order['modified']),'route':route_name,
            'options':{},
        })
        raise AssertionError('extra make options were accepted')
    except frappe.ValidationError as error:
        assert str(error)=='操作提案参数无效',error
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==rejected_proposals
    assert frappe.db.count('Purchase Receipt',{'owner':actor})==rejected_receipts
    assert site_document_counts()==rejected_documents_before

    purchase_receipt_make=run_tool(**cap,tool='erp_propose_make',arguments={
        'source_doctype':'Purchase Order','source_name':purchase_order,
        'source_version':str(submitted_order['modified']),'route':route_name,
    })
    assert purchase_receipt_make['target']['doctype']=='Purchase Receipt'
    assert purchase_receipt_make['target']['supplier']==supplier_name
    assert len(purchase_receipt_make['target']['items'])==1
    assert purchase_receipt_make['target']['items'][0]['purchase_order']==purchase_order
    assert purchase_receipt_make['target']['items'][0]['item_code']==item_code
    assert flt(purchase_receipt_make['target']['items'][0]['qty'])==purchase_qty
    assert purchase_receipt_make['target']['items'][0]['warehouse']==warehouse
    assert site_document_counts()==rejected_documents_before
    purchase_receipt_created=confirm_once(purchase_receipt_make,purchase_receipt_make_run)
    assert purchase_receipt_created['status']=='Succeeded',purchase_receipt_created
    purchase_receipt=purchase_receipt_created['name']
    purchase_receipt_names.append(purchase_receipt)
    purchase_receipt_doc=frappe.get_doc('Purchase Receipt',purchase_receipt)
    assert purchase_receipt_doc.docstatus==0 and purchase_receipt_doc.owner==actor
    assert purchase_receipt_doc.items[0].purchase_order==purchase_order

    # The receipt submit changes stock exactly once; duplicate confirm is idempotent.
    cap,purchase_receipt_submit_run=new_run()
    frappe.set_user('Guest')
    purchase_receipt_read=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Receipt','name':purchase_receipt,
    })
    purchase_receipt_submit_documents_before=site_document_counts()
    purchase_receipt_submit=run_tool(**cap,tool='erp_propose_action',arguments={
        'doctype':'Purchase Receipt','name':purchase_receipt,'action':'submit',
        'version':str(purchase_receipt_read['modified']),
    })
    assert purchase_receipt_submit['impact']=={'kind':'stock','entries':[
        {'item_code':item_code,'quantity':purchase_qty,'uom':'Nos','warehouse':warehouse},
    ]},purchase_receipt_submit
    assert site_document_counts()==purchase_receipt_submit_documents_before
    assert frappe.db.get_value('Purchase Receipt',purchase_receipt,'docstatus')==0
    purchase_receipt_submitted=confirm_once(
        purchase_receipt_submit,purchase_receipt_submit_run
    )
    assert purchase_receipt_submitted['status']=='Succeeded',purchase_receipt_submitted
    assert quantity()==pre_bin+purchase_qty,(pre_bin,quantity())
    assert flt(frappe.db.get_value(
        'Purchase Order',purchase_order,'per_received'
    ))==100
    ledger_rows=frappe.get_all(
        'Stock Ledger Entry',
        filters={
            'voucher_type':'Purchase Receipt',
            'voucher_no':purchase_receipt,
        },
        fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )
    assert ledger_rows==[{
        'voucher_type':'Purchase Receipt',
        'voucher_no':purchase_receipt,
        'item_code':item_code,
        'warehouse':warehouse,
        'actual_qty':purchase_qty,
    }],ledger_rows
    receipt_count_after_submit=frappe.db.count('Purchase Receipt',{'owner':actor})
    bin_after_submit=quantity()
    duplicate=confirm(
        purchase_receipt_submit['id'],purchase_receipt_submit['digest'],uuid.uuid4().hex
    )
    assert duplicate==purchase_receipt_submitted
    assert frappe.db.count(
        'DS Execution Record',{'proposal':purchase_receipt_submit['id']}
    )==1
    assert frappe.db.count('Purchase Receipt',{'owner':actor})==receipt_count_after_submit
    assert quantity()==bin_after_submit
    assert frappe.get_all(
        'Stock Ledger Entry',
        filters={
            'voucher_type':'Purchase Receipt',
            'voucher_no':purchase_receipt,
        },
        fields=['voucher_type','voucher_no','item_code','warehouse','actual_qty'],
        order_by='creation asc,name asc',
    )==ledger_rows

    # Purchase Receipt drafts are make-only; direct create is denied with no write.
    cap,direct_create_run=new_run()
    frappe.set_user('Guest')
    purchase_receipt_schema=run_tool(
        **cap,tool='erp_read_schema',arguments={'doctype':'Purchase Receipt'}
    )
    direct_proposals=frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )
    direct_receipts=frappe.db.count('Purchase Receipt',{'owner':actor})
    direct_documents_before=site_document_counts()
    try:
        run_tool(**cap,tool='erp_propose_create',arguments={
            'doctype':'Purchase Receipt',
            'values':{'supplier':supplier_name,'company':company,'items':[]},
            'version':str(purchase_receipt_schema['modified']),
        })
        raise AssertionError('direct Purchase Receipt create proposal was accepted')
    except frappe.PermissionError as error:
        assert '未允许 Purchase Receipt 的 create 操作' in str(error),error
    assert frappe.db.count(
        'DS Operation Proposal',{'conversation':conversation}
    )==direct_proposals
    assert frappe.db.count('Purchase Receipt',{'owner':actor})==direct_receipts
    assert site_document_counts()==direct_documents_before

    # Read native outcomes through the same server-bound tool boundary.
    cap,outcome_run=new_run()
    frappe.set_user('Guest')
    receipt_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Receipt','name':purchase_receipt,
    })
    order_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Purchase Order','name':purchase_order,
    })
    bin_name=frappe.db.get_value(
        'Bin',{'item_code':item_code,'warehouse':warehouse},'name'
    )
    bin_outcome=run_tool(**cap,tool='erp_read_record',arguments={
        'doctype':'Bin','name':bin_name,
    })
    assert receipt_outcome['fields']['docstatus']==1
    assert flt(order_outcome['fields']['per_received'])==100
    assert flt(bin_outcome['fields']['actual_qty'])==pre_bin+purchase_qty

    evidence={
        'roles':sorted(roles),
        'site_document_baseline':initial_document_counts,
        'supplier':supplier_name,
        'purchase_order':purchase_order,
        'purchase_receipt':purchase_receipt,
        'warehouse':warehouse,
        'bin_before':pre_bin,
        'bin_after':quantity(),
        'delete_linked_ledger_entries_before':delete_linked_ledger_entries_before,
        'per_received':flt(order_outcome['fields']['per_received']),
        'duplicate_submit':{
            'execution_id':purchase_receipt_submitted['execution_id'],
            'receipt_count':receipt_count_after_submit,
            'stock_ledger_rows':len(ledger_rows),
        },
    }
finally:
    frappe.db.rollback()
    frappe.set_user('Administrator')
    purchase_order_names.extend(frappe.get_all(
        'Purchase Order',filters={'owner':actor},pluck='name'
    ))
    purchase_order_names=list(dict.fromkeys(filter(None,purchase_order_names)))
    purchase_receipt_names.extend(frappe.get_all(
        'Purchase Receipt',filters={'owner':actor},pluck='name'
    ))
    if purchase_order_names:
        purchase_receipt_names.extend(frappe.get_all(
            'Purchase Receipt Item',
            filters={'purchase_order':['in',purchase_order_names]},pluck='parent',
        ))
    purchase_receipt_names=list(dict.fromkeys(filter(None,purchase_receipt_names)))
    receipt_docs=[]
    for name in purchase_receipt_names:
        if frappe.db.exists('Purchase Receipt',name):
            receipt_docs.append(frappe.get_doc('Purchase Receipt',name))
    for doc in receipt_docs:
        if doc.docstatus==1:
            doc.cancel()
    from frappe.tests.utils import change_settings
    with change_settings('Accounts Settings',{'delete_linked_ledger_entries':1}):
        for doc in receipt_docs:
            if frappe.db.exists('Purchase Receipt',doc.name):
                frappe.delete_doc('Purchase Receipt',doc.name,ignore_permissions=True)
    for name in purchase_order_names:
        if frappe.db.exists('Purchase Order',name):
            doc=frappe.get_doc('Purchase Order',name)
            if doc.docstatus==1:
                doc.cancel()
            frappe.delete_doc('Purchase Order',name,ignore_permissions=True)
    if frappe.db.exists('Supplier',supplier_name):
        frappe.delete_doc('Supplier',supplier_name,ignore_permissions=True)
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
        fresh_bin=quantity() if warehouse and pre_bin is not None else pre_bin
        if warehouse and pre_bin is not None:
            assert fresh_bin==pre_bin,(pre_bin,fresh_bin)
        assert not frappe.db.exists('User',actor)
        assert not frappe.db.exists('Supplier',supplier_name)
        assert all(
            not frappe.db.exists('Purchase Order',name) for name in purchase_order_names
        )
        assert frappe.db.count('Purchase Order',{'owner':actor})==0
        assert all(
            not frappe.db.exists('Purchase Receipt',name)
            for name in purchase_receipt_names
        )
        assert frappe.db.count('Purchase Receipt',{'owner':actor})==0
        assert frappe.db.count('Stock Ledger Entry',{
            'voucher_type':'Purchase Receipt',
            'voucher_no':['in',purchase_receipt_names],
        })==0
        if delete_linked_ledger_entries_before is not None:
            assert int(frappe.db.get_single_value(
                'Accounts Settings','delete_linked_ledger_entries'
            ) or 0)==delete_linked_ledger_entries_before
        assert all(
            not frappe.db.exists('DS Execution Record',name) for name in execution_names
        )
        assert all(
            not frappe.db.exists('DS Operation Proposal',name) for name in proposal_names
        )
        assert all(not frappe.db.exists('DS Model Run',name) for name in run_names)
        if conversation:
            assert not frappe.db.exists('DS Conversation',conversation)
        if frappe.db.exists('DS Doctype Policy','Purchase Order'):
            purchase_order_policy=frappe.get_doc('DS Doctype Policy','Purchase Order')
            assert [row.route_name for row in purchase_order_policy.routes]==[
                route_name,'purchase_order_to_subcontracting_order',
            ]
        if evidence:
            evidence['bin_cleanup']=fresh_bin
            evidence['fresh_residual']={
                'supplier':int(bool(frappe.db.exists('Supplier',supplier_name))),
                'purchase_orders':sum(
                    bool(frappe.db.exists('Purchase Order',name))
                    for name in purchase_order_names
                ),
                'purchase_receipts':sum(
                    bool(frappe.db.exists('Purchase Receipt',name))
                    for name in purchase_receipt_names
                ),
                'actor_purchase_orders':frappe.db.count('Purchase Order',{'owner':actor}),
                'actor_purchase_receipts':frappe.db.count(
                    'Purchase Receipt',{'owner':actor}
                ),
                'user':int(bool(frappe.db.exists('User',actor))),
                'runs':sum(
                    bool(frappe.db.exists('DS Model Run',name)) for name in run_names
                ),
                'proposals':sum(
                    bool(frappe.db.exists('DS Operation Proposal',name))
                    for name in proposal_names
                ),
                'executions':sum(
                    bool(frappe.db.exists('DS Execution Record',name))
                    for name in execution_names
                ),
                'stock_ledger_entries':frappe.db.count('Stock Ledger Entry',{
                    'voucher_type':'Purchase Receipt',
                    'voucher_no':['in',purchase_receipt_names],
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
        "supplier": 0,
        "purchase_orders": 0,
        "purchase_receipts": 0,
        "actor_purchase_orders": 0,
        "actor_purchase_receipts": 0,
        "user": 0,
        "runs": 0,
        "proposals": 0,
        "executions": 0,
        "stock_ledger_entries": 0,
    }
