"""Exercise the complete native OAuth authorization-code flow across two v16 Sites."""
from html.parser import HTMLParser
import os
import time
from urllib.parse import parse_qs, urlparse

import httpx


BUSINESS = "http://127.0.0.1:28082"
PLATFORM = "http://127.0.0.1:28083"


class ConsentForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "frappe.integrations.oauth2.approve" in attrs.get("action", ""):
            self.action = attrs["action"]
        if tag == "input" and attrs.get("name") == "csrf_token":
            self.csrf = attrs.get("value")


password = os.environ.get("DSHERP_V16_PROBE_MEMBER_PASSWORD")
if not password:
    raise SystemExit("Missing required environment: DSHERP_V16_PROBE_MEMBER_PASSWORD")

with (
    httpx.Client(base_url=BUSINESS, trust_env=False, timeout=30, follow_redirects=False) as business,
    httpx.Client(base_url=PLATFORM, headers={"Host": "platform.localhost"}, trust_env=False,
                 timeout=30, follow_redirects=False) as platform,
):
    for _ in range(30):
        if platform.get("/api/method/ping").status_code == 200:
            break
        time.sleep(1)
    else:
        raise AssertionError("Probe platform frontend did not become ready")
    login = platform.post("/api/method/login", json={
        "usr": "probe-member@example.invalid",
        "pwd": password,
    })
    assert login.status_code == 200, login.text

    start = business.get("/api/method/dsherp_bridge.sso.start")
    assert start.status_code == 302, start.text
    target = urlparse(start.headers["location"])
    assert target.netloc == "platform.localhost:28083", target
    query = parse_qs(target.query)
    assert query["redirect_uri"] == [
        "http://localhost:28082/api/method/dsherp_bridge.sso.callback"
    ]

    authorization = platform.get(target.path + "?" + target.query)
    if authorization.status_code == 200:
        form = ConsentForm()
        form.feed(authorization.text)
        assert form.action and form.csrf, "Native OAuth consent form missing"
        authorization = platform.post(form.action, data={"csrf_token": form.csrf})
    assert authorization.status_code == 302, authorization.text

    callback = urlparse(authorization.headers["location"])
    assert callback.netloc == "localhost:28082", callback
    result = business.get(callback.path + "?" + callback.query)
    assert result.status_code == 302, result.text
    assert result.headers["location"] == "/desk/home"

    identity = business.get("/api/method/frappe.auth.get_logged_user")
    assert identity.status_code == 200, identity.text
    assert identity.json()["message"] == "probe-business@example.invalid"
    replay = business.get(callback.path + "?" + callback.query)
    assert replay.status_code == 403

print("C1-R2-R3 PASS: native OAuth client→authorize→callback→business session on /desk")
