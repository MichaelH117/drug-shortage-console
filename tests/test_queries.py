"""AE-02: queue, object view, audit queries and the stale-banner rule (AC-5.1 to 5.4).

The queue is reconciled against an independent pandas computation from the base tables, so a bug in the mart SQL
cannot pass by agreeing with itself.
"""
from __future__ import annotations

import datetime as dt
import shutil

import pandas as pd
import pytest

from console import actions, queries
from console.freshness import is_stale
from ontokit.actions import ActionRefused
from pipeline.config import connect


# ---------------------------------------------------------------------------------------------- is_stale
@pytest.mark.parametrize("age,stale", [(0, False), (1, False), (2, True), (10, True)])
def test_is_stale_boundary_is_exactly_one_day(age, stale):
    today = dt.date(2026, 10, 30)
    assert is_stale(today - dt.timedelta(days=age), today) is stale


def test_is_stale_without_snapshot_and_custom_age():
    assert is_stale(None, dt.date(2026, 10, 30)) is True
    assert is_stale(dt.date(2026, 10, 27), dt.date(2026, 10, 30), max_age_days=3) is False


# ---------------------------------------------------------------------------------------------- fixtures
@pytest.fixture
def wcon(demo_build, tmp_path):
    """Private writable copy of the session build."""
    src = demo_build["paths"].db
    dst = tmp_path / "console.duckdb"
    shutil.copy(src, dst)
    c = connect(dst)
    yield c
    c.close()


def _expected_queue(con) -> pd.DataFrame:
    """Independent re-derivation from base tables (no views), ADR 007: NEW/WORSENED in the latest snapshot, not a
    baseline row, matched to a stocked item, actionable; ranked by availability, days, tier, sites desc, key."""
    feed = con.execute("SELECT * FROM core.change_feed").fetchdf()
    latest = con.execute("SELECT max(snapshot_date) FROM stage.snapshot").fetchone()[0]
    feed = feed[(feed["snapshot_date"] == pd.Timestamp(latest)) & feed["change_type"].isin(["NEW", "WORSENED"])
                & feed["prev_snapshot_date"].notna()]
    feed = feed[(feed["curr_status"] != "Resolved")
                & (feed["curr_availability"].isin(["Limited", "Unavailable"])
                   | (feed["curr_status"] == "To Be Discontinued"))]
    match = con.execute("SELECT item_id, event_key, match_tier FROM core.formulary_match").fetchdf()
    stock = con.execute("SELECT facility_id, item_id, days_on_hand FROM seed.facility_stock").fetchdf()
    hit = match.merge(stock, on="item_id")
    agg = hit.groupby("event_key").agg(min_days=("days_on_hand", "min"), n_fac=("facility_id", "nunique"),
                                       tier=("match_tier", "min")).reset_index()
    out = feed.merge(agg, on="event_key")
    out["rank"] = out["curr_availability"].map(queries.AVAIL_ORDER).fillna(2)
    out["neg_fac"] = -out["n_fac"]
    return out.sort_values(["rank", "min_days", "tier", "neg_fac", "event_key"]).reset_index(drop=True)


def _truncate_to_baseline(con) -> None:
    """Make a DB whose only snapshot is the first fixture day (a single-snapshot / baseline-day DB)."""
    base = con.execute("SELECT min(snapshot_date) FROM stage.snapshot").fetchone()[0]
    for t in ("stage.snapshot", "stage.shortage_record", "core.change_feed"):
        con.execute(f"DELETE FROM {t} WHERE snapshot_date > ?", [base])


# ---------------------------------------------------------------------------------------------- queue
def test_mart_view_exists_and_matches_inline_sql(demo_db):
    mart = demo_db.execute("SELECT * FROM mart.triage_queue").fetchdf()
    inline = queries.queue_for_snapshot(demo_db, None)
    assert len(mart) == len(inline) > 0
    assert sorted(mart["event_key"]) == sorted(inline["event_key"])


