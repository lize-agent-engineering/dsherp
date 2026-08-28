"""Synthetic fixtures only; executed over stdin in the selected validation container.

The host supplies seed_input and stores stdout as a private local fixture file.
No secret is accepted through shell arguments or printed to the conversation.
"""
import contextlib
import json
import os
import secrets
import sys
import frappe
from frappe.utils.password import update_password

kind = seed_input['kind']
if kind not in ('platform', 'beta'):
    raise RuntimeError('Unexpected validation target')
site = f'dsherp-{kind}.localhost'
output = {}
with contextlib.redirect_stdout(sys.stderr):
    os.chdir("/home/frappe/frappe-bench/sites")
    frappe.init(site=site,sites_path='/home/frappe/frappe-bench/sites')
    frappe.connect()
    frappe.set_user('Administrator')
    role='DSHERP Member' if kind=='platform' else 'DSHERP Beta Reader'
    if not frappe.db.exists('Role',role):
        frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':1}).insert()
    actors={'member':'member@example.invalid','outsider':'outsider@example.invalid','operator':'operator@example.invalid'} if kind=='platform' else {'reader':'beta-reader@example.invalid'}
    for actor,user in actors.items():
        if frappe.db.exists('User',user):
            raise RuntimeError('Synthetic user already exists; do not regenerate credentials blindly')
        password=secrets.token_urlsafe(24)
        key=secrets.token_hex(12);secret=secrets.token_urlsafe(24)
        doc=frappe.get_doc({'doctype':'User','email':user,'first_name':'平台验收' if kind=='platform' else '乙企业只读用户','enabled':1,'user_type':'System User','send_welcome_email':0,'language':'zh','time_zone':'Asia/Shanghai','roles':[{'role':'System Manager' if actor=='operator' else role}],'api_key':key,'api_secret':secret}).insert()
        update_password(user,password)
        output[actor]={'user':user,'password':password,'site':site,'api_key':key,'api_secret':secret}
    if kind=='beta':
        for dt in ['Item','Customer']:
            frappe.permissions.add_permission(dt,role,ptype='read')
        for dt,fields in [('UOM',{'uom_name':'DSHERP-BETA-NOS','must_be_whole_number':1}),('Item Group',{'item_group_name':'DSHERP-BETA-GROUP','is_group':0})]:
            for field in fields:
                if not frappe.get_meta(dt).has_field(field):raise RuntimeError('Unexpected target metadata')
            frappe.get_doc({'doctype':dt,**fields}).insert()
        item={'item_code':'DSHERP-BETA-ITEM','item_name':'乙企业专属合成物料','item_group':'DSHERP-BETA-GROUP','stock_uom':'DSHERP-BETA-NOS','is_stock_item':0}
        for field in item:
            if not frappe.get_meta('Item').has_field(field):raise RuntimeError('Unexpected Item metadata')
        frappe.get_doc({'doctype':'Item',**item}).insert()
    settings=frappe.get_single('System Settings')
    settings.language='zh';settings.time_zone='Asia/Shanghai';settings.country='China';settings.currency='CNY';settings.date_format='yyyy-mm-dd';settings.enable_telemetry=0
    settings.save()
    frappe.db.commit();frappe.clear_cache();frappe.destroy()
print(json.dumps(output,ensure_ascii=False))
