"""QA-05 (part): ADR 007 queue rules. Baseline snapshot, actionable filter, deterministic rank, visible cap.

Owner: qa-engineer. Builds use the SYNTHETIC fixture snapshots (D1..D3) copied into tmp folders, so the repo's
data/ is never touched. ``demo_db`` is DE's read-only session build (tests/conftest.py).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

import console.metrics as M
import console.queries as Q
from pipeline.config import FIXTURE_SNAPSHOTS, connect, paths_for
from pipeline.run import build

ACTIONABLE = {"Limited", "Unavailable"}


def _is_actionable(status, avail) -> bool:
    return status != "Resolved" and (avail in ACTIONABLE or status == "To Be Discontinued")


def _build_with(tmp: Path, days: list[str]):
    """Copy fixture days into a private raw folder named by snapshot date, then build offline.

    The folder name is the ISO date so ``ensure_snapshot`` gets a cache hit for ``run_date`` (zero network calls).
    """
    raw = tmp / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for d in days:
        src = Path(FIXTURE_SNAPSHOTS) / d
        iso = json.loads((src / "manifest.json").read_text(encoding="utf-8"))["snapshot_date"]
        if not (raw / iso).exists():
            shutil.copytree(src, raw / iso)
    latest = max(dt.date.fromisoformat(p.name) for p in raw.iterdir() if p.is_dir())
    paths = dataclasses.replace(paths_for("demo", tmp), raw=raw)
    build(paths, client=None, refresh=False, seed=42, run_date=latest, evaluate=False)
    return paths


def _snapshot_dates(con):
    return [r[0] for r in con.execute("SELECT snapshot_date FROM stage.snapshot ORDER BY 1").fetchall()]


# ------------------------------------------------------------------------------------------- baseline
def test_single_snapshot_gives_empty_queue_and_a_worklist(tmp_path):
    paths = _build_with(tmp_path, ["SYNTHETIC_D1"])
    con = connect(paths.db, read_only=True)
    try:
        assert Q.is_baseline_day(con)
        assert Q.triage_queue(con).empty, "day one is a baseline, not news (ADR 007 point 1)"
        wl = Q.baseline_worklist(con)
        assert not wl.empty, "the one-time backlog must still be visible"
        assert wl["item_id"].is_unique, "worklist is item grain"
        assert wl["is_baseline_day"].all()
    finally:
        con.close()


def test_backfilling_an_older_snapshot_moves_the_baseline(tmp_path):
    paths = _build_with(tmp_path, ["SYNTHETIC_D2", "SYNTHETIC_D3"])
    con = connect(paths.db, read_only=True)
    try:
        d2, d3 = _snapshot_dates(con)
        assert Q.queue_for_snapshot(con, d2).empty, "D2 is the baseline before the backfill"
    finally:
        con.close()

    _build_with(tmp_path, ["SYNTHETIC_D1"])  # an older day slots in (ADR 004 / E4)
    con = connect(paths.db, read_only=True)
    try:
        d1 = _snapshot_dates(con)[0]
        assert d1 < d2
        assert not Q.is_baseline_day(con)
        nulls = con.execute("SELECT count(*) FROM core.change_feed WHERE snapshot_date = ? "
                            "AND prev_snapshot_date IS NULL", [d2]).fetchone()[0]
        assert nulls == 0, "after the backfill, D2 rows compare against D1"
        base = con.execute("SELECT count(*) FROM core.change_feed WHERE prev_snapshot_date IS NULL "
                           "AND snapshot_date <> ?", [d1]).fetchone()[0]
        assert base == 0, "only the earliest snapshot is a baseline"
    finally:
        con.close()


# -------------------------------------------------------------------------------------- actionable filter
def test_queue_rows_are_actionable_and_watch_rows_are_not(demo_db):
    q, w = Q.triage_queue(demo_db), Q.watch_list(demo_db)
    assert not q.empty
    assert all(_is_actionable(s, a) for s, a in zip(q["status"], q["availability_normalized"], strict=True))
    assert not any(_is_actionable(s, a) for s, a in zip(w["status"], w["availability_normalized"], strict=True))
    assert not set(q["event_key"]) & set(w["event_key"]), "an event is in the queue or the watch list, not both"
    assert "Available" not in set(q["availability_normalized"])


def test_queue_excludes_baseline_rows(demo_db):
    q = Q.triage_queue(demo_db)
    assert q["prev_snapshot_date"].notna().all()


# ------------------------------------------------------------------------------------------------- rank
def _expected_order(df: pd.DataFrame, key: str) -> list:
    tmp = df.assign(_dh=df["min_days_on_hand"].isna(), _nf=-df["n_facilities"])
    return tmp.sort_values(["avail_rank", "_dh", "min_days_on_hand", "best_match_tier", "_nf", key],
                           kind="mergesort")[key].tolist()


def test_queue_rank_is_deterministic_and_follows_adr(demo_db):
    a, b = Q.triage_queue(demo_db), Q.triage_queue(demo_db)
    pd.testing.assert_frame_equal(a, b)
    assert a["queue_rank"].tolist() == list(range(1, len(a) + 1))
    assert a["event_key"].tolist() == _expected_order(a, "event_key")
    assert (a["actionable_total"] == len(a)).all()


def test_worklist_rank_is_deterministic(demo_db):
    a, b = Q.baseline_worklist(demo_db), Q.baseline_worklist(demo_db)
    pd.testing.assert_frame_equal(a, b)
    assert a["worklist_rank"].tolist() == list(range(1, len(a) + 1))
    assert a["item_id"].tolist() == _expected_order(a, "item_id")


# --------------------------------------------------------------------------------------------------- cap
def test_cap_never_changes_guardrail_b(demo_db, monkeypatch):
    """ADR 007 point 5: guardrail (b) is the UNCAPPED queue. Shrinking the UI cap must not move it."""
    full = len(Q.triage_queue(demo_db))
    assert full > 0
    before = M._guard_b(demo_db)
    monkeypatch.setattr(Q, "QUEUE_CAP", 0)
    after = M._guard_b(demo_db)
    assert before["demo_queue_length"] == after["demo_queue_length"] == full
    assert before.get("value") == after.get("value") and before["status"] == after["status"]


@pytest.mark.parametrize("cap", [0, 1, 40])
def test_queue_function_is_uncapped(demo_db, monkeypatch, cap):
    full = len(Q.triage_queue(demo_db))
    monkeypatch.setattr(Q, "QUEUE_CAP", cap)
    assert len(Q.triage_queue(demo_db)) == full
