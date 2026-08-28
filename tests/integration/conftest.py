"""Real isolated ERP HTTP fixtures; no production site or model credentials."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pytest


@pytest.fixture
def erp():
    path = Path(__file__).resolve().parents[2] / ".runtime" / "erp-users.json"
    if not path.is_file():
        pytest.fail("Isolated ERP credentials missing; provision the validation site first")
    credentials = json.loads(path.read_text())
    opener = build_opener(ProxyHandler({}))

    def request(actor, endpoint, **params):
        profile = credentials["reader" if actor == "guest" else actor]
        headers = {"X-Frappe-Site-Name": profile["site"]}
        if actor != "guest":
            headers["Authorization"] = "token " + profile["api_key"] + ":" + profile["api_secret"]
        url = profile["base_url"] + endpoint + "?" + urlencode(params)
        try:
            with opener.open(Request(url, headers=headers), timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    return request
