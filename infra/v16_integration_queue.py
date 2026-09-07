"""Queue hygiene for the four isolated v16 validation Sites."""

import json
import re
import subprocess


PURGE_TARGETS = (
    ("dsherp-validation-backend-1", "dsherp-validation.localhost"),
    ("dsherp-validation-backend-1", "dsherp-daily.localhost"),
    ("dsherp-validation-beta-backend-1", "dsherp-beta.localhost"),
    ("dsherp-validation-platform-backend-1", "dsherp-platform.localhost"),
    # The throwaway native-test Sites sit on the same two benches, so their jobs land in the
    # same redis queues as the development ones and count against the same insert cap.
    ("dsherp-validation-backend-1", "dsherp-test.localhost"),
    ("dsherp-validation-platform-backend-1", "dsherp-platform-test.localhost"),
)
INSPECT_COMMAND = [
    "docker","exec","dsherp-validation-backend-1","bench","--site",
    "dsherp-validation.localhost","execute","frappe.utils.background_jobs.get_jobs",
    "--kwargs",'{"key":"method"}',
]
_PURGEABLE_METHODS = {
    "frappe.ping",
    "frappe.core.doctype.user.user.create_contact",
    "frappe.model.delete_doc.delete_dynamic_links",
}
# `bench migrate` enqueues the website route-index rebuild and the setup wizard enqueues the
# timezone write, both as bare function objects, so the queue reports repr(f) rather than a
# dotted path. Both are idempotent housekeeping on a synthetic Site, and the timezone is
# already written synchronously (checked on dsherp-test.localhost: Asia/Shanghai was set
# before this job ever ran). Only these two names are accepted - anything else is still an
# unexpected job, because this module refuses to purge what it cannot name.
_PURGEABLE_FUNCTIONS = {"build_index_for_all_routes", "set_timezone"}
_FUNCTION_REPR = re.compile(r"^<function (?P<name>[A-Za-z_][A-Za-z0-9_]*) at 0x[0-9a-f]+>$")


def _purgeable(method):
    if method in _PURGEABLE_METHODS:
        return True
    match = _FUNCTION_REPR.match(method) if isinstance(method, str) else None
    return bool(match) and match.group("name") in _PURGEABLE_FUNCTIONS



def _inspect(run):
    """What the shared queue holds, or a RuntimeError that says where to look.

    Every module in the integration suite asks this before its first test, so a stack that
    cannot answer used to surface as one `CalledProcessError` per remaining test - a hundred
    identical tracebacks naming nothing. The most common cause by far is that a container is
    simply gone: the one database that serves every Site runs with `restart: "no"`, so once the
    kernel kills it at its memory limit with 137 it stays dead until someone starts it."""
    inspection=run(INSPECT_COMMAND,check=False,capture_output=True,text=True,timeout=60)
    if inspection.returncode:
        tail="\n".join(((inspection.stderr or "")+"\n"+(inspection.stdout or "")).strip().splitlines()[-6:])
        raise RuntimeError(
            f"读不到隔离栈的共享队列（退出码 {inspection.returncode}）。这几乎总是栈本身的问题，不是队列的："
            "先看 `docker ps -a --filter name=dsherp-validation-`；退出码 137 的容器是被内存上限杀掉的，"
            "上限就是 infra/compose.validation.yml 里该服务的 mem_limit，`restart: \"no\"` 决定了它不会自愈。"
            f"命令最后的输出：\n{tail}")
    try:
        jobs=json.loads(inspection.stdout) if inspection.stdout.strip() else {}
    except (TypeError,json.JSONDecodeError) as error:
        raise RuntimeError("Invalid validation queue inspection") from error
    if not isinstance(jobs,dict):raise RuntimeError("Invalid validation queue inspection")
    return jobs


def validation_queue_depth(*, run=subprocess.run):
    """Queued job count across the validation Sites. Frappe refuses new inserts at 600."""
    jobs=_inspect(run)
    return sum(len(methods) for methods in jobs.values() if isinstance(methods,list))


def purge_validation_jobs_if_backlogged(threshold=200, *, run=subprocess.run):
    """Nothing consumes the queue during an integration run: every synthetic document
    deleted by a test leaves a `delete_dynamic_links` job behind. Left alone they cross
    Frappe's 600 cap mid-suite and the remaining tests fail on QueueOverloaded."""
    if validation_queue_depth(run=run)<threshold:return None
    return purge_validation_jobs(run=run)


def purge_validation_jobs(*, run=subprocess.run):
    jobs=_inspect(run)
    sites={site for _,site in PURGE_TARGETS}
    if (not isinstance(jobs,dict) or not set(jobs)<=sites
            or any(not isinstance(methods,list) or not all(_purgeable(method) for method in methods)
                   for methods in jobs.values())):
        raise RuntimeError("Unexpected validation queue jobs")
    purged={}
    for container,site in PURGE_TARGETS:
        command=["docker","exec",container,"bench","purge-jobs","--site",site]
        result=run(command,check=True,capture_output=True,text=True,timeout=60)
        match=re.fullmatch(r"Purged (\d+) jobs\n?",result.stdout)
        if not match:
            raise RuntimeError(f"Invalid purge-jobs result for {site}")
        purged[site]=int(match.group(1))
    return purged
