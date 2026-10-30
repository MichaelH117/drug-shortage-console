"""Held-out answer sets (v2, v3) are frozen, pending review, and disjoint from every earlier set.

Owner: qa-engineer. Structure and hashes only. Nothing here prints rows, so a failure does not leak held-out
labels to whoever reads the test output.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

FIX = Path(__file__).parent / "fixtures"
GENERATOR_VARIANTS = {"ndc_442", "ndc_532", "ndc_541", "ndc_11digit", "ndc_leading_zero_error",
                      "name_brand", "name_salt", "name_case_ws"}
ITEM_COLS = ["ndc_raw", "name_raw", "strength_raw", "dosage_form_raw", "route_raw"]
# (pairs file, items file, events file); order matters: each set must be disjoint from all earlier ones.
SETS = {
    "gold": ("gold_matches.csv", "SYNTHETIC_gold_formulary.csv", "gold_events.json"),
    "v2": ("holdout_v2.csv", "SYNTHETIC_holdout_v2_formulary.csv", "holdout_v2_events.json"),
    "v3": ("holdout_v3.csv", "SYNTHETIC_holdout_v3_formulary.csv", "holdout_v3_events.json"),
}
HELD_OUT = ["v2", "v3"]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _csv(name: str, comment: str | None = None) -> pd.DataFrame:
    return pd.read_csv(FIX / name, comment=comment, dtype=str, keep_default_na=False)


@pytest.mark.parametrize("version", HELD_OUT)
def test_holdout_hashes(version):
    lock = {}
    for line in (FIX / f"holdout_{version}.sha256").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            lock[name] = digest
    assert set(lock) == set(SETS[version])
    bad = [n for n, d in lock.items() if _sha(FIX / n) != d]
    assert not bad, f"held-out files changed after freeze: {bad}"


@pytest.mark.parametrize("version", HELD_OUT)
def test_holdout_shape_and_review_state(version):
    h = _csv(SETS[version][0], comment="#")
    ok = (
        len(h) >= 60
        and (h["reviewed_by"] == "pending").all()
        and (h["labeled_by"] == "qa-engineer").all()
        and (h["label"] == "1").sum() >= 35
        and (h["label"] == "0").sum() >= 25
        and h["pair_id"].is_unique
        and all((h["variant_type"] == v).sum() >= 3 for v in GENERATOR_VARIANTS)
        and (~h["variant_type"].isin(GENERATOR_VARIANTS)).sum() >= 10
    )
    assert ok, f"{version} fails a structural rule (details withheld on purpose)"


def test_v3_has_combo_and_brand_traps():
    h = _csv(SETS["v3"][0], comment="#")
    neg_combo = ((h["variant_type"] == "combination_product") & (h["label"] == "0")).sum()
    assert neg_combo >= 8 and (h["variant_type"] == "name_brand").sum() >= 8, "v3 trap mix too thin"


@pytest.mark.parametrize("version", HELD_OUT)
def test_holdout_disjoint_from_earlier_sets(version):
    pairs, items, _ = SETS[version]
    h, hi = _csv(pairs, comment="#"), _csv(items)
    assert set(h["item_id"]) <= set(hi["item_id"])
    for earlier in list(SETS)[: list(SETS).index(version)]:
        ep, ei, _ = SETS[earlier]
        g, gi = _csv(ep, comment="#"), _csv(ei)
        assert not set(h["event_key"]) & set(g["event_key"]), f"{version} shares records with {earlier}"
        assert not set(hi["item_id"]) & set(gi["item_id"]), f"{version} shares item ids with {earlier}"
        assert hi.merge(gi, on=ITEM_COLS).empty, f"{version} shares formulary items with {earlier}"
