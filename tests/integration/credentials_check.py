"""Does a stored actor profile still authenticate, and if not, why not.

Three answers, two of them actionable in opposite ways: a key the Site refuses (401/403, or
one it maps to someone else) is reissued through the provisioner; a Site that cannot be
reached, answers with another status, or does not speak JSON is a broken stack and the run
stops naming the site and its port - reissuing into it would rewrite .runtime/erp-*.json
against a Site that may come back holding the old key.
"""
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

OK, UNAUTHENTICATED, UNREACHABLE = 'ok', 'unauthenticated', 'unreachable'
PROBE = '/api/method/frappe.auth.get_logged_user'


def default_opener():
    return build_opener(ProxyHandler({}))


def classify(profile, *, opener=None, timeout=15):
    """('ok' | 'unauthenticated' | 'unreachable', detail)"""
    opener = opener or default_opener()
    request = Request(profile['base_url'] + PROBE, headers={
        'X-Frappe-Site-Name': profile['site'],
        'Authorization': 'token ' + profile['api_key'] + ':' + profile['api_secret']})
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            return UNAUTHENTICATED, f'HTTP {error.code}'
        return UNREACHABLE, f'HTTP {error.code}'
    except OSError as error:  # URLError, ConnectionRefusedError and socket.timeout are all OSError
        return UNREACHABLE, f'{type(error).__name__}: {getattr(error, "reason", error)}'
    except ValueError as error:
        return UNREACHABLE, f'应答不是 JSON：{error}'
    if not isinstance(payload, dict):
        return UNREACHABLE, '应答不是对象'
    if payload.get('message') == profile['user']:
        return OK, profile['user']
    return UNAUTHENTICATED, f'站点把这把密钥认作 {payload.get("message")!r}'
