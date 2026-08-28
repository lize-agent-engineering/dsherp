import subprocess


def test_native_configuration_save_respects_site_lock_across_commits():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from frappe.database import get_db
from dsherp_bridge.configuration_locks import acquire,release,lock_key
other=get_db(host=frappe.conf.db_host,user=frappe.conf.db_name,password=frappe.conf.db_password,port=frappe.conf.db_port,cur_db_name=frappe.conf.db_name)
try:
    key=lock_key('Item')
    assert other.sql('SELECT GET_LOCK(%s,0)',key)[0][0]==1
    frappe.set_user('Administrator')
    before=frappe.db.count('Property Setter',{'doc_type':'Item'})
    try:
        frappe.get_doc({'doctype':'Property Setter','doctype_or_field':'DocField','doc_type':'Item','field_name':'item_name','property':'label','property_type':'Data','value':'Must not be saved'}).insert()
        raise AssertionError('native save bypassed publication lock')
    except frappe.ValidationError as error:assert '正在保存或发布' in str(error)
    assert frappe.db.count('Property Setter',{'doc_type':'Item'})==before
    other.sql('SELECT RELEASE_LOCK(%s)',key)
    acquire(['Item']);frappe.db.commit()
    assert other.sql('SELECT GET_LOCK(%s,0)',key)[0][0]==0
    release()
    assert other.sql('SELECT GET_LOCK(%s,0)',key)[0][0]==1
finally:
    release();other.close();frappe.db.rollback();frappe.clear_cache(doctype='Item');frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
