"""Three identical calls is a loop; two is persistence; a truncated one is not comparable."""
from dsherp import loop_guard


def test_two_entries_are_not_enough():
    assert not loop_guard.repeats(['a'], 'a')
    assert not loop_guard.repeats([], 'a')


def test_three_identical_calls_repeat():
    assert loop_guard.repeats(['a', 'a'], 'a')


def test_a_different_argument_resets_the_streak():
    assert not loop_guard.repeats(['a', 'b'], 'a')
    assert not loop_guard.repeats(['b', 'a'], 'a')


def test_only_the_most_recent_calls_count():
    """An identical call ten steps ago is not a loop; the run did other things since."""
    assert not loop_guard.repeats(['a', 'a', 'b', 'c'], 'a')
    assert loop_guard.repeats(['b', 'c', 'a', 'a'], 'a')


def test_truncated_arguments_are_never_comparable():
    """`sanitize` cuts long values short, so two calls differing only inside the cut part
    would look identical. This judgement stops a person's run — a missed loop costs a few
    calls the budget already caps; an invented one ends work that was going fine."""
    long_key = '["erp_read_record", {"name": "x…[truncated]"}]'
    assert not loop_guard.comparable(long_key)
    assert not loop_guard.repeats([long_key, long_key], long_key)
    assert not loop_guard.repeats(['a', 'a'], long_key)
    assert not loop_guard.repeats([long_key, 'a'], 'a')


def test_the_window_is_the_declared_limit():
    assert loop_guard.LOOP_LIMIT == 3
    assert loop_guard.repeats(['a'], 'a', window=2)
    assert not loop_guard.repeats([], 'a', window=2)


def _body(text):
    return text.split('"""', 2)[2]


def test_host_and_bridge_copies_are_identical():
    """The container has no Frappe and the Site has no `dsherp`; neither can import the
    other's copy, so the copies are what keep one judgement. Same precedent as `usage.py`
    and `tool_limits.py`."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    host = (root / 'dsherp/loop_guard.py').read_text(encoding='utf-8')
    bridge = (root / 'frappe_app/dsherp_bridge/loop_guard.py').read_text(encoding='utf-8')
    assert _body(host) == _body(bridge), 'the two copies of the loop judgement differ'
