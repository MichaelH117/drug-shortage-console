"""Stale-data decision for the header banner (AE-02, AC-5.4). Pure, so it is tested without pixels."""
from __future__ import annotations

import datetime as dt


def snapshot_age_days(snapshot_date: dt.date, today: dt.date) -> int:
    """Whole days between the newest snapshot and today (both dates in America/New_York)."""
    return (today - snapshot_date).days


def is_stale(snapshot_date: dt.date | None, today: dt.date, max_age_days: int = 1) -> bool:
    """True when the newest snapshot is OLDER than ``max_age_days``.

    Exactly ``max_age_days`` old is not stale: yesterday's pull is still fine at 7:30 before today's run.
    No snapshot at all counts as stale.
    """
    if snapshot_date is None:
        return True
    return snapshot_age_days(snapshot_date, today) > max_age_days
