"""Complete a fresh validation Site through ERPNext's native setup exactly once."""
import argparse
import subprocess


TARGETS={
    'alpha':{'container':'dsherp-validation-backend-1','site':'dsherp-validation.localhost',
             'company':'DSHERP 原生验收测试公司','company_abbr':'DVT'},
    'beta':{'container':'dsherp-validation-beta-backend-1','site':'dsherp-beta.localhost',
            'company':'DSHERP 隔离预览合成公司','company_abbr':'DPR'},
}


SCRIPT = r'''
import os
import frappe

SITE=__SITE__
COMPANY=__COMPANY__
ABBR=__ABBR__
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE);frappe.connect();frappe.set_user('Administrator')
try:
    if frappe.is_setup_complete() or frappe.db.count('Company'):
        raise RuntimeError('Company already initialized; inspect instead of retrying')
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
    result=setup_complete({
        'language':'简体中文','lang':'zh','country':'China','timezone':'Asia/Shanghai',
        'currency':'CNY','enable_telemetry':0,
        'company_name':COMPANY,'company_abbr':ABBR,'chart_of_accounts':'Standard',
        'fy_start_date':'2026-01-01','fy_end_date':'2026-12-31','setup_demo':0,
    })
    if result!={'status':'ok'}:
        raise RuntimeError('Native setup returned an unexpected result')
    if not frappe.is_setup_complete() or not frappe.db.exists('Company',COMPANY):
        raise RuntimeError('Native setup did not persist the synthetic company')
    frappe.db.commit()
finally:
    frappe.destroy()
'''


def main(target='alpha'):
    if target not in TARGETS:
        raise ValueError('Unknown validation company target')
    config=TARGETS[target]
    script=(SCRIPT.replace('__SITE__',repr(config['site']))
                  .replace('__COMPANY__',repr(config['company']))
                  .replace('__ABBR__',repr(config['company_abbr'])))
    result=subprocess.run(
        ['docker','exec','-i',config['container'],
         '/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=180,
    )
    if result.returncode:
        raise RuntimeError('Validation company provisioning failed; inspect the Site before retrying')
    print(f'{target}: native setup completed with one synthetic company.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',choices=tuple(TARGETS),default='alpha')
    main(parser.parse_args().target)
