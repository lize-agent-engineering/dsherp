"""The host-side record of the backup chain (design §4.4).

Written by dsherp-admin at every phase boundary, read by the worker every tick. A failure
never erases the last success: the two are separate fields, because 'when did this Site last
have a good backup' is the question the RPO alert answers, and 'what went wrong last time'
is the question the operator answers.

Missing and corrupt are told apart on purpose: a corrupt file may not be silently replaced
by an empty one, because the protection of the newest complete and newest verified set -
the last copies a prune may not delete - lives in it."""
import json
import time
from pathlib import Path

FORMAT = 1
PHASES = ('backup', 'sync', 'check', 'drill')
SITE_PHASES = ('backup', 'offsite', 'verified')
SET_STATES = ('staged', 'data_uploaded', 'secrets_uploaded', 'complete', 'verified')
PROTECTED_STATES = ('complete', 'verified')


def now_iso(clock=time.time):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))


def empty():
    return {'format': FORMAT,
            'runs': {phase: {'last_attempt': None, 'last_success': None} for phase in PHASES},
            'sites': {}, 'sets': {}}


def read(path):
    """(status, state) where state is 'ok', 'missing' or 'corrupt'."""
    path = Path(path)
    if not path.exists():
        return None, 'missing'
    try:
        status = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, 'corrupt'
    if not isinstance(status, dict) or status.get('format') != FORMAT:
        return None, 'corrupt'
    for key in ('runs', 'sites', 'sets'):
        if not isinstance(status.get(key), dict):
            return None, 'corrupt'
    return status, 'ok'


def load(path):
    return read(path)[0]


def save(path, status):
    from dsherp.admin import _write_json
    return _write_json(Path(path), status)


def record_run(status, phase, *, at, ok, error=None):
    if phase not in PHASES:
        raise ValueError('Unknown phase: ' + repr(phase))
    row = status['runs'].setdefault(phase, {'last_attempt': None, 'last_success': None})
    row['last_attempt'] = {'at': at, 'ok': bool(ok), 'error': error}
    if ok:
        row['last_success'] = {'at': at}


def record_site(status, site, phase, *, at, ok, error=None, **facts):
    if phase not in SITE_PHASES:
        raise ValueError('Unknown site phase: ' + repr(phase))
    row = status['sites'].setdefault(site, {}).setdefault(phase, {'last_attempt': None, 'last_success': None})
    attempt = {'at': at, 'ok': bool(ok), 'error': error}
    if not ok:
        attempt.update(facts)  # e.g. deferred='busy'
    row['last_attempt'] = attempt
    if ok:
        first = (row.get('last_success') or {}).get('first_at') or at
        row['last_success'] = {'at': at, 'first_at': first, **facts}


def record_set(status, set_doc, state, **snapshots):
    if state not in SET_STATES:
        raise ValueError('Unknown set state: ' + repr(state))
    row = status['sets'].setdefault(set_doc['set_id'], {'data_snapshot': None, 'secrets_snapshot': None})
    row.update({'site': set_doc['site'], 'kind': set_doc['kind'], 'stamp': set_doc['stamp'],
                'image_tag': set_doc.get('image_tag'), 'image_id': set_doc.get('image_id'), 'state': state})
    for key in ('data_snapshot', 'secrets_snapshot'):
        if key in snapshots:
            row[key] = snapshots[key]
    row['updated'] = now_iso()
    return row


def sets_of(status, site):
    """This Site's sets, oldest first; each row carries its own set_id."""
    rows = [dict(row, set_id=set_id) for set_id, row in status['sets'].items() if row.get('site') == site]
    return sorted(rows, key=lambda row: row['stamp'])


def protected_sets(status, site):
    """The set ids no prune may delete: this Site's newest complete and newest verified."""
    protected = set()
    for state in PROTECTED_STATES:
        candidates = [row for row in sets_of(status, site) if row.get('state') == state]
        if candidates:
            protected.add(candidates[-1]['set_id'])
    return protected
