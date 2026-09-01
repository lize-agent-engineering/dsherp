import json
import subprocess
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


COMPOSE = ['docker', 'compose', '-f', 'infra/compose.validation.yml']


def test_daily_site_has_one_explicitly_synthetic_enterprise_and_no_extra_standing_service():
    script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-daily.localhost');frappe.connect()
operator='daily-operator@example.invalid';frappe.set_user(operator)
checks={f'{d}:{p}':bool(frappe.has_permission(d,p)) for d,p in (('Item','read'),('Item','write'),('Customer','create'),('Customer','write'),('Sales Order','create'),('Sales Order','write'),('Sales Order','submit'),('Sales Order','cancel'),('BOM','read'),('Bin','read'),('Warehouse','read'),('Work Order','create'),('Work Order','submit'),('Stock Entry','create'),('Stock Entry','submit'),('Purchase Order','create'),('Purchase Order','submit'),('Purchase Receipt','create'),('Purchase Receipt','submit'),('Subcontracting Order','create'),('Subcontracting Order','submit'),('Subcontracting Receipt','create'),('Subcontracting Receipt','submit'),('Delivery Note','create'),('Delivery Note','submit'))}
fixture_warehouses=frappe.get_all('Warehouse',filters={'warehouse_name':['like','DSHERP 制造测试合成%']},pluck='name')
print(json.dumps({'apps':sorted(frappe.get_installed_apps()),'setup_complete':int(frappe.db.get_single_value('System Settings','setup_complete') or 0),'companies':frappe.get_all('Company',pluck='name'),'items':sorted(frappe.get_all('Item',pluck='name')),'customers':frappe.get_all('Customer',pluck='customer_name'),'sales_orders':frappe.db.count('Sales Order'),'roles':sorted(frappe.get_roles()),'permissions':checks,'fixture_warehouses':sorted(fixture_warehouses),'fixture_supplier':frappe.db.count('Supplier',{'name':'DSHERP 制造测试合成供应商'}),'fixture_bom':frappe.db.count('BOM',{'name':'BOM-DSHERP-MFG-SYN-FG-001','docstatus':1}),'fixture_reconciliation':frappe.db.count('Stock Reconciliation',{'name':'DSHERP-MFG-SYN-OPENING-STOCK','docstatus':1}),'raw_qty':frappe.db.get_value('Bin',{'item_code':'DSHERP-MFG-SYN-RM','warehouse':'DSHERP 制造测试合成原料仓 - DSE'},'actual_qty') or 0,'policy_rows':frappe.db.count('DS Doctype Policy'),'policy_routes':frappe.db.count('DS Doctype Policy Route')}))
frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'backend', '/home/frappe/frappe-bench/env/bin/python', '-c', script],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert {'frappe', 'erpnext', 'dsherp_bridge'}.issubset(state['apps'])
    assert state['setup_complete'] == 1
    assert state['companies'] == ['DSHERP 日常合成企业']
    assert state['items'] == [
        'DAILY-AGENT-ITEM',
        'DSHERP-MFG-SYN-FG',
        'DSHERP-MFG-SYN-RM',
        'DSHERP-MFG-SYN-SERVICE',
    ]
    assert state['customers'] == ['日常 Agent 合成客户']
    assert state['sales_orders'] == 0
    assert {
        'Sales User', 'Sales Manager', 'Stock Manager', 'Item Manager',
        'Manufacturing User', 'Purchase User', 'Purchase Master Manager', 'Stock User',
    }.issubset(state['roles'])
    assert all(state['permissions'].values())
    assert state['fixture_warehouses'] == [
        'DSHERP 制造测试合成仓库 - DSE',
        'DSHERP 制造测试合成原料仓 - DSE',
        'DSHERP 制造测试合成在制仓 - DSE',
        'DSHERP 制造测试合成委外仓 - DSE',
        'DSHERP 制造测试合成成品仓 - DSE',
    ]
    assert state['fixture_supplier'] == 1
    assert state['fixture_bom'] == 1
    assert state['fixture_reconciliation'] == 1
    assert state['raw_qty'] == 100
    assert state['policy_rows'] == 14
    assert state['policy_routes'] == 7

    services = subprocess.run([*COMPOSE, 'ps', '--services', '--status', 'running'], text=True, capture_output=True, check=True).stdout.splitlines()
    assert 'daily-provision' not in services
    assert not {'worker', 'websocket', 'platform-websocket', 'scheduler'}.intersection(services)


