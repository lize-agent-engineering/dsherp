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


def test_a_long_list_or_dict_that_was_cut_is_not_comparable():
    """The false positive this guard would otherwise kill a working run with.

    `sanitize` keeps only the first 50 entries of a list or dict. A model fixing line 55 of a
    60-row proposal and then line 58 sends three genuinely different argument dicts whose
    kept halves are identical — before every lossy step left a mark, all three produced the
    same key and the third was refused as a loop, ending a run that was making progress on
    every step.
    """
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dsherp.run_events import sanitize

    def key(arguments):
        return json.dumps(['erp_propose_create', sanitize(arguments)], ensure_ascii=False,
                          sort_keys=True, separators=(',', ':'), default=str)

    rows = [{'item_code': f'I-{i}', 'qty': 1} for i in range(60)]
    first = key({'doctype': 'Sales Order', 'items': rows})
    second = key({'doctype': 'Sales Order',
                  'items': rows[:55] + [{'item_code': 'FIXED', 'qty': 2}] + rows[56:]})
    assert first == second, '前 50 行相同，被裁掉的部分不同——正是会被误判的那种形状'
    assert not loop_guard.comparable(first), '裁掉过内容的键不能参与循环判定'
    assert not loop_guard.repeats([first, first], second)

    wide = {f'f{i}': i for i in range(60)}
    assert not loop_guard.comparable(key({'values': wide}))
    deep = {'a': {'b': {'c': {'d': {'e': {'f': {'g': {'h': 1}}}}}}}}
    assert not loop_guard.comparable(key(deep)), '深到被折叠的结构同样不可比'


def test_a_short_repeated_call_is_still_a_loop():
    """The marks must not turn the guard off: an ordinary repeated call carries none."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dsherp.run_events import sanitize

    key = json.dumps(['erp_read_record', sanitize({'doctype': 'Item', 'name': 'I-1'})],
                     ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert loop_guard.comparable(key)
    assert loop_guard.repeats([key, key], key)
