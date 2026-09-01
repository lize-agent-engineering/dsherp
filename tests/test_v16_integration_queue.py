from subprocess import CompletedProcess

from infra.v16_integration_queue import PURGE_TARGETS, purge_validation_jobs


def test_all_four_validation_sites_are_purged_across_their_own_backends():
    calls=[]
    def run(command, **options):
        calls.append((command,options))
        return CompletedProcess(command,0,"Purged 3 jobs\n","")

    assert purge_validation_jobs(run=run)=={
        "dsherp-validation.localhost":3,
        "dsherp-daily.localhost":3,
        "dsherp-beta.localhost":3,
        "dsherp-platform.localhost":3,
    }
    assert [command for command,_ in calls]==[
        ["docker","exec",container,"bench","purge-jobs","--site",site]
        for container,site in PURGE_TARGETS
    ]
    assert all(options=={"check":True,"capture_output":True,"text":True,"timeout":60}
               for _,options in calls)


def test_unexpected_purge_output_fast_fails():
    def run(command, **_options):
        return CompletedProcess(command,0,"unexpected\n","")
    try:
        purge_validation_jobs(run=run)
    except RuntimeError as error:
        assert str(error)=="Invalid purge-jobs result for dsherp-validation.localhost"
    else:
        raise AssertionError("invalid purge result accepted")