def test_queue_reconciles_to_base_tables(demo_db):
    q = queries.triage_queue(demo_db)
    exp = _expected_queue(demo_db)
    assert q["event_key"].tolist() == exp["event_key"].tolist()          # same rows, same order
    assert q["min_days_on_hand"].tolist() == exp["min_days"].tolist()
    assert q["n_facilities"].tolist() == exp["n_fac"].tolist()


def test_queue_only_new_or_worsened_with_a_facility_match(demo_db):
    q = queries.triage_queue(demo_db)
    assert set(q["change_type"]) <= {"NEW", "WORSENED"}
    assert (q["n_facilities"] >= 1).all()
    # a WORSENED event that no facility stocks must be filtered out (fixture: hydromorphone suppository)
    others = queries.other_changes(demo_db)
    unstocked = others[others["change_type"].isin(["NEW", "WORSENED"])]
    assert len(unstocked) >= 1 and not unstocked["stocked"].any()
    assert not set(unstocked["event_key"]) & set(q["event_key"])


def test_queue_sort_order_and_rank(demo_db):
    q = queries.triage_queue(demo_db)
    keys = list(zip(q["avail_rank"], q["min_days_on_hand"], q["best_match_tier"], -q["n_facilities"], strict=True))
    assert keys == sorted(keys)
    assert q["queue_rank"].tolist() == list(range(1, len(q) + 1))
    assert (q["actionable_total"] == len(q)).all()
    assert q.iloc[0]["availability_normalized"] == "Unavailable"
    again = queries.triage_queue(demo_db)
    assert again["event_key"].tolist() == q["event_key"].tolist()          # deterministic


def test_available_goes_to_watch_not_queue(demo_db):
    """ADR 007 point 3: NEW but still Available events are matched but not actionable."""
    q, watch = queries.triage_queue(demo_db), queries.watch_list(demo_db)
    assert ((q["availability_normalized"] != "Available") | (q["status"] == "To Be Discontinued")).all()
    assert len(watch) == 3 and set(watch["availability_normalized"]) == {"Available"}
    assert not set(watch["event_key"]) & set(q["event_key"])
    # queue + watch = every stocked NEW/WORSENED change since the previous snapshot (nothing dropped)
    stocked = demo_db.execute("""SELECT count(*) FROM core.change_feed f
        WHERE f.snapshot_date = (SELECT max(snapshot_date) FROM stage.snapshot)
          AND f.change_type IN ('NEW','WORSENED') AND f.prev_snapshot_date IS NOT NULL
          AND EXISTS (SELECT 1 FROM link.shortage_event_facility l WHERE l.event_key = f.event_key)""").fetchone()[0]
    assert len(q) + len(watch) == stocked


def test_baseline_day_has_empty_queue_and_item_grain_worklist(wcon):
    _truncate_to_baseline(wcon)
    assert queries.is_baseline_day(wcon) is True
    assert queries.triage_queue(wcon).empty
    w = queries.baseline_worklist(wcon)
    assert len(w) > 0 and w["is_baseline_day"].all() and w["item_id"].is_unique
    assert w["worklist_rank"].tolist() == list(range(1, len(w) + 1))
    assert (w["worst_availability"] != "Available").all()
    watch = queries.watch_list(wcon)
    assert len(watch) > 0 and set(watch["change_type"]) == {"BASELINE"}
    assert queries.other_changes(wcon).empty                                # baseline rows are not changes


def test_mart_views_match_inline_sql(demo_db):
    for view, fn in (("baseline_worklist", queries.baseline_worklist), ("watch_list", queries.watch_list)):
        n = demo_db.execute(f"SELECT count(*) FROM mart.{view}").fetchone()[0]
        assert n == len(fn(demo_db))


def test_queue_row_has_ac52_columns(demo_db):
    q = queries.triage_queue(demo_db)
    for col in ("generic_name", "change_type", "availability_raw", "availability_normalized", "facilities",
                "best_match_tier", "open_tasks", "substitute_approved"):
        assert col in q.columns


