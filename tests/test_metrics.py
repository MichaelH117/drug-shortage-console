"""AE-04: simulation and impact metrics (AC-8.1 to 8.3). Every metric is reconciled to its source here."""
from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest

from console import metrics, simulate
from ontokit.audit import verify_chain
from pipeline.config import connect


@pytest.fixture(scope="module")
def sim(demo_build, tmp_path_factory):
    """A private copy of the session build with the SIMULATED script applied once."""
    dst = tmp_path_factory.mktemp("sim") / "console.duckdb"
    shutil.copy(demo_build["paths"].db, dst)
    con = connect(dst)
    result = simulate.run_simulation(con)
    mq = json.loads((demo_build["paths"].reports / "match_quality.json").read_text(encoding="utf-8"))
    rd = demo_build["paths"].reports          # tmp reports: v1-format match_quality.json only
    yield {"con": con, "result": result, "mq": mq, "rd": rd,
           "m": metrics.compute_impact_metrics(con, mq, reports_dir=rd)}
    con.close()


def test_simulation_runs_and_is_labeled(sim):
    r = sim["result"]
    assert r["skipped"] is False and r["success"] == 12 and r["refused"] == 2
    assert r["label"] == "SIMULATED demo usage, not real users"
    reasons = sim["con"].execute("SELECT reason FROM audit.action_log").fetchdf()["reason"]
    assert reasons.str.startswith("[SIMULATED]").all()
    assert verify_chain(sim["con"]).ok


def test_simulation_is_idempotent(sim):
    n = sim["con"].execute("SELECT count(*) FROM audit.action_log").fetchone()[0]
    assert simulate.run_simulation(sim["con"])["skipped"] is True
    assert sim["con"].execute("SELECT count(*) FROM audit.action_log").fetchone()[0] == n


def test_simulation_refuses_live_db(mem_db):
    mem_db.execute("CREATE SCHEMA stage")
    mem_db.execute("CREATE TABLE stage.snapshot AS SELECT DATE '2026-10-30' AS snapshot_date, 'live' AS source")
    with pytest.raises(RuntimeError, match="live"):
        simulate.run_simulation(mem_db)


def test_primary_metric_reconciles_to_match_quality(sim):
    p, mq = sim["m"]["primary"], sim["mq"]
    assert p["matcher_recall"] == mq["matcher"]["overall"]["recall"]
    fair = mq.get("baseline_v2", mq["baseline"])            # fairer baseline when the evaluator reports one
    assert p["baseline_recall"] == fair["overall"]["recall"]
    assert p["run"] == "v1 matcher on the gold set"
    assert p["matcher_tp"] + p["matcher_fn"] == mq["gold"]["positives"]
    assert p["status"] == ("PASS" if mq["matcher"]["overall"]["recall"] >= mq["targets"]["recall"] else "FAIL")
    assert p["gold_sha256"] == mq["gold"]["sha256"]


def test_guardrails_reconcile(sim):
    con, g = sim["con"], {x["id"]: x for x in sim["m"]["guardrails"]}
    assert set(g) == {"a", "b", "c", "d", "e"}
    assert g["a"]["value"] == sim["mq"]["matcher"]["overall"]["precision"]
    # (b): the demo DB has no live snapshot, so the live guardrail is n/a, and the demo length is the queue length
    assert g["b"]["status"] == "N/A"
    latest = con.execute("SELECT max(snapshot_date) FROM stage.snapshot").fetchone()[0]
    assert g["b"]["demo_queue_length"] == len(metrics.queue_for_snapshot(con, latest))
    assert g["c"]["status"] == "PASS" and g["c"]["value"] == "OK"
    assert g["d"]["value"] == 0 and "2 attempts refused" in g["d"]["detail"]
    dq = con.execute("SELECT status FROM core.dq_results WHERE snapshot_date = "
                     "(SELECT max(snapshot_date) FROM core.dq_results)").fetchdf()["status"]
    checked = dq[dq != "SKIPPED"]
    assert g["e"]["value"] == round((checked == "PASS").sum() / len(checked), 4)


def _copy(demo_build, tmp_path, name="x.duckdb"):
    dst = tmp_path / name
    shutil.copy(demo_build["paths"].db, dst)
    return connect(dst)


