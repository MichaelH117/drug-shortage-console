"""DE-05: SCD2 history and order independence (AC-2.1, AC-2.4, ADR 004)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pipeline.changes import build_change_feed
from pipeline.config import FIXTURE_SNAPSHOTS, load_availability_map
from pipeline.history import build_events, build_history
from pipeline.ingest import parse_all, parse_snapshot
from pipeline.snapshots import list_snapshots

MAP = load_availability_map()
D1, D2, D3 = dt.date(2026, 10, 28), dt.date(2026, 10, 29), dt.date(2026, 10, 30)


@pytest.fixture(scope="module")
def refs():
    return list_snapshots(FIXTURE_SNAPSHOTS)


@pytest.fixture(scope="module")
def stage(refs):
    return parse_all(refs, MAP)


@pytest.fixture(scope="module")
def hist(stage):
    return build_history(stage)


def _versions(hist, stage, ndc):
    key = stage.loc[stage["package_ndc_raw"] == ndc, "event_key"].iloc[0]
    return hist[hist["event_key"] == key].sort_values("valid_from").reset_index(drop=True)


def test_one_current_version_per_event_and_unique_key(hist):
    assert not hist.duplicated(["event_key", "valid_from"]).any()
    assert hist.groupby("event_key")["is_current"].sum().eq(1).all()
    assert hist.loc[hist["is_current"], "valid_to"].isna().all()
    assert hist.loc[~hist["is_current"], "valid_to"].notna().all()


def test_worsening_twice_gives_three_versions(hist, stage):
    v = _versions(hist, stage, "0641-6024-10")       # Available -> Limited -> Unavailable (+ update_date)
    assert list(v["availability_normalized"]) == ["Available", "Limited", "Unavailable"]
    assert list(v["valid_from"]) == [D1, D2, D3]
    assert list(v["valid_to"][:2]) == [D2, D3]


def test_reason_only_change_opens_a_version(hist, stage):
    v = _versions(hist, stage, "75987-050-07")
    assert len(v) == 2 and v.loc[0, "shortage_reason"] == "SYNTHETIC earlier reason"


def test_update_date_only_change_opens_a_version(hist, stage):
    v = _versions(hist, stage, "0832-0273-11")
    assert list(v["update_date"]) == [dt.date(2025, 10, 1), dt.date(2025, 12, 22)]


def test_dropped_event_gets_a_not_present_version(hist, stage):
    v = _versions(hist, stage, "0093-7767-24")
    assert list(v["present"]) == [True, False]
    assert v.loc[1, "valid_from"] == D2 and bool(v.loc[1, "is_current"])


def test_new_event_starts_on_its_first_day(hist, stage):
    v = _versions(hist, stage, "0409-2634-50")
    assert len(v) == 1 and v.loc[0, "valid_from"] == D3


def test_unchanged_event_has_one_version(hist, stage):
    assert len(_versions(hist, stage, "0409-1160-01")) == 1


def _frames(refs_in_load_order):
    stage = pd.concat([parse_snapshot(r, MAP) for r in refs_in_load_order], ignore_index=True)
    return build_history(stage), build_change_feed(stage), build_events(stage)


def test_out_of_order_load_gives_the_same_result(refs):
    d1, d2, d3 = refs
    a = _frames([d1, d2, d3])
    b = _frames([d1, d3, d2])
    c = _frames([d3, d2, d1])
    for x, y in ((a, b), (a, c)):
        for fx, fy in zip(x, y, strict=True):
            pd.testing.assert_frame_equal(fx.reset_index(drop=True), fy.reset_index(drop=True))


def test_events_have_first_and_last_seen(stage):
    ev = build_events(stage).set_index("package_ndc_raw")
    assert ev.loc["0093-7767-24", "last_seen"] == D1 and not ev.loc["0093-7767-24", "present_in_latest"]
    assert ev.loc["0409-2634-50", "first_seen"] == D3 and ev.loc["0409-2634-50", "present_in_latest"]
    assert ev["event_key"].is_unique


def test_present_in_latest_is_false_when_the_latest_snapshot_is_empty(stage):
    """REVIEW #6 / DE-09: a latest snapshot with 0 records means nothing is present in it."""
    ev = build_events(stage, snapshot_dates=[D1, D2, D3, dt.date(2026, 10, 31)])
    assert not ev["present_in_latest"].any()
    ev = build_events(stage, snapshot_dates=[D1, D2, D3])
    assert ev["present_in_latest"].sum() == len(stage[stage["snapshot_date"] == D3])
