"""What the read tools will and will not return, and the sentences that say so.

Two copies, byte for byte identical below this docstring: the container has no Frappe and the
Site has no `dsherp` package, so neither can import the other's. A test asserts they have not
drifted — the same discipline `usage.py` already uses, and for the same reason: a description
that promises one page length while the server applies another is worse than no description,
because the model plans around it.

Deliberately **not** fetched from the server at runtime. The MCP server is started over stdio
with `failOnStartupError: true`, so a backend hiccup during tool construction would take the
whole run down — a description is not worth that.

Every number in a tool description is interpolated from this table. Today's drift is exactly
what that prevents: `read_tools.py` says "up to 100 rows" while `api.py` returns 20 for the
name and no-argument branches.

Kept byte-identical to dsherp/tool_limits.py below this docstring (a test asserts it):
the Site enforces these numbers and the container's tool descriptions state them, so a
drift would have the model planning around a page length nobody applies.
"""

LIMITS = {
    # What the model may receive in one tool result, as FastMCP serialises it.
    'record_max_bytes': 16384,
    'schema_max_bytes': 16384,
    # Page lengths the server actually applies, per branch of search_records.
    'search_page_length': 100,
    'search_name_page_length': 20,
    # Child rows returned per expanded table, and how many tables may be expanded at once.
    'child_rows_per_page': 20,
    'max_child_tables': 5,
    # Argument ceilings the server already enforced before this module existed.
    'max_filter_fields': 20,
    'max_in_values': 100,
    'max_result_fields': 20,
    'query_max_chars': 140,
}


def describe_read_schema():
    return (
        'Read the field structure of one business object allowed by policy. '
        f'Child tables are listed but not expanded unless named in `tables` '
        f'(at most {LIMITS["max_child_tables"]}). '
        f'Results are capped at {LIMITS["schema_max_bytes"]} bytes; when truncated the reply '
        'says so and returns `next_after_fieldname` to continue from. A wide table\'s columns '
        'page too - `columns_truncated` carries `next_after_child_fieldname` for `child_after`.'
    )


def describe_read_record():
    return (
        'Read one record allowed for the current business user. '
        'By default only fields that have a value are returned, and child tables are reported '
        'as row counts rather than expanded - name the ones you need in `children` '
        f'(at most {LIMITS["max_child_tables"]}, {LIMITS["child_rows_per_page"]} rows each). '
        f'Results are capped at {LIMITS["record_max_bytes"]} bytes; when a table is truncated '
        'the reply says so and returns a cursor for `after_idx`. '
        '`routes` lists the server-owned next steps this exact record is ready for, and for '
        'the rest why not - use those names with erp_propose_make.'
    )


def describe_search():
    return (
        'Search records of one business object. '
        f'With `filters` (at most {LIMITS["max_filter_fields"]} fields, `in` lists at most '
        f'{LIMITS["max_in_values"]} values) this returns up to {LIMITS["search_page_length"]} rows '
        f'and the columns named in `fields` (at most {LIMITS["max_result_fields"]}). '
        f'With `query` (at most {LIMITS["query_max_chars"]} characters) or with neither, it '
        f'returns up to {LIMITS["search_name_page_length"]} rows of name and modified only. '
        'Rows come back ordered by name; pass the last name you saw as `after_name` to continue.'
    )
