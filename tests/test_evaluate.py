"""ML-07 tests for pipeline.evaluate (AC-4.4, AC-4.5). The gold files are read, never written."""
import pandas as pd

from pipeline import evaluate as ev


def test_evaluate_arithmetic_on_toy_gold():
    gold = pd.DataFrame({
        "pair_id": ["P1", "P2", "P3", "P4"], "item_id": ["i1", "i2", "i3", "i4"], "event_key": ["e"] * 4,
        "label": [1, 1, 0, 0], "variant_type": ["a", "a", "b", "b"], "rationale": ["r"] * 4})
    pred = pd.DataFrame({"item_id": ["i1", "i3", "zz"], "event_key": ["e", "e", "e"], "match_tier": [1, 3, 2],
                         "matched_on": ["x", "y", "z"]})
    m = ev.evaluate(pred, gold)
    o = m["overall"]
    assert (o["tp"], o["fp"], o["fn"], o["tn"]) == (1, 1, 1, 1)
    assert o["precision"] == 0.5 and o["recall"] == 0.5
    assert m["per_tier"]["1"]["precision"] == 1.0 and m["per_tier"]["3"]["precision"] == 0.0
    assert m["per_variant_type"]["a"]["recall"] == 0.5
    assert {f["pair_id"] for f in m["failing_pairs"]} == {"P2", "P3"}
    assert m["unlabeled_predictions"] == 1


def test_run_evaluation_reports_frozen_hash_and_does_not_touch_gold():
    before = ev.lf_sha256(ev.GOLD)
    report = ev.run_evaluation()
    assert ev.lf_sha256(ev.GOLD) == before
    assert report["gold"]["hash_ok"] is True
    assert report["gold"]["gold_event_keys_missing_from_events"] == []
    for side in ("matcher", "baseline", "baseline_v2"):
        assert {"precision", "recall", "f1"} <= set(report[side]["overall"])
        assert isinstance(report[side]["pass"], bool)
    assert report["matcher"]["pass"] == (report["matcher"]["overall"]["recall"] >= 0.95
                                         and report["matcher"]["overall"]["precision"] >= 0.90)


def test_v2_mode_without_holdout_exits_cleanly(tmp_path):
    assert ev.main(["--v2", "--holdout", str(tmp_path / "missing.csv")]) == 3


# ---------------------------------------------------------------- baseline_v2 (TEST_PLAN 6.2, spec by QA)
from pipeline.match import base_name, baseline_v2_match  # noqa: E402


def test_base_name_strips_dosage_form():
    assert base_name("Furosemide Injection") == "furosemide"


def test_base_name_keeps_salt_strips_release_form():
    assert base_name("Metoprolol Succinate Tablet, Extended Release") == "metoprolol succinate"


def test_base_name_combination_slash_becomes_space():
    assert base_name("Quinapril/Hydrochlorothiazide Tablet") == "quinapril hydrochlorothiazide"


def test_base_name_case_whitespace_and_strength():
    assert base_name("  CLONAZEPAM 0.5MG TAB ") == "clonazepam"


def test_base_name_empty_never_matches():
    assert base_name("Injection") == ""
    f = pd.DataFrame([{"item_id": "i", "name_raw": "Tablet"}])
    e = pd.DataFrame([{"event_key": "e", "generic_name": "Injection"}])
    assert baseline_v2_match(f, e).empty
