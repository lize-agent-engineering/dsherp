"""Two actual Sites and signed internal HTTP; no model or business DDL."""
import json
import subprocess


def test_alpha_package_is_imported_once_into_beta_with_live_source_authorization():
    def execute(container,site,script):
        prefix="import os,json,frappe\nos.chdir('/home/frappe/frappe-bench/sites');frappe.init(site="+repr(site)+");frappe.connect()\n"
        result=subprocess.run(['docker','exec','-i',container,'/home/frappe/frappe-bench/env/bin/python','-'],input=prefix+script,text=True,capture_output=True,timeout=50)
        assert result.returncode==0,result.stderr
        return result.stdout
    def source(script):return execute('dsherp-validation-backend-1','dsherp-validation.localhost',script)
    def preview(script):return execute('dsherp-validation-beta-backend-1','dsherp-beta.localhost',script)
    prepared=None;imported=None
    try:
        prepared=json.loads(source("""
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer
frappe.set_user('dsherp-preview@example.invalid')
conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Two Site transfer test'}).insert(ignore_permissions=True)
package={'version':1,'doctypes':[{'name':'DS HTTP Transfer Test','module':'DSHERP Bridge','fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
bundle=propose_bundle(conversation.name,package);frappe.db.commit()
assert not bundle['preview_available'] and bundle['preview_transfer_available']
transfer=prepare_transfer(bundle['id'],bundle['digest'],'two-site-transfer-request')
frappe.db.commit();print(json.dumps({'session':conversation.name,'bundle':bundle['id'],'transfer':transfer['id'],'digest':bundle['digest']}));frappe.destroy()
"""))
        imported=json.loads(preview("""
from dsherp_bridge.configuration_transfer import accept_transfer
from dsherp_bridge.configuration_execution import prepare_preview
frappe.set_user('dsherp-preview@example.invalid')
result=accept_transfer(TRANSFER)
again=accept_transfer(TRANSFER);assert again['bundle']['id']==result['bundle']['id']
doc=frappe.get_doc('DS Configuration Bundle',result['bundle']['id']);origin=json.loads(doc.payload)['origin']
assert origin['source_site']=='dsherp-validation.localhost' and origin['bundle_digest']==DIGEST
confirmation=prepare_preview(doc.name,doc.digest)
assert not frappe.db.exists('DocType','DS HTTP Transfer Test')
frappe.db.commit();print(json.dumps({'session':result['session_id'],'bundle':doc.name,'confirmation':confirmation['id']}));frappe.destroy()
""".replace('TRANSFER',repr(prepared['transfer'])).replace('DIGEST',repr(prepared['digest']))))
        source("""
assert not frappe.db.exists('DocType','DS HTTP Transfer Test')
frappe.destroy()
""")
    finally:
        if imported:
            preview("""
frappe.set_user('Administrator')
frappe.delete_doc('DS Configuration Confirmation',CONFIRMATION,force=True)
frappe.delete_doc('DS Configuration Bundle',BUNDLE,force=True)
frappe.delete_doc('DS Conversation',SESSION,force=True)
frappe.db.commit();frappe.destroy()
""".replace('CONFIRMATION',repr(imported['confirmation'])).replace('BUNDLE',repr(imported['bundle'])).replace('SESSION',repr(imported['session'])))
        if prepared:
            source("""
frappe.set_user('Administrator')
frappe.delete_doc('DS Configuration Transfer',TRANSFER,force=True)
frappe.delete_doc('DS Configuration Bundle',BUNDLE,force=True)
frappe.delete_doc('DS Conversation',SESSION,force=True)
frappe.db.commit();frappe.destroy()
""".replace('TRANSFER',repr(prepared['transfer'])).replace('BUNDLE',repr(prepared['bundle'])).replace('SESSION',repr(prepared['session'])))
