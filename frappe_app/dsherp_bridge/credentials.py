"""Issuing, lending and killing one business user's short-lived API credential (S2).

The platform used to keep a member's business api_key/api_secret for as long as the binding
existed. It now borrows one with a stated end: this Site issues the pair, records the window
in DS Business Credential, and refuses the key once that window has passed. A login inside
the renewal window replaces it while the old one still works, so nothing dies mid-request.

Frappe stores one api_secret per User, so the credential belongs to the business user rather
than to a browser session; the platform side keeps a business user bound to a single member
for exactly that reason."""
import frappe
from frappe.utils import now_datetime
from frappe.utils.password import get_decrypted_password

from dsherp_bridge import business_credentials as policy

DOCTYPE = 'DS Business Credential'
FIELDS = ('name', 'user', 'api_key', 'issued_at', 'expires_at', 'version', 'revoked', 'legacy')


def record(user):
    if not frappe.db.exists(DOCTYPE, user):
        return None
    return frappe.db.get_value(DOCTYPE, user, FIELDS, as_dict=True)


def _write(user, values):
    if frappe.db.exists(DOCTYPE, user):
        document = frappe.get_doc(DOCTYPE, user)
        document.update(values)
        document.save(ignore_permissions=True)
        return document
    return frappe.get_doc({'doctype': DOCTYPE, 'user': user, **values}).insert(ignore_permissions=True)


def _new_secret(user):
    """A fresh secret for this user, written where Frappe reads it and nowhere else.

    Not `User.save()`: that runs the User controller, which clears the user's cache and
    enqueues a contact job on every save - a background job and a controller's validations
    inside every login that renews. Frappe checks an API key straight from the database
    (`validate_api_key_secret` reads the User row and the encrypted secret), so writing the
    two columns is the whole operation."""
    from frappe.utils.password import set_encrypted_password
    key = frappe.db.get_value('User', user, 'api_key')
    if not key:
        key = frappe.generate_hash(length=15)
        frappe.db.set_value('User', user, 'api_key', key, update_modified=False)
    secret = frappe.generate_hash(length=15)
    set_encrypted_password('User', user, secret, 'api_secret')
    frappe.clear_cache(user=user)
    return key, secret


def issue(user, issued_for=None):
    """A fresh secret for this user, and the window it lives in."""
    previous = record(user)
    key, secret = _new_secret(user)
    issued_at = now_datetime()
    _write(user, {'api_key': key, 'issued_at': issued_at, 'expires_at': policy.expiry(issued_at),
                  'version': int(previous['version']) + 1 if previous else 1, 'revoked': 0, 'legacy': 0,
                  'issued_for': issued_for or ''})
    return {'api_key': key, 'api_secret': secret,
            'expires_at': str(policy.expiry(issued_at)),
            'version': int(previous['version']) + 1 if previous else 1}


def current(user, issued_for=None):
    """What the platform should be holding after this login: the live pair, or a new one.

    A second login by the same member must not evict the first one's credential, so a pair
    that is still comfortably inside its window is handed over unchanged."""
    row = record(user)
    secret = get_decrypted_password('User', user, 'api_secret', raise_exception=False)
    if not secret or policy.renewal_due(row, now_datetime()):
        return issue(user, issued_for)
    return {'api_key': row['api_key'], 'api_secret': secret,
            'expires_at': str(row['expires_at']), 'version': int(row['version'])}


REFUSALS = {'missing': '该业务用户没有登记在案的凭据，请通过企业平台登录',
            'expired': '业务凭据已过期，请重新通过企业平台登录',
            'revoked': '业务凭据已吊销，请联系企业管理员'}


def require(user):
    """Refuse a key whose window has passed. Missing is a refusal too, never a free pass.

    Raised as an authentication failure, not a permission one, and the difference is load
    bearing: it reaches the caller as 401, which is how the platform tells "this credential
    is finished, go and renew it" from "this member may not read that". A 403 here made a
    login that had expired impossible to repair."""
    row = record(user)
    condition = policy.state(row, now_datetime())
    if condition in REFUSALS:
        raise frappe.AuthenticationError(REFUSALS[condition])
    if row['api_key'] != frappe.db.get_value('User', user, 'api_key'):
        raise frappe.AuthenticationError('业务凭据与登记不一致，请重新通过企业平台登录')
    return row


def revoke(user, reason=''):
    """Mark it dead and make it dead: the secret is replaced by one nobody holds, so a
    caller that somehow skips the window check still fails to authenticate."""
    if not frappe.db.exists(DOCTYPE, user):
        return {'user': user, 'revoked': False, 'why': 'no credential on record'}
    _new_secret(user)
    _write(user, {'revoked': 1, 'issued_for': reason or frappe.db.get_value(DOCTYPE, user, 'issued_for')})
    return {'user': user, 'revoked': True}
