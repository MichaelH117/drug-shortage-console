"""Parse one snapshot folder into ``stage.shortage_record`` rows (owner: data-engineer, DESIGN 3.2, AC-1.5, AC-2.5).

* Dates are ``MM/DD/YYYY`` strings; an unparseable date becomes null and its field name is listed in ``bad_dates``.
* NDC: the TOP-LEVEL ``package_ndc`` only (SPEC R4), via ``pipeline.ndc.normalize_ndc``. ``openfda.package_ndc`` is
  kept as JSON for display and tier 2 context, never as the key.
* event_key = ``ndc11|initial_posting_date ISO``; fallbacks ``raw:<package_ndc>|date`` (NDC unparseable) and
  ``pres:<sha1(presentation)[:12]>|date`` (no package_ndc). ``event_key_source`` records which rule was used.
* Duplicate event_keys in one snapshot: keep the latest ``update_date``, then the first in API order. The kept row's
  ``dup_count`` says how many records shared the key, so DQ can WARN with the count.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pandas as pd

from pipeline.availability import normalize_availability
from pipeline.ndc import normalize_ndc
from pipeline.snapshots import SnapshotRef

DATE_FIELDS = ("update_date", "change_date", "initial_posting_date", "discontinued_date")
OPENFDA_LISTS = {
    "openfda_rxcui": "rxcui",
    "openfda_route": "route",
    "openfda_substance_name": "substance_name",
    "openfda_product_ndc": "product_ndc",
    "openfda_package_ndc": "package_ndc",
    "openfda_brand_name": "brand_name",
    "openfda_manufacturer_name": "manufacturer_name",
}
STAGE_COLUMNS = [
    "snapshot_date", "event_key", "event_key_source", "api_order", "package_ndc_raw", "ndc11", "ndc_reason",
    "product_ndc9", "generic_name", "proprietary_name", "company_name", "presentation", "dosage_form", "strength",
    "therapeutic_category", "status", "availability_raw", "availability_normalized", "availability_mapped",
    "shortage_reason", "update_date", "change_date", "initial_posting_date", "discontinued_date", "bad_dates",
    "has_openfda", *OPENFDA_LISTS, "dup_count", "record_hash",
]


def parse_us_date(value) -> tuple[dt.date | None, bool]:
    """``"10/19/2026"`` -> ``(date, True)``. Missing -> ``(None, True)``. Unparseable -> ``(None, False)``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, True
    try:
        return dt.datetime.strptime(str(value).strip(), "%m/%d/%Y").date(), True
    except ValueError:
        return None, False


def _json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def record_hash(rec: dict) -> str:
    """sha256 of the whole raw record (sorted keys), so unknown upstream fields still change the hash (T12)."""
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def event_key_for(package_ndc_raw: str | None, ndc11: str | None, presentation: str | None,
                  initial_posting: dt.date | None) -> tuple[str, str]:
    """Return ``(event_key, source)`` with source ``ndc11`` / ``raw`` / ``presentation`` (DESIGN 3.2)."""
    date_part = initial_posting.isoformat() if initial_posting else ""
    if ndc11:
        return f"{ndc11}|{date_part}", "ndc11"
    if package_ndc_raw is not None and str(package_ndc_raw).strip():
        return f"raw:{str(package_ndc_raw).strip()}|{date_part}", "raw"
    digest = hashlib.sha1((presentation or "").encode("utf-8")).hexdigest()[:12]
    return f"pres:{digest}|{date_part}", "presentation"


