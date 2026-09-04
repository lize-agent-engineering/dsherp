"""Server-side run budget is the single source of truth."""

import subprocess


def test_budget_defaults_and_invalid_override_fastfail():
    script = r'''
import os,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.run_budget import budget
MODEL_KEYS=('model_max_calls','model_max_input_bytes_per_call','model_max_input_bytes_total','model_max_output_tokens_per_call','model_max_output_tokens_total')
DOMAIN_DEFAULTS={'query':(8,131072,524288,2048,16384),'configuration':(8,131072,524288,3072,16384),'operation':(10,131072,524288,3072,30720)}
had_budget='dsherp_run_budget' in frappe.conf
original_budget=frappe.conf.get('dsherp_run_budget')
had_policy='dsherp_model_policy' in frappe.conf
original_policy=frappe.conf.get('dsherp_model_policy')
had_concurrency='dsherp_site_concurrency' in frappe.conf
original_concurrency=frappe.conf.get('dsherp_site_concurrency')

def clear_conf():
    frappe.conf.pop('dsherp_run_budget',None)
    frappe.conf.pop('dsherp_model_policy',None)
    frappe.conf.pop('dsherp_site_concurrency',None)

def restore_conf():
    if had_budget:frappe.conf.dsherp_run_budget=original_budget
    else:frappe.conf.pop('dsherp_run_budget',None)
    if had_policy:frappe.conf.dsherp_model_policy=original_policy
    else:frappe.conf.pop('dsherp_model_policy',None)
    if had_concurrency:frappe.conf.dsherp_site_concurrency=original_concurrency
    else:frappe.conf.pop('dsherp_site_concurrency',None)

try:
    clear_conf()
    query=budget('query')
    assert query['run_total_seconds']==300,query
    configuration=budget('configuration')
    assert configuration['run_total_seconds']==300,configuration
    operation=budget('operation')
    assert operation['run_total_seconds']==600,operation
    assert query['site_concurrency']==1,query
    assert query['model']=='deepseek-v4-flash',query
    assert query['provider']=='deepseek-official',query
    for domain,expected in DOMAIN_DEFAULTS.items():
        values=budget(domain)
        actual=tuple(values[key] for key in MODEL_KEYS)
        assert actual==expected,(domain,actual,values)
    frappe.conf.dsherp_run_budget={'model_max_calls':3,'model_max_input_bytes_per_call':1024,'model_max_input_bytes_total':2048,'model_max_output_tokens_per_call':256,'model_max_output_tokens_total':512}
    for domain in DOMAIN_DEFAULTS:
        overridden=budget(domain)
        assert tuple(overridden[key] for key in MODEL_KEYS)==(3,1024,2048,256,512),(domain,overridden)
    clear_conf()
    frappe.conf.dsherp_model_policy={'provider':'deepseek-official','model':'synthetic-model'}
    policy=budget('query')
    assert policy['provider']=='deepseek-official' and policy['model']=='synthetic-model',policy
    assert tuple(policy[key] for key in MODEL_KEYS)==DOMAIN_DEFAULTS['query'],policy
    clear_conf()
    for invalid in (
        [],
        'not-an-object',
        {'unknown_budget_key':1},
        {'run_total_seconds':'x'},
        {'model_max_calls':0},
        {'model_max_calls':-1},
        {'model_max_calls':True},
        {'model_max_output_tokens_total':False},
        {'model_max_input_bytes_per_call':524289},
        {'model_max_input_bytes_per_call':200,'model_max_input_bytes_total':100},
        {'model_max_output_tokens_per_call':2049,'model_max_output_tokens_total':2048},
        {'lease_renew_below_seconds':180},
        {'lease_renew_below_seconds':181},
        {'lease_seconds':90,'lease_renew_below_seconds':90},
        {'lease_seconds':60,'lease_renew_below_seconds':90},
    ):
        frappe.conf.dsherp_run_budget=invalid
        try:
            budget('query');raise AssertionError('invalid override accepted',invalid)
        except frappe.ValidationError:
            pass
        clear_conf()
    for invalid in (
        'not-an-object',
        ['deepseek-official','synthetic-model'],
        {'provider':'deepseek-official','model':'synthetic-model','unknown_policy_key':1},
        {'provider':'other','model':'synthetic-model'},
        {'provider':'deepseek-official','model':''},
        {'provider':'deepseek-official','model':' '},
        {'provider':'deepseek-official','model':' synthetic-model '},
        {'provider':'deepseek-official','model':True},
    ):
        frappe.conf.dsherp_model_policy=invalid
        try:
            budget('query');raise AssertionError('invalid policy accepted',invalid)
        except frappe.ValidationError:
            pass
        clear_conf()
    try:
        budget('unknown');raise AssertionError('unknown domain accepted')
    except frappe.ValidationError:
        pass
    print('OK')
finally:
    restore_conf()
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
