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
# The marks `run_events.sanitize` leaves wherever it dropped something. A key built from a
# value that lost content cannot be compared with another: the two calls may differ exactly
# in what was dropped.
TRUNCATED = '…[truncated]'
DEPTH_CUT = '…[depth]'
LOSSY = (TRUNCATED, DEPTH_CUT)


def comparable(key):
    """Whether two calls can be compared at all.

    Arguments are stored through `sanitize`, which cuts long strings, long dicts, long lists
    and deep nesting. Two calls differing only inside a part that was cut would look identical
    here — so a key carrying any of those marks is treated as **not comparable** and never
    counts towards a repeat.

    Deliberately biased towards missing a loop rather than inventing one: this judgement stops
    a person's run. A false negative costs a few more calls, which the budget already caps. A
    false positive ends work that was going fine — a model fixing line 55 of a 60-row proposal
    and then line 58 sends three genuinely different arguments, and before the marks existed
    all three collapsed to the same key.
    """
    return not any(mark in key for mark in LOSSY)


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
