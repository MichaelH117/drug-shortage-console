"""DE-07: one-command build, idempotency, pull-only, and write-back surviving a re-ingest (AC-1.1, 2.2, 6.4, 5.1)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from pipeline.config import connect, paths_for
from pipeline.run import build, main, table_fingerprints
from pipeline.snapshots import make_writable

NOW = dt.datetime(2026, 10, 30, 13, 0, 0)


class FakeClient:
    def __init__(self, records, last_updated="2026-10-29"):
        self.records, self.last_updated, self.calls = records, last_updated, 0

    def get_json(self, url, params):
        self.calls += 1
        s, n = params["skip"], params["limit"]
        return {"meta": {"last_updated": self.last_updated,
                         "results": {"skip": s, "limit": n, "total": len(self.records)}},
                "results": self.records[s: s + n]}


class ExplodingClient:
    def get_json(self, url, params):
        raise AssertionError("no network call expected")


def test_demo_build_summary(demo_build):
    s = demo_build["summary"]
    assert s["mode"] == "demo" and s["latest_snapshot"] == "2026-10-30"
    assert s["change_counts_latest"] == {"DROPPED": 1, "IMPROVED": 2, "NEW": 3, "RESOLVED": 1, "UNCHANGED": 66,
                                         "WORSENED": 4}
    assert "FAIL" not in s["dq"]
    assert s["ontology_problems"] == []
    assert (demo_build["paths"].reports / "match_quality.json").exists()


def test_build_writes_the_contract_tables(demo_db):
    for t in ("stage.snapshot", "stage.shortage_record", "core.dq_results", "core.shortage_history",
              "core.change_feed", "core.shortage_event", "seed.facility", "seed.formulary_item",
              "seed.facility_stock", "seed.app_user", "core.drug", "core.formulary_match",
              "core.substitute_candidate"):
        assert demo_db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] > 0, t
    assert demo_db.execute("SELECT count(*) - count(DISTINCT event_key) FROM core.shortage_event").fetchone()[0] == 0
    assert demo_db.execute("SELECT bool_and(is_synthetic) FROM seed.formulary_item").fetchone()[0]


def test_rebuild_is_idempotent(tmp_path):
    paths = paths_for("demo", tmp_path)
    first = build(paths, client=None, seed=42, evaluate=False)
    second = build(paths, client=None, seed=42, evaluate=False)
    assert first["tables"] == second["tables"]
    assert first["run_id"] == second["run_id"]
    assert first["tables"]["stage.shortage_record"]["rows"] == 223


def test_edit_survives_a_re_ingest(tmp_path):
    from console.actions import propose_substitute

    paths = paths_for("demo", tmp_path)
    build(paths, client=None, seed=42, evaluate=False)
    con = connect(paths.db)
    try:
        cand = con.execute("SELECT event_key, alt_ndc11, substitute_id FROM core.substitute_candidate "
                           "ORDER BY substitute_id LIMIT 1").fetchone()
        res = propose_substitute(con, "dana.ruiz", cand[0], cand[1], "SYNTHETIC test: alternate labeler", now=NOW)
        assert res.outcome == "SUCCESS"
        before = con.execute("SELECT count(*) FROM audit.action_log").fetchone()[0]
    finally:
        con.close()

    build(paths, client=None, seed=42, evaluate=False)          # re-ingest drops stage/core/seed/obj/link/mart only
    con = connect(paths.db, read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM ops.substitute_edit WHERE substitute_id = ?",
                           [cand[2]]).fetchone()[0] == 1
        assert con.execute("SELECT state FROM obj.substitute WHERE substitute_id = ?", [cand[2]]).fetchone()[0] \
            == "proposed"
        assert con.execute("SELECT count(*) FROM audit.action_log").fetchone()[0] == before
        from ontokit.audit import verify_chain

        assert verify_chain(con).ok
    finally:
        con.close()


def test_live_mode_pulls_once_then_uses_the_cache(tmp_path):
    sample = json.loads((paths_for("demo").raw / "SYNTHETIC_D3" / "records.json").read_text(encoding="utf-8"))
    paths = paths_for("live", tmp_path)
    client = FakeClient(sample)
    s = build(paths, client=client, seed=42, run_date=dt.date(2026, 10, 30), evaluate=False)
    assert client.calls == 1 and s["mode"] == "live"
    assert set(s["change_counts_latest"]) == {"NEW"}                 # first live day: everything is NEW (T2)
    s2 = build(paths, client=ExplodingClient(), seed=42, run_date=dt.date(2026, 10, 30), evaluate=False)
    assert s2["tables"] == s["tables"]
    make_writable(paths.raw)


def test_pull_only_with_a_cache_changes_nothing(tmp_path, monkeypatch):
    import pipeline.run as run

    sample = json.loads((paths_for("demo").raw / "SYNTHETIC_D3" / "records.json").read_text(encoding="utf-8"))
    paths = paths_for("live", tmp_path)
    build(paths, client=FakeClient(sample), seed=42, run_date=dt.date(2026, 10, 30), evaluate=False)
    con = connect(paths.db, read_only=True)
    before = table_fingerprints(con)
    con.close()
    mtime = paths.db.stat().st_mtime_ns

    monkeypatch.setattr(run, "paths_for", lambda mode, base=None: paths)
    monkeypatch.setattr(run, "local_today", lambda: dt.date(2026, 10, 30))
    monkeypatch.setattr(run, "default_client", lambda: ExplodingClient())
    assert main(["--pull-only"]) == 0
    assert paths.db.stat().st_mtime_ns == mtime
    con = connect(paths.db, read_only=True)
    try:
        assert table_fingerprints(con) == before
    finally:
        con.close()
    make_writable(paths.raw)


def test_fixtures_cannot_be_combined_with_refresh():
    with pytest.raises(SystemExit):
        main(["--fixtures", "--refresh"])


def test_failed_ml_stage_rolls_back_the_whole_rebuild(tmp_path, monkeypatch):
    """REVIEW #5: if matching fails, the previous build (incl. obj views) must still be there, unchanged."""
    import pipeline.objects as objects

    paths = paths_for("demo", tmp_path)
    build(paths, client=None, seed=42, evaluate=False)
    con = connect(paths.db, read_only=True)
    before = table_fingerprints(con, schemas=("stage", "core", "seed"))
    con.close()

    def boom(con):
        raise RuntimeError("SYNTHETIC failure in build_matching")

    monkeypatch.setattr(objects, "build_matching", boom)
    with pytest.raises(RuntimeError, match="SYNTHETIC failure"):
        build(paths, client=None, seed=7, evaluate=False)      # different seed: would change seed.* if committed
    con = connect(paths.db, read_only=True)
    try:
        assert table_fingerprints(con, schemas=("stage", "core", "seed")) == before
        for v in ("obj.substitute", "obj.shortage_event", "obj.mitigation_task"):
            con.execute(f"SELECT count(*) FROM {v}").fetchone()
        assert con.execute("SELECT count(*) FROM core.formulary_match").fetchone()[0] > 0
    finally:
        con.close()


