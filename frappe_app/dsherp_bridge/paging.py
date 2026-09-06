"""Paging that costs the page.

Listing sessions used to load every conversation a user owns in order to show ten of them:
each one was fully read and re-authorized just to produce a title, a timestamp and a count.
The authorization did not change - a conversation is still fully re-checked before anything
about it is exposed - but only the conversations on the page are.

Pure: the caller supplies the query and the authorization, so it is testable without Frappe.
"""
MAX_ROUNDS = 20


def page(fetch, authorize, *, limit, offset):
    """(rows, has_more) for one page.

    `fetch(limit, offset)` returns candidate names, newest first; `authorize(name)` returns the
    row to show or raises to say this reader may not see it. Rows a reader may not see are
    skipped and the page is filled from behind them, bounded by MAX_ROUNDS so an archive full
    of unreadable rows cannot turn one request into a full scan."""
    rows, seen, cursor = [], 0, 0
    wanted = offset + limit
    for _ in range(MAX_ROUNDS):
        candidates = fetch(max(wanted - seen + 1, 1), cursor)
        if not candidates:
            break
        cursor += len(candidates)
        for name in candidates:
            try:
                row = authorize(name)
            except Exception:
                continue
            seen += 1
            if seen > offset:
                rows.append(row)
            if len(rows) > limit:
                return rows[:limit], True
        if seen >= wanted + 1:
            break
    return rows[:limit], len(rows) > limit
