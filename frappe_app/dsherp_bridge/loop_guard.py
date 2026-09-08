"""When a run is repeating itself, and — just as important — when it only looks like it is.

The Site's copy. The container has no Frappe and this Site has no `dsherp` package, so
neither deployment unit can import the other's copy — the same reason `usage.py` and
`tool_limits.py` exist twice, and `tests/test_loop_guard.py` asserts the two bodies are
identical.

A model that calls the same tool with the same arguments over and over is not making
progress, and every round costs a paid call. Three in a row is the point at which continuing
is certainly waste rather than possibly persistence.

Pure functions so the judgement can be tested without a Site: the server passes in the last
few calls it recorded and asks whether this one repeats them.
"""

LOOP_LIMIT = 3
# The marker `run_events.sanitize` leaves when it cuts a long value short.
TRUNCATED = '…[truncated]'


def comparable(key):
    """Whether two calls can be compared at all.

    Arguments are stored through `sanitize`, which truncates long values. Two calls whose
    arguments differ only inside the part that was cut would look identical here — so a
    truncated key is treated as **not comparable** and never counts towards a repeat.

    Deliberately biased towards missing a loop rather than inventing one: this judgement
    stops a person's run. A false negative costs a few more calls, which the budget already
    caps. A false positive ends work that was going fine.
    """
    return TRUNCATED not in key


def repeats(previous, key, window=LOOP_LIMIT):
    """Whether `key` makes it `window` identical calls in a row.

    `previous` is the keys of the calls before this one, most recent last.
    """
    if not comparable(key):
        return False
    needed = window - 1
    if needed <= 0:
        return True
    recent = list(previous)[-needed:]
    if len(recent) < needed:
        return False
    return all(comparable(item) and item == key for item in recent)
