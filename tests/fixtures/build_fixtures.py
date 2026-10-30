"""Rebuild the offline fixtures from ONE cached real openFDA pull (owner: data-engineer, DE-02).

Usage (no network; reads a snapshot folder already saved by ``pipeline.snapshots.ensure_snapshot``):

    .venv/Scripts/python tests/fixtures/build_fixtures.py data/live/raw/2026-10-30

Writes, deterministically (same input -> byte-identical output):

* ``openfda_page_sample.json``: about 80 REAL public records (unchanged), chosen by the rules in ``select()``, plus the
  real ``meta`` block with ``results.total`` set to the sample size. Trimmed = fewer records, not edited records.
* ``snapshots/SYNTHETIC_D1``, ``SYNTHETIC_D2``, ``SYNTHETIC_D3``: three fake days built from the sample, with the
  PLANTED changes listed in ``PLANTS`` (source ``SYNTHETIC_fixture``). The records are real; the day-to-day changes
  are synthetic (DESIGN T5).
* ``SYNTHETIC_renamed_field.json``: one page where ``availability`` is renamed to ``availabilty`` (AC-1.5 c).

``expected_changes.csv`` is NOT written here. It is hand-written from ``PLANTS`` (DE-02), so the change classifier is
checked against an answer key that no code produced.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.snapshots import make_writable, write_snapshot  # noqa: E402

SAMPLE_SIZE = 80
DAYS = {"SYNTHETIC_D1": dt.date(2026, 10, 28), "SYNTHETIC_D2": dt.date(2026, 10, 29),
        "SYNTHETIC_D3": dt.date(2026, 10, 30)}
FETCHED_AT = {name: dt.datetime.combine(d, dt.time(11, 30)) for name, d in DAYS.items()}  # fixed: deterministic

# Hand-picked real records (by top-level package_ndc; every record with that NDC is taken, so real duplicates come
# along). Grouped by what they exercise.
PICKS = [
    # SPEC R4 regression: top-level 0378-6860-77, openfda.package_ndc lists other packages first. Same product family.
    "0378-6860-77", "0378-6858-77", "0406-5111-01", "59417-117-01",
    # real duplicate event keys in one snapshot (same package_ndc + initial_posting_date twice)
    "0409-1160-01", "65862-618-90",
    # no dosage_form; 11-digit NDCs already 5-4-2
    "0173-0716-22", "75987-050-07", "76045-0210-10", "76045-0212-10",
    # Resolved records (Resolved status has no availability)
    "25021-321-10", "64980-642-03", "0487-9901-30",
    # same ingredient, several routes / forms / strengths (hard negatives for matching)
    "0409-1893-01", "63323-452-01", "0832-0273-11",
    "0409-2634-50", "0574-7224-06", "0641-0121-25",
    "0409-3793-01", "25021-701-01",
    "23155-473-41", "16729-500-08",
    "0338-3612-50", "0009-0395-14",
    "62037-710-01",
    # records with no openfda block (AC-4.6), incl. 5-4-1 NDCs
    "44567-600-04", "49884-125-91", "0093-7767-24", "0641-6024-10", "0641-6028-25", "61958-0602-1", "70121-2537-6",
]
NAME_PICKS = ["Hydroxychloroquine", "Hydroxocobalamin", "Ketorolac Tromethamine Tablet", "Furosemide Oral"]

# Planted changes. Keyed by package_ndc; every value is applied to the record(s) with that NDC on that day.
# None = record absent that day. Records not listed are identical on all three days (real D3 values).
# D3 is the real pull. D1/D2 are edited copies. A comment names the change type each one is meant to produce.
PLANTS: dict[str, dict[str, dict | None]] = {
    # Real D3 values are in brackets. Expected change types: D2 = D1 -> D2, D3 = D2 -> D3.
    "44567-600-04": {"SYNTHETIC_D1": None},                                     # D2 NEW
    "70121-2537-6": {"SYNTHETIC_D1": None},                                     # D2 NEW
    "0093-7767-24": {"SYNTHETIC_D2": None, "SYNTHETIC_D3": None},               # D2 DROPPED
    "0641-6024-10": {"SYNTHETIC_D1": {"availability": "Available"},             # D2 WORSENED, D3 WORSENED
                     "SYNTHETIC_D2": {"availability": "Limited Availability"}},  #   [Unavailable]
    "0409-1893-01": {"SYNTHETIC_D1": {"availability": "Limited Availability"}},  # D2 WORSENED [Unavailable]
    "0641-0121-25": {"SYNTHETIC_D1": {"availability": "Unavailable"}},          # D2 IMPROVED [Available]
    "0487-9901-30": {"SYNTHETIC_D1": {"status": "Current", "availability": "Limited Availability"}},  # D2 RESOLVED
    "62037-710-01": {"SYNTHETIC_D1": {"status": "Resolved"}},                   # D2 WORSENED reopened [TBD]
    "0009-0395-14": {"SYNTHETIC_D1": {"status": "Current"}},                    # D2 WORSENED Current -> TBD
    "0574-7224-06": {"SYNTHETIC_D1": {"status": "Current", "availability": "Available"},  # D2 UNCHANGED (to
                     "SYNTHETIC_D2": {"status": "Current",                                # Unknown, DQ row),
                                      "availability": "SYNTHETIC call distributor for status"}},  # D3 WORSENED [TBD]
    "75987-050-07": {"SYNTHETIC_D1": {"shortage_reason": "SYNTHETIC earlier reason"}},  # D2 UNCHANGED, SCD2 version
    "0409-2634-50": {"SYNTHETIC_D1": None, "SYNTHETIC_D2": None},               # D3 NEW
    "0338-3612-50": {"SYNTHETIC_D1": None, "SYNTHETIC_D2": None},               # D3 NEW
    "63323-452-01": {"SYNTHETIC_D1": None, "SYNTHETIC_D2": None},               # D3 NEW
    "0378-6860-77": {"SYNTHETIC_D1": {"availability": "Unavailable"},           # D3 IMPROVED [Available]
                     "SYNTHETIC_D2": {"availability": "Unavailable"}},
    "23155-473-41": {"SYNTHETIC_D1": {"availability": "Unavailable"},           # D3 IMPROVED [Available]
                     "SYNTHETIC_D2": {"availability": "Unavailable"}},
    "0409-3793-01": {"SYNTHETIC_D1": {"availability": "Available"},             # D3 WORSENED [Unavailable]
                     "SYNTHETIC_D2": {"availability": "Available"}},
    "16729-500-08": {"SYNTHETIC_D1": {"availability": "Limited Availability"},  # D3 WORSENED [Unavailable]
                     "SYNTHETIC_D2": {"availability": "Limited Availability"}},
    "64980-642-03": {"SYNTHETIC_D1": {"status": "Current", "availability": "Unavailable"},  # D3 RESOLVED
                     "SYNTHETIC_D2": {"status": "Current", "availability": "Unavailable"}},
    "49884-125-91": {"SYNTHETIC_D3": None},                                     # D3 DROPPED
    "0832-0273-11": {"SYNTHETIC_D1": {"update_date": "10/01/2025"},             # D3 UNCHANGED, SCD2 version
                     "SYNTHETIC_D2": {"update_date": "10/01/2025"}},            #   (only update_date moved)
}


def _key(rec: dict) -> tuple[str, str]:
    return rec.get("package_ndc", ""), rec.get("initial_posting_date", "")


def select(records: list[dict]) -> list[dict]:
    """Choose about ``SAMPLE_SIZE`` real records, keeping API order. Deterministic for a given input."""
    chosen: set[int] = set()

    def take(pred, limit=None):
        n = 0
        for i, r in enumerate(records):
            if i not in chosen and pred(r):
                chosen.add(i)
                n += 1
                if limit and n >= limit:
                    return

    for ndc in PICKS:
        take(lambda r, ndc=ndc: r.get("package_ndc") == ndc)
    for name in NAME_PICKS:
        take(lambda r, name=name: r.get("generic_name", "").startswith(name), limit=1)
    standard = {"Available", "Unavailable", "Limited Availability", None}
    take(lambda r: r.get("availability") not in standard)                       # every odd availability text
    take(lambda r: len(r.get("package_ndc", "")) == 12 and r["package_ndc"].count("-") == 2
         and len(r["package_ndc"].split("-")[2]) == 1, limit=4)                 # more 5-4-1
    take(lambda r: r.get("status") == "To Be Discontinued", limit=8)
    seen_names = {records[i].get("generic_name") for i in chosen}
    for i, r in enumerate(records):                                             # fill with distinct Current drugs
        if len(chosen) >= SAMPLE_SIZE:
            break
        if i not in chosen and r.get("status") == "Current" and r.get("generic_name") not in seen_names:
            chosen.add(i)
            seen_names.add(r.get("generic_name"))
    return [records[i] for i in sorted(chosen)]


def build_day(sample: list[dict], day: str) -> list[dict]:
    out = []
    for rec in sample:
        plant = PLANTS.get(rec.get("package_ndc"), {})
        if day in plant:
            change = plant[day]
            if change is None:
                continue
            rec = copy.deepcopy(rec)
            for k, v in change.items():
                if v is None:
                    rec.pop(k, None)
                else:
                    rec[k] = v
            if rec.get("status") == "Resolved":
                rec.pop("availability", None)       # real Resolved records carry no availability
        out.append(rec)
    return out


def _meta(real_meta: dict, total: int, last_updated: str) -> dict:
    meta = copy.deepcopy(real_meta)
    meta["results"] = {"skip": 0, "limit": 1000, "total": total}
    meta["last_updated"] = last_updated
    return meta


def main(src: Path) -> None:
    records = json.loads((src / "records.json").read_text(encoding="utf-8"))
    real_meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    sample = select(records)
    page = {"meta": _meta(real_meta, len(sample), real_meta.get("last_updated")), "results": sample}
    (HERE / "openfda_page_sample.json").write_text(json.dumps(page, indent=1, ensure_ascii=False) + "\n",
                                                   encoding="utf-8", newline="\n")

    snaps = HERE / "snapshots"
    if snaps.exists():
        make_writable(snaps)
        shutil.rmtree(snaps)
    snaps.mkdir()
    for name, day in DAYS.items():
        recs = build_day(sample, name)
        last_updated = (day - dt.timedelta(days=1)).isoformat()
        write_snapshot(snaps, name, day, recs, _meta(real_meta, len(recs), last_updated),
                       source="SYNTHETIC_fixture", fetched_at_utc=FETCHED_AT[name])

    renamed = []
    for rec in sample[:10]:
        rec = dict(rec)
        if "availability" in rec:
            rec["availabilty"] = rec.pop("availability")
        renamed.append(rec)
    bad = {"meta": _meta(real_meta, len(renamed), real_meta.get("last_updated")), "results": renamed}
    (HERE / "SYNTHETIC_renamed_field.json").write_text(json.dumps(bad, indent=1, ensure_ascii=False) + "\n",
                                                       encoding="utf-8", newline="\n")
    print(f"sample {len(sample)} records; days: "
          + ", ".join(f"{n}={len(build_day(sample, n))}" for n in DAYS))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "live" / "raw" / "2026-10-30")
