"""Run status is a cheap capability check and renews leases near expiry."""

import subprocess


def test_run_status_renews_only_near_expiry_without_locking_or_revision_reads():
    script = r'''
import hashlib,inspect,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_events as events
from dsherp_bridge.context_execution import run_status
from dsherp_bridge.context_permissions import run_revision
actor='dsherp-reader@example.invalid';conversation=None;run=None;capability=uuid.uuid4().hex
try:
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Lease renewal'}).insert(ignore_permissions=True)
    run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'query','status':'Running',
        'question':'lease','page_context':'{"schema_version":1,"page_type":"unknown","route":[]}',
        'permission_revision':run_revision(actor,'query'),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
        'expires_at':add_to_date(now_datetime(),seconds=60)}).insert(ignore_permissions=True)
    frappe.db.commit();frappe.set_user('Guest')
    renewed=run_status(run.name,capability)
    assert renewed['run_id']==run.name and renewed['status']=='Running',renewed
    assert 175<=renewed['lease_remaining_seconds']<=180,renewed
    last=events.list_events(run.name)[-1]
    assert last['kind']=='lease_renewed' and last['payload'].get('expires_at'),last

    frappe.set_user('Administrator')
    frappe.db.set_value('DS Model Run',run.name,'expires_at',add_to_date(now_datetime(),seconds=150))
    before=frappe.db.count('DS Run Event',{'run':run.name});frappe.db.commit();frappe.set_user('Guest')
    unchanged=run_status(run.name,capability)
    assert 140<=unchanged['lease_remaining_seconds']<=150,unchanged
    assert frappe.db.count('DS Run Event',{'run':run.name})==before
    for run_id,provided in ((run.name,'wrong'),('missing-run',capability)):
        try:run_status(run_id,provided);raise AssertionError('invalid capability accepted')
        except frappe.PermissionError:pass
    source=inspect.getsource(run_status)
    assert 'for_update=True' not in source,source
    print('RUN_LEASE_OK')
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if run:
        frappe.db.delete('DS Run Event',{'run':run.name})
        if frappe.db.exists('DS Model Run',run.name):frappe.delete_doc('DS Model Run',run.name,ignore_permissions=True)
    if conversation and frappe.db.exists('DS Conversation',conversation.name):
        frappe.delete_doc('DS Conversation',conversation.name,ignore_permissions=True)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUN_LEASE_OK" in result.stdout, result.stdout
