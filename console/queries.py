"""Read-side queries for the console (AE-02, AC-5.1 to 5.4, 7.4). The app reads ONLY through this module.

Everything reads the ontology views (``obj.*``, ``link.*``) plus ``core.change_feed`` and ``core.shortage_history``.
Views are live, so a successful action shows up on the next Streamlit rerun (AC-6.5). Nothing here writes.

``TRIAGE_QUEUE_SQL`` holds the text of ``mart.triage_queue``, ``mart.baseline_worklist`` and ``mart.watch_list``
(ADR 007); ``pipeline/objects.py`` executes it during the build (ML runs it, AE owns the text, DESIGN 3.4).
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

# ADR 007. Rank (a sort, not a score): availability Unavailable, Limited, then To Be Discontinued / Unknown;
# min SYNTHETIC days on hand (NULL last); best match tier; facilities (desc); key.
AVAIL_ORDER = {"Unavailable": 0, "Limited": 1}       # anything else that passes the filter ranks 2
QUEUE_CHANGE_TYPES = ("NEW", "WORSENED")
QUEUE_CAP = 40                                       # UI cap only; metrics use the uncapped queue (ADR 007 point 5)

_LATEST = "(SELECT max(snapshot_date) FROM stage.snapshot)"
_BASELINE = "(SELECT min(snapshot_date) FROM stage.snapshot)"


def _actionable(status: str, avail: str) -> str:
    """ADR 007 point 3, on that snapshot's own values (so a past day's queue is judged as it was that day)."""
    return (f"({status} <> 'Resolved' AND ({avail} IN ('Limited', 'Unavailable') "
            f"OR {status} = 'To Be Discontinued'))")


def _rank(avail: str) -> str:
    return f"CASE {avail} WHEN 'Unavailable' THEN 0 WHEN 'Limited' THEN 1 ELSE 2 END"


_FAC = """
fac AS (
    SELECT l.event_key,
           string_agg(DISTINCT l.facility_id, ', ' ORDER BY l.facility_id) AS facilities,
           count(DISTINCT l.facility_id) AS n_facilities,
           min(l.days_on_hand) AS min_days_on_hand,
           min(l.match_tier) AS best_match_tier
    FROM link.shortage_event_facility l
    GROUP BY l.event_key
),
tasks AS (
    SELECT event_key, count(*) FILTER (WHERE state = 'open') AS open_tasks
    FROM obj.mitigation_task GROUP BY event_key
),
subs AS (
    SELECT event_key,
           bool_or(state = 'approved') AS substitute_approved,
           count(*) FILTER (WHERE state = 'proposed') AS substitutes_proposed,
           count(*) FILTER (WHERE coalesce(state, 'candidate') <> 'rejected') AS substitute_options
    FROM obj.substitute GROUP BY event_key
)"""

# Daily queue for snapshot S: NEW/WORSENED since the previous snapshot (baseline rows, prev_snapshot_date IS NULL,
# never enter), matched to a stocked formulary item, and actionable. ``__SNAPSHOT__`` = a date expression.
# ``__KEEP__`` = the actionable predicate (the Watch list uses its negation).
_EVENTS_SELECT = """
WITH feed AS (
    SELECT f.snapshot_date, f.prev_snapshot_date, f.event_key, f.change_type, f.rule,
           f.prev_status, f.curr_status, f.prev_availability, f.curr_availability
    FROM core.change_feed f
    WHERE f.snapshot_date = __SNAPSHOT__ AND f.change_type IN ('NEW', 'WORSENED')
      AND f.prev_snapshot_date IS NOT NULL
),""" + _FAC + """
SELECT feed.snapshot_date, feed.prev_snapshot_date, feed.event_key, e.ndc11, e.generic_name, e.presentation,
       e.company_name, feed.change_type, feed.rule AS change_rule, feed.prev_availability,
       e.availability_raw, feed.curr_availability AS availability_normalized, feed.curr_status AS status,
       e.shortage_reason, fac.facilities, fac.n_facilities, fac.min_days_on_hand, fac.best_match_tier,
       coalesce(tasks.open_tasks, 0) AS open_tasks,
       coalesce(subs.substitute_approved, false) AS substitute_approved,
       coalesce(subs.substitutes_proposed, 0) AS substitutes_proposed,
       coalesce(subs.substitute_options, 0) AS substitute_options,
       """ + _rank("feed.curr_availability") + """ AS avail_rank,
       row_number() OVER (ORDER BY """ + _rank("feed.curr_availability") + """, fac.min_days_on_hand NULLS LAST,
                          fac.best_match_tier, fac.n_facilities DESC, feed.event_key) AS queue_rank,
       count(*) OVER () AS actionable_total
