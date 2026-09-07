"""A container script that exits 0 without printing anything did not do what was asked.

The first nightly ran the from-zero provisioning on a real Linux runner and died eleven
minutes in, inside this script, on `json.loads('')`. The subprocess had returned 0, so the
only check there was - the return code - let it through, and the failure surfaced as the json
module complaining about column 1. Exit 0 proves nothing ran badly; it does not prove anything
ran. Where output is the result, missing output is a failure, and it has to say so itself."""
import subprocess

import pytest

from infra import initialize_daily_synthetic as daily


def _result(returncode=0, stdout='', stderr=''):
    def run(command, **options):
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    return run


def test_output_is_returned_when_the_script_actually_printed_something(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', _result(stdout='{"setup_complete": false}\n'))
    assert daily.execute('c', 's', 'print(1)') == '{"setup_complete": false}\n'


def test_a_non_zero_exit_still_names_the_container(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', _result(returncode=1, stderr='boom'))
    with pytest.raises(RuntimeError, match='dsherp-validation-backend-1'):
        daily.execute('dsherp-validation-backend-1', 's', 'print(1)')


def test_exit_zero_with_no_output_is_a_failure_that_names_where_it_happened(monkeypatch):
    """This is the shape the first nightly hit: returncode 0, empty stdout, and a JSONDecodeError
    several frames away from the call that actually went wrong."""
    monkeypatch.setattr(subprocess, 'run', _result(stdout='   \n', stderr='some warning\n'))
    with pytest.raises(RuntimeError) as error:
        daily.execute('dsherp-validation-backend-1', 'dsherp-daily.localhost', "print(json.dumps({}))")
    message = str(error.value)
    assert 'dsherp-validation-backend-1' in message and 'dsherp-daily.localhost' in message
    assert 'some warning' in message, '容器说了什么要留在消息里'
