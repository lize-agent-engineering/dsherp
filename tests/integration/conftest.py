"""Real isolated ERP HTTP fixtures; no production site or model credentials."""
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from credentials_check import UNAUTHENTICATED, UNREACHABLE, classify
from infra.v16_integration_queue import purge_validation_jobs, purge_validation_jobs_if_backlogged

INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Every item under this directory is an integration test; the marker is what
    `-m integration` selects and what the junit report groups by."""
    for item in items:
        if INTEGRATION_DIR in Path(item.path).resolve().parents:
            item.add_marker(pytest.mark.integration)

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


@pytest.fixture(autouse=True)
def fixture_credentials_agree_with_the_sites():
    """The synthetic actors' API secrets live in .runtime/erp-*.json. A business credential is
    short-lived (S2): a platform login renews it and a membership revocation kills it - both
    legitimately, both inside this very suite - and the files are left behind. Before each test
    the profiles are tried. One the Site refuses is reissued through the provisioner, the one
    path that puts the Site, the files and the platform binding back on one secret. A Site that
    does not answer is not a credential problem: the run stops here, naming it, because
    reissuing into a Site that may come back holding the old key would rewrite the files for
    nothing."""
    from infra.run_validation_provision import reissue
    path = Path(__file__).resolve().parents[2] / ".runtime" / "erp-users.json"
    if path.is_file():
        profiles = json.loads(path.read_text())
        for actor in ("reader", "denied"):
            if actor not in profiles:
                continue
            state, detail = classify(profiles[actor])
            if state == UNAUTHENTICATED:
                reissue(actor)
            elif state == UNREACHABLE:
                profile = profiles[actor]
                pytest.fail(f"站点 {profile['site']}（{profile['base_url']}）不可达或应答异常：{detail}。"
                            "这不是凭据问题，不重发；先确认 dsherp-validation 栈在运行、backend 端口 18081 可达")
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
