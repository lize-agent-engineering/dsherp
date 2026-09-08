"""Read failed DS Model Run rows from a Frappe container and write eval cases."""

from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dsherp.eval_cases import build_case

PYTHON = "/home/frappe/frappe-bench/env/bin/python"
# evals/run.py stamps every request it sends with this prefix.
EXCLUDE_PREFIX = "eval-"
CASE_PREFIX = "EVAL_CASE\t"
DONE_PREFIX = "EVAL_DONE\t"


def _reader_script(site, status, exclude_request_prefix=EXCLUDE_PREFIX):
    # Anti-backflow: an evaluation run leaves a DS Model Run on the Site that nobody can
    # delete (ds_model_run.on_trash refuses unconditionally). Without this filter the next
    # export would pick up the evaluator's own synthetic runs and re-export them as if they
    # were real failures - the corpus would slowly become a recording of itself.
    return f"""
import json
import os

import frappe

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site={site!r})
frappe.connect()
frappe.set_user('Administrator')
from dsherp_bridge.context_events import list_events

def all_events(run_name):
    page = 1
    items = []
    while True:
        batch = list_events(run_name, page=page, page_length=200)
        items.extend(batch)
        if len(batch) < 200:
            return items
        page += 1

runs = frappe.get_all(
    'DS Model Run',
    filters={{'status': {status!r}, 'request_id': ('not like', {exclude_request_prefix!r} + '%')}},
    fields=['name', 'domain', 'question', 'page_context', 'status', 'error', 'sources', 'creation', 'owner'],
    order_by='creation asc, name asc',
    limit_page_length=0,
)
count = 0
for run in runs:
    events = all_events(run.name)
    proposals = frappe.get_all(
        'DS Operation Proposal',
        filters={{'model_run': run.name}},
        fields=['name', 'status', 'payload'],
        order_by='creation asc, name asc',
        limit_page_length=0,
    )
    payload = {{
        'run': {{
            'name': run.name,
            'domain': run.domain,
            'question': run.question,
            'page_context': run.page_context,
            'status': run.status,
            'error': run.error,
            'sources': run.sources,
            'creation': str(run.creation),
        }},
        'events': events,
        'proposals': proposals,
    }}
    print({CASE_PREFIX!r} + json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    count += 1
print({DONE_PREFIX!r} + json.dumps({{'count': count}}, ensure_ascii=False), flush=True)
frappe.destroy()
"""


def _decode_line(prefix, line):
    try:
        return json.loads(line[len(prefix) :])
    except ValueError as error:
        raise RuntimeError(f"eval export line is not JSON: {line!r}") from error


def parse_export_lines(stdout):
    rows = []
    done = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(CASE_PREFIX):
            rows.append(_decode_line(CASE_PREFIX, line))
            continue
        if line.startswith(DONE_PREFIX):
            if done is not None:
                raise RuntimeError("eval export sent EVAL_DONE more than once")
            done = _decode_line(DONE_PREFIX, line)
            continue
    if done is None:
        raise RuntimeError("eval export finished without EVAL_DONE")
    if done.get("count") != len(rows):
        raise RuntimeError(
            f"eval export count mismatch: done={done.get('count')!r} rows={len(rows)}"
        )
    return rows


def read_container_rows(container, site, status, exclude_request_prefix=EXCLUDE_PREFIX):
    result = subprocess.run(
        ["docker", "exec", "-i", container, PYTHON, "-"],
        input=_reader_script(site, status, exclude_request_prefix),
        text=True,
        capture_output=True,
        timeout=300,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"eval export failed in {container}: {detail}")
    return parse_export_lines(result.stdout)


def write_cases(rows, site):
    exported = 0
    skipped = 0
    directory = ROOT / "evals" / "cases" / site
    directory.mkdir(parents=True, exist_ok=True)
    for row in rows:
        case = build_case(row["run"], row["events"], row["proposals"], site=site)
        path = directory / f"{case['run_id']}.json"
        if path.exists():
            skipped += 1
            continue
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n")
        exported += 1
    return {"exported": exported, "skipped": skipped}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--status", default="Failed")
    parser.add_argument("--exclude-request-prefix", default=EXCLUDE_PREFIX,
                        help="skip runs whose request_id starts with this (the evaluator's own)")
    args = parser.parse_args(argv)
    rows = read_container_rows(args.container, args.site, args.status, args.exclude_request_prefix)
    summary = write_cases(rows, args.site)
    print(summary)
    return summary


if __name__ == "__main__":
    main()
