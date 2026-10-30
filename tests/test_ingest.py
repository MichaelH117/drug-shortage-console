"""DE-04: parsing, event keys, availability buckets (AC-2.5, DESIGN 3.2)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from pipeline.availability import BUCKETS, normalize_availability
from pipeline.config import FIXTURE_SNAPSHOTS, ROOT, load_availability_map
from pipeline.ingest import event_key_for, parse_all, parse_records, parse_us_date
from pipeline.snapshots import list_snapshots

MAP = load_availability_map()
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "openfda_page_sample.json").read_text(encoding="utf-8"))
DAY = dt.date(2026, 10, 30)


@pytest.fixture(scope="module")
def stage():
    return parse_all(list_snapshots(FIXTURE_SNAPSHOTS), MAP)


@pytest.mark.parametrize("raw,bucket", [
    ("Available", "Available"), ("Unavailable", "Unavailable"), ("Limited Availability", "Limited"),
    ("Limited Availalbility", "Limited"), ("Unvailable", "Unavailable"), ("  limited   AVAILABILITY ", "Limited"),
    ("Limited Supply Available. Next Delivery and Estimated Recovery: June 2027", "Limited"),
])
def test_observed_values_map(raw, bucket):
    assert normalize_availability(raw, MAP) == (bucket, True)


def test_missing_is_unknown_but_not_unmapped():
    assert normalize_availability(None, MAP) == ("Unknown", True)
    assert normalize_availability("  ", MAP) == ("Unknown", True)


def test_unmapped_text_goes_to_unknown():
    assert normalize_availability("call the distributor", MAP) == ("Unknown", False)


def test_every_raw_value_in_the_fixtures_maps_to_a_bucket(stage):
    pairs = stage[["availability_raw", "availability_normalized", "availability_mapped"]].drop_duplicates()
    assert set(pairs["availability_normalized"]) <= set(BUCKETS)
    unmapped = pairs[~pairs["availability_mapped"]]
    # The only unmapped value is the one planted on purpose in SYNTHETIC_D2.
    assert list(unmapped["availability_raw"]) == ["SYNTHETIC call distributor for status"]
    assert set(unmapped["availability_normalized"]) == {"Unknown"}
    real = {r.get("availability") for r in SAMPLE["results"]}
    assert all(normalize_availability(v, MAP)[1] for v in real), "a real API value is not in the map"


def test_dates_parse_and_bad_dates_become_null():
    assert parse_us_date("10/19/2026") == (dt.date(2026, 10, 19), True)
    assert parse_us_date(None) == (None, True)
    assert parse_us_date("2026-10-19") == (None, False)
    rec = dict(SAMPLE["results"][0], update_date="13/45/2026")
    row = parse_records([rec], DAY, MAP).iloc[0]
    assert row["update_date"] is None and row["bad_dates"] == "update_date"


def test_event_key_rules():
    d = dt.date(2023, 7, 14)
    assert event_key_for("0378-6860-77", "00378686077", "x", d) == ("00378686077|2023-07-14", "ndc11")
    assert event_key_for("ABC-12", None, "x", d) == ("raw:ABC-12|2023-07-14", "raw")
    key, src = event_key_for(None, None, "Some presentation", d)
    assert key.startswith("pres:") and key.endswith("|2023-07-14") and src == "presentation"


def test_tier1_key_uses_top_level_package_ndc_not_openfda():
    rec = next(r for r in SAMPLE["results"] if r["package_ndc"] == "0378-6860-77")
    assert rec["openfda"]["package_ndc"][0] == "0378-6854-77"      # the SPEC R4 trap is in the fixture
    row = parse_records([rec], DAY, MAP).iloc[0]
    assert row["ndc11"] == "00378686077"
    assert row["event_key"] == "00378686077|2023-07-14"


def test_duplicate_keys_keep_one_row_and_count(stage):
    d3 = stage[stage["snapshot_date"] == DAY]
    assert d3["event_key"].is_unique
    dup = d3[d3["dup_count"] > 1]
    assert set(dup["package_ndc_raw"]) == {"0409-1160-01", "65862-618-90"}


def test_duplicate_keeps_latest_update_date_then_api_order():
    base = SAMPLE["results"][0]
    a = dict(base, update_date="10/01/2026", generic_name="older")
    b = dict(base, update_date="10/05/2026", generic_name="newer")
    c = dict(base, update_date="10/05/2026", generic_name="newer-but-later")
    df = parse_records([a, b, c], DAY, MAP)
    assert len(df) == 1 and df.iloc[0]["generic_name"] == "newer" and df.iloc[0]["dup_count"] == 3


def test_records_without_openfda_are_kept(stage):
    d3 = stage[stage["snapshot_date"] == DAY]
    assert (~d3["has_openfda"]).sum() >= 5
    assert d3.loc[~d3["has_openfda"], "openfda_route"].isna().all()
