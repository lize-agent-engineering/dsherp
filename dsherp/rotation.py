"""When each long-lived credential was last changed, and whether that was long enough ago.

A rotation is recorded as an event: kind, target, version, effective time, and a fingerprint
of the value. The value itself is never written here - the ledger's job is to let an operator
answer "when did this last change, and is what runs now the thing I rotated?" without keeping
the old secret around to compare against.

The windows are deliberately plain numbers rather than a policy engine: a provider key and a
Site's own execution identity get three months, an OAuth client secret half a year, because
rotating it costs a login round for every member."""
import datetime
import hashlib
import json
import os
from pathlib import Path

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
MAX_AGE_DAYS = {'provider': 90, 'runtime': 90, 'oauth-client': 180}
# Warn while there is still time to plan the change, not on the day it is already late.
WARN_FRACTION = 0.8
KINDS = tuple(sorted(MAX_AGE_DAYS))


def fingerprint(value):
    """Names a value without carrying it. Truncated: this is an identity, not a password check."""
    return hashlib.sha256((value or '').encode()).hexdigest()[:16]


def entry(*, kind, target, value, previous, version, at):
    if kind not in MAX_AGE_DAYS:
        raise ValueError(f'未知的轮换类别：{kind}')
    return {'kind': kind, 'target': target, 'version': int(version),
            'effective_at': at.strftime(TIME_FORMAT),
            'fingerprint': fingerprint(value),
            'previous_fingerprint': fingerprint(previous) if previous else None}


def read(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f'轮换账簿已损坏，不要重建，先检查 {path}') from error
    if not isinstance(rows, list) or any(not isinstance(row, dict) or 'kind' not in row for row in rows):
        raise ValueError(f'轮换账簿格式无效，不要重建，先检查 {path}')
    return rows


def record(path, item):
    """Append one event. The ledger is a history, so nothing here rewrites what is already in it."""
    path = Path(path)
    rows = read(path) + [item]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + f'.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write('\n')
    os.replace(temporary, path)
    return rows


def _of(rows, kind, target):
    return [row for row in rows if row.get('kind') == kind and row.get('target') == target]


def latest(rows, kind, target):
    found = _of(rows, kind, target)
    return max(found, key=lambda row: int(row.get('version') or 0)) if found else None


def next_version(rows, kind, target):
    last = latest(rows, kind, target)
    return int(last['version']) + 1 if last else 1


def age_days(rows, kind, target, now):
    last = latest(rows, kind, target)
    if not last:
        return None
    at = datetime.datetime.strptime(last['effective_at'], TIME_FORMAT)
    return (now - at).days


def state(rows, kind, target, now):
    age = age_days(rows, kind, target, now)
    if age is None:
        return 'never'
    window = MAX_AGE_DAYS[kind]
    if age > window:
        return 'overdue'
    return 'ageing' if age >= window * WARN_FRACTION else 'fresh'


NOTES = {
    'never': '从未登记过轮换：轮换一次即可把它纳入账簿',
    'ageing': '接近轮换窗口，安排一次轮换',
    'overdue': '已超过轮换窗口',
}


def findings(rows, targets, now):
    """Only what an operator has to act on: fresh credentials say nothing."""
    found = []
    for kind in sorted(targets):
        for target in targets[kind]:
            condition = state(rows, kind, target, now)
            if condition == 'fresh':
                continue
            found.append({'kind': kind, 'target': target, 'state': condition,
                          'age_days': age_days(rows, kind, target, now),
                          'window_days': MAX_AGE_DAYS[kind], 'note': NOTES[condition]})
    return found


def replace_value(path, key, value):
    """Rewrite exactly one `KEY=value` line, keeping the file's own shape and privacy.

    The key has to be there already: a rotation that invents a second definition would leave
    the file with two, and which one wins depends on the reader."""
    path = Path(path)
    if not value or not value.strip() or '\n' in value:
        raise ValueError('新值不能为空，也不能含换行')
    lines = path.read_text().splitlines()
    hits = [index for index, line in enumerate(lines) if line.split('=', 1)[0].strip() == key]
    if len(hits) != 1:
        raise ValueError(f'{path} 里必须正好有一行 {key}=…，当前有 {len(hits)} 行')
    previous = lines[hits[0]].split('=', 1)[1] if '=' in lines[hits[0]] else ''
    lines[hits[0]] = f'{key}={value}'
    mode = path.stat().st_mode & 0o777
    temporary = path.with_name('.' + path.name + f'.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)
    return previous
