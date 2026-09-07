"""Perform the product's own login so the platform holds a live business credential.

Short-lived credentials (S2) are renewed by logging in. Any suite that reads a business Site
through the platform needs a member who has logged in recently, and the honest way to arrange
that is to log in - not to write a credential into the database behind the product's back."""
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from test_platform_identity import platform_client

ENTERPRISES = {
    'alpha': 'http://localhost:18082',
    'beta': 'http://preview.localhost:18085',
    'daily': 'http://daily.localhost:18086',
}


class _Consent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and 'frappe.integrations.oauth2.approve' in attrs.get('action', ''):
            self.action = attrs['action']
        if tag == 'input' and attrs.get('name') == 'csrf_token':
            self.csrf = attrs.get('value')


def establish(business_url, actor='member'):
    """One real OAuth round trip. Returns the business client, already logged in."""
    with httpx.Client(base_url=business_url, trust_env=False, timeout=30) as business, \
            platform_client(actor) as platform:
        start = business.get('/api/method/dsherp_bridge.sso.start')
        assert start.status_code == 302, start.text
        target = urlparse(start.headers['location'])
        authorization = platform.get(target.path + '?' + target.query)
        if authorization.status_code == 200:
            form = _Consent()
            form.feed(authorization.text)
            assert form.action and form.csrf, 'Native OAuth consent form missing'
            authorization = platform.post(form.action, data={'csrf_token': form.csrf})
        assert authorization.status_code == 302, authorization.text
        callback = urlparse(authorization.headers['location'])
        result = business.get(callback.path + '?' + callback.query)
        assert result.status_code == 302, result.text
        return result


def establish_all(actor='member'):
    return {name: establish(url, actor).status_code for name, url in ENTERPRISES.items()}
