"""Initialize the daily Site with explicitly synthetic data and bind one ordinary member."""
import json
import subprocess


PYTHON = '/home/frappe/frappe-bench/env/bin/python'
BUSINESS = 'dsherp-validation-backend-1'
PLATFORM = 'dsherp-validation-platform-backend-1'
SITE = 'dsherp-daily.localhost'
ERP_USER = 'daily-operator@example.invalid'
PLATFORM_USER = 'member@example.invalid'


def execute(container, site, body, timeout=180):
    script = "import os,json,frappe\nos.chdir('/home/frappe/frappe-bench/sites')\nfrappe.init(site=" + repr(site) + ");frappe.connect();frappe.set_user('Administrator')\n" + body
    result = subprocess.run(['docker', 'exec', '-i', container, PYTHON, '-'], input=script,
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Synthetic daily initialization failed in {container}; inspect exact state before retrying')
    return result.stdout


def main():
    state = json.loads(execute(BUSINESS, SITE, f"""
assert (not frappe.is_setup_complete() and frappe.db.count('Company')==0) or frappe.db.exists('Company','DSHERP 日常合成企业')
assert not frappe.db.exists('User',{ERP_USER!r})
print(json.dumps({{'setup_complete':bool(frappe.is_setup_complete())}}))
frappe.destroy()
"""))
    execute(PLATFORM, 'dsherp-platform.localhost', f"""
enterprise=frappe.get_doc('DS Enterprise','daily')
assert enterprise.status=='Provisioning'
assert frappe.db.exists('User',{PLATFORM_USER!r})
assert not frappe.db.exists('DS Membership',{{'enterprise':'daily','platform_user':{PLATFORM_USER!r}}})
frappe.destroy()
""")
    credentials = json.loads(execute(BUSINESS, SITE, f"""
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
if not frappe.is_setup_complete():
 result=setup_complete({{
  'language':'简体中文','lang':'zh','country':'China','timezone':'Asia/Shanghai','currency':'CNY','enable_telemetry':0,
  'company_name':'DSHERP 日常合成企业','company_abbr':'DSE','chart_of_accounts':'Standard',
  'fy_start_date':'2026-01-01','fy_end_date':'2026-12-31','setup_demo':0,
 }})
 assert result=={{'status':'ok'}},result
else:
 assert frappe.db.exists('Company','DSHERP 日常合成企业')
user=frappe.get_doc({{'doctype':'User','email':{ERP_USER!r},'first_name':'日常合成操作员','enabled':1,
 'user_type':'System User','send_welcome_email':0,'roles':[{{'role':'Sales User'}},{{'role':'Sales Manager'}},
 {{'role':'Stock Manager'}},{{'role':'Item Manager'}}]}}).insert()
from frappe.core.doctype.user.user import generate_keys
keys=generate_keys(user.name)
frappe.set_user(user.name)
checks=[('Item','read'),('Item','write'),('Customer','create'),('Customer','write'),
        ('Sales Order','create'),('Sales Order','write'),('Sales Order','submit'),('Sales Order','cancel')]
assert all(frappe.has_permission(doctype,ptype) for doctype,ptype in checks),checks
from frappe.utils.nestedset import get_root_of
item=frappe.get_doc({{'doctype':'Item','item_code':'DAILY-AGENT-ITEM','item_name':'日常 Agent 合成物料',
 'item_group':get_root_of('Item Group'),'stock_uom':frappe.db.get_single_value('Stock Settings','stock_uom') or 'Nos','is_stock_item':0}}).insert()
customer=frappe.get_doc({{'doctype':'Customer','customer_name':'日常 Agent 合成客户','customer_type':'Individual',
 'customer_group':'个人','territory':'China'}}).insert()
frappe.db.commit()
print(json.dumps({{'user':user.name,'api_key':keys['api_key'],'api_secret':keys['api_secret'],
 'company':'DSHERP 日常合成企业','item':item.name,'customer':customer.name}}))
frappe.destroy()
"""))
    execute(PLATFORM, 'dsherp-platform.localhost', f"""
credentials=json.loads({json.dumps(credentials)!r})
frappe.get_doc({{'doctype':'DS Membership','enterprise':'daily','platform_user':{PLATFORM_USER!r},'enabled':1,
 'erp_user':credentials['user'],'api_key':credentials['api_key'],'api_secret':credentials['api_secret']}}).insert()
enterprise=frappe.get_doc('DS Enterprise','daily');enterprise.title='日常合成企业';enterprise.status='Ready';enterprise.save()
frappe.db.commit();frappe.clear_cache();frappe.destroy()
""")
    print('Synthetic daily company, ordinary ERP user, fixtures, and platform membership created through native methods.')


if __name__ == '__main__':
    main()