def test_snapshot_info_and_change_counts(demo_db):
    info = queries.snapshot_info(demo_db)
    assert info["latest"] == dt.date(2026, 10, 30) and info["previous"] == dt.date(2026, 10, 29)
    assert info["synthetic_history"] is True
    counts = queries.change_counts(demo_db)
    assert counts["NEW"] == 3 and counts["WORSENED"] == 4     # tests/fixtures/expected_changes.csv, D3


# ---------------------------------------------------------------------------------------------- object + live update
def test_drug_object_links(demo_db):
    ek = queries.triage_queue(demo_db)["event_key"].iloc[0]
    o = queries.drug_object(demo_db, ek)
    assert o["event"]["event_key"] == ek
    assert o["drug"]["ndc11"] == o["event"]["ndc11"]
    assert len(o["facilities"]) >= 1 and len(o["history"]) >= 1 and len(o["changes"]) >= 1
    assert queries.drug_object(demo_db, "nope|2020-01-01")["event"] is None


def test_action_shows_in_queue_and_object_without_rebuild(wcon):
    """AC-6.5: views are live; an approved substitute and a new task show on the next read."""
    q = queries.triage_queue(wcon)
    row = next(r for r in q.itertuples() if r.substitute_options > 0)
    sub = next(s for s in queries.drug_object(wcon, row.event_key)["substitutes"] if not s["alt_is_short"])
    actions.propose_substitute(wcon, "lee.chen", row.event_key, sub["alt_ndc11"], "test proposal")
    actions.approve_substitute(wcon, "dana.ruiz", sub["substitute_id"], "test approval")
    fac = queries.drug_object(wcon, row.event_key)["facilities"][0]["facility_id"]
    actions.open_mitigation_task(wcon, "dana.ruiz", row.event_key, fac, "lee.chen",
                                 (dt.date.today() + dt.timedelta(days=5)).isoformat(), "test task")
    after = queries.triage_queue(wcon).set_index("event_key").loc[row.event_key]
    assert bool(after["substitute_approved"]) and int(after["open_tasks"]) == 1
    o = queries.drug_object(wcon, row.event_key)
    assert len(o["audit"]) == 3 and set(o["audit"]["outcome"]) == {"SUCCESS"}


def test_audit_entries_filters(wcon):
    ek = queries.triage_queue(wcon)["event_key"].iloc[0]
    fac = queries.drug_object(wcon, ek)["facilities"][0]["facility_id"]
    with pytest.raises(ActionRefused):
        actions.open_mitigation_task(wcon, "jordan.blake", ek, fac, "lee.chen", "2099-01-01", "viewer try")
    actions.open_mitigation_task(wcon, "dana.ruiz", ek, fac, "lee.chen", "2099-01-01", "ok")
    assert len(queries.audit_entries(wcon)) == 2
    assert len(queries.audit_entries(wcon, outcome="REFUSED")) == 1
    assert queries.audit_entries(wcon, actor_id="jordan.blake")["failed_criterion"].tolist() == ["role"]
    assert len(queries.audit_entries(wcon, action_type="open_mitigation_task", actor_id="dana.ruiz")) == 1
    assert queries.audit_entries(wcon, object_key=[]).empty


def test_action_permission_mirrors_ontology(demo_db):
    assert queries.action_permission(demo_db, "dana.ruiz", "approve_substitute")[0] is True
    ok, why = queries.action_permission(demo_db, "lee.chen", "approve_substitute")
    assert ok is False and "pharmacist" in why
    assert queries.action_permission(demo_db, "lee.chen", "open_mitigation_task")[0] is True
    assert queries.action_permission(demo_db, "jordan.blake", "propose_substitute")[0] is False
    assert queries.action_permission(demo_db, "morgan.ellis", "approve_substitute")[0] is False   # inactive
    for name, spec in actions.ontology().actions.items():
        for role, uid in (("pharmacist", "dana.ruiz"), ("buyer", "lee.chen"), ("viewer", "jordan.blake")):
            assert queries.action_permission(demo_db, uid, name)[0] is (role in spec.roles)
