"""Real concurrent MariaDB transactions; ERP HTTP is a synchronization stub."""
from site_exec import run_site_script

PLATFORM = 'dsherp-platform.localhost'
MEMBER = 'member@example.invalid'
# Idempotent. Re-enabling bumps binding_version (enabled is a binding field): that is the same
# behaviour the script's own finally already relied on, so this adds no new effect on the Site.
MEMBERSHIP_RESTORE = r'''
name = frappe.db.get_value('DS Membership', {'enterprise':'alpha', 'platform_user':'member@example.invalid'}, 'name')
assert name, 'alpha membership for member@example.invalid is missing'
doc = frappe.get_doc('DS Membership', name)
if not doc.enabled:
    doc.enabled = 1
    doc.save()
    frappe.db.commit()
print(json.dumps({'enabled': frappe.db.get_value('DS Membership', name, 'enabled')}))
'''

CONCURRENT_REVOCATION = r'''
import subprocess
import sys
from unittest.mock import patch

from dsherp_platform.api import read_record

# A separate process has an independent connection and commits while the first
# request retains its read snapshot. Use the normal Document save operation.
writer = """
import os, sys, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost'); frappe.connect()
frappe.set_user('Administrator')
name = frappe.db.get_value('DS Membership', {'enterprise':'alpha', 'platform_user':'member@example.invalid'}, 'name')
doc = frappe.get_doc('DS Membership', name)
doc.enabled = int(sys.argv[1]); doc.save(); frappe.db.commit(); frappe.destroy()
"""

def set_enabled(value):
    result = subprocess.run([sys.executable, '-c', writer, str(value)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, 'Independent membership update failed'

original = frappe.db.get_value('DS Membership', {'enterprise':'alpha', 'platform_user':'member@example.invalid'}, 'enabled')
assert original == 1, 'Expected active synthetic alpha membership'
business_reads = []

class Response:
    status_code = 200
    def __init__(self, value): self.value = value
    def json(self): return {'message': self.value}

class Client:
    def __init__(self): self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs):
        if url.endswith('/frappe.auth.get_logged_user'):
            erp_user = frappe.db.get_value('DS Membership', {'enterprise':'alpha', 'platform_user':'member@example.invalid'}, 'erp_user')
            set_enabled(0)
            return Response(erp_user)
        business_reads.append(url)
        return Response({'name':'SHOULD-NOT-BE-RETURNED', 'fields':{}})

denied = False
try:
    with patch('requests.Session', Client):
        try:
            read_record('alpha', 'Item', 'DSHERP-TEST-ITEM')
        except frappe.PermissionError:
            denied = True
    assert denied, 'Concurrent revocation did not deny the in-flight request'
    assert not business_reads, 'ERP business read occurred after membership revocation'
finally:
    frappe.db.rollback()
    set_enabled(original)
'''


def test_revocation_committed_during_identity_check_stops_business_read(residue):
    # The script disables the membership mid-request and re-enables it in its own finally. A host
    # timeout kills the docker client, not the container interpreter, so that finally may never run
    # and the shared alpha binding would stay disabled for every later test: register the restore
    # before the script gets the chance to disable anything.
    residue.restore(PLATFORM, 'alpha membership of member@example.invalid enabled', MEMBERSHIP_RESTORE)
    run_site_script(PLATFORM, CONCURRENT_REVOCATION, user=MEMBER, timeout=90)
