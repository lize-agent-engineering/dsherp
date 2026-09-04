import json

import pytest

from infra.load_runs import (
    load_alpha_actors,
    percentile95,
    require_worker_stopped,
    summarize_run,
    validate_load,
)

READER = "dsherp-reader@example.invalid"
DENIED = "dsherp-denied@example.invalid"
WRITER = "dsherp-writer@example.invalid"
ALPHA = "dsherp-validation.localhost"
DAILY = "dsherp-daily.localhost"


def test_require_worker_stopped_fastfails_on_live_pid(tmp_path):
    pid_path = tmp_path / "agent-worker.pid"
    pid_path.write_text(str(__import__("os").getpid()))
    with pytest.raises(RuntimeError, match="resident worker"):
        require_worker_stopped(pid_path)
    require_worker_stopped(tmp_path / "missing.pid")


def test_percentile95_uses_nearest_rank_and_requires_twenty_samples():
    assert percentile95([index / 100 for index in range(1, 21)]) == 0.19
    with pytest.raises(RuntimeError):
        percentile95([0.1] * 19)


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _erp_users(reader=READER, denied=DENIED):
    return {
        "reader": {
            "user": reader,
            "api_key": "reader-key",
            "api_secret": "reader-secret",
            "base_url": "http://127.0.0.1:18081",
            "site": ALPHA,
        },
        "denied": {
            "user": denied,
            "api_key": "denied-key",
            "api_secret": "denied-secret",
            "base_url": "http://127.0.0.1:18081",
            "site": ALPHA,
        },
    }


def _writer_profile(user=WRITER):
    return {
        "user": user,
        "password": "synthetic-writer-password",
        "item": "DSHERP-HITL-ITEM",
        "baseline": "2026-09-04 00:00:00.000000",
    }


def _event(seq, kind, recorded_at):
    return {
        "name": f"evt-{seq:06d}",
        "seq": seq,
        "kind": kind,
        "source": "server",
        "error_class": "",
        "payload": {},
        "recorded_at": recorded_at,
    }


def _events(queued_at, claimed_at, finished_at):
    return [
        _event(1, "queued", queued_at),
        _event(2, "claimed", claimed_at),
        _event(3, "finished", finished_at),
    ]


def test_load_alpha_actors_returns_reader_denied_and_writer(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    actors = load_alpha_actors(users_path, writer_path)
    users = [actor["user"] for actor in actors]

    assert users == [READER, DENIED, WRITER]
    assert len(users) == 3
    assert len(set(users)) == 3


def test_load_alpha_actors_fastfails_when_reader_is_missing(tmp_path):
    payload = _erp_users()
    del payload["reader"]
    users_path = _write_json(tmp_path / "erp-users.json", payload)
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_load_alpha_actors_fastfails_when_denied_is_missing(tmp_path):
    payload = _erp_users()
    del payload["denied"]
    users_path = _write_json(tmp_path / "erp-users.json", payload)
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_load_alpha_actors_fastfails_when_writer_profile_is_missing(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, tmp_path / "context-writer.json")


def test_load_alpha_actors_fastfails_when_users_are_duplicated(tmp_path):
    users_path = _write_json(tmp_path / "erp-users.json", _erp_users())
    writer_path = _write_json(tmp_path / "context-writer.json", _writer_profile(user=READER))

    with pytest.raises(RuntimeError):
        load_alpha_actors(users_path, writer_path)


def test_summarize_run_uses_recorded_at_and_keeps_strict_seq_kinds():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.500000"),
        _event(3, "runtime_started", "2026-09-04 10:00:03.000000"),
        _event(4, "finished", "2026-09-04 10:00:10.000000"),
    ]

    row = summarize_run("run-alpha-1", ALPHA, READER, "Succeeded", events)

    assert row["run_id"] == "run-alpha-1"
    assert row["site"] == ALPHA
    assert row["owner"] == READER
    assert row["status"] == "Succeeded"
    assert row["queued_to_claimed_seconds"] == 2.5
    assert row["total_seconds"] == 10.0
    assert row["kinds"] == ["queued", "claimed", "runtime_started", "finished"]
    assert row["queued_at"] == "2026-09-04 10:00:00.000000"
    assert row["claimed_at"] == "2026-09-04 10:00:02.500000"
    assert row["finished_at"] == "2026-09-04 10:00:10.000000"


