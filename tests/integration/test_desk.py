"""Native Desk HTTP transport, using only the isolated synthetic site."""
import re

import httpx


BASE_URL = "http://127.0.0.1:18082"


def test_native_login_serves_its_styles_and_scripts():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        page = client.get('/login')
        assert page.status_code == 200
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+\.(?:css|js))', page.text)
        assert assets, 'Native login must reference bundled assets'
        for asset in assets:
            response = client.get(asset)
            assert response.status_code == 200, asset
            assert 'text/html' not in response.headers['content-type'], asset


def test_native_realtime_transport_is_available():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/socket.io/', params={'EIO': '4', 'transport': 'polling'})
        assert response.status_code == 200
        assert response.text.startswith('0{')
        assert '"sid"' in response.text


def test_native_desk_requires_login():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/app')
        assert response.status_code in (301, 302, 303)
        assert '/login' in response.headers['location']


def test_authenticated_realtime_namespace():
    import json
    from pathlib import Path

    profile = json.loads((Path(__file__).resolve().parents[2] / '.runtime/erp-reader.json').read_text())
    # Match the existing ordinary-user profile; no administrator session is used.
    headers = {'Authorization': f"token {profile['api_key']}:{profile['api_secret']}",
               'Origin': BASE_URL}
    with httpx.Client(base_url=BASE_URL, headers=headers, trust_env=False, timeout=15) as client:
        handshake = client.get('/socket.io/', params={'EIO':4, 'transport':'polling'})
        sid = json.loads(handshake.text[1:])['sid']
        params = {'EIO':4, 'transport':'polling', 'sid':sid}
        namespace = '/' + profile['site']
        sent = client.post('/socket.io/', params=params, content='40' + namespace + ',')
        assert sent.status_code == 200
        reply = client.get('/socket.io/', params=params)
        assert reply.text.startswith('40' + namespace + ','), 'Native authenticated namespace refused'
        client.post('/socket.io/', params=params, content='1')


def test_realtime_rejects_foreign_origin():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/socket.io/', params={'EIO':4, 'transport':'polling'},
                              headers={'Origin':'https://untrusted.example'})
        assert response.status_code == 403
