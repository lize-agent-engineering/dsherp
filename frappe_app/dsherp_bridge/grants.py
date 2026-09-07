"""Where a run's platform authorization lives while the run is in flight (S2, R7).

The grant is the member's encrypted platform identity plus the OAuth token behind it. It
used to be written into the DS Model Run row and stayed there for as long as the row did: in
backups, in exports, in the audit table. It is needed only while a background executor acts
for the member - to ask the platform, on every step, whether that member is still allowed.

So it is kept in the Site's cache under the run's id, with a lifetime bounded by the run's own
budget, and dropped the moment the run reaches a terminal state. Losing it early (a cache
flush) fails the run closed at its next check; it never lets the run continue unasked."""
import frappe

PREFIX = 'dsherp_run_grant:'
# Queue wait plus the longest run budget plus the lease slack: nothing legitimate outlives it.
SLACK_SECONDS = 900


def lifetime(domain):
    from dsherp_bridge.run_budget import budget
    plan = budget(domain)
    return int(plan['queue_expires_seconds']) + int(plan['run_total_seconds']) + SLACK_SECONDS


def stash(run_id, grant, domain):
    if grant:
        frappe.cache().set_value(PREFIX + run_id, grant, expires_in_sec=lifetime(domain))


def of(run_id):
    return frappe.cache().get_value(PREFIX + run_id)


def drop(run_id):
    frappe.cache().delete_value(PREFIX + run_id)


# A configuration transfer's authorization is held the same way, keyed by the transfer and
# living exactly as long as the transfer's window (configuration_transfer.TRANSFER_WINDOW_SECONDS).
TRANSFER_PREFIX = 'dsherp_transfer_grant:'


def stash_transfer(transfer_id, grant, seconds):
    """Write the transfer's lease. It is written even when there is no grant - a Site that
    still allows password login has none - so that a missing entry always means 'expired or
    lost', never 'never had one'."""
    frappe.cache().set_value(TRANSFER_PREFIX + transfer_id, {'grant': grant or None},
                             expires_in_sec=int(seconds))


def of_transfer(transfer_id):
    """The lease as {'grant': str | None}; None once it has expired or was lost.

    Read through to redis rather than through frappe.local: set_value writes the process-local
    copy unconditionally and ignores expires_in_sec (checked against the pinned image), so a
    process that stashed a lease would keep answering with it after the window closed. The
    window itself is enforced from the row, but a lease that outlives its own TTL is not a
    lease."""
    return frappe.cache().get_value(TRANSFER_PREFIX + transfer_id, use_local_cache=False)


def drop_transfer(transfer_id):
    frappe.cache().delete_value(TRANSFER_PREFIX + transfer_id)
