"""Real isolated ERP HTTP fixtures; no production site or model credentials."""
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from infra.v16_integration_queue import purge_validation_jobs, purge_validation_jobs_if_backlogged

_SEED_WORKER_HEARTBEAT = """
import os,frappe
from frappe.utils import now_datetime
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
value=now_datetime().isoformat()
frappe.cache().set_value('dsherp_worker_heartbeat',value,expires_in_sec=3600)
written=frappe.cache().get_value('dsherp_worker_heartbeat')
if written!=value:raise RuntimeError('dsherp_worker_heartbeat was not written')
print(written)
frappe.destroy()
"""


def seed_validation_worker_heartbeat(*, run=subprocess.run):
    result = run(
        [
            "docker",
            "exec",
            "-i",
            "dsherp-validation-backend-1",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=_SEED_WORKER_HEARTBEAT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not result.stdout.strip():
        raise RuntimeError("Failed to seed validation worker heartbeat")
    return result.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def validation_queue_backlog():
    """Keep the shared queue below Frappe's insert cap for the length of the suite."""
    purge_validation_jobs_if_backlogged()
    yield


@pytest.fixture(scope="session", autouse=True)
def validation_queue_hygiene():
    purge_validation_jobs()
    seed_validation_worker_heartbeat()
    try:
        yield
    finally:
        purge_validation_jobs()


@pytest.fixture(autouse=True)
def validation_worker_heartbeat():
    seed_validation_worker_heartbeat()


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