def test_daily_site_has_isolated_http_entry_and_complete_backup_set():
    opener = build_opener(ProxyHandler({}))
    response = opener.open(Request('http://127.0.0.1:18086/login', headers={'Host': 'daily.localhost'}), timeout=10)
    assert response.status == 200 and b'login' in response.read().lower()
    for headers, status in (({'Host': 'wrong.localhost'}, 421), ({'Host': 'daily.localhost', 'Origin': 'http://evil.invalid'}, 403)):
        try:
            opener.open(Request('http://127.0.0.1:18086/login', headers=headers), timeout=10)
            raise AssertionError(f'Expected HTTP {status}')
        except HTTPError as error:
            assert error.code == status

    listing = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'backend', 'find', 'sites/dsherp-daily.localhost/private/backups', '-maxdepth', '1', '-type', 'f', '-size', '+0'],
        text=True, capture_output=True, check=True,
    ).stdout
    assert '-database.sql.gz' in listing
    assert '-site_config_backup.json' in listing
    assert '-files.tgz' in listing
    assert '-private-files.tgz' in listing
    assert 'dsherp-daily-restore.localhost' not in subprocess.run(
        [*COMPOSE, 'exec', '-T', 'backend', 'find', 'sites', '-maxdepth', '1', '-type', 'd'],
        text=True, capture_output=True, check=True,
    ).stdout


def test_daily_agent_uses_separate_permissionless_runtime_and_explicit_member_mapping():
    business_script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-daily.localhost');frappe.connect()
user='dsherp-context-runtime@example.invalid';doc=frappe.get_doc('User',user)
print(json.dumps({'runtime_user':frappe.conf.get('dsherp_runtime_user'),'oauth':frappe.conf.get('dsherp_platform_oauth'),'roles':[r.role for r in doc.roles],'business_read':any(frappe.has_permission(d,'read',user=user) for d in ('Item','Customer','Sales Order')),'conversations':frappe.db.count('DS Conversation'),'runs':frappe.db.count('DS Model Run'),'active_runs':frappe.db.count('DS Model Run',{'status':['in',['Queued','Running','Cancelling']]}),'conversation_owners':frappe.get_all('DS Conversation',pluck='owner')}))
frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'backend', '/home/frappe/frappe-bench/env/bin/python', '-c', business_script],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert state['runtime_user'] == 'dsherp-context-runtime@example.invalid'
    assert state['oauth']['enterprise'] == 'daily'
    assert state['roles'] == [] and state['business_read'] is False
    assert state['active_runs'] == 0
    assert set(state['conversation_owners']).issubset({'daily-operator@example.invalid'})

    platform_script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-platform.localhost');frappe.connect()
enterprise=frappe.get_doc('DS Enterprise','daily')
members=frappe.get_all('DS Membership',filters={'enterprise':'daily'},fields=['platform_user','erp_user','enabled'])
print(json.dumps({'site':enterprise.site,'title':enterprise.title,'status':enterprise.status,'memberships':members,'business_url':frappe.conf.dsherp_business_sites.get(enterprise.site),'desk_url':frappe.conf.dsherp_desk_sites.get(enterprise.site)}))
frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'platform-backend', '/home/frappe/frappe-bench/env/bin/python', '-c', platform_script],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert state == {'site': 'dsherp-daily.localhost', 'title': '日常合成企业', 'status': 'Ready',
                     'memberships': [{'platform_user': 'member@example.invalid',
                                      'erp_user': 'daily-operator@example.invalid', 'enabled': 1}],
                     'business_url': 'http://backend:8000',
                     'desk_url': 'http://daily.localhost:18086/api/method/dsherp_bridge.sso.start'}
