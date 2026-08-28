import subprocess


def test_preview_operator_is_native_config_user_not_administrator():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
try:
    user='dsherp-preview@example.invalid'
    assert frappe.conf.dsherp_preview==1
    assert frappe.db.exists('User',user)
    frappe.set_user(user)
    assert frappe.session.user!='Administrator'
    for doctype in ('DocType','Custom Field','Workflow'):assert frappe.has_permission(doctype,'create')
    assert frappe.db.exists('User','beta-reader@example.invalid')
    assert [row.role for row in frappe.get_doc('User','beta-reader@example.invalid').roles]==['DSHERP Beta Reader']
    assert frappe.db.get_single_value('System Settings','setup_complete')==1
    company=frappe.get_doc('Company','DSHERP 隔离预览合成公司')
    assert company.abbr=='DPR' and company.country=='China' and company.default_currency=='CNY'
    assert frappe.db.count('Sales Order')==0
finally:frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
