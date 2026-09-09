import subprocess


def test_sales_user_reads_order_schema_items_and_explicit_unsaved_context():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.api import read_schema,read_record
from dsherp_bridge.context_api import _context
from dsherp_bridge.context_execution import authorize_sources

def schema_fields(doctype,**kwargs):
    """Every field of a schema, following the cursor.

    Sales Order is 112 fields and does not fit one 16KB page even with no child table
    inlined, so paging is the ordinary path here, not an edge case."""
    page=read_schema(doctype,**kwargs);fields=list(page['fields'])
    cursor=page.get('next_after_fieldname');guard=0
    while cursor and guard<40:
        page=read_schema(doctype,after_fieldname=cursor,**kwargs)
        fields+=page['fields'];cursor=page.get('next_after_fieldname');guard+=1
    assert not cursor,'schema cursor did not terminate'
    return fields

def child_columns(doctype,table):
    """Every column of one explicitly expanded child table, following its column cursor.

    A named table is expanded on exactly one page, so the read starts at the field just
    before it; Sales Order Item has 80 columns and pages them too."""
    names=[field['fieldname'] for field in schema_fields(doctype)]
    position=names.index(table)
    start={} if position==0 else {'after_fieldname':names[position-1]}
    columns=[];cursor=None;guard=0
    while guard<40:
        page=read_schema(doctype,tables=[table],**start,**({'child_after':{table:cursor}} if cursor else {}))
        entry=next(field for field in page['fields'] if field['fieldname']==table)
        columns+=entry['fields']
        cursor=entry.get('columns_truncated',{}).get('next_after_child_fieldname')
        guard+=1
        if not cursor:break
    assert not cursor,'child column cursor did not terminate'
    return columns
try:
    frappe.set_user('dsherp-writer@example.invalid')
    fields={field['fieldname']:field for field in schema_fields('Sales Order')}
    assert fields['items']['fieldtype']=='Table'
    # 新默认：子表只报名（rows_of）不展开，要看列就显式点名 tables。
    assert fields['items']['rows_of']=='Sales Order Item'
    assert 'fields' not in fields['items']
    child={field['fieldname']:field for field in child_columns('Sales Order','items')}
    assert child['qty']['read_only']==0
    assert child['conversion_factor']['read_only']==1
    record=read_record('Sales Order','SAL-ORD-2026-00001')
    assert record['fields']['docstatus']==0
    # 新默认：子表只报行数不展开，要看行就显式点名 children。
    assert 'items' not in record['fields']
    assert record['child_tables']['items']['child_doctype']=='Sales Order Item'
    assert record['child_tables']['items']['rows']==1  # 基线夹具就是一行（provision_alpha_sales_baseline）
    expanded=read_record('Sales Order','SAL-ORD-2026-00001',children=['items'])
    assert expanded['fields']['items'][0]['qty']==2
    assert expanded['fields']['items'][0]['item_code']=='DSHERP-UI-ITEM'
    row=expanded['fields']['items'][0]['name']
    context={'schema_version':1,'page_type':'form','route':['Form','Sales Order',record['name']],'doctype':'Sales Order','name':record['name'],'dirty':True,'unsaved':{'items':[{'name':row,'qty':5}]}}
    accepted=_context(context)
    assert accepted['unsaved']['items'][0]['qty']==5
    assert frappe.get_doc('Sales Order',record['name']).items[0].qty==2
    context['unsaved']['items'][0]['name']='foreign-row'
    try:_context(context);raise AssertionError('foreign child accepted')
    except frappe.ValidationError:pass
    source={'tool':'erp_read_record','arguments':{'doctype':'Sales Order'},'fields':['items'],'records':[record['name']],'child_fields':{'items':['qty']}}
    authorize_sources([source])
    source['child_fields']['items']=['nonexistent_private_column']
    try:authorize_sources([source]);raise AssertionError('unreadable historical child field accepted')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
