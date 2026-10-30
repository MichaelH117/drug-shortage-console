"""SCD type 2 status history and the ShortageEvent table (owner: data-engineer, AC-2.1, AC-2.4, ADR 004).

Pure functions of the stacked stage rows: the result depends only on the SET of snapshots, never on load order.

Tracked fields: ``status``, ``availability_normalized``, ``shortage_reason``, ``update_date``, plus ``present``.
A new version opens on the first snapshot where any of them differs from the open version. An event that is absent
from a later snapshot gets a ``present = false`` version (attributes carried from its last present row), and a new
``present = true`` version if it comes back. Gaps between snapshot dates are allowed: a version stays valid until the
next snapshot where it differs.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

TRACKED = ["status", "availability_normalized", "shortage_reason", "update_date"]
HISTORY_COLUMNS = ["event_key", "valid_from", "valid_to", "is_current", "present", *TRACKED, "attr_hash"]
EVENT_COLUMNS = [
    "event_key", "ndc11", "product_ndc9", "package_ndc_raw", "generic_name", "proprietary_name", "company_name",
    "presentation", "dosage_form", "strength", "therapeutic_category", "status", "availability_raw",
    "availability_normalized", "shortage_reason", "update_date", "change_date", "initial_posting_date",
    "discontinued_date", "has_openfda", "openfda_rxcui", "openfda_route", "openfda_substance_name",
    "openfda_product_ndc", "openfda_package_ndc", "openfda_brand_name", "openfda_manufacturer_name",
    "first_seen", "last_seen", "present_in_latest",
]


def _val(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.isoformat() if hasattr(v, "isoformat") else v


def attr_hash(present: bool, values: dict) -> str:
    payload = {"present": bool(present), **{k: _val(values.get(k)) for k in TRACKED}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def build_history(stage: pd.DataFrame, snapshot_dates: list | None = None) -> pd.DataFrame:
    """``core.shortage_history`` rows from all stacked stage rows (any row order).

    ``snapshot_dates`` defaults to the distinct dates in ``stage``; pass it when a snapshot may have zero records.
    """
    if stage.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    dates = sorted(set(snapshot_dates) if snapshot_dates is not None else set(stage["snapshot_date"]))
    by_event: dict[str, dict] = {}
    for rec in stage.sort_values(["snapshot_date", "event_key"]).to_dict("records"):
        by_event.setdefault(rec["event_key"], {})[rec["snapshot_date"]] = rec
    out = []
    for key in sorted(by_event):
        seen = by_event[key]
        first = min(seen)
        open_row: dict | None = None
        last_attrs: dict = {}
        for d in dates:
            if d < first:
                continue
            present = d in seen
            if present:
                last_attrs = {k: seen[d].get(k) for k in TRACKED}
            h = attr_hash(present, last_attrs)
            if open_row is not None and open_row["attr_hash"] == h:
                continue
            if open_row is not None:
                open_row["valid_to"] = d
                open_row["is_current"] = False
            open_row = {"event_key": key, "valid_from": d, "valid_to": None, "is_current": True,
                        "present": present, **{k: last_attrs.get(k) for k in TRACKED}, "attr_hash": h}
            out.append(open_row)
    return pd.DataFrame(out, columns=HISTORY_COLUMNS)


def build_events(stage: pd.DataFrame, snapshot_dates: list | None = None) -> pd.DataFrame:
    """``core.shortage_event``: one row per event_key ever seen, with its latest PRESENT attributes and
    first_seen / last_seen / present_in_latest (any row order).

    ``snapshot_dates`` (DE-09): the dates of ALL valid snapshots. Pass it, so that a latest snapshot with zero
    records makes every event ``present_in_latest = false``; without it the latest date is inferred from ``stage``.
    """
    if stage.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    latest_date = max(snapshot_dates) if snapshot_dates else max(stage["snapshot_date"])
    s = stage.sort_values(["event_key", "snapshot_date"])
    span = s.groupby("event_key")["snapshot_date"].agg(first_seen="min", last_seen="max").reset_index()
    last = s.drop_duplicates("event_key", keep="last").merge(span, on="event_key")
    last["present_in_latest"] = last["last_seen"] == latest_date
    return last[EVENT_COLUMNS].sort_values("event_key").reset_index(drop=True)
