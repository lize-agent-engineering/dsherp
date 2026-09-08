"""Single source of truth for run and model budgets from Site configuration."""
import frappe

DEFAULTS={
    'model_request_timeout_seconds':90,
    # 领取先给一个短确认租约：claim 的 HTTP 超时后服务端仍会提交 Running，那条运行
    # 没有执行者却占着并发位与用户在飞位。执行者第一次 run_status 就把它升到完整租约。
    'claim_ack_seconds':90,
    'lease_seconds':180,
    'lease_renew_below_seconds':90,
    'queue_expires_seconds':600,
    'heartbeat_stale_seconds':60,
    'model_max_input_bytes_per_call':131072,
    'model_max_input_bytes_total':524288,
}
# Tenant quotas, deliberately **not** part of `budget(domain)`. That dict is handed to the
# container whole and compared key by key against the plan it claimed; two keys the container
# has no use for would have to be copied into three hand-written test fixtures as well. These
# are judged on the Site, before a run exists, and never leave it.
#
# 0 means unlimited, and 0 is the default: without a real usage distribution, switching a
# refusal on for everyone would be a gate against ordinary work rather than against abuse.
QUOTA_DEFAULTS={'user_daily_model_calls':0,'site_monthly_tokens':0}
DOMAINS={
    'query':{
        'run_total_seconds':300,
        'model_max_calls':8,
        'model_max_output_tokens_per_call':2048,
        'model_max_output_tokens_total':16384,
    },
    'configuration':{
        'run_total_seconds':300,
        'model_max_calls':8,
        'model_max_output_tokens_per_call':3072,
        'model_max_output_tokens_total':16384,
    },
    'operation':{
        'run_total_seconds':600,
        'model_max_calls':10,
        'model_max_output_tokens_per_call':3072,
        'model_max_output_tokens_total':30720,
    },
}


def _model_policy():
    policy=frappe.conf.get('dsherp_model_policy')
    if policy is None:policy={}
    if not isinstance(policy,dict):frappe.throw('dsherp_model_policy 必须是对象')
    unknown=set(policy)-{'provider','model'}
    if unknown:frappe.throw(f'dsherp_model_policy.{sorted(unknown)[0]} 无效')
    provider=policy.get('provider','deepseek-official')
    model=policy.get('model','deepseek-v4-flash')
    if provider!='deepseek-official':frappe.throw('dsherp_model_policy.provider 无效')
    if not isinstance(model,str) or not model or model.strip()!=model:
        frappe.throw('dsherp_model_policy.model 无效')
    return {'provider':provider,'model':model}


def budget(domain):
    if domain not in DOMAINS:frappe.throw('未知业务领域')
    values={**DEFAULTS,**DOMAINS[domain]}
    override=frappe.conf.get('dsherp_run_budget')
    if override is None:override={}
    if not isinstance(override,dict):frappe.throw('dsherp_run_budget 必须是对象')
    for key,value in override.items():
        if key not in values or type(value) is not int or value<=0:frappe.throw(f'dsherp_run_budget.{key} 无效')
        values[key]=value
    if values['model_max_input_bytes_per_call']>values['model_max_input_bytes_total']:
        frappe.throw('模型单次输入预算不能超过累计输入预算')
    if values['model_max_output_tokens_per_call']>values['model_max_output_tokens_total']:
        frappe.throw('模型单次输出预算不能超过累计输出预算')
    if values['lease_renew_below_seconds']>=values['lease_seconds']:
        frappe.throw('租约续期阈值必须小于租约时长')
    concurrency=frappe.conf.get('dsherp_site_concurrency',1)
    if type(concurrency) is not int or concurrency<1:frappe.throw('dsherp_site_concurrency 无效')
    values.update({'site_concurrency':concurrency,**_model_policy()})
    return values


def quota():
    """Tenant quotas from Site configuration. Both default to 0, which means unlimited."""
    values=dict(QUOTA_DEFAULTS)
    override=frappe.conf.get('dsherp_quota')
    if override is None:override={}
    if not isinstance(override,dict):frappe.throw('dsherp_quota 必须是对象')
    for key,value in override.items():
        # `>=0`, unlike `dsherp_run_budget`'s `>0`: there 0 would be a budget that refuses
        # everything, here it is the documented way to say "no limit" — a Site turning a
        # quota back off must be able to write the value the default already has.
        if key not in values or type(value) is not int or value<0:frappe.throw(f'dsherp_quota.{key} 无效')
        values[key]=value
    return values
