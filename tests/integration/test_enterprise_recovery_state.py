"""A recovery's closed provisioning takes a Ready enterprise into Provisioning and the ordinary
call that follows takes it back to Ready. An operator's Disabled or Failed is a decision of
its own: neither call touches it (third-round R4). Runs the scripts ensure_enterprise really
generates, inside a rollback-only transaction on the real platform Site."""
import json
import subprocess
import uuid

from dsherp import admin
from tests.test_admin_cli import RELEASE


class Capture:
    def __init__(self):
        self.scripts = []

    def python(self, site, body, timeout=900):
        self.scripts.append(body)
        return json.dumps('captured')


def _sequences(cases):
    script = '''import os,json,io,contextlib,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost');frappe.connect();frappe.set_user('Administrator')
cases=json.loads(CASES)
commit=frappe.db.commit
frappe.db.commit=lambda:None
out=[]
try:
    for case in cases:
        if case['initial']:
            frappe.get_doc({'doctype':'DS Enterprise','enterprise_id':case['slug'],'title':'recovery state probe',
                'site':case['site'],'base_url':RELEASE_URL,'status':case['initial']}).insert(ignore_permissions=True)
        seen=[case['initial']]
        for body in case['scripts']:
            with contextlib.redirect_stdout(io.StringIO()):exec(body)
            seen.append(frappe.db.get_value('DS Enterprise',case['slug'],'status'))
        out.append(seen)
finally:
    frappe.db.commit=commit;frappe.db.rollback()
    clean=all(not frappe.db.exists('DS Enterprise',case['slug']) for case in cases)
    print(json.dumps({'sequences':out,'rollback_verified':clean}))
    frappe.destroy()
'''.replace('CASES', repr(json.dumps(cases))).replace('RELEASE_URL', repr(RELEASE['tenant_internal_url']))
    result = subprocess.run(['docker', 'exec', '-i', 'dsherp-validation-platform-backend-1',
                             '/home/frappe/frappe-bench/env/bin/python', '-'],
                            input=script, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_closed_then_ordinary_provisioning_moves_only_ready_and_new_enterprises_and_leaves_operator_decisions():
    cases = []
    for initial in (None, 'Ready', 'Disabled', 'Failed'):
        slug = 'recovery-state-' + uuid.uuid4().hex[:10]
        site = slug + '.invalid'
        bench = Capture()
        admin.ensure_enterprise(bench, RELEASE, slug, site, status='Provisioning')
        admin.ensure_enterprise(bench, RELEASE, slug, site, status='Ready')
        cases.append({'slug': slug, 'site': site, 'initial': initial, 'scripts': bench.scripts})
    outcome = _sequences(cases)
    assert outcome['rollback_verified'] is True
    new, ready, disabled, failed = outcome['sequences']
    assert new == [None, 'Provisioning', 'Ready'], new
    assert ready == ['Ready', 'Provisioning', 'Ready'], ready
    assert disabled == ['Disabled', 'Disabled', 'Disabled'], disabled
    assert failed == ['Failed', 'Failed', 'Failed'], failed
