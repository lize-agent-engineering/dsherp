"""Exercise v16 Desk routes, Page transport, assets, CSRF, and REST surfaces."""
import os
import re
import httpx


BASE_URL = os.environ.get("DSHERP_V16_PROBE_BASE_URL", "http://127.0.0.1:28082")
PASSWORD = os.environ.get("DSHERP_V16_PROBE_ADMIN_PASSWORD")
if not PASSWORD:
    raise SystemExit("Missing DSHERP_V16_PROBE_ADMIN_PASSWORD")


with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=30, follow_redirects=False) as client:
    legacy = client.get("/app/home")
    assert legacy.status_code == 301, legacy.text
    assert legacy.headers["location"] == "/desk/home", legacy.headers
    desk_guest = client.get("/desk/home")
    assert desk_guest.status_code == 301, desk_guest.text
    assert desk_guest.headers["location"].startswith("/login?redirect-to="), desk_guest.headers

    assert client.get("/api/method/ping").status_code == 200
    assert client.get("/api/v2/doctype/Item/meta").status_code in (401, 403)

    login = client.post("/api/method/login", data={"usr": "Administrator", "pwd": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["message"] == "Logged In"

    desk = client.get("/desk")
    assert desk.status_code == 200, desk.text[:500]
    token_match = re.search(r'frappe\.csrf_token\s*=\s*"([^"]+)"', desk.text)
    assert token_match, "frappe.csrf_token was not exposed in authenticated Desk boot"
    assert "/assets/dsherp_bridge/dist/context-agent.js" in desk.text
    assert "/assets/dsherp_bridge/dist/context-agent.css" in desk.text

    pages = {
        "dsherp-agent": ("agent-workbench.js", "agent-workbench.css"),
        "dsherp-studio": ("studio.js", "studio.css"),
        "dsherp-configuration-preview": (),
        "dsherp-home": ("studio.js", "studio.css"),
    }
    for page_name, assets in pages.items():
        response = client.get(
            "/api/method/frappe.desk.desk_page.getpage",
            params={"name": page_name},
        )
        assert response.status_code == 200, (page_name, response.text)
        page = response.json()["docs"][0]
        assert page["name"] == page_name
        assert "frappe.pages" in page["script"] and "on_page_load" in page["script"]
        for asset in assets:
            assert asset in page["script"], (page_name, asset)

    for asset in (
        "context-agent.js", "context-agent.css", "agent-workbench.js", "agent-workbench.css",
        "studio.js", "studio.css",
    ):
        response = client.get("/assets/dsherp_bridge/dist/" + asset)
        assert response.status_code == 200, asset
        assert len(response.content) > 100, asset

    assert client.get("/api/resource/DocType/Item").status_code == 200
    assert client.get("/api/v2/doctype/Item/meta").status_code == 200
    post = client.post(
        "/api/method/frappe.client.get_count",
        data={"doctype": "ToDo"},
        headers={"X-Frappe-CSRF-Token": token_match.group(1)},
    )
    assert post.status_code == 200, post.text

print("C1 HTTP PASS: /app→/desk, four Pages, app assets, CSRF header, v1/resource/v2")
