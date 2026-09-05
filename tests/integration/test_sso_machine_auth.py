"""SSO enforcement's machine exception must be real authentication, not a header prefix.

Audit finding: a grant-less browser session could add `Authorization: token anything`
and skip the grant requirement. Verified inside the alpha backend with a real user.
"""
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = r'''
import os, json, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost'); frappe.connect()
from dsherp_bridge import sso
from frappe.core.doctype.user.user import generate_keys
user = 'dsherp-reader@example.invalid'
keys = generate_keys(user); frappe.db.commit()

class Request:
    def __init__(self, path): self.path = path; self.remote_addr = '127.0.0.1'

def attempt(path, headers):
    frappe.local.request = Request(path)
    frappe.local.request_headers = headers
    frappe.session.user = user
    frappe.session.data = {}
    try:
        sso.validate_session(); return 'allowed'
    except frappe.PermissionError as error:
        return 'refused: ' + str(error)

frappe.get_request_header = lambda name, default=None: (frappe.local.request_headers or {}).get(name, default)
out = {
    'no_header': attempt('/api/resource/Item', {}),
    'bogus_token': attempt('/api/resource/Item', {'Authorization': 'token not-a-key:not-a-secret'}),
    'bare_prefix': attempt('/app/item', {'Authorization': 'token'}),
    'wrong_secret': attempt('/api/resource/Item', {'Authorization': 'token %s:wrong' % keys['api_key']}),
    'real_key': attempt('/api/resource/Item', {'Authorization': 'token %s:%s' % (keys['api_key'], keys['api_secret'])}),
}
print(json.dumps(out)); frappe.destroy()
'''


def test_only_the_session_users_own_api_key_counts_as_machine_authentication():
    result = subprocess.run(
        ['docker', 'exec', '-i', 'dsherp-validation-backend-1', '/home/frappe/frappe-bench/env/bin/python', '-'],
        input=SCRIPT, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr[-1500:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out['real_key'] == 'allowed'
    for case in ('no_header', 'bogus_token', 'bare_prefix', 'wrong_secret'):
        assert out[case].startswith('refused'), (case, out[case])