FROM feed
JOIN fac USING (event_key)
JOIN obj.shortage_event e USING (event_key)
LEFT JOIN tasks USING (event_key)
LEFT JOIN subs USING (event_key)
WHERE __KEEP__
"""
_QUEUE_ORDER = " ORDER BY queue_rank"
_KEEP = _actionable("feed.curr_status", "feed.curr_availability")
_QUEUE_SELECT = _EVENTS_SELECT.replace("__KEEP__", _KEEP)
_WATCH_DAILY = _EVENTS_SELECT.replace("__KEEP__", "NOT " + _KEEP)

# Baseline worklist (ADR 007 point 2): one row per formulary item with at least one actionable matched event in
# the latest snapshot (the backlog as it stands). Home shows it as the main list when the latest snapshot IS the
# baseline; otherwise it stays available as a collapsed onboarding view.
_WORKLIST_SELECT = """
WITH rec AS (
    SELECT r.event_key, r.status, r.availability_normalized
    FROM stage.shortage_record r WHERE r.snapshot_date = """ + _LATEST + """
),
hit AS (
    SELECT l.item_id, l.facility_id, l.days_on_hand, l.match_tier, rec.event_key, rec.status,
           rec.availability_normalized, """ + _rank("rec.availability_normalized") + """ AS avail_rank
    FROM link.shortage_event_facility l JOIN rec USING (event_key)
    WHERE """ + _actionable("rec.status", "rec.availability_normalized") + """
),
agg AS (
    SELECT item_id,
           min(avail_rank) AS avail_rank,
           string_agg(DISTINCT facility_id, ', ' ORDER BY facility_id) AS facilities,
           count(DISTINCT facility_id) AS n_facilities,
           min(days_on_hand) AS min_days_on_hand,
           min(match_tier) AS best_match_tier,
           count(DISTINCT event_key) AS n_events,
           arg_min(event_key, avail_rank * 1000000 + coalesce(days_on_hand, 999999)) AS worst_event_key
    FROM hit GROUP BY item_id
)
SELECT """ + _LATEST + """ = """ + _BASELINE + """ AS is_baseline_day, agg.item_id,
       i.name_raw AS formulary_name, i.ndc_raw, CASE agg.avail_rank WHEN 0 THEN 'Unavailable' WHEN 1 THEN 'Limited'
       ELSE 'To Be Discontinued / Unknown' END AS worst_availability, agg.*  EXCLUDE (item_id),
       row_number() OVER (ORDER BY agg.avail_rank, agg.min_days_on_hand NULLS LAST, agg.best_match_tier,
                          agg.n_facilities DESC, agg.item_id) AS worklist_rank,
       count(*) OVER () AS worklist_total
FROM agg LEFT JOIN seed.formulary_item i USING (item_id)
"""
_WORKLIST_ORDER = " ORDER BY worklist_rank"

# Watch list: matched events that fail the actionable filter. Daily: NEW/WORSENED since the previous snapshot.
# Baseline day: every matched event in the baseline that is not actionable. Counted, never dropped.
_WATCH_BASELINE = """
SELECT r.snapshot_date, NULL::DATE AS prev_snapshot_date, r.event_key, e.ndc11, e.generic_name, e.presentation,
       e.company_name, 'BASELINE' AS change_type, NULL AS change_rule, NULL AS prev_availability,
       r.availability_raw, r.availability_normalized, r.status, e.shortage_reason,
       string_agg(DISTINCT l.facility_id, ', ' ORDER BY l.facility_id) AS facilities,
       count(DISTINCT l.facility_id) AS n_facilities, min(l.days_on_hand) AS min_days_on_hand,
       min(l.match_tier) AS best_match_tier
FROM stage.shortage_record r
JOIN link.shortage_event_facility l USING (event_key)
JOIN obj.shortage_event e USING (event_key)
WHERE r.snapshot_date = """ + _LATEST + """ AND """ + _LATEST + """ = """ + _BASELINE + """
  AND NOT """ + _actionable("r.status", "r.availability_normalized") + """
