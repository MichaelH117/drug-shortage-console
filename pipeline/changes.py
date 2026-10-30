"""Change feed: classify each event between a snapshot and the previous one (owner: data-engineer, AC-2.3, DESIGN 3.5).

Rules, first match wins:

1. in P, not in S                                                   DROPPED
2. not in P, in S (also every event on the first snapshot)          NEW
3. S.status = Resolved and P.status != Resolved                     RESOLVED
4. P.status = Resolved and S.status != Resolved                     WORSENED (reopened)
5. rank(S.avail) > rank(P.avail), or status Current -> To Be Disc.  WORSENED
6. rank(S.avail) < rank(P.avail)                                    IMPROVED
7. otherwise, including any move to or from Unknown                 UNCHANGED

Rank: Available 0, Limited 1, Unavailable 2. Unknown has no rank, so it never produces WORSENED or IMPROVED.
"""

from __future__ import annotations

import pandas as pd

from pipeline.availability import RANK

CHANGE_TYPES = ("NEW", "WORSENED", "IMPROVED", "RESOLVED", "DROPPED", "UNCHANGED")
FEED_COLUMNS = ["snapshot_date", "event_key", "prev_snapshot_date", "change_type", "rule", "prev_status",
                "curr_status", "prev_availability", "curr_availability"]


def classify_one(prev: dict | None, curr: dict | None) -> tuple[str, int]:
    """``(change_type, rule number)`` for one event. ``prev``/``curr`` hold ``status`` and
    ``availability_normalized``; ``None`` means absent from that snapshot."""
    if prev is not None and curr is None:
        return "DROPPED", 1
    if prev is None and curr is not None:
        return "NEW", 2
    if prev is None and curr is None:
        raise ValueError("event absent from both snapshots")
    ps, cs = prev.get("status"), curr.get("status")
    if cs == "Resolved" and ps != "Resolved":
        return "RESOLVED", 3
    if ps == "Resolved" and cs != "Resolved":
        return "WORSENED", 4
    pr, cr = RANK.get(prev.get("availability_normalized")), RANK.get(curr.get("availability_normalized"))
    if (pr is not None and cr is not None and cr > pr) or (ps == "Current" and cs == "To Be Discontinued"):
        return "WORSENED", 5
    if pr is not None and cr is not None and cr < pr:
        return "IMPROVED", 6
    return "UNCHANGED", 7


def classify_changes(prev: pd.DataFrame | None, curr: pd.DataFrame) -> pd.DataFrame:
    """Change-feed rows for snapshot ``curr`` against ``prev`` (stage rows of one snapshot each; ``prev`` None on
    the first snapshot). One row per event present in either snapshot."""
    s_date = curr["snapshot_date"].iloc[0] if len(curr) else None
    p_date = prev["snapshot_date"].iloc[0] if prev is not None and len(prev) else None
    cols = ["event_key", "status", "availability_normalized"]
    p_map = {} if prev is None else {r["event_key"]: r for r in prev[cols].to_dict("records")}
    c_map = {r["event_key"]: r for r in curr[cols].to_dict("records")}
    rows = []
    for key in sorted(set(p_map) | set(c_map)):
        p, c = p_map.get(key), c_map.get(key)
        change, rule = classify_one(p, c)
        rows.append({
            "snapshot_date": s_date, "event_key": key, "prev_snapshot_date": p_date, "change_type": change,
            "rule": rule,
            "prev_status": p and p.get("status"), "curr_status": c and c.get("status"),
            "prev_availability": p and p.get("availability_normalized"),
            "curr_availability": c and c.get("availability_normalized"),
        })
    return pd.DataFrame(rows, columns=FEED_COLUMNS)


def build_change_feed(stage: pd.DataFrame, snapshot_dates: list | None = None) -> pd.DataFrame:
    """``core.change_feed`` for every snapshot against the previous one BY DATE (not load order, ADR 004)."""
    dates = sorted(set(snapshot_dates) if snapshot_dates is not None else set(stage["snapshot_date"]))
    frames = []
    prev = None
    for d in dates:
        curr = stage[stage["snapshot_date"] == d]
        if curr.empty:
            # A snapshot with zero records: everything in prev is DROPPED.
            curr = stage.iloc[0:0].copy()
        feed = classify_changes(prev, curr)
        feed["snapshot_date"] = d
        frames.append(feed)
        prev = curr
    if not frames:
        return pd.DataFrame(columns=FEED_COLUMNS)
    return pd.concat(frames, ignore_index=True)[FEED_COLUMNS]
