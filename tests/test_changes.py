"""DE-05: change classification against the hand-written answer key (AC-2.3, DESIGN 3.5)."""

from __future__ import annotations

import pandas as pd
import pytest

from pipeline.changes import build_change_feed, classify_one
from pipeline.config import FIXTURE_SNAPSHOTS, ROOT, load_availability_map
from pipeline.ingest import parse_all
from pipeline.snapshots import list_snapshots

EXPECTED = pd.read_csv(ROOT / "tests" / "fixtures" / "expected_changes.csv", comment="#", dtype=str)


@pytest.fixture(scope="module")
def stage():
    return parse_all(list_snapshots(FIXTURE_SNAPSHOTS), load_availability_map())


@pytest.fixture(scope="module")
def feed(stage):
    keys = stage[["event_key", "package_ndc_raw", "initial_posting_date"]].drop_duplicates("event_key")
    keys = keys.assign(initial_posting_date=pd.to_datetime(keys["initial_posting_date"]).dt.strftime("%m/%d/%Y"))
    f = build_change_feed(stage).merge(keys, on="event_key")
    return f.assign(snapshot_date=f["snapshot_date"].astype(str))


def test_first_snapshot_is_all_new(feed):
    d1 = feed[feed["snapshot_date"] == "2026-10-28"]
    assert set(d1["change_type"]) == {"NEW"} and len(d1) == 73


@pytest.mark.parametrize("day", ["2026-10-29", "2026-10-30"])
def test_changes_match_the_hand_written_answer_key(feed, day):
    got = feed[(feed["snapshot_date"] == day) & (feed["change_type"] != "UNCHANGED")]
    got = set(zip(got["package_ndc_raw"], got["initial_posting_date"], got["change_type"], strict=True))
    exp = EXPECTED[EXPECTED["snapshot_date"] == day]
    exp = set(zip(exp["package_ndc"], exp["initial_posting_date"], exp["change_type"], strict=True))
    assert got == exp


def test_counts_per_type(feed):
    got = feed[feed["snapshot_date"] != "2026-10-28"].groupby(["snapshot_date", "change_type"]).size()
    exp = EXPECTED.groupby(["snapshot_date", "change_type"]).size()
    assert got.drop(index="UNCHANGED", level=1).to_dict() == exp.to_dict()


def test_each_event_has_exactly_one_type_per_snapshot(feed):
    assert not feed.duplicated(["snapshot_date", "event_key"]).any()


def test_rules_report_the_rule_number(feed):
    d3 = feed[feed["snapshot_date"] == "2026-10-30"].set_index("package_ndc_raw")
    exp = EXPECTED[EXPECTED["snapshot_date"] == "2026-10-30"].set_index("package_ndc")
    for ndc, row in exp.iterrows():
        assert str(d3.loc[ndc, "rule"]) == row["rule"], ndc


def S(status="Current", avail="Available"):
    return {"status": status, "availability_normalized": avail}


@pytest.mark.parametrize("prev,curr,expected", [
    (S(), None, ("DROPPED", 1)),
    (None, S(), ("NEW", 2)),
    (S("Current", "Unavailable"), S("Resolved", "Unknown"), ("RESOLVED", 3)),
    (S("Resolved", "Unknown"), S("Current", "Available"), ("WORSENED", 4)),
    (S("Current", "Available"), S("Current", "Limited"), ("WORSENED", 5)),
    (S("Current", "Limited"), S("Current", "Unavailable"), ("WORSENED", 5)),
    (S("Current", "Unknown"), S("To Be Discontinued", "Unknown"), ("WORSENED", 5)),
    (S("Current", "Unavailable"), S("Current", "Available"), ("IMPROVED", 6)),
    (S("Current", "Available"), S("Current", "Available"), ("UNCHANGED", 7)),
])
def test_one_case_per_rule(prev, curr, expected):
    assert classify_one(prev, curr) == expected


@pytest.mark.parametrize("prev,curr", [
    (S("Current", "Available"), S("Current", "Unknown")),
    (S("Current", "Unknown"), S("Current", "Unavailable")),
    (S("Current", "Unavailable"), S("Current", "Unknown")),
])
def test_unknown_never_gives_worsened_or_improved(prev, curr):
    assert classify_one(prev, curr) == ("UNCHANGED", 7)


def test_resolved_beats_availability_rank():
    # Resolved wins even though availability moved Available -> Unknown.
    assert classify_one(S("Current", "Available"), S("Resolved", "Unknown"))[0] == "RESOLVED"