GROUP BY ALL
"""
_WATCH_SELECT = ("SELECT snapshot_date, prev_snapshot_date, event_key, ndc11, generic_name, presentation, "
                 "company_name, change_type, change_rule, prev_availability, availability_raw, "
                 "availability_normalized, status, shortage_reason, facilities, n_facilities, min_days_on_hand, "
                 "best_match_tier FROM (" + _WATCH_DAILY.replace("__SNAPSHOT__", _LATEST) + ") d "
                 "UNION ALL " + _WATCH_BASELINE)
_WATCH_ORDER = " ORDER BY change_type, generic_name, event_key"

# ``pipeline/objects.py`` executes this one constant (ML runs it, AE owns the text); DuckDB runs all three.
TRIAGE_QUEUE_SQL = ";\n".join([
    "CREATE OR REPLACE VIEW mart.triage_queue AS " + _QUEUE_SELECT.replace("__SNAPSHOT__", _LATEST) + _QUEUE_ORDER,
    "CREATE OR REPLACE VIEW mart.baseline_worklist AS " + _WORKLIST_SELECT + _WORKLIST_ORDER,
    "CREATE OR REPLACE VIEW mart.watch_list AS " + _WATCH_SELECT + _WATCH_ORDER,
])


# ---------------------------------------------------------------------------------------------- snapshot info
def snapshot_info(con) -> dict:
    """Latest and previous snapshot dates, ``meta.last_updated``, source and count (header, AC-5.4)."""
    rows = con.execute("SELECT snapshot_date, source, meta_last_updated, record_count FROM stage.snapshot "
                       "ORDER BY snapshot_date DESC").fetchall()
    if not rows:
        return {"latest": None, "previous": None, "meta_last_updated": None, "source": None, "snapshots": 0,
                "records": 0}
    latest = rows[0]
    return {"latest": latest[0], "previous": rows[1][0] if len(rows) > 1 else None,
            "meta_last_updated": latest[2], "source": latest[1], "snapshots": len(rows), "records": latest[3],
            "synthetic_history": any(str(r[1]).startswith("SYNTHETIC") for r in rows)}


def change_counts(con, snapshot_date: dt.date | None = None) -> dict[str, int]:
    """change_type -> count for one snapshot (default: latest), all events, not only the ones we stock."""
    target = "?" if snapshot_date else _LATEST
    params = [snapshot_date] if snapshot_date else []
    return dict(con.execute(f"SELECT change_type, count(*) FROM core.change_feed WHERE snapshot_date = {target} "
                            "GROUP BY 1 ORDER BY 1", params).fetchall())


# ---------------------------------------------------------------------------------------------- queue
def _has_view(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'mart' "
                            "AND table_name = ?", [name]).fetchone()[0])


def is_baseline_day(con) -> bool:
    """True when the latest snapshot is the earliest one: nothing to compare with yet (ADR 007 point 1)."""
    r = con.execute(f"SELECT {_LATEST} = {_BASELINE}").fetchone()
    return bool(r[0]) if r and r[0] is not None else True


def triage_queue(con) -> pd.DataFrame:
    """The daily queue, UNCAPPED and ranked (AC-5.1, 5.2, ADR 007). The UI shows the top ``QUEUE_CAP``.

    Uses ``mart.triage_queue`` when the build created it, else the same SQL inline.
    """
    if _has_view(con, "triage_queue"):
        return con.execute("SELECT * FROM mart.triage_queue" + _QUEUE_ORDER).fetchdf()
    return queue_for_snapshot(con, None)


def queue_for_snapshot(con, snapshot_date: dt.date | None) -> pd.DataFrame:
    """The daily queue as it was on one snapshot date (simulation, metrics). Empty on the baseline snapshot."""
    if snapshot_date is None:
        return con.execute(_QUEUE_SELECT.replace("__SNAPSHOT__", _LATEST) + _QUEUE_ORDER).fetchdf()
    return con.execute(_QUEUE_SELECT.replace("__SNAPSHOT__", "?::DATE") + _QUEUE_ORDER, [snapshot_date]).fetchdf()


def baseline_worklist(con) -> pd.DataFrame:
    """Item-grain backlog of actionable matched shortages in the latest snapshot (ADR 007 point 2)."""
    sql = "SELECT * FROM mart.baseline_worklist" if _has_view(con, "baseline_worklist") else _WORKLIST_SELECT
    return con.execute(sql + _WORKLIST_ORDER).fetchdf()


def watch_list(con) -> pd.DataFrame:
    """Matched events that fail the actionable filter (ADR 007 point 3). Counted and shown, never dropped."""
    sql = "SELECT * FROM mart.watch_list" if _has_view(con, "watch_list") else _WATCH_SELECT
    return con.execute(sql + _WATCH_ORDER).fetchdf()


def other_changes(con) -> pd.DataFrame:
    """Latest-snapshot changes that are neither in the queue nor on the Watch list: not stocked, or IMPROVED /
    RESOLVED / DROPPED. Baseline rows are not changes and are excluded. Shown for transparency."""
    return con.execute(f"""
        SELECT f.change_type, f.event_key, e.generic_name, f.prev_availability, f.curr_availability,
               f.prev_status, f.curr_status,
               EXISTS (SELECT 1 FROM link.shortage_event_facility l WHERE l.event_key = f.event_key) AS stocked
        FROM core.change_feed f LEFT JOIN obj.shortage_event e USING (event_key)
        WHERE f.snapshot_date = {_LATEST} AND f.change_type <> 'UNCHANGED' AND f.prev_snapshot_date IS NOT NULL
          AND NOT (f.change_type IN ('NEW', 'WORSENED')
                   AND EXISTS (SELECT 1 FROM link.shortage_event_facility l WHERE l.event_key = f.event_key))
        ORDER BY f.change_type, e.generic_name""").fetchdf()


def event_choices(con) -> pd.DataFrame:
    """Every shortage event that touches our formulary, queue items first (for the object-view picker)."""
    return con.execute(f"""
        SELECT e.event_key, e.generic_name, e.availability_normalized,
               coalesce(q.change_type, '') AS change_type
        FROM obj.shortage_event e
        LEFT JOIN (SELECT event_key, change_type FROM core.change_feed
                   WHERE snapshot_date = {_LATEST} AND change_type IN ('NEW', 'WORSENED')
                     AND prev_snapshot_date IS NOT NULL) q USING (event_key)
        WHERE EXISTS (SELECT 1 FROM link.shortage_event_facility l WHERE l.event_key = e.event_key)
        ORDER BY (q.change_type IS NULL), e.generic_name, e.event_key""").fetchdf()


# ---------------------------------------------------------------------------------------------- object view
def _records(con, sql: str, params: list) -> list[dict]:
    df = con.execute(sql, params).fetchdf()
    return df.astype(object).where(df.notna(), None).to_dict("records")


def drug_object(con, event_key: str) -> dict:
    """Everything the object view shows for one shortage event and its Drug (AC-5.3).

    Keys: event, drug, sibling_events (other ShortageEvents linked to the same Drug), history, changes, facilities,
    substitutes, tasks, audit. ``event`` is None when the key is unknown.
    """
    ev = _records(con, "SELECT * FROM obj.shortage_event WHERE event_key = ?", [event_key])
    if not ev:
        return {"event": None, "drug": None, "sibling_events": [], "history": [], "changes": [], "facilities": [],
                "substitutes": [], "tasks": [], "audit": pd.DataFrame()}
    event = ev[0]
    ndc11 = event.get("ndc11")
    drug = _records(con, "SELECT * FROM obj.drug WHERE ndc11 = ?", [ndc11])
    siblings = _records(con, """
        SELECT e.event_key, e.status, e.availability_normalized, e.initial_posting_date, e.present_in_latest
        FROM link.drug_shortage_event l JOIN obj.shortage_event e USING (event_key)
        WHERE l.ndc11 = ? AND l.event_key <> ? ORDER BY e.initial_posting_date""", [ndc11, event_key])
    history = _records(con, """
        SELECT valid_from, valid_to, is_current, present, status, availability_normalized, shortage_reason,
               update_date
        FROM core.shortage_history WHERE event_key = ? ORDER BY valid_from""", [event_key])
    changes = _records(con, """
        SELECT snapshot_date, prev_snapshot_date, change_type, rule, prev_status, curr_status, prev_availability,
               curr_availability
        FROM core.change_feed WHERE event_key = ? ORDER BY snapshot_date""", [event_key])
    facilities = _records(con, """
        SELECT l.facility_id, f.name AS facility, l.item_id, i.name_raw AS formulary_name, i.ndc_raw,
               l.match_tier, l.match_confidence, l.matched_on, l.days_on_hand
        FROM link.shortage_event_facility l
        JOIN obj.facility f USING (facility_id)
        LEFT JOIN seed.formulary_item i USING (item_id)
        WHERE l.event_key = ? ORDER BY l.days_on_hand, l.facility_id, l.item_id""", [event_key])
    substitutes = _records(con, """
        SELECT s.substitute_id, coalesce(s.state, 'candidate') AS state, s.alt_ndc11, s.alt_item_id,
               d.generic_name AS alt_name, d.manufacturer AS alt_manufacturer, d.strength_norm AS alt_strength,
               s.basis, s.reason, s.actor_id, s.edited_at_utc,
               EXISTS (SELECT 1 FROM obj.shortage_event x
                       WHERE x.present_in_latest
                         AND (x.ndc11 = s.alt_ndc11 OR x.product_ndc9 = coalesce(d.product_ndc9, left(s.alt_ndc11, 9)))
                         AND (x.status = 'Current' OR x.availability_normalized IN ('Limited', 'Unavailable')))
                   AS alt_is_short
        FROM obj.substitute s LEFT JOIN obj.drug d ON d.ndc11 = s.alt_ndc11
        WHERE s.event_key = ?
        ORDER BY CASE coalesce(s.state, 'candidate') WHEN 'approved' THEN 0 WHEN 'proposed' THEN 1
                 WHEN 'candidate' THEN 2 ELSE 3 END, s.substitute_id""", [event_key])
    tasks = _records(con, """
        SELECT t.task_id, t.state, t.facility_id, f.name AS facility, t.assignee_id, u.display_name AS assignee,
               t.due_date, t.note, t.edited_at_utc
        FROM obj.mitigation_task t
        LEFT JOIN obj.facility f USING (facility_id)
        LEFT JOIN seed.app_user u ON u.user_id = t.assignee_id
        WHERE t.event_key = ? ORDER BY t.state DESC, t.task_id""", [event_key])
    keys = [event_key] + [s["substitute_id"] for s in substitutes] + [t["task_id"] for t in tasks]
    return {"event": event, "drug": drug[0] if drug else None, "sibling_events": siblings, "history": history,
            "changes": changes, "facilities": facilities, "substitutes": substitutes, "tasks": tasks,
            "audit": audit_entries(con, object_key=keys)}


# ---------------------------------------------------------------------------------------------- audit
def audit_entries(con, object_key: str | list[str] | None = None, actor_id: str | None = None,
                  action_type: str | None = None, outcome: str | None = None) -> pd.DataFrame:
    """Action-log rows, newest first, filtered by object key(s), actor, action type and outcome (AC-7.4)."""
    where, params = [], []
    if object_key is not None:
        keys = [object_key] if isinstance(object_key, str) else list(object_key)
        if not keys:
            keys = ["\x00"]
        where.append(f"a.object_key IN ({', '.join('?' * len(keys))})")
        params += keys
    if actor_id:
        where.append("a.actor_id = ?")
        params.append(actor_id)
    if action_type:
        where.append("a.action_type = ?")
        params.append(action_type)
    if outcome:
        where.append("a.outcome = ?")
        params.append(outcome)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return con.execute(f"""
        SELECT a.seq, a.ts_utc, a.action_type, a.object_type, a.object_key, a.actor_id,
               u.display_name AS actor, a.role, a.outcome, a.failed_criterion, a.reason, a.old_value, a.new_value,
               a.prev_hash, a.row_hash
        FROM audit.action_log a LEFT JOIN seed.app_user u ON u.user_id = a.actor_id
        {clause} ORDER BY a.seq DESC""", params).fetchdf()


# ---------------------------------------------------------------------------------------------- people + rules
def users(con) -> pd.DataFrame:
    return con.execute("SELECT user_id, display_name, role, active FROM seed.app_user "
                       "ORDER BY CASE role WHEN 'pharmacist' THEN 0 WHEN 'buyer' THEN 1 ELSE 2 END, "
                       "NOT active, display_name").fetchdf()


def facilities(con) -> pd.DataFrame:
    return con.execute("SELECT facility_id, name, kind, beds FROM obj.facility ORDER BY facility_id").fetchdf()


def action_permission(con, user_id: str, action: str) -> tuple[bool, str]:
    """Can this demo user run ``action``? Roles come from the ontology via ``console.actions`` and the user's role
    from ``seed.app_user`` (``console.identity``), so the UI shows exactly the rule the action layer enforces.

    This only drives the button state; the action layer re-checks on every call (SPEC R9).
    """
    from console.actions import ontology
    from console.identity import get_actor

    spec = ontology().actions[action]
    actor = get_actor(con, user_id)
    allowed = sorted(spec.roles)
    if not actor.active:
        return False, f"{actor.user_id} is not an active user. Requires an active {' or '.join(allowed)}."
    if actor.role not in spec.roles:
        return False, (f"Requires role: {' or '.join(allowed)}. You are acting as a {actor.role}. "
                       "The action layer refuses this and logs it, even if the button is bypassed.")
    return True, f"Allowed for {' or '.join(allowed)}."


def parse_json(value) -> dict | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
