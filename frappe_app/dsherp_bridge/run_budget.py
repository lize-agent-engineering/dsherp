"""Single source of truth for run and model budgets from Site configuration."""
import frappe

# Official values, 2026-09-09, from the live batch of 34 cases on deepseek-v4-flash
# (`docs/engineering/agent-quality-evidence.md`, Task 6.5). Three of them could not be derived
# by the plan's `ceil(P95 × 1.5)`, because the observation was censored by the limit itself —
# the sizing note sits on each one.
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
    # Observed max 487,976 on one operation run — 93% of the old 524,288. Nothing enforced
    # headroom, and the next tool result would have ended a working run on budget.
    # ceil(487,976 × 1.5) rounded up to 1024 = 732,160; taken to 768 KiB for a round number.
    'model_max_input_bytes_total':786432,
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
    # `model_max_output_tokens_per_call` is 8192 everywhere, and it is the one number here
    # that is not derived from a percentile. deepseek-v4-flash is a reasoning model: its
    # reasoning tokens are charged against the same allowance as the answer. Measured on
    # 2026-09-09, five of the seven responses that reached the old ceiling had
    # `reasoningTokens == outputTokens == the ceiling` — the model thought until the budget
    # was gone and emitted no answer at all, and the run ended '没有答复'. Every one of those
    # observations is censored by the ceiling, so a percentile of them measures the ceiling.
    # 8192 leaves roughly 4k of reasoning beside a full answer; what makes it real is the
    # acceptance criterion, not the arithmetic: a live batch with zero `max-tokens` turns.
    'query':{
        'run_total_seconds':300,
        # Observed max 7 of 8 — near the ceiling. ceil(7 × 1.5) = 11.
        'model_max_calls':11,
        'model_max_output_tokens_per_call':8192,
        # Observed cumulative max 6,231. ceil(× 1.5) → 16,384 was already enough, but the
        # per-call rise has to fit: raised in step so one long answer cannot exhaust the run.
        'model_max_output_tokens_total':24576,
    },
    'configuration':{
        'run_total_seconds':300,
        # Observed max 3 of 8; left as it is rather than tightened on four runs.
        'model_max_calls':8,
        'model_max_output_tokens_per_call':8192,
        'model_max_output_tokens_total':24576,
    },
    'operation':{
        # Observed max 188,850 ms of 600,000. Deliberately **not** tightened to ceil(P95×1.5):
        # 19 synthetic runs are not the distribution to cut a live timeout from, and the
        # per-call output rise will make turns longer, not shorter.
        'run_total_seconds':600,
        # Observed max 10 — exactly the old ceiling, on two runs. Censored, so ceil(10 × 1.5).
        'model_max_calls':15,
        'model_max_output_tokens_per_call':8192,
        # Observed cumulative max 13,533; ceil(× 1.5) = 20,300, but 15 calls at 8,192 need
        # room to be spent on answers rather than refused halfway.
        'model_max_output_tokens_total':61440,
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
