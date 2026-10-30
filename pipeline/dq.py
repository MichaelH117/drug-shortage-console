"""Data-quality checks, stored as rows in ``core.dq_results`` on every load (owner: data-engineer, AC-1.5, AC-2.5).

One row per (run_id, snapshot_date, check_name). Status is PASS / WARN / FAIL / SKIPPED. Checks:

* ``snapshot_integrity``   PASS per valid folder (manifest and sha256 re-checked)
* ``snapshot_skipped``     manifest, sha256 or duplicate-date problem: FAIL, and the folder is skipped (ADR 004)
* ``record_count``         record_count = meta_total
* ``volume``               record count within +/- ``dq.volume_tolerance`` of the previous snapshot (a); SKIPPED on
                           the first snapshot
* ``freshness``            meta.last_updated no older than ``dq.freshness_days`` before the snapshot date (b). Measured
                           against the snapshot date, not the wall clock, so rebuilding old snapshots is deterministic
* ``schema_drift``         unexpected or missing top-level field, named in ``detail`` (c)
* ``dates_unparseable``    count of records with a date that is not MM/DD/YYYY (d)
* ``availability_unmapped`` raw values that map to no bucket (they go to Unknown), named in ``detail`` (AC-2.5)
* ``availability_missing`` records with no availability (informational, PASS)
* ``availability_to_unknown`` events that moved from a ranked bucket to Unknown (DESIGN 3.5)
* ``event_key_fallback``   event keys built from the raw NDC or the presentation text
* ``duplicate_event_keys`` extra records sharing an event key in one snapshot (kept: latest update_date)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from pipeline.config import load_settings
from pipeline.snapshots import SnapshotRef

DQ_COLUMNS = ["run_id", "snapshot_date", "check_name", "status", "measured", "threshold", "detail",
              "checked_at_utc"]


def _row(snapshot_date, check, status, measured=None, threshold=None, detail=None) -> dict:
    return {"snapshot_date": snapshot_date, "check_name": check, "status": status,
            "measured": None if measured is None else float(measured),
            "threshold": None if threshold is None else float(threshold), "detail": detail}


def schema_drift(records: list[dict], schema: dict | None = None) -> tuple[list[str], list[str]]:
    """Return ``(unexpected, missing)`` top-level field names for one snapshot's records (both sorted)."""
    schema = schema or load_settings()["schema"]
    seen = {k for rec in records for k in rec}
    unexpected = sorted(seen - set(schema["known_fields"]))
    missing = sorted(set(schema["required_fields"]) - seen)
    return unexpected, missing


def schema_drift_row(snapshot_date, records: list[dict], schema: dict | None = None) -> dict:
    unexpected, missing = schema_drift(records, schema)
    if not unexpected and not missing:
        return _row(snapshot_date, "schema_drift", "PASS", 0, 0, None)
    parts = []
    if unexpected:
        parts.append("unexpected: " + ", ".join(unexpected))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    return _row(snapshot_date, "schema_drift", "FAIL", len(unexpected) + len(missing), 0, "; ".join(parts))


