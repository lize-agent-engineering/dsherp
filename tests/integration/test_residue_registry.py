"""The registry's whole reason for existing, proved against a real Site.

A host-side `subprocess.run(timeout=...)` kills the docker client, not the interpreter inside
the container: whatever that interpreter already committed stays, and its own `finally` never
runs. This file makes that happen on purpose - commit, then sleep past the timeout - and then
asserts the registered row is taken away anyway.
"""
import subprocess
import uuid

import pytest
from site_exec import run_site_json, run_site_script

import residue

SITE = 'dsherp-validation.localhost'
# Commit first, then outlive the host's patience. The sleep is short: the container-side
# interpreter keeps running after the client is killed, and nothing should wait for it.
COMMIT_THEN_HANG = '''
import time
frappe.get_doc({'doctype': 'DS Conversation', 'title': TITLE}).insert(ignore_permissions=True)
frappe.db.commit()
print(json.dumps({'committed': True}))
time.sleep(20)
frappe.delete_doc('DS Conversation', frappe.db.get_value('DS Conversation', {'title': TITLE}, 'name'),
                  force=True, ignore_permissions=True)
frappe.db.commit()
'''


def _count(title):
    return run_site_json(SITE, f"print(json.dumps(frappe.db.count('DS Conversation', {{'title': {title!r}}})))",
                         timeout=60)


def test_a_script_killed_by_the_host_timeout_leaves_a_row_and_the_registry_takes_it_away():
    title = 'residue timeout drill ' + uuid.uuid4().hex
    # Not the `residue` fixture: this test drives sweep() itself so it can look at the Site
    # between the kill and the sweep.
    registry = residue.Registry('tests/integration/test_residue_registry.py::timeout-drill', 'function')
    with registry.active():
        registry.doc(SITE, 'DS Conversation', {'title': title})
        with pytest.raises(subprocess.TimeoutExpired):
            run_site_script(SITE, COMMIT_THEN_HANG.replace('TITLE', repr(title)), timeout=6)
        assert _count(title) == 1, 'the killed script had already committed - that is the point'
        report = registry.sweep()
    assert report['interrupted'] == [{'site': SITE, 'body': COMMIT_THEN_HANG.replace('TITLE', repr(title)).strip()[:160]}]
    assert report['removed'][SITE] == {'DS Conversation': 1} and report['leftovers'] == []
    assert _count(title) == 0
    assert residue.read_ledger(registry.ledger)['entries'] == []


def test_the_registry_refuses_to_register_a_persistent_fixture_of_this_very_site():
    """The names below are real rows on this Site that every other test depends on."""
    registry = residue.Registry('tests/integration/test_residue_registry.py::protection', 'function')
    assert run_site_json(SITE, "print(json.dumps(bool(frappe.db.exists('Item', 'DSHERP-TEST-ITEM'))))") is True
    for doctype, selector in (('Item', 'DSHERP-TEST-ITEM'), ('Item', 'DSHERP-MFG-SYN-RM'),
                              ('Sales Order', 'SAL-ORD-2026-00001')):
        with pytest.raises(ValueError):
            registry.doc(SITE, doctype, selector)
    with pytest.raises(ValueError):
        registry.user(SITE, 'dsherp-reader@example.invalid')
    assert registry.entries == []
