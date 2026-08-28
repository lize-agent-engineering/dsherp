import subprocess


def test_sales_user_reads_order_schema_items_and_explicit_unsaved_context():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.api import read_schema,read_record
from dsherp_bridge.context_api import _context
from dsherp_bridge.context_execution import authorize_sources
try:
    frappe.set_user('dsherp-writer@example.invalid')
    schema=read_schema('Sales Order')
    fields={field['fieldname']:field for field in schema['fields']}
    assert fields['items']['fieldtype']=='Table'
    child={field['fieldname']:field for field in fields['items']['fields']}
    assert child['qty']['read_only']==0
    assert child['conversion_factor']['read_only']==1
    record=read_record('Sales Order','SAL-ORD-2026-00001')
    assert record['fields']['docstatus']==0
    assert record['fields']['items'][0]['qty']==2
    assert record['fields']['items'][0]['item_code']=='DSHERP-UI-ITEM'
    row=record['fields']['items'][0]['name']
    context={'schema_version':1,'page_type':'form','route':['Form','Sales Order',record['name']],'doctype':'Sales Order','name':record['name'],'dirty':True,'unsaved':{'items':[{'name':row,'qty':5}]}}
    accepted=_context(context)
    assert accepted['unsaved']['items'][0]['qty']==5
    assert frappe.get_doc('Sales Order',record['name']).items[0].qty==2
    context['unsaved']['items'][0]['name']='foreign-row'
    try:_context(context);raise AssertionError('foreign child accepted')
    except frappe.ValidationError:pass
    source={'arguments':{'doctype':'Sales Order'},'fields':['items'],'records':[record['name']],'child_fields':{'items':['qty']}}
    authorize_sources([source])
    source['child_fields']['items']=['nonexistent_private_column']
    try:authorize_sources([source]);raise AssertionError('unreadable historical child field accepted')
    except frappe.PermissionError:pass
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
