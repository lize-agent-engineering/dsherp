import json
import subprocess
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


COMPOSE = ['docker', 'compose', '-f', 'infra/compose.validation.yml']


def test_daily_site_is_empty_pre_setup_and_uses_no_extra_standing_service():
    script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-daily.localhost');frappe.connect()
print(json.dumps({'apps':sorted(frappe.get_installed_apps()),'setup_complete':int(frappe.db.get_single_value('System Settings','setup_complete') or 0),'counts':{d:frappe.db.count(d) for d in ('Company','Item','Customer','Sales Order')}}))
frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'backend', '/home/frappe/frappe-bench/env/bin/python', '-c', script],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert {'frappe', 'erpnext', 'dsherp_bridge'}.issubset(state['apps'])
    assert state['setup_complete'] == 0
    assert state['counts'] == {'Company': 0, 'Item': 0, 'Customer': 0, 'Sales Order': 0}

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


def test_daily_agent_is_prepared_without_member_business_data_or_business_roles():
    business_script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-daily.localhost');frappe.connect()
user='dsherp-context-runtime@example.invalid';doc=frappe.get_doc('User',user)
print(json.dumps({'runtime_user':frappe.conf.get('dsherp_runtime_user'),'oauth':frappe.conf.get('dsherp_platform_oauth'),'roles':[r.role for r in doc.roles],'business_read':any(frappe.has_permission(d,'read',user=user) for d in ('Item','Customer','Sales Order')),'conversations':frappe.db.count('DS Conversation'),'runs':frappe.db.count('DS Model Run')}))
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
    assert state['conversations'] == 0 and state['runs'] == 0

    platform_script = r'''
import json,os,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-platform.localhost');frappe.connect()
enterprise=frappe.get_doc('DS Enterprise','daily')
print(json.dumps({'site':enterprise.site,'status':enterprise.status,'memberships':frappe.db.count('DS Membership',{'enterprise':'daily'}),'business_url':frappe.conf.dsherp_business_sites.get(enterprise.site),'desk_url':frappe.conf.dsherp_desk_sites.get(enterprise.site)}))
frappe.destroy()
'''
    result = subprocess.run(
        [*COMPOSE, 'exec', '-T', 'platform-backend', '/home/frappe/frappe-bench/env/bin/python', '-c', platform_script],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert state == {'site': 'dsherp-daily.localhost', 'status': 'Provisioning', 'memberships': 0,
                     'business_url': 'http://backend:8000',
                     'desk_url': 'http://daily.localhost:18086/api/method/dsherp_bridge.sso.start'}
