"""Which off-site sets to keep (design §4.7).

Pure: the caller lists the sets the repositories hold, this decides, the caller forgets the
dropped ones on both sides. The policy is applied per Site over the set stamps, because
restic's own grouping cannot express it: every set carries a unique `set=` tag, so
`forget --group-by tags` would put each snapshot in a group of its own and expire nothing.

What the policy means, stated the way restic states it: a period without a backup uses no
slot, so `keep 3 monthly` is not a promise that anything disappears after three months, and
nothing expires at all unless a sync run happens."""
from datetime import datetime, timezone

KEEP = (('daily', 7), ('weekly', 4), ('monthly', 3))
EXPIRABLE = ('complete', 'verified')
PROTECTED_KINDS = ('retire',)


def _when(stamp):
    return datetime.strptime(stamp, '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)


def _periods(when):
    year, week, _ = when.isocalendar()
    return {'daily': when.strftime('%Y-%m-%d'), 'weekly': f'{year}-W{week:02d}', 'monthly': when.strftime('%Y-%m')}


def _protected(rows):
    """The newest complete set and the newest verified set of this Site: the last copy that is
    known to exist and the last one known to restore."""
    protected = set()
    for state in EXPIRABLE:
        candidates = [row for row in rows if row.get('state') == state]
        if candidates:
            protected.add(max(candidates, key=lambda row: row['stamp'])['set_id'])
    return protected


def select(sets, now=None):
    """{set_id: 'keep' | 'drop'}. `now` is not consulted: the policy is defined over the sets
    that exist, so a run after a long outage does not suddenly delete everything."""
    decision = {}
    by_site = {}
    for row in sets:
        by_site.setdefault(row['site'], []).append(row)
    for rows in by_site.values():
        rows = sorted(rows, key=lambda row: row['stamp'], reverse=True)
        protected = _protected(rows)
        seen = {rule: set() for rule, _ in KEEP}
        for row in rows:
            keep = row['state'] not in EXPIRABLE or row.get('kind') in PROTECTED_KINDS or row['set_id'] in protected
            try:
                periods = _periods(_when(row['stamp']))
            except (ValueError, TypeError):
                decision[row['set_id']] = 'keep'  # an unreadable stamp is never guessed away
                continue
            for rule, limit in KEEP:
                period = periods[rule]
                if period in seen[rule]:
                    continue
                if keep:
                    seen[rule].add(period)   # a protected set occupies its own slots
                elif len(seen[rule]) < limit:
                    seen[rule].add(period)
                    keep = True
            decision[row['set_id']] = 'keep' if keep else 'drop'
    return decision
