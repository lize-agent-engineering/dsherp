import json
from subprocess import CompletedProcess

from infra.v16_integration_queue import (INSPECT_COMMAND, PURGE_TARGETS, purge_validation_jobs,
                                        purge_validation_jobs_if_backlogged, validation_queue_depth)


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
    # The inspection judges its own returncode so a dead stack gets one findable message
    # instead of a CalledProcessError per test; the purges still fast-fail on check.
    assert calls[0][1]=={"check":False,"capture_output":True,"text":True,"timeout":60}
    assert all(options=={"check":True,"capture_output":True,"text":True,"timeout":60}
               for _,options in calls[1:])


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


def test_a_stack_that_cannot_answer_names_itself_instead_of_raising_a_bare_subprocess_error():
    """Every module in the integration suite touches the shared queue before its first test.
    When the stack is gone the old code raised CalledProcessError from `check=True`, so one
    dead container produced a hundred-odd identical tracebacks and named nothing. The four-Site
    database has a 1 GiB mem_limit and `restart: no`: it is killed with 137 and stays dead, and
    that is the case this message has to make findable."""
    def run(command, **_options):
        return CompletedProcess(command, 1, "",
                                "Error response from daemon: container dsherp-validation-db-1 is not running")
    for call in (validation_queue_depth, purge_validation_jobs, purge_validation_jobs_if_backlogged):
        try:
            call(run=run)
        except RuntimeError as error:
            message = str(error)
            assert "docker ps -a" in message and "137" in message and "mem_limit" in message, message
            assert "is not running" in message, "守护进程自己的话要留在消息里"
        else:
            raise AssertionError(f"{call.__name__} 接受了一个已经死掉的栈")
