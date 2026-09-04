"""Queue hygiene for the four isolated v16 validation Sites."""

import json
import re
import subprocess


PURGE_TARGETS = (
    ("dsherp-validation-backend-1", "dsherp-validation.localhost"),
    ("dsherp-validation-backend-1", "dsherp-daily.localhost"),
    ("dsherp-validation-beta-backend-1", "dsherp-beta.localhost"),
    ("dsherp-validation-platform-backend-1", "dsherp-platform.localhost"),
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


def validation_queue_depth(*, run=subprocess.run):
    """Queued job count across the validation Sites. Frappe refuses new inserts at 600."""
    inspection=run(INSPECT_COMMAND,check=True,capture_output=True,text=True,timeout=60)
    try:
        jobs=json.loads(inspection.stdout) if inspection.stdout.strip() else {}
    except (TypeError,json.JSONDecodeError) as error:
        raise RuntimeError("Invalid validation queue inspection") from error
    if not isinstance(jobs,dict):raise RuntimeError("Invalid validation queue inspection")
    return sum(len(methods) for methods in jobs.values() if isinstance(methods,list))


def purge_validation_jobs_if_backlogged(threshold=200, *, run=subprocess.run):
    """Nothing consumes the queue during an integration run: every synthetic document
    deleted by a test leaves a `delete_dynamic_links` job behind. Left alone they cross
    Frappe's 600 cap mid-suite and the remaining tests fail on QueueOverloaded."""
    if validation_queue_depth(run=run)<threshold:return None
    return purge_validation_jobs(run=run)


def purge_validation_jobs(*, run=subprocess.run):
    inspection=run(INSPECT_COMMAND,check=True,capture_output=True,text=True,timeout=60)
    try:
        jobs=json.loads(inspection.stdout) if inspection.stdout.strip() else {}
    except (TypeError,json.JSONDecodeError) as error:
        raise RuntimeError("Invalid validation queue inspection") from error
    sites={site for _,site in PURGE_TARGETS}
    if (not isinstance(jobs,dict) or not set(jobs)<=sites
            or any(not isinstance(methods,list) or not set(methods)<=_PURGEABLE_METHODS
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
