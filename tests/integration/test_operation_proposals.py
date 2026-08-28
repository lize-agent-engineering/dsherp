"""Native metadata and field permissions; no paid worker or business writes."""
import subprocess


def test_update_diff_uses_native_permissions_and_rejects_system_fields():
    script=r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import update_diff
try:
    frappe.set_user('dsherp-reader@example.invalid')
    doc=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    try:update_diff(doc,{'item_name':'Changed'});raise AssertionError('read-only writer accepted')
    except frappe.PermissionError:pass
    # Administrator is only a metadata test fixture, not a business API actor.
    frappe.set_user('Administrator')
    changes=update_diff(doc,{'item_name':'Synthetic proposed name'})
    assert changes==[{'field':'item_name','label':doc.meta.get_field('item_name').label,'before':doc.item_name,'after':'Synthetic proposed name'}]
    assert frappe.db.get_value('Item',doc.name,'item_name')==doc.item_name
    for changes in ({'docstatus':1},{'modified':'forged'},{'name':'other'},{'item_name':doc.item_name},{'item_name':{'nested':'value'}},{'unknown_field':'x'}):
        try:update_diff(doc,changes);raise AssertionError('invalid change accepted')
        except (frappe.ValidationError,frappe.PermissionError):pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_proposal_is_immutable_owner_bound_and_does_not_write_business_data():
    script=r'''
import os,uuid,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.operations import propose_update,get_proposal
try:
    frappe.set_user('Administrator')
    actor='proposal-'+uuid.uuid4().hex+'@example.invalid'
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic proposal writer','enabled':1,'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic proposal'}).insert(ignore_permissions=True)
    doc=frappe.get_doc('Item','DSHERP-TEST-ITEM')
    result=propose_update(conversation.name,'Item',doc.name,{'item_name':'Proposed only'},str(doc.modified))
    assert result['status']=='Pending' and result['digest']
    assert result['changes'][0]['after']=='Proposed only'
    assert frappe.db.get_value('Item',doc.name,'item_name')==doc.item_name
    assert get_proposal(result['id'])==result
    try:propose_update(conversation.name,'Item',doc.name,{'item_name':'Other'},'stale');raise AssertionError('stale version accepted')
    except frappe.ValidationError:pass
    stored=frappe.get_doc('DS Operation Proposal',result['id'])
    stored.payload='{}'
    try:stored.save(ignore_permissions=True);raise AssertionError('proposal body mutable')
    except frappe.ValidationError:pass
    frappe.set_user('dsherp-reader@example.invalid')
    try:get_proposal(result['id']);raise AssertionError('other owner accepted')
    except frappe.PermissionError:pass
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
