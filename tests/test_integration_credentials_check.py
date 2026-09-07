"""Reissue only a key the Site refuses. A Site that does not answer is a broken stack, and
reissuing into it would rewrite the profile files against a Site that may come back with the
old key intact."""
import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from tests.integration.credentials_check import OK, PROBE, UNAUTHENTICATED, UNREACHABLE, classify

PROFILE = {'user': 'dsherp-reader@example.invalid', 'api_key': 'k', 'api_secret': 's',
           'base_url': 'http://127.0.0.1:18081', 'site': 'dsherp-validation.localhost'}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Opener:
    def __init__(self, outcome):
        self.outcome, self.requests = outcome, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return Response(self.outcome)


def test_a_key_that_answers_as_its_user_is_ok_and_the_probe_carries_site_and_token():
    opener = Opener(json.dumps({'message': PROFILE['user']}).encode())
    assert classify(PROFILE, opener=opener) == (OK, PROFILE['user'])
    request, timeout = opener.requests[0]
    assert request.full_url == PROFILE['base_url'] + PROBE and timeout == 15
    assert request.get_header('X-frappe-site-name') == PROFILE['site']
    assert request.get_header('Authorization') == 'token k:s'


@pytest.mark.parametrize('code', [401, 403])
def test_a_refused_key_is_unauthenticated(code):
    state, detail = classify(PROFILE, opener=Opener(HTTPError('u', code, 'refused', {}, None)))
    assert state == UNAUTHENTICATED and str(code) in detail


def test_a_key_the_site_maps_to_someone_else_is_unauthenticated_too():
    state, detail = classify(PROFILE, opener=Opener(json.dumps({'message': 'Guest'}).encode()))
    assert state == UNAUTHENTICATED and 'Guest' in detail


@pytest.mark.parametrize('outcome', [
    URLError(ConnectionRefusedError(61, 'refused')), socket.timeout('timed out'), OSError('boom'),
    HTTPError('u', 502, 'bad gateway', {}, None), HTTPError('u', 404, 'gone', {}, None),
    b'<html>nginx 502</html>', b'[]',
])
def test_anything_else_is_unreachable_never_a_reason_to_reissue(outcome):
    state, detail = classify(PROFILE, opener=Opener(outcome))
    assert state == UNREACHABLE and detail
