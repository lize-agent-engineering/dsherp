"""Single source of truth for run time/lease/queue budgets. Values come from site_config overrides."""
import frappe

DEFAULTS={'model_request_timeout_seconds':90,'lease_seconds':180,'lease_renew_below_seconds':90,
          'queue_expires_seconds':600,'heartbeat_stale_seconds':60}
TOTAL={'query':300,'configuration':300,'operation':600}


def budget(domain):
    if domain not in TOTAL:frappe.throw('未知业务领域')
    values={**DEFAULTS,'run_total_seconds':TOTAL[domain]}
    override=frappe.conf.get('dsherp_run_budget') or {}
    if not isinstance(override,dict):frappe.throw('dsherp_run_budget 必须是对象')
    for key,value in override.items():
        if key not in values or type(value) is not int or value<=0:frappe.throw(f'dsherp_run_budget.{key} 无效')
        values[key]=value
    concurrency=frappe.conf.get('dsherp_site_concurrency',1)
    if type(concurrency) is not int or concurrency<1:frappe.throw('dsherp_site_concurrency 无效')
    policy=frappe.conf.get('dsherp_model_policy') or {}
    values.update({'site_concurrency':concurrency,'provider':policy.get('provider','deepseek-official'),
                   'model':policy.get('model','deepseek-v4-flash')})
    return values
