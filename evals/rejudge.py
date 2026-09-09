"""Re-judge a batch that already ran, with today's cases and oracle. No provider calls.

Why this exists: a corpus change is a change to **judging**, never to the run. `oracle.judge`
is a pure function of what the Site recorded, and `run.observe` rebuilds exactly that from the
Site. So after rewriting an `expect`, the honest way to see what it does is to run the real
oracle over the real recorded behaviour — not arithmetic on the old report, and not another
paid batch that would also re-roll the model's non-determinism.

Its limit, and it matters: this re-judges **the runs that happened**. It cannot show what a
model would do after a prompt, skill or server change — that needs a real batch. Reports
written from here carry `rejudged_from` so nobody reads one as a fresh measurement.

    .venv/bin/python evals/rejudge.py work/evals-live/report.json dsherp-daily.localhost \
        work/evals-live-rejudged
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import oracle  # noqa: E402
from evals.run import markdown, observe, report  # noqa: E402

CARRIED = ('model_calls', 'input_bytes', 'actual_input_tokens', 'actual_output_tokens',
           'usage_unknown_calls', 'prompt_version', 'runtime_revision')


def rejudge(previous, site, cases_dir, observer=observe):
    """Judge each recorded run again. `observer` is injectable so this is testable Site-free."""
    results = []
    for row in previous['cases']:
        if not row.get('run_id'):
            results.append(dict(row))
            continue
        case = json.loads((Path(cases_dir) / f"{row['case_id']}.json").read_text(encoding='utf-8'))
        seen = {**observer(site, row['run_id']), 'mode': previous['mode']}
        results.append({'case_id': row['case_id'], 'mode': previous['mode'],
                        'domain': case['domain'], 'tags': case.get('tags') or [],
                        'origin_run_id': (case.get('origin') or {}).get('run_id'),
                        'skip_reason': case.get('skip_reason', ''),
                        **oracle.judge(case, seen), 'run_id': row['run_id'],
                        'final_status': seen.get('status'),
                        **{key: row.get(key) for key in CARRIED}})
    summary = report(results, mode=previous['mode'], site=site, started=previous.get('started'))
    # Never silently passes for a fresh run: the field is what a reader checks.
    summary['rejudged_from'] = {'started': previous.get('started'),
                                'pass_rate_before': previous.get('pass_rate')}
    return summary


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    previous_path, site, out = argv
    previous = json.loads(Path(previous_path).read_text(encoding='utf-8'))
    summary = rejudge(previous, site, ROOT / 'evals/cases' / site)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                           encoding='utf-8')
    (directory / 'report.md').write_text(markdown(summary), encoding='utf-8')
    print(markdown(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
