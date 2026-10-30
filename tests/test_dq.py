"""DE-04: data-quality checks stored as rows (AC-1.5, AC-2.5)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from pipeline.config import FIXTURE_SNAPSHOTS, ROOT, connect, load_availability_map
from pipeline.dq import check_snapshots, run_dq, schema_drift, schema_drift_row
from pipeline.ingest import parse_all, parse_records
from pipeline.schema import DQ_DDL
from pipeline.snapshots import SnapshotRef, list_snapshots

MAP = load_availability_map()
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def refs():
    return list_snapshots(FIXTURE_SNAPSHOTS)


@pytest.fixture(scope="module")
def rows(refs):
    return pd.DataFrame(check_snapshots(refs, parse_all(refs, MAP)))


def _get(rows, day, check):
    r = rows[(rows["snapshot_date"].astype(str) == day) & (rows["check_name"] == check)]
    assert len(r) == 1, f"{day} {check}"
    return r.iloc[0]


def test_renamed_field_fails_schema_drift_and_names_it():
    bad = json.loads((FIX / "SYNTHETIC_renamed_field.json").read_text(encoding="utf-8"))["results"]
    unexpected, missing = schema_drift(bad)
    assert unexpected == ["availabilty"]
    assert missing == ["availability"]
    row = schema_drift_row(None, bad)
    assert row["status"] == "FAIL"
    assert "availabilty" in row["detail"] and "availability" in row["detail"]


def test_real_sample_has_no_schema_drift():
    good = json.loads((FIX / "openfda_page_sample.json").read_text(encoding="utf-8"))["results"]
    assert schema_drift(good) == ([], [])


def test_fixture_days_pass_volume_freshness_and_schema(rows):
    assert _get(rows, "2026-10-28", "volume")["status"] == "SKIPPED"
    for day in ("2026-10-29", "2026-10-30"):
        assert _get(rows, day, "volume")["status"] == "PASS"
    for day in ("2026-10-28", "2026-10-29", "2026-10-30"):
        assert _get(rows, day, "freshness")["status"] == "PASS"
        assert _get(rows, day, "schema_drift")["status"] == "PASS"
        assert _get(rows, day, "record_count")["status"] == "PASS"


def test_unmapped_availability_and_move_to_unknown_are_reported(rows):
    r = _get(rows, "2026-10-29", "availability_unmapped")
    assert r["status"] == "WARN" and "SYNTHETIC call distributor for status" in r["detail"]
    moved = _get(rows, "2026-10-29", "availability_to_unknown")
    assert moved["status"] == "WARN" and "00574722406|2026-07-23" in moved["detail"]
    assert _get(rows, "2026-10-30", "availability_unmapped")["status"] == "PASS"


def test_duplicate_keys_warn_with_count(rows):
    r = _get(rows, "2026-10-30", "duplicate_event_keys")
    assert r["status"] == "WARN" and r["measured"] == 2


def _ref(date, count, last_updated="2026-10-29"):
    return SnapshotRef(pd.Timestamp(date).date(), FIXTURE_SNAPSHOTS / "SYNTHETIC_D3",
                       {"record_count": count, "meta_total": count, "meta_last_updated": last_updated,
                        "sha256": {"records.json": "x"}})


def _check(refs):
    stage = pd.DataFrame(columns=["snapshot_date", "bad_dates", "availability_mapped", "availability_raw",
                                  "availability_normalized", "event_key", "event_key_source", "dup_count"])
    return pd.DataFrame(check_snapshots(refs, stage))


def test_volume_drop_of_25_percent_fails():
    out = _check([_ref("2026-10-29", 100), _ref("2026-10-30", 75)])
    r = out[(out["check_name"] == "volume") & (out["snapshot_date"].astype(str) == "2026-10-30")].iloc[0]
    assert r["status"] == "FAIL" and r["measured"] == -0.25


def test_volume_change_of_20_percent_passes():
    out = _check([_ref("2026-10-29", 100), _ref("2026-10-30", 80)])
    assert out[(out["check_name"] == "volume")]["status"].tolist() == ["SKIPPED", "PASS"]


def test_stale_meta_fails_freshness():
    out = _check([_ref("2026-10-30", 10, last_updated="2026-10-26")])
    assert out[out["check_name"] == "freshness"].iloc[0]["status"] == "FAIL"
    out = _check([_ref("2026-10-30", 10, last_updated="2026-10-27")])
    assert out[out["check_name"] == "freshness"].iloc[0]["status"] == "PASS"


def test_unparseable_dates_are_counted():
    rec = json.loads((FIX / "openfda_page_sample.json").read_text(encoding="utf-8"))["results"][0]
    stage = parse_records([dict(rec, update_date="31/12/2026")], pd.Timestamp("2026-10-30").date(), MAP)
    out = pd.DataFrame(check_snapshots([_ref("2026-10-30", 1)], stage))
    r = out[out["check_name"] == "dates_unparseable"].iloc[0]
    assert r["status"] == "WARN" and r["measured"] == 1 and "update_date" in r["detail"]


def test_skipped_folder_is_a_fail_row():
    bad = SnapshotRef(pd.Timestamp("2026-10-30").date(), FIXTURE_SNAPSHOTS / "X", {}, "sha256_mismatch: records.json")
    out = pd.DataFrame(check_snapshots([bad], pd.DataFrame()))
    assert out.iloc[0]["check_name"] == "snapshot_skipped" and out.iloc[0]["status"] == "FAIL"


def test_run_dq_writes_rows(refs):
    con = connect(":memory:")
    try:
        con.execute("CREATE SCHEMA core")
        con.execute(DQ_DDL)
        df = run_dq(con, "run-test", refs, parse_all(refs, MAP))
        n = con.execute("SELECT count(*) FROM core.dq_results WHERE run_id = 'run-test'").fetchone()[0]
        assert n == len(df) > 0
    finally:
        con.close()
