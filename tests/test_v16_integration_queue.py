import json
from subprocess import CompletedProcess

from infra.v16_integration_queue import INSPECT_COMMAND, PURGE_TARGETS, purge_validation_jobs


def test_all_four_validation_sites_are_purged_across_their_own_backends():
    calls=[]
    def run(command, **options):
        calls.append((command,options))
        if command==INSPECT_COMMAND:
            return CompletedProcess(command,0,json.dumps({
                "dsherp-validation.localhost":["frappe.model.delete_doc.delete_dynamic_links"],
                "dsherp-beta.localhost":["frappe.core.doctype.user.user.create_contact"],
            }),"")
        return CompletedProcess(command,0,"Purged 3 jobs\n","")

    assert purge_validation_jobs(run=run)=={
        "dsherp-validation.localhost":3,
        "dsherp-daily.localhost":3,
        "dsherp-beta.localhost":3,
        "dsherp-platform.localhost":3,
    }
    assert [command for command,_ in calls]==[INSPECT_COMMAND]+[
        ["docker","exec",container,"bench","purge-jobs","--site",site]
        for container,site in PURGE_TARGETS
    ]
    assert all(options=={"check":True,"capture_output":True,"text":True,"timeout":60}
               for _,options in calls)


def test_unexpected_purge_output_fast_fails():
    def run(command, **_options):
        if command==INSPECT_COMMAND:return CompletedProcess(command,0,"{}","")
        return CompletedProcess(command,0,"unexpected\n","")
    try:
        purge_validation_jobs(run=run)
    except RuntimeError as error:
        assert str(error)=="Invalid purge-jobs result for dsherp-validation.localhost"
    else:
        raise AssertionError("invalid purge result accepted")


def test_unknown_queue_job_fast_fails_before_purge():
    calls=[]
    def run(command, **_options):
        calls.append(command)
        return CompletedProcess(command,0,json.dumps({
            "dsherp-validation.localhost":["unknown.production.job"],
        }),"")
    try:
        purge_validation_jobs(run=run)
    except RuntimeError as error:
        assert str(error)=="Unexpected validation queue jobs"
    else:
        raise AssertionError("unknown queue job was purged")
    assert calls==[INSPECT_COMMAND]


def test_empty_bench_output_means_the_shared_queue_is_empty():
    def run(command, **_options):
        if command==INSPECT_COMMAND:return CompletedProcess(command,0,"\n","")
        return CompletedProcess(command,0,"Purged 0 jobs\n","")
    assert set(purge_validation_jobs(run=run).values())=={0}
