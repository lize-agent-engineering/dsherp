"""Fresh-only native alpha records required by the integration acceptance suite."""
import subprocess


FIXTURE_SCRIPT=r'''
writer='dsherp-writer@example.invalid'
item_code='DSHERP-UI-ITEM'
customer_name='DSHERP-HITL-CUSTOMER'
order_name='SAL-ORD-2026-00001'
assert frappe.db.exists('User',writer),'Synthetic writer is missing'
assert not frappe.db.exists('Item',item_code),'Alpha UI Item already exists'
assert not frappe.db.exists('Customer',customer_name),'Alpha HITL Customer already exists'
assert frappe.db.count('Sales Order')==0,'Alpha Sales Order baseline is not empty'
reference=frappe.get_doc('Customer','DSHERP-TEST-CUSTOMER')
frappe.set_user(writer)
ui_item=frappe.copy_doc(frappe.get_doc('Item','DSHERP-HITL-ITEM'))
ui_item.item_code=item_code
ui_item.item_name='验收中文维修服务'
ui_item.description='验收中文维修服务'
ui_item.insert()
customer=frappe.get_doc({
    'doctype':'Customer','customer_name':customer_name,'customer_type':'Company',
    'customer_group':reference.customer_group,'territory':reference.territory,
}).insert()
order=frappe.get_doc({
    'doctype':'Sales Order','customer':customer.name,
    'transaction_date':'2026-08-28','delivery_date':'2026-08-31',
    'items':[{'item_code':ui_item.name,'qty':2,'rate':1234.56,'delivery_date':'2026-08-31'}],
}).insert()
assert order.name==order_name,('Unexpected alpha reference Sales Order',order.name)
assert order.docstatus==0 and len(order.items)==1
assert order.items[0].item_code==item_code and order.items[0].qty==2
'''


def main():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect();frappe.set_user('Administrator')
try:
'''+''.join('    '+line+'\n' for line in FIXTURE_SCRIPT.splitlines())+r'''
    frappe.db.commit()
except Exception:
    frappe.db.rollback()
    raise
finally:
    frappe.destroy()
'''
    result=subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1',
         '/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=60,
    )
    if result.returncode:
        raise RuntimeError('Alpha sales baseline provisioning failed; inspect the fresh Site')
    print('Created native alpha Item, Customer, and draft Sales Order baseline.')


if __name__=='__main__':main()
