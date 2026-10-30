"""QA-02: the gold answer set is frozen and well-formed (SPEC AC-4.3, AC-4.5; DESIGN section 5, E1).

Owner: qa-engineer. This test never runs the matcher. It proves the file has not changed since it was frozen,
that every row is still `reviewed_by = pending`, and that the mix DESIGN asks for is present.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

FIX = Path(__file__).parent / "fixtures"
GOLD = FIX / "gold_matches.csv"
ITEMS = FIX / "SYNTHETIC_gold_formulary.csv"
EVENTS = FIX / "gold_events.json"
LOCK = FIX / "gold_matches.sha256"

COLUMNS = ["pair_id", "item_id", "event_key", "label", "pair_kind", "variant_type", "rationale",
           "labeled_by", "reviewed_by"]
# Variant types the generator (DE-06, AC-3.3) is expected to produce. Everything else is hand-written.
GENERATOR_VARIANTS = {"ndc_442", "ndc_532", "ndc_541", "ndc_11digit", "ndc_leading_zero_error",
                      "name_brand", "name_salt", "name_case_ws"}
HARD_CASES = {"missing_ndc", "typo", "diff_strength", "diff_dosage_form", "diff_route", "combination_product",
              "true_non_match", "lookalike_name", "same_labeler_diff_product", "openfda_sibling_strength"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.fixture(scope="module")
def gold() -> pd.DataFrame:
    return pd.read_csv(GOLD, comment="#", dtype=str, keep_default_na=False)


def test_hashes_match_lock_file():
    expected = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            expected[name] = digest
    assert set(expected) == {GOLD.name, ITEMS.name, EVENTS.name}
    for name, digest in expected.items():
        assert _sha(FIX / name) == digest, f"{name} changed after freeze; log a new gold version in HANDOFF"


def test_header_states_rule_and_pending_review():
    header = [ln for ln in GOLD.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
    text = "\n".join(header)
    assert "LABELING RULE" in text
    assert "reviewed_by = pending" in text


def test_columns_exact(gold):
    assert list(gold.columns) == COLUMNS


def test_every_row_pending_and_labeled_by_qa(gold):
    assert (gold["reviewed_by"] == "pending").all()
    assert (gold["labeled_by"] == "qa-engineer").all()
    # E1: nobody may claim the owner reviewed it.
    assert not gold.apply(lambda r: "michael" in " ".join(r).lower(), axis=1).any()


def test_counts_and_mix(gold):
    assert len(gold) >= 60
    assert gold["pair_id"].is_unique
    assert not gold.duplicated(["item_id", "event_key"]).any()
    assert set(gold["label"]) <= {"0", "1"}
    kind = gold["label"].map({"1": "positive", "0": "hard_negative"})
    assert (kind == gold["pair_kind"]).all()
    assert (gold["label"] == "1").sum() >= 35
    assert (gold["label"] == "0").sum() >= 25
    assert (gold["rationale"].str.strip().str.len() > 10).all()


def test_variant_coverage(gold):
    counts = gold["variant_type"].value_counts()
    for v in GENERATOR_VARIANTS:
        assert counts.get(v, 0) >= 3, f"variant {v} has {counts.get(v, 0)} pairs"
    assert (~gold["variant_type"].isin(GENERATOR_VARIANTS)).sum() >= 10
    assert HARD_CASES <= set(counts.index), HARD_CASES - set(counts.index)


def test_required_hard_negatives(gold):
    neg = gold[gold["label"] == "0"]["variant_type"]
    for v in ("diff_route", "diff_strength", "lookalike_name", "same_labeler_diff_product",
              "ndc_leading_zero_error", "combination_product", "true_non_match"):
        assert (neg == v).any(), f"no hard negative of type {v}"


def test_references_resolve(gold):
    items = pd.read_csv(ITEMS, dtype=str, keep_default_na=False)
    assert items["item_id"].is_unique
    assert (items["is_synthetic"] == "true").all()
    assert set(gold["item_id"]) <= set(items["item_id"])

    events = json.loads(EVENTS.read_text(encoding="utf-8"))["results"]
    no_openfda = sum(1 for r in events if not r.get("openfda"))
    assert no_openfda >= 5, "gold events must exercise AC-4.6 (no openfda block)"
    keys = set()
    for r in events:
        a, b, c = r["package_ndc"].split("-")
        pad = {(4, 4, 2): "0" + a + b + c, (5, 3, 2): a + "0" + b + c,
               (5, 4, 1): a + b + "0" + c, (5, 4, 2): a + b + c}[(len(a), len(b), len(c))]
        m, d, y = r["initial_posting_date"].split("/")
        keys.add(f"{pad}|{y}-{m}-{d}")
    assert set(gold["event_key"]) <= keys
