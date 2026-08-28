import subprocess


def test_confirm_fill_authorizes_draft_only_and_never_saves_business_record():
    script=r'''
import os,json,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_fill,confirm
conversation=None
try:
    frappe.set_user('dsherp-writer@example.invalid')
    doc=frappe.get_doc('Item','DSHERP-HITL-ITEM');before=doc.item_name;version=str(doc.modified)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic draft fill'}).insert(ignore_permissions=True).name
    proposal=propose_fill(conversation,'Item',doc.name,{'item_name':'Suggested draft only'},version);frappe.db.commit()
    result=confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Authorized',result
    assert result['target']=='browser-draft'
    assert frappe.get_doc('Item',doc.name).item_name==before
    assert str(frappe.get_doc('Item',doc.name).modified)==version
    assert confirm(proposal['id'],proposal['digest'],uuid.uuid4().hex)==result
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if conversation:
        for proposal in frappe.get_all('DS Operation Proposal',filters={'conversation':conversation},pluck='name'):
            for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal},pluck='name'):frappe.delete_doc('DS Execution Record',execution,ignore_permissions=True)
            frappe.delete_doc('DS Operation Proposal',proposal,ignore_permissions=True)
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
