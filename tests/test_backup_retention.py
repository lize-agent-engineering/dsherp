"""Retention is decided on the host, per Site, from the set stamps.

restic's own `forget --group-by tags` would make every uniquely tagged snapshot its own
group and never expire anything, so the policy lives here and restic is only told which
snapshots to drop."""
from datetime import datetime, timedelta, timezone

from dsherp import backup_retention


START = datetime(2026, 9, 6, 2, tzinfo=timezone.utc)


def _sets(site, days, *, state="complete", kind="scheduled", start=START):
    rows = []
    for offset in days:
        stamp = (start - timedelta(days=offset)).strftime("%Y%m%d_%H%M%S")
        rows.append({"set_id": f"{stamp}-{site.replace('.', '_')}-aaaaaa", "site": site, "kind": kind,
                     "stamp": stamp, "state": state})
    return rows


def _kept(decision, rows):
    return [row for row in rows if decision[row["set_id"]] == "keep"]


def test_seven_daily_four_weekly_and_three_monthly_are_kept_per_site():
    site = "acme.tenant.example.com"
    rows = _sets(site, range(0, 200))
    decision = backup_retention.select(rows)
    kept = _kept(decision, rows)
    stamps = sorted(row["stamp"] for row in kept)
    assert len([stamp for stamp in stamps if stamp >= "20260831"]) == 7, "the last seven days"
    assert 10 <= len(kept) <= 14, kept
    assert decision[rows[0]["set_id"]] == "keep" and decision[rows[-1]["set_id"]] == "drop"
    weeks = {datetime.strptime(row["stamp"], "%Y%m%d_%H%M%S").isocalendar()[:2] for row in kept}
    months = {row["stamp"][:6] for row in kept}
    assert len(weeks) >= 4 and len(months) >= 3


def test_twice_daily_sets_keep_the_newest_of_each_day_week_and_month():
    site = "acme.tenant.example.com"
    morning = _sets(site, range(0, 40))
    afternoon = _sets(site, range(0, 40), start=datetime(2026, 9, 6, 14, tzinfo=timezone.utc))
    decision = backup_retention.select(morning + afternoon)
    for day in range(0, 7):
        assert decision[afternoon[day]["set_id"]] == "keep", day
        assert decision[morning[day]["set_id"]] == "drop", day


def test_pending_retired_and_protected_sets_are_never_dropped_and_sites_do_not_share_quota():
    acme = _sets("acme.tenant.example.com", range(0, 30))
    beta = _sets("beta.tenant.example.com", range(0, 30))
    pending = _sets("acme.tenant.example.com", [40], state="data_uploaded")
    retired = _sets("gone.tenant.example.com", [200], kind="retire")
    old_verified = _sets("acme.tenant.example.com", [90], state="verified")
    decision = backup_retention.select(acme + beta + pending + retired + old_verified)
    assert decision[pending[0]["set_id"]] == "keep", "a half-sent set is not a candidate for expiry"
    assert decision[retired[0]["set_id"]] == "keep", "a retired tenant's final set stays until a ruling says otherwise"
    assert decision[old_verified[0]["set_id"]] == "keep", "the newest verified set of a Site is the proof it restores"
    assert decision[acme[0]["set_id"]] == "keep", "the newest complete set"
    assert len(_kept(decision, acme)) == len(_kept(decision, beta)), "each Site gets its own quota"


def test_a_gap_in_the_schedule_does_not_expire_anything_by_itself():
    """restic's semantics, stated plainly: a period with no backup uses no slot, so 'keep 3
    monthly' is not a promise that anything is deleted after three months."""
    site = "acme.tenant.example.com"
    rows = _sets(site, [0, 400, 800])
    decision = backup_retention.select(rows)
    assert all(value == "keep" for value in decision.values()), "three sets, three monthly slots"


def test_only_sets_whose_two_halves_were_verified_can_be_dropped():
    site = "acme.tenant.example.com"
    rows = _sets(site, range(0, 30))
    rows[10]["state"] = "secrets_uploaded"
    decision = backup_retention.select(rows)
    assert decision[rows[10]["set_id"]] == "keep"


def test_a_malformed_stamp_is_kept_and_reported_rather_than_guessed():
    rows = _sets("acme.tenant.example.com", range(0, 30))
    rows[20]["stamp"] = "not-a-stamp"
    decision = backup_retention.select(rows)
    assert decision[rows[20]["set_id"]] == "keep"