def test_summarize_run_fastfails_when_queued_is_missing():
    events = [
        _event(1, "claimed", "2026-09-04 10:00:02.000000"),
        _event(2, "finished", "2026-09-04 10:00:10.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_claimed_is_missing():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "finished", "2026-09-04 10:00:10.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_terminal_event_is_missing():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def test_summarize_run_fastfails_when_seq_is_disordered():
    events = [
        _event(1, "queued", "2026-09-04 10:00:00.000000"),
        _event(3, "finished", "2026-09-04 10:00:10.000000"),
        _event(2, "claimed", "2026-09-04 10:00:02.000000"),
    ]
    with pytest.raises(RuntimeError):
        summarize_run("run-x", ALPHA, READER, "Succeeded", events)


def _row(run_id, site, owner, status, queued_at, claimed_at, finished_at):
    return summarize_run(
        run_id,
        site,
        owner,
        status,
        _events(queued_at, claimed_at, finished_at),
    )


def _valid_load_rows():
    return [
        _row(
            "alpha-1",
            ALPHA,
            READER,
            "Succeeded",
            "2026-09-04 10:00:00.000000",
            "2026-09-04 10:00:01.000000",
            "2026-09-04 10:00:08.000000",
        ),
        _row(
            "alpha-2",
            ALPHA,
            DENIED,
            "Succeeded",
            "2026-09-04 10:00:00.100000",
            "2026-09-04 10:00:09.000000",
            "2026-09-04 10:00:16.000000",
        ),
        _row(
            "alpha-3",
            ALPHA,
            WRITER,
            "Succeeded",
            "2026-09-04 10:00:00.200000",
            "2026-09-04 10:00:17.000000",
            "2026-09-04 10:00:24.000000",
        ),
        _row(
            "daily-1",
            DAILY,
            READER,
            "Succeeded",
            "2026-09-04 10:00:00.000000",
            "2026-09-04 10:00:01.400000",
            "2026-09-04 10:00:07.000000",
        ),
    ]


def test_validate_load_accepts_terminal_alpha_order_and_parallel_daily():
    validate_load(_valid_load_rows(), ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_a_run_is_not_terminal():
    rows = _valid_load_rows()
    rows[0] = _row(
        "alpha-1",
        ALPHA,
        READER,
        "Running",
        "2026-09-04 10:00:00.000000",
        "2026-09-04 10:00:01.000000",
        "2026-09-04 10:00:08.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_any_run_failed():
    rows = _valid_load_rows()
    rows[2] = _row(
        "alpha-3",
        ALPHA,
        WRITER,
        "Failed",
        "2026-09-04 10:00:00.200000",
        "2026-09-04 10:00:17.000000",
        "2026-09-04 10:00:24.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_alpha_claimed_order_differs():
    with pytest.raises(RuntimeError):
        validate_load(_valid_load_rows(), ["alpha-2", "alpha-1", "alpha-3"], "daily-1", 0.4)


def test_validate_load_accepts_next_claim_after_needs_input_terminal_before_finished_event():
    rows = _valid_load_rows()
    rows[1] = _row(
        "alpha-2",
        ALPHA,
        DENIED,
        "NeedsInput",
        "2026-09-04 10:00:00.100000",
        "2026-09-04 10:00:02.000000",
        "2026-09-04 10:00:09.000000",
    )
    validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_daily_claimed_after_second_alpha():
    rows = _valid_load_rows()
    rows[3] = _row(
        "daily-1",
        DAILY,
        READER,
        "Succeeded",
        "2026-09-04 10:00:00.000000",
        "2026-09-04 10:00:09.500000",
        "2026-09-04 10:00:12.000000",
    )
    with pytest.raises(RuntimeError):
        validate_load(rows, ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 0.4)


def test_validate_load_fastfails_when_run_status_p95_is_not_under_one():
    with pytest.raises(RuntimeError):
        validate_load(_valid_load_rows(), ["alpha-1", "alpha-2", "alpha-3"], "daily-1", 1.0)
