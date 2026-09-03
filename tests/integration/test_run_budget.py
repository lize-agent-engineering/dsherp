"""Server-side run budget is the single source of truth."""

import subprocess


def test_budget_defaults_and_invalid_override_fastfail():
    script = r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.run_budget import budget
try:
    query=budget('query')
    assert query['run_total_seconds']==300,query
    operation=budget('operation')
    assert operation['run_total_seconds']==600,operation
    assert query['site_concurrency']==1,query
    assert query['model']=='deepseek-v4-flash',query
    original=frappe.conf.get('dsherp_run_budget')
    frappe.conf.dsherp_run_budget={'run_total_seconds':'x'}
    try:
        budget('query');raise AssertionError('invalid override accepted')
    except frappe.ValidationError:
        pass
    finally:
        if original is None:
            frappe.conf.pop('dsherp_run_budget',None)
        else:
            frappe.conf.dsherp_run_budget=original
    try:
        budget('unknown');raise AssertionError('unknown domain accepted')
    except frappe.ValidationError:
        pass
    print('OK')
finally:
    frappe.destroy()
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
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
