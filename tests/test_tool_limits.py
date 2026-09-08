"""The numbers the server applies and the numbers the descriptions state are one table.

The drift this prevents is not hypothetical: before this module, `read_tools.py` told the
model "up to 100 rows" while `api.py` returned 20 for the name and no-argument branches. A
model plans around what the description says, so a wrong description is worse than none.
"""
import re
from pathlib import Path

from dsherp import tool_limits

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTIONS = ('describe_read_schema', 'describe_read_record', 'describe_search')


def _body(text):
    return text.split('"""', 2)[2]


def test_host_and_bridge_limit_modules_are_identical():
    """The container has no Frappe and the Site has no `dsherp`; neither can import the
    other's copy, so the copies are what keep one table."""
    host = (ROOT / 'dsherp/tool_limits.py').read_text(encoding='utf-8')
    bridge = (ROOT / 'frappe_app/dsherp_bridge/tool_limits.py').read_text(encoding='utf-8')
    assert _body(host) == _body(bridge), 'the two copies of the tool limits differ'


def test_every_limit_is_a_positive_int():
    assert tool_limits.LIMITS
    for key, value in tool_limits.LIMITS.items():
        assert isinstance(value, int) and not isinstance(value, bool), key
        assert value > 0, key


def test_every_integer_in_a_description_comes_from_the_table():
    """A behaviour assertion, not a source-text one: whatever the sentences end up saying,
    every number in them has to be one the server actually applies."""
    allowed = {str(value) for value in tool_limits.LIMITS.values()}
    for name in DESCRIPTIONS:
        text = getattr(tool_limits, name)()
        numbers = set(re.findall(r'\d+', text))
        assert numbers, f'{name} states no limit at all'
        assert numbers <= allowed, f'{name} states {sorted(numbers - allowed)}, which no limit matches'


def test_the_search_description_states_both_page_lengths_apart():
    """The two branches really do differ (100 with filters, 20 otherwise); a description that
    gave one number for both is the drift this module exists to end."""
    text = tool_limits.describe_search()
    assert str(tool_limits.LIMITS['search_page_length']) in text
    assert str(tool_limits.LIMITS['search_name_page_length']) in text
    assert 'after_name' in text


def test_the_record_description_says_what_is_omitted_by_default():
    text = tool_limits.describe_read_record()
    assert 'children' in text and 'after_idx' in text
    assert str(tool_limits.LIMITS['record_max_bytes']) in text


def test_the_schema_description_says_child_tables_are_not_expanded_by_default():
    text = tool_limits.describe_read_schema()
    assert 'tables' in text and 'next_after_fieldname' in text
