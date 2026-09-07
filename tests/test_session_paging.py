"""Listing sessions must cost the page, not the archive.

Every conversation the list shows is still authorized exactly as before (provenance is
re-checked before a title or a timestamp is exposed); what changed is that conversations
outside the page are no longer loaded to produce it."""

from frappe_app.dsherp_bridge import paging


def test_a_page_costs_the_page_and_a_look_ahead_not_the_whole_archive():
    loaded = []

    def fetch(limit, offset):
        return [f"c{index}" for index in range(offset, min(offset + limit, 500))]

    def authorize(name):
        loaded.append(name)
        return {"id": name}

    page, has_more = paging.page(fetch, authorize, limit=10, offset=0)
    assert [row["id"] for row in page] == [f"c{index}" for index in range(10)]
    assert has_more is True
    assert len(loaded) <= 11, f"loaded {len(loaded)} conversations to show 10"


def test_rows_the_reader_may_not_see_are_skipped_and_the_page_is_still_full():
    def fetch(limit, offset):
        return [f"c{index}" for index in range(offset, min(offset + limit, 100))]

    def authorize(name):
        if int(name[1:]) % 2:
            raise PermissionError(name)
        return {"id": name}

    page, has_more = paging.page(fetch, authorize, limit=5, offset=0)
    assert [row["id"] for row in page] == ["c0", "c2", "c4", "c6", "c8"]
    assert has_more is True


def test_the_last_page_says_so_and_an_empty_source_is_not_an_error():
    def fetch(limit, offset):
        return [f"c{index}" for index in range(offset, min(offset + limit, 3))]

    page, has_more = paging.page(fetch, lambda name: {"id": name}, limit=10, offset=0)
    assert len(page) == 3 and has_more is False
    empty, more = paging.page(lambda limit, offset: [], lambda name: {"id": name}, limit=10, offset=0)
    assert empty == [] and more is False


def test_an_offset_past_the_end_returns_nothing_rather_than_the_last_page():
    def fetch(limit, offset):
        return [f"c{index}" for index in range(offset, min(offset + limit, 4))]

    page, has_more = paging.page(fetch, lambda name: {"id": name}, limit=10, offset=10)
    assert page == [] and has_more is False


def test_a_reader_who_may_see_nothing_gets_an_empty_page_not_an_endless_scan():
    attempts = []

    def fetch(limit, offset):
        attempts.append((limit, offset))
        return [f"c{index}" for index in range(offset, min(offset + limit, 10_000))]

    def authorize(name):
        raise PermissionError(name)

    page, has_more = paging.page(fetch, authorize, limit=10, offset=0)
    assert page == []
    assert len(attempts) <= paging.MAX_ROUNDS, "the scan is bounded"