def parse_records(records: list[dict], snapshot_date: dt.date, mapping: dict) -> pd.DataFrame:
    """Raw API records -> one row per event_key (duplicates resolved). Pure; used by ``parse_snapshot``."""
    rows = []
    for order, rec in enumerate(records):
        dates, bad = {}, []
        for f in DATE_FIELDS:
            d, ok = parse_us_date(rec.get(f))
            dates[f] = d
            if not ok:
                bad.append(f)
        pkg = rec.get("package_ndc")
        ndc = normalize_ndc(pkg)
        key, source = event_key_for(pkg, ndc.ndc11, rec.get("presentation"), dates["initial_posting_date"])
        bucket, mapped = normalize_availability(rec.get("availability"), mapping)
        openfda = rec.get("openfda") if isinstance(rec.get("openfda"), dict) else None
        row = {
            "snapshot_date": snapshot_date,
            "event_key": key,
            "event_key_source": source,
            "api_order": order,
            "package_ndc_raw": pkg,
            "ndc11": ndc.ndc11,
            "ndc_reason": ndc.reason,
            "product_ndc9": ndc.product_ndc9,
            "generic_name": rec.get("generic_name"),
            "proprietary_name": rec.get("proprietary_name"),
            "company_name": rec.get("company_name"),
            "presentation": rec.get("presentation"),
            "dosage_form": rec.get("dosage_form"),
            "strength": _json(rec.get("strength")),
            "therapeutic_category": _json(rec.get("therapeutic_category")),
            "status": rec.get("status"),
            "availability_raw": rec.get("availability"),
            "availability_normalized": bucket,
            "availability_mapped": mapped,
            "shortage_reason": rec.get("shortage_reason"),
            **dates,
            "bad_dates": ",".join(bad) or None,
            "has_openfda": openfda is not None,
            **{col: _json(openfda.get(src)) if openfda else None for col, src in OPENFDA_LISTS.items()},
            "dup_count": 1,
            "record_hash": record_hash(rec),
        }
        rows.append(row)
    df = pd.DataFrame(rows, columns=STAGE_COLUMNS)
    if df.empty:
        return df
    counts = df.groupby("event_key")["event_key"].transform("size")
    df["dup_count"] = counts.astype(int)
    # Keep the latest update_date, then the first in API order (nulls sort last).
    df["_upd"] = pd.to_datetime(df["update_date"])
    df = df.sort_values(["event_key", "_upd", "api_order"], ascending=[True, False, True], na_position="last")
    df = df.drop_duplicates("event_key", keep="first").drop(columns="_upd")
    return df.sort_values("api_order").reset_index(drop=True)


def parse_snapshot(ref: SnapshotRef, mapping: dict) -> pd.DataFrame:
    """``stage.shortage_record`` rows for one snapshot folder."""
    return parse_records(ref.load_records(), ref.snapshot_date, mapping)


def parse_all(refs: list[SnapshotRef], mapping: dict) -> pd.DataFrame:
    """All valid snapshots stacked, sorted by (snapshot_date, api_order). Order of ``refs`` does not matter."""
    frames = [parse_snapshot(r, mapping) for r in sorted(refs, key=lambda r: r.snapshot_date) if r.problem is None]
    if not frames:
        return pd.DataFrame(columns=STAGE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def snapshot_rows(refs: list[SnapshotRef]) -> pd.DataFrame:
    """``stage.snapshot`` rows (valid snapshots only)."""
    rows = []
    for r in sorted(refs, key=lambda r: r.snapshot_date):
        if r.problem is not None:
            continue
        m = r.manifest
        last = m.get("meta_last_updated")
        rows.append({
            "snapshot_date": r.snapshot_date,
            "folder": r.path.name,
            "source": m.get("source"),
            "fetched_at_utc": dt.datetime.fromisoformat(m["fetched_at_utc"]) if m.get("fetched_at_utc") else None,
            "meta_last_updated": dt.date.fromisoformat(last) if last else None,
            "meta_total": int(m.get("meta_total") or 0),
            "record_count": int(m.get("record_count") or 0),
            "records_sha256": (m.get("sha256") or {}).get("records.json"),
        })
    return pd.DataFrame(rows, columns=["snapshot_date", "folder", "source", "fetched_at_utc", "meta_last_updated",
                                       "meta_total", "record_count", "records_sha256"])