def test_guardrail_d_catches_a_disallowed_success(sim, demo_build, tmp_path):
    """Planted: a SUCCESS approve by a buyer written straight into the log must fail (d), and (c) too."""
    c = _copy(demo_build, tmp_path)
    try:
        c.execute("INSERT INTO audit.action_log (seq, action_type, object_type, object_key, actor_id, role, outcome, "
                  "ts_utc, prev_hash, row_hash) VALUES (1, 'approve_substitute', 'Substitute', 'S-x', 'lee.chen', "
                  "'buyer', 'SUCCESS', now(), 'x', 'y')")
        g = {x["id"]: x for x in metrics.compute_impact_metrics(c, sim["mq"], reports_dir=sim["rd"])["guardrails"]}
        assert g["d"]["status"] == "FAIL" and g["d"]["value"] == 1
        assert g["c"]["status"] == "FAIL"
    finally:
        c.close()


def test_guardrail_b_live_rule(sim, demo_build, tmp_path, monkeypatch):
    """ADR 007: (b) is the UNCAPPED daily queue on the latest live snapshot; a baseline day is N/A with the
    worklist reported separately; the UI cap cannot change it."""
    from console import queries

    c = _copy(demo_build, tmp_path)
    try:
        c.execute("UPDATE stage.snapshot SET source = 'live'")
        g = metrics._guard_b(c)
        assert g["status"] == "PASS" and g["value"] == len(metrics.queue_for_snapshot(c, None)) == 3
        assert g["live_watch_length"] == 3
        monkeypatch.setattr(queries, "QUEUE_CAP", 1)                       # a smaller cap changes nothing
        assert metrics._guard_b(c)["value"] == 3
        base = c.execute("SELECT min(snapshot_date) FROM stage.snapshot").fetchone()[0]
        for t in ("stage.snapshot", "stage.shortage_record", "core.change_feed"):
            c.execute(f"DELETE FROM {t} WHERE snapshot_date > ?", [base])
        g = metrics._guard_b(c)
        assert g["status"] == "N/A" and "baseline day" in g["detail"]
        assert g["live_queue_length"] == 0 and g["live_worklist_length"] > 0
    finally:
        c.close()
    assert metrics.QUEUE_LENGTH_MAX == 40 and metrics.QUEUE_PRECISION_MIN == 0.90


def test_time_to_first_action_reconciles_to_audit_log(sim):
    """Recompute the secondary metric independently from the raw tables."""
    con, s = sim["con"], sim["m"]["secondary"]
    assert s["label"] == "SIMULATED demo usage, not real users"
    snaps = con.execute("SELECT snapshot_date, fetched_at_utc FROM stage.snapshot ORDER BY 1").fetchall()
    entered = {}
    for d, t in snaps[1:]:
        for ek in metrics.queue_for_snapshot(con, d)["event_key"]:
            entered.setdefault(ek, pd.Timestamp(t))
    log = con.execute("SELECT object_key, ts_utc FROM audit.action_log WHERE outcome = 'SUCCESS'").fetchdf()
    subs = dict(con.execute("SELECT substitute_id, event_key FROM ops.substitute_edit").fetchall())
    tasks = dict(con.execute("SELECT task_id, event_key FROM ops.mitigation_task_edit").fetchall())
    log["event_key"] = log["object_key"].map(lambda k: subs.get(k) or tasks.get(k) or k)
    first = log.groupby("event_key")["ts_utc"].min()
    mins = sorted(round((first[ek] - t0).total_seconds() / 60, 1) for ek, t0 in entered.items() if ek in first)
    assert s["affected_events"] == len(entered) == 7      # ADR 007: D2 5 + D3 3, fentanyl counted once
    assert s["actioned_events"] == len(mins) == 6
    assert s["median_minutes"] == pd.Series(mins).median()
    assert s["max_minutes"] == max(mins)


def test_secondary_metric_ignores_non_simulated_rows(sim, demo_build, tmp_path):
    c = _copy(demo_build, tmp_path, "fresh.duckdb")
    try:
        s = metrics.compute_impact_metrics(c, sim["mq"], reports_dir=sim["rd"])["secondary"]
        assert s["actioned_events"] == 0 and "console.simulate" in s["detail"]
    finally:
        c.close()


def test_render_impact_block_round_trip(sim, tmp_path):
    doc = tmp_path / "impact.md"
    doc.write_text("intro\n<!-- impact:start -->\nold\n<!-- impact:end -->\noutro\n", encoding="utf-8")
    assert metrics.render_impact_md(sim["m"], doc) is True
    text = doc.read_text(encoding="utf-8")
    assert text.startswith("intro\n") and text.endswith("outro\n") and "OLD_BODY_X" not in text
    assert f"{sim['m']['primary']['matcher_recall']:.3f}" in text
    assert "SIMULATED demo usage, not real users" in text
    assert metrics.render_impact_md(sim["m"], tmp_path / "missing.md") is False


