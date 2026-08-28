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