def test_build_passes_snapshot_dates_to_events(tmp_path):
    """DE-09 end to end: an empty latest live snapshot makes every event not present_in_latest."""
    from pipeline.snapshots import write_snapshot

    sample = json.loads((paths_for("demo").raw / "SYNTHETIC_D3" / "records.json").read_text(encoding="utf-8"))
    paths = paths_for("live", tmp_path)
    build(paths, client=FakeClient(sample), seed=42, run_date=dt.date(2026, 10, 30), evaluate=False)
    write_snapshot(paths.raw, "2026-10-31", dt.date(2026, 10, 31), [],
                   {"last_updated": "2026-10-30", "results": {"total": 0}}, source="backfill",
                   fetched_at_utc=dt.datetime(2026, 10, 31, 12, 0))
    s = build(paths, client=ExplodingClient(), seed=42, run_date=dt.date(2026, 10, 31), evaluate=False)
    assert s["latest_snapshot"] == "2026-10-31"
    con = connect(paths.db, read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM core.shortage_event WHERE present_in_latest").fetchone()[0] == 0
    finally:
        con.close()
    make_writable(paths.raw)


def _mq(unseen_key, p, r):
    sec = {"gold": {"pairs": 90}, "matcher": {"overall": {"precision": p, "recall": r, "f1": 0.7}},
           "baseline": {"overall": {"precision": 1.0, "recall": 0.1, "f1": 0.2}}}
    return {unseen_key: sec, "gold_v1_seen_reference": {"matcher": {"overall": {"precision": 0.98, "recall": 0.98}}}}


def test_headline_is_the_newest_unseen_holdout(tmp_path):
    """BUG-PE-02: numeric version order (v10 > v9 > v2), sections without 'unseen' are skipped."""
    from pipeline.run import latest_unseen_holdout

    (tmp_path / "match_quality_v1.json").write_text(json.dumps({"matcher": {}}), encoding="utf-8")
    (tmp_path / "match_quality_v2.json").write_text(json.dumps(_mq("holdout_unseen", 0.95, 0.93)), encoding="utf-8")
    (tmp_path / "match_quality_v10.json").write_text(json.dumps(_mq("holdout_v10_unseen", 0.96, 0.60)),
                                                     encoding="utf-8")
    (tmp_path / "match_quality_v11.json").write_text(json.dumps({"no_holdout": {}}), encoding="utf-8")
    h = latest_unseen_holdout(tmp_path)
    assert h["version"] == "v10" and h["matcher"]["recall"] == 0.60 and h["pairs"] == 90
    assert latest_unseen_holdout(tmp_path / "missing") is None


def test_readme_block_headlines_unseen_and_labels_gold_as_seen(tmp_path, monkeypatch):
    import pipeline.run as run

    (tmp_path / "README.md").write_text("x\n<!-- results:start -->\nold\n<!-- results:end -->\n", encoding="utf-8")
    monkeypatch.setattr(run, "ROOT", tmp_path)
    summary = {"snapshots": [1, 2, 3], "latest_snapshot": "2026-10-30", "mode": "demo",
               "change_counts_latest": {"NEW": 3}, "dq": {"PASS": 1},
               "match_quality": {"holdout_unseen": {"version": "v3", "pairs": 90, "file": "reports/x.json",
                                                    "matcher": {"precision": 0.963, "recall": 0.6047},
                                                    "baseline": {"precision": 1.0, "recall": 0.1}},
                                 "gold_seen": {"matcher": {"precision": 0.9825, "recall": 0.9825}}}}
    run.write_readme_results({"summary": summary, "tests": None})
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    head = next(line for line in text.splitlines() if "Matcher P / R" in line)
    assert "unseen holdout v3" in head and "0.6047" in head and "0.9825" not in head
    gold = next(line for line in text.splitlines() if "0.9825" in line)
    assert "seen, optimistic" in gold