def _round_from(mq: dict, n: int, recall: float, precision: float, fair_recall: float) -> dict:
    """A per-round report shaped like ML's (``holdout_vN_unseen`` + seen references), built from the v1 one so
    this test does not depend on ML's files."""
    import copy

    head = copy.deepcopy(mq)
    head["matcher"]["overall"].update(recall=recall, precision=precision)
    head["matcher"]["failing_pairs"] = []
    head["baseline_v2"] = {"overall": {"precision": 0.571, "recall": fair_recall, "f1": 0.16, "tp": 4, "fp": 3,
                                       "fn": 39}, "definition": "base-name equality"}
    seen = copy.deepcopy(mq)
    seen["matcher"]["overall"].update(recall=0.983, precision=0.983)
    return {f"holdout_v{n}_unseen": head, "gold_v1_seen_reference": seen, "note": "test"}


def test_newest_blind_round_is_headline_and_seen_is_labeled(sim, tmp_path):
    (tmp_path / "match_quality_v1.json").write_text(json.dumps(sim["mq"]), encoding="utf-8")
    (tmp_path / "match_quality_v2.json").write_text(json.dumps(_round_from(sim["mq"], 2, 0.936, 0.957, 0.234)),
                                                    encoding="utf-8")
    (tmp_path / "match_quality_v3.json").write_text(json.dumps(_round_from(sim["mq"], 3, 0.605, 0.963, 0.093)),
                                                    encoding="utf-8")
    m = metrics.compute_impact_metrics(sim["con"], sim["mq"], reports_dir=tmp_path)
    p = m["primary"]
    assert p["run"].startswith("v3 matcher on the unseen") and p["source"].endswith("match_quality_v3.json")
    assert p["matcher_recall"] == 0.605 and p["status"] == "FAIL"           # red against 0.95
    assert p["baseline_recall"] == 0.093 and p["baseline_name"].startswith("Fairer")
    blind = [h for h in p["history"] if not h["seen"]]
    assert [h["version"] for h in blind] == [1, 2, 3] and [h["headline"] for h in blind] == [False, False, True]
    assert blind[0]["recall"] == sim["mq"]["matcher"]["overall"]["recall"] and blind[1]["recall"] == 0.936
    seen = [h for h in p["history"] if h["seen"]]
    assert seen and all("seen, optimistic" in h["run"] for h in seen)
    assert next(g for g in m["guardrails"] if g["id"] == "a")["value"] == 0.963
    fs = p["fail_safe"]
    assert fs["min_precision"] == min(h["precision"] for h in p["history"]) and fs["sets"] == len(p["history"])
    text = metrics.render_impact_block(m)
    assert "seen, optimistic" in text and "0.605" in text and "**FAIL**" in text and "Fails safe" in text


def test_category_lists_match_principal_engineer_source():
    """The per-category pair lists are a copy of docs/img/make_charts.py; parse (not import) the source."""
    import ast

    src = metrics.ROOT / "docs" / "img" / "make_charts.py"
    if not src.exists():
        pytest.skip("docs/img/make_charts.py not present")
    found = {}
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("COMBO", "PREMIX"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert found["COMBO"] == metrics.CATEGORY_COMBO
    assert found["PREMIX"] == metrics.CATEGORY_PREMIX


def test_v3_categories_reconcile_to_frozen_files():
    """Headline category counts on the real v3 report: single 14/16, combination 12/23, premix 0/4."""
    rep = metrics.REPORTS / "match_quality_v3.json"
    if not rep.exists():
        pytest.skip("reports/match_quality_v3.json not present")
    q = metrics.load_quality(json.loads(rep.read_text(encoding="utf-8")), metrics.REPORTS)
    assert q["version"] >= 3
    if q["version"] == 3:
        got = {c["category"].split(" ")[0]: (c["found"], c["positives"]) for c in q["categories"]}
        assert got == {"Single": (14, 16), "Combination": (12, 23), "Premix": (0, 4)}
        assert sum(n for _, n in got.values()) == q["head"]["matcher"]["overall"]["tp"] + \
            q["head"]["matcher"]["overall"]["fn"]