def _parse_iso(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def check_snapshots(refs: list[SnapshotRef], stage: pd.DataFrame, settings: dict | None = None) -> list[dict]:
    """All DQ rows for a set of snapshots. Pure (no DB). ``stage`` = parsed rows of the valid snapshots."""
    s = settings or load_settings()
    tol = float(s["dq"]["volume_tolerance"])
    max_age = int(s["dq"]["freshness_days"])
    rows: list[dict] = []
    for r in refs:
        if r.problem is not None:
            snap = r.snapshot_date if r.snapshot_date != dt.date.min else None
            rows.append(_row(snap, "snapshot_skipped", "FAIL", 1, 0, f"{r.path.name}: {r.problem}; skipped"))

    valid = sorted((r for r in refs if r.problem is None), key=lambda r: r.snapshot_date)
    prev_ref: SnapshotRef | None = None
    prev_rows: pd.DataFrame | None = None
    for r in valid:
        d = r.snapshot_date
        m = r.manifest
        rows.append(_row(d, "snapshot_integrity", "PASS", 0, 0, r.path.name))
        count, total = int(m.get("record_count") or 0), int(m.get("meta_total") or 0)
        rows.append(_row(d, "record_count", "PASS" if count == total else "FAIL", count, total,
                         None if count == total else f"record_count {count} != meta_total {total}"))
        if prev_ref is None:
            rows.append(_row(d, "volume", "SKIPPED", count, tol, "first snapshot: nothing to compare"))
        else:
            prev_count = int(prev_ref.manifest.get("record_count") or 0)
            change = (count - prev_count) / prev_count if prev_count else float("inf")
            ok = abs(change) <= tol
            rows.append(_row(d, "volume", "PASS" if ok else "FAIL", round(change, 4), tol,
                             f"{prev_count} -> {count} vs {prev_ref.snapshot_date}"))
        last = _parse_iso(m.get("meta_last_updated"))
        if last is None:
            rows.append(_row(d, "freshness", "FAIL", None, max_age,
                             f"meta.last_updated missing or not ISO: {m.get('meta_last_updated')!r}"))
        else:
            age = (d - last).days
            rows.append(_row(d, "freshness", "PASS" if age <= max_age else "FAIL", age, max_age,
                             f"meta.last_updated {last.isoformat()}"))
        rows.append(schema_drift_row(d, r.load_records(), s["schema"]))

        cur = stage[stage["snapshot_date"] == d]
        bad = cur["bad_dates"].dropna()
        fields = sorted({f for v in bad for f in str(v).split(",")})
        rows.append(_row(d, "dates_unparseable", "WARN" if len(bad) else "PASS", len(bad), 0,
                         ("fields: " + ", ".join(fields)) if fields else None))
        unmapped = cur[~cur["availability_mapped"].astype(bool)]
        values = sorted(unmapped["availability_raw"].astype(str).unique())
        rows.append(_row(d, "availability_unmapped", "WARN" if len(unmapped) else "PASS", len(unmapped), 0,
                         ("values: " + " | ".join(values)) if values else None))
        missing = int(cur["availability_raw"].isna().sum())
        rows.append(_row(d, "availability_missing", "PASS", missing, None,
                         "no availability field (To Be Discontinued / Resolved records); bucket Unknown"))
        if prev_rows is None:
            rows.append(_row(d, "availability_to_unknown", "SKIPPED", None, 0, "first snapshot"))
        else:
            j = cur.merge(prev_rows[["event_key", "availability_normalized"]], on="event_key",
                          suffixes=("", "_prev"))
            # Only text that maps to nothing counts; a record that simply has no availability (Resolved, To Be
            # Discontinued) is a known state, reported by availability_missing.
            moved = j[(j["availability_normalized"] == "Unknown") & (j["availability_normalized_prev"] != "Unknown")
                      & j["availability_raw"].notna()]
            keys = sorted(moved["event_key"])
            rows.append(_row(d, "availability_to_unknown", "WARN" if keys else "PASS", len(keys), 0,
                             ("events: " + ", ".join(keys[:20])) if keys else None))
        fb = cur[cur["event_key_source"] != "ndc11"]
        rows.append(_row(d, "event_key_fallback", "WARN" if len(fb) else "PASS", len(fb), 0,
                         ("keys: " + ", ".join(sorted(fb["event_key"])[:20])) if len(fb) else None))
        dups = cur[cur["dup_count"] > 1]
        extra = int((dups["dup_count"] - 1).sum())
        rows.append(_row(d, "duplicate_event_keys", "WARN" if extra else "PASS", extra, 0,
                         ("kept latest update_date for: " + ", ".join(sorted(dups["event_key"])[:20]))
                         if extra else None))
        prev_ref, prev_rows = r, cur
    return rows


def run_dq(con, run_id: str, snapshots: list[SnapshotRef], stage: pd.DataFrame | None = None,
           now: dt.datetime | None = None) -> pd.DataFrame:
    """Run every check and append the rows to ``core.dq_results`` (the table must exist). Returns the rows.

    ``stage`` defaults to ``stage.shortage_record`` read from ``con``.
    """
    if stage is None:
        stage = con.execute("SELECT * FROM stage.shortage_record").df()
        stage["snapshot_date"] = pd.to_datetime(stage["snapshot_date"]).dt.date
    checked = (now or dt.datetime.now(dt.UTC)).replace(tzinfo=None, microsecond=0)
    rows = check_snapshots(snapshots, stage)
    df = pd.DataFrame(rows)
    df.insert(0, "run_id", run_id)
    df["checked_at_utc"] = checked
    df = df[DQ_COLUMNS]
    con.register("_dq_frame", df)
    try:
        con.execute(
            """INSERT INTO core.dq_results
               SELECT run_id, CAST(snapshot_date AS DATE), check_name, status, CAST(measured AS DOUBLE),
                      CAST(threshold AS DOUBLE), detail, CAST(checked_at_utc AS TIMESTAMP) FROM _dq_frame""")
    finally:
        con.unregister("_dq_frame")
    return df
