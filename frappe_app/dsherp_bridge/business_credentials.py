"""Mirror of dsherp/business_credentials.py; the Site app cannot import the host package.
Edit the host copy and copy it here - tests/test_business_credentials.py compares them."""
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
