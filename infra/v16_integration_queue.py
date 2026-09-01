"""Queue hygiene for the four isolated v16 validation Sites."""

import re
import subprocess


PURGE_TARGETS = (
    ("dsherp-validation-backend-1", "dsherp-validation.localhost"),
    ("dsherp-validation-backend-1", "dsherp-daily.localhost"),
    ("dsherp-validation-beta-backend-1", "dsherp-beta.localhost"),
    ("dsherp-validation-platform-backend-1", "dsherp-platform.localhost"),
)


def purge_validation_jobs(*, run=subprocess.run):
    purged={}
    for container,site in PURGE_TARGETS:
        command=["docker","exec",container,"bench","purge-jobs","--site",site]
        result=run(command,check=True,capture_output=True,text=True,timeout=60)
        match=re.fullmatch(r"Purged (\d+) jobs\n?",result.stdout)
        if not match:
            raise RuntimeError(f"Invalid purge-jobs result for {site}")
        purged[site]=int(match.group(1))
    return purged
