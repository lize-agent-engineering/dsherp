"""When a member's business credential is alive, and when a login must replace it (S2).

The platform holds a credential the business Site issued; the business Site decides whether
that credential still counts. If the two sides judged "expired" differently, one of them
would either refuse a live credential or honour a dead one, so the judgement lives here and
this file is mirrored into the Site app verbatim below the docstring.

Frappe keeps exactly one api_secret per User, so a credential belongs to a business user,
not to a browser session: a second login by the same member reuses the live one instead of
issuing a new pair, and renewal happens while the old one still works."""
import datetime

TTL_HOURS = 12
# Renew while the current credential is still usable, so no request starts on one that
# is about to die. A login inside this window gets a fresh pair; outside it, the live one.
RENEW_WITHIN_HOURS = 4
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def moment(value):
    """A datetime from what Frappe hands back: a datetime in process, a string over HTTP."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    text = str(value).strip().replace('T', ' ')
    if not text:
        return None
    return datetime.datetime.strptime(text.split('.')[0][:19], TIME_FORMAT)


def expiry(issued_at):
    return moment(issued_at) + datetime.timedelta(hours=TTL_HOURS)


def state(row, now):
    """One word for what this credential is, so reports and refusals say the same thing."""
    if not row:
        return 'missing'
    if row.get('revoked'):
        return 'revoked'
    expires = moment(row.get('expires_at'))
    if expires is None or expires <= moment(now):
        return 'expired'
    if expires - moment(now) <= datetime.timedelta(hours=RENEW_WITHIN_HOURS):
        return 'renewable'
    return 'fresh'


def usable(row, now):
    return state(row, now) in ('fresh', 'renewable')


def renewal_due(row, now):
    return state(row, now) != 'fresh'
