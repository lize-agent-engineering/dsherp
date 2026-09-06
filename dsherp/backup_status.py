"""The host-side record of the backup chain (design §4.4).

Written by dsherp-admin at every phase boundary, read by the worker every tick. A failure
never erases the last success: the two are separate fields, because 'when did this Site last
have a good backup' is the question the RPO alert answers, and 'what went wrong last time'
is the question the operator answers.

Missing and corrupt are told apart on purpose: a corrupt file may not be silently replaced
by an empty one, because the protection of the newest complete and newest verified set -
the last copies a prune may not delete - lives in it."""
import calendar
import json
import time
from pathlib import Path

from dsherp.alerts import Alert

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


# What the invariant means in numbers (design §1): a Site whose newest complete off-site set
# holds data older than a day has missed the promise; twenty hours is the warning that says so
# while there is still time to act. The judgement is over the data's own timestamp, not over
# when the upload finished.
RPO_WARNING_HOURS = 20
RPO_HOURS = 24
# Two runs a day: a gap longer than half a day plus a run's own window means the timer did not
# fire at all, which no per-Site alert would show.
RUN_MISSING_HOURS = 13
VERIFY_DAYS = 8
GAUGES = ('dsherp_backup_sites_expected', 'dsherp_backup_sites_rpo_ok', 'dsherp_backup_offsite_oldest_hours',
          'dsherp_backup_local_oldest_hours', 'dsherp_backup_status_age_seconds', 'dsherp_backup_last_run_ok',
          'dsherp_backup_unverified_days_max')


def _epoch(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


def stamp_age_hours(stamp, now):
    return (now - calendar.timegm(time.strptime(stamp, '%Y%m%d_%H%M%S'))) / 3600


def _failed_after_success(row):
    attempt, success = (row or {}).get('last_attempt'), (row or {}).get('last_success')
    if not attempt or attempt.get('ok') or attempt.get('deferred'):
        return False
    return not success or _epoch(attempt['at']) > _epoch(success['at'])


def evaluate(status, expected_sites, now, status_age_seconds=None):
    """Gauges and alerts from the record. `expected_sites` is the authoritative list (the
    tenant list plus the platform Site); None means it could not be read, which is reported -
    never narrowed to whatever the record happens to mention."""
    alerts = []
    gauges = {name: 0 for name in GAUGES}
    if status_age_seconds is not None:
        gauges['dsherp_backup_status_age_seconds'] = status_age_seconds
    if expected_sites is None:
        gauges['dsherp_backup_sites_expected'] = -1
        gauges['dsherp_backup_offsite_oldest_hours'] = -1
        alerts.append(Alert('backup_scope_unknown', 'critical', '读不到租户清单：无法判断应当备份哪些站点'))
        return {'gauges': gauges, 'alerts': alerts}
    gauges['dsherp_backup_sites_expected'] = len(expected_sites)
    if status is None:
        gauges['dsherp_backup_offsite_oldest_hours'] = -1
        alerts.append(Alert('backup_status_missing', 'critical', '备份状态文件缺失或损坏：backups/status.json'))
        return {'gauges': gauges, 'alerts': alerts}
    attempt = (status['runs'].get('backup') or {}).get('last_attempt')
    if attempt is None or (now - _epoch(attempt['at'])) / 3600 > RUN_MISSING_HOURS:
        alerts.append(Alert('backup_status_missing', 'critical',
                            f'备份任务超过 {RUN_MISSING_HOURS} 小时没有运行；查 systemctl list-timers dsherp-backup.timer'))
    unmet, warning, unverified, failed = [], [], [], []
    offsite_oldest = local_oldest = unverified_days = 0.0
    never = False
    for site in expected_sites:
        row = status['sites'].get(site, {})
        # The newest set the repositories were proven to hold - not the last one an upload
        # happened to finish, which may be an older set catching up.
        proven = [item for item in sets_of(status, site) if item.get('state') in PROTECTED_STATES]
        complete = ({'stamp': proven[-1]['stamp']} if proven
                    else (row.get('offsite') or {}).get('last_success'))
        if not complete:
            unmet.append(site)
            never = True
        else:
            age = stamp_age_hours(complete['stamp'], now)
            offsite_oldest = max(offsite_oldest, age)
            if age >= RPO_HOURS:
                unmet.append(site)
            elif age >= RPO_WARNING_HOURS:
                warning.append(site)
        local = (row.get('backup') or {}).get('last_success')
        if local:
            local_oldest = max(local_oldest, stamp_age_hours(local['stamp'], now))
        for phase in SITE_PHASES:
            if _failed_after_success(row.get(phase)):
                failed.append(f'{site}/{phase}')
        verified = (row.get('verified') or {}).get('last_success')
        if verified:
            days = (now - _epoch(verified['at'])) / 86400
            unverified_days = max(unverified_days, days)
            if days > VERIFY_DAYS:
                unverified.append(site)
        elif local and (now - _epoch(local.get('first_at') or local['at'])) / 86400 > VERIFY_DAYS:
            unverified.append(site)      # backed up for more than a drill cycle and never proven
    for phase in ('sync', 'check', 'drill'):
        if _failed_after_success(status['runs'].get(phase)):
            failed.append(phase)
    gauges['dsherp_backup_sites_rpo_ok'] = len(expected_sites) - len(unmet)
    gauges['dsherp_backup_offsite_oldest_hours'] = -1 if never else round(offsite_oldest, 3)
    gauges['dsherp_backup_local_oldest_hours'] = round(local_oldest, 3)
    gauges['dsherp_backup_last_run_ok'] = 1 if attempt and attempt.get('ok') else 0
    gauges['dsherp_backup_unverified_days_max'] = round(unverified_days, 3)
    if unmet:
        alerts.append(Alert('backup_rpo_unmet', 'critical', '24 小时内没有完整的异地备份：' + '、'.join(unmet)))
    if warning:
        alerts.append(Alert('backup_rpo_warning', 'warning', '异地备份已超过 20 小时：' + '、'.join(warning)))
    if failed:
        alerts.append(Alert('backup_run_failed', 'warning', '备份链最近一次尝试失败：' + '、'.join(failed)))
    if unverified:
        alerts.append(Alert('restore_unverified', 'warning', '超过 8 天没有验证过恢复：' + '、'.join(unverified)))
    return {'gauges': gauges, 'alerts': alerts}
