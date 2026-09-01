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


def test_native_realtime_transport_is_retired_for_http_polling_agent():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/socket.io/', params={'EIO': '4', 'transport': 'polling'})
        assert response.status_code == 404


def test_native_desk_requires_login():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/desk')
        assert response.status_code in (301, 302, 303)
        assert '/login' in response.headers['location']


def test_authenticated_realtime_namespace_is_also_retired():
    with httpx.Client(base_url=BASE_URL, headers={'Origin': BASE_URL}, trust_env=False, timeout=15) as client:
        assert client.get('/socket.io/', params={'EIO':4, 'transport':'polling'}).status_code == 404


def test_realtime_rejects_foreign_origin():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        response = client.get('/socket.io/', params={'EIO':4, 'transport':'polling'},
                              headers={'Origin':'https://untrusted.example'})
        assert response.status_code == 403


def test_dsherp_page_uses_native_authenticated_loader(erp):
    status, body = erp('reader', '/api/method/frappe.desk.desk_page.getpage', name='dsherp-studio')
    assert status == 200
    page = body['docs'][0]
    assert page['name'] == 'dsherp-studio'
    assert '/assets/dsherp_bridge/dist/studio.js' in page['script']
    status, _ = erp('guest', '/api/method/frappe.desk.desk_page.getpage', name='dsherp-studio')
    assert status in (401, 403)


def test_agent_workbench_uses_native_authenticated_loader(erp):
    status, body = erp('reader', '/api/method/frappe.desk.desk_page.getpage', name='dsherp-agent')
    assert status == 200
    page = body['docs'][0]
    assert page['name'] == 'dsherp-agent'
    assert '/assets/dsherp_bridge/dist/agent-workbench.js' in page['script']
    assert '/assets/dsherp_bridge/dist/agent-workbench.css' in page['script']

    status, body = erp('reader', '/api/method/frappe.desk.desktop.get_workspace_sidebar_items')
    assert status == 200
    workspace = next(item for item in body['message']['pages'] if item['name'] == 'DSHERP')
    assert workspace['public'] == 1


def test_dsherp_browser_bundle_is_served_without_replacing_native_assets():
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=15) as client:
        for name, content_type in [('studio.js', 'javascript'), ('studio.css', 'text/css')]:
            response = client.get('/assets/dsherp_bridge/dist/' + name)
            assert response.status_code == 200
            assert content_type in response.headers['content-type']
            assert len(response.content) > 100

        for name, content_type in [('agent-workbench.js', 'javascript'), ('agent-workbench.css', 'text/css')]:
            response = client.get('/assets/dsherp_bridge/dist/' + name)
            assert response.status_code == 200
            assert content_type in response.headers['content-type']
            assert len(response.content) > 100
