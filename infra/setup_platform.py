"""Complete the native Frappe setup flow for the isolated platform test site."""
import json
from pathlib import Path
import httpx

profile = json.loads(Path('.runtime/platform-users.json').read_text())['operator']
with httpx.Client(base_url='http://127.0.0.1:18083', headers={
    'Host':'platform.localhost',
    'Authorization': f"token {profile['api_key']}:{profile['api_secret']}",
}, trust_env=False, timeout=60) as client:
    response = client.post('/api/method/frappe.desk.page.setup_wizard.setup_wizard.setup_complete', json={'args':{
        'language':'简体中文', 'country':'China', 'timezone':'Asia/Shanghai',
        'currency':'CNY', 'enable_telemetry':0,
    }})
    if response.status_code != 200 or response.json().get('message',{}).get('status') != 'ok':
        raise SystemExit(f'Native platform setup failed (HTTP {response.status_code}); inspect local server log')
    print('Native platform setup completed')
