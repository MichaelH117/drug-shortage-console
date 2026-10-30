"""Match-quality evaluation against the frozen gold set (ML-07, AC-4.4, AC-4.5).

``python -m pipeline.evaluate`` runs the tiered matcher and the name-only baseline on the gold inputs
(tests/fixtures/gold_events.json + SYNTHETIC_gold_formulary.csv, drafted and frozen by QA), scores both against
tests/fixtures/gold_matches.csv, and writes reports/match_quality.json.

It never edits the gold files. Scores cover the labeled pairs only: a predicted pair that is not in the gold
set is neither a TP nor an FP (it is counted separately as ``unlabeled_predictions``).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import sys
from pathlib import Path

import pandas as pd

from pipeline.match import (
    baseline_match,
    baseline_v2_match,
    build_brand_map,
    match,
    normalize_events,
    normalize_formulary,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
GOLD = FIXTURES / "gold_matches.csv"
GOLD_EVENTS = FIXTURES / "gold_events.json"
GOLD_FORMULARY = FIXTURES / "SYNTHETIC_gold_formulary.csv"
GOLD_SHA = FIXTURES / "gold_matches.sha256"
REPORT = ROOT / "reports" / "match_quality.json"
REPORT_V2 = ROOT / "reports" / "match_quality_v2.json"
HOLDOUT_V2 = FIXTURES / "holdout_v2.csv"
HOLDOUT_V2_EVENTS = FIXTURES / "holdout_v2_events.json"
HOLDOUT_V2_FORMULARY = FIXTURES / "SYNTHETIC_holdout_v2_formulary.csv"
REPORT_V3 = ROOT / "reports" / "match_quality_v3.json"
HOLDOUT_V3 = FIXTURES / "holdout_v3.csv"
HOLDOUT_V3_EVENTS = FIXTURES / "holdout_v3_events.json"
HOLDOUT_V3_FORMULARY = FIXTURES / "SYNTHETIC_holdout_v3_formulary.csv"
TARGETS = {"recall": 0.95, "precision": 0.90}
MATCHER_VERSION = "v3 (ADR 006 + addendum + TL conditions, round 3)"  # SPEC AC-4.5, EM decision E2


def lf_sha256(path: Path) -> str:
    """sha256 of LF-normalized bytes (the rule QA used for gold_matches.sha256)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def frozen_hashes(path: Path = GOLD_SHA) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            h, name = line.split()[:2]
            out[name.lstrip("*")] = h
    return out


def load_gold(path: Path = GOLD) -> pd.DataFrame:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    g = pd.read_csv(io.StringIO("\n".join(lines)), dtype=str, keep_default_na=False)
    g["label"] = g["label"].astype(int)
    return g


def load_gold_inputs(events_path: Path = GOLD_EVENTS,
                     formulary_path: Path = GOLD_FORMULARY) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(events, formulary) for the gold set. Events go through DE's parse_records so event_key follows DESIGN 3.2."""
    from pipeline.config import load_availability_map
    from pipeline.ingest import parse_records

    data = json.loads(Path(events_path).read_text(encoding="utf-8"))
    records = data["results"] if isinstance(data, dict) else data
    events = parse_records(records, dt.date(2026, 10, 30), load_availability_map())
    formulary = pd.read_csv(formulary_path, dtype=str, keep_default_na=False).replace({"": None})
    return events, formulary


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    rnd = lambda x: None if x is None else round(x, 4)  # noqa: E731
    return {"tp": tp, "fp": fp, "fn": fn, "precision": rnd(p), "recall": rnd(r), "f1": rnd(f1)}


def evaluate(pred: pd.DataFrame, gold: pd.DataFrame) -> dict:
    """P/R/F1 overall, per tier and per variant_type, plus the failing pairs."""
    pred = pred.drop_duplicates(["item_id", "event_key"])
    j = gold.merge(pred[["item_id", "event_key", "match_tier", "matched_on"]], on=["item_id", "event_key"],
                   how="left")
    j["predicted"] = j["match_tier"].notna()
    tp = int(((j.label == 1) & j.predicted).sum())
    fp = int(((j.label == 0) & j.predicted).sum())
    fn = int(((j.label == 1) & ~j.predicted).sum())
    tn = int(((j.label == 0) & ~j.predicted).sum())
    overall = {**_prf(tp, fp, fn), "tn": tn, "pairs": len(j), "positives": int((j.label == 1).sum())}

    per_tier = {}
    positives = max(1, overall["positives"])
    for tier, g in j[j.predicted].groupby("match_tier"):
        t_tp, t_fp = int((g.label == 1).sum()), int((g.label == 0).sum())
        per_tier[str(int(tier))] = {"tp": t_tp, "fp": t_fp,
                                    "precision": round(t_tp / (t_tp + t_fp), 4) if t_tp + t_fp else None,
                                    "recall_share": round(t_tp / positives, 4)}

    per_variant = {}
    for vt, g in j.groupby("variant_type"):
        v = _prf(int(((g.label == 1) & g.predicted).sum()), int(((g.label == 0) & g.predicted).sum()),
                 int(((g.label == 1) & ~g.predicted).sum()))
        v["pairs"] = len(g)
        per_variant[vt] = v

    fail = j[(j.label == 1) != j.predicted]
    failing = [{"pair_id": r.pair_id, "item_id": r.item_id, "event_key": r.event_key, "label": int(r.label),
                "error": "FN" if r.label == 1 else "FP", "variant_type": r.variant_type,
                "predicted_tier": None if pd.isna(r.match_tier) else int(r.match_tier),
                "matched_on": None if pd.isna(r.matched_on) else r.matched_on, "rationale": r.rationale}
               for r in fail.sort_values("pair_id").itertuples()]
    gold_pairs = set(zip(gold.item_id, gold.event_key, strict=False))
    unlabeled = sum(1 for k in zip(pred.item_id, pred.event_key, strict=False) if k not in gold_pairs)
    return {"overall": overall, "per_tier": per_tier, "per_variant_type": per_variant,
            "failing_pairs": failing, "unlabeled_predictions": unlabeled}


def _passes(m: dict) -> bool:
    o = m["overall"]
    return (o["recall"] or 0) >= TARGETS["recall"] and (o["precision"] or 0) >= TARGETS["precision"]


def _frozen_hash_for(gold_path: Path) -> str | None:
    """Look for the lock in <stem>.sha256 next to the file, then in gold_matches.sha256."""
    for lock in (gold_path.with_suffix(".sha256"), GOLD_SHA):
        if lock.exists():
            h = frozen_hashes(lock).get(gold_path.name)
            if h:
                return h
    return None


def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def run_evaluation(gold_path: Path = GOLD, events_path: Path = GOLD_EVENTS,
                   formulary_path: Path = GOLD_FORMULARY) -> dict:
    """Score the matcher and two name-only baselines on one gold file. Read-only; returns the report dict.

    ``baseline`` is the v1 AC-4.4 baseline (exact lowercase generic_name); ``baseline_v2`` is QA's fairer
    baseline (TEST_PLAN 6.2: base_name equality, dosage-form and strength words removed).
    """
    gold_path = Path(gold_path)
    gold = load_gold(gold_path)
    events, formulary = load_gold_inputs(events_path, formulary_path)
    bm = build_brand_map(events)
    enorm = normalize_events(events, bm)
    pred = match(normalize_formulary(formulary, bm), enorm)
    matcher = evaluate(pred, gold)
    fair = evaluate(baseline_v2_match(formulary, events), gold)
    exact = evaluate(baseline_match(formulary, events), gold)
    actual = lf_sha256(gold_path)
    expected = _frozen_hash_for(gold_path)
    missing_keys = sorted(set(gold.event_key) - set(events.event_key))
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
        "matcher_version": MATCHER_VERSION,
        "gold": {"file": _rel(gold_path), "events_file": _rel(events_path), "formulary_file": _rel(formulary_path),
                 "sha256": actual, "sha256_frozen": expected, "hash_ok": actual == expected, "pairs": len(gold),
                 "positives": int((gold.label == 1).sum()), "hard_negatives": int((gold.label == 0).sum()),
                 "labeled_by": "qa-engineer", "reviewed_by": "pending (team-labeled, not reviewed by the owner)",
                 "gold_event_keys_missing_from_events": missing_keys},
        "inputs": {"events": len(events), "formulary_items": len(formulary), "brand_map_entries": len(bm)},
        "targets": TARGETS,
        "matcher": {**matcher, "pass": _passes(matcher)},
        "baseline": {**exact, "pass": _passes(exact),
                     "definition": "v1 (AC-4.4): exact lowercase equality of formulary name_raw and event "
                                   "generic_name"},
        "baseline_v2": {**fair, "pass": _passes(fair),
                        "definition": "TEST_PLAN 6.2: base_name(name_raw) == base_name(generic_name), non-empty; "
                                      "no salts, brands, NDCs, routes, strengths or fuzzy matching"},
    }


def _print(report: dict, label: str) -> None:
    g = report["gold"]
    print(f"[{label}] {g['file']} sha256 {g['sha256'][:12]}... (frozen match: {g['hash_ok']}), {g['pairs']} pairs, "
          f"{g['positives']} positive / {g['hard_negatives']} hard negative, reviewed_by pending")
    for name in ("matcher", "baseline", "baseline_v2"):
        r = report[name]
        o = r["overall"]
        print(f"  {name:14s} P={o['precision']} R={o['recall']} F1={o['f1']} tp={o['tp']} fp={o['fp']} "
              f"fn={o['fn']} tn={o['tn']}  target pass={r['pass']}")
    for t, v in report["matcher"]["per_tier"].items():
        print(f"    tier {t}: tp={v['tp']} fp={v['fp']} precision={v['precision']}")
    for f in report["matcher"]["failing_pairs"]:
        print(f"    {f['error']} {f['pair_id']} {f['item_id']} {f['event_key']} [{f['variant_type']}] "
              f"tier={f['predicted_tier']} {f['matched_on'] or ''}")


def main(argv: list[str] | None = None) -> int:
    """``python -m pipeline.evaluate`` -> reports/match_quality.json on the v1 gold set.

    ``python -m pipeline.evaluate --v2 [--events E --formulary F]`` scores the held-out set
    tests/fixtures/holdout_v2.csv ONCE, plus the v1 set for reference, into reports/match_quality_v2.json.
    Run --v2 only after QA marks the holdout locked in HANDOFF.md.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="python -m pipeline.evaluate")
    ap.add_argument("--v2", action="store_true", help="score holdout_v2.csv (+ v1 reference)")
    ap.add_argument("--v3", action="store_true",
                    help="score holdout_v3.csv ONCE as unseen (+ gold and holdout_v2 as seen references)")
    ap.add_argument("--holdout", type=Path, default=HOLDOUT_V2)
    ap.add_argument("--events", type=Path, default=GOLD_EVENTS, help="events JSON for the holdout")
    ap.add_argument("--formulary", type=Path, default=GOLD_FORMULARY, help="formulary CSV for the holdout")
    args = ap.parse_args(argv)

    if args.v3:
        for f in (HOLDOUT_V3, HOLDOUT_V3_EVENTS, HOLDOUT_V3_FORMULARY):
            if not f.exists():
                print(f"holdout v3 file not found: {f}", file=sys.stderr)
                return 3
        unseen = run_evaluation(HOLDOUT_V3, HOLDOUT_V3_EVENTS, HOLDOUT_V3_FORMULARY)
        seen_gold = run_evaluation()
        seen_v2 = run_evaluation(HOLDOUT_V2, HOLDOUT_V2_EVENTS, HOLDOUT_V2_FORMULARY)
        out = {"holdout_v3_unseen": unseen, "gold_v1_seen_reference": seen_gold,
               "holdout_v2_seen_reference": seen_v2,
               "note": "holdout_v3_unseen is the honest v3 score: the matcher author never opened that file. "
                       "gold and holdout_v2 drove earlier error analyses, so their v3 scores are optimistic."}
        REPORT_V3.parent.mkdir(parents=True, exist_ok=True)
        REPORT_V3.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        _print(unseen, "holdout v3, UNSEEN")
        _print(seen_gold, "v1 gold, SEEN reference")
        _print(seen_v2, "holdout v2, SEEN reference")
        print(f"-> {_rel(REPORT_V3)}")
        ok = all(r["gold"]["hash_ok"] for r in (unseen, seen_gold, seen_v2))
    elif args.v2:
        if not args.holdout.exists():
            print(f"holdout not found: {args.holdout}", file=sys.stderr)
            return 3
        holdout = run_evaluation(args.holdout, args.events, args.formulary)
        reference = run_evaluation()
        out = {"holdout_unseen": holdout, "v1_gold_seen_reference": reference,
               "note": "holdout_unseen is the honest v2 score: the matcher author never opened that file. "
                       "v1_gold_seen_reference drove the v1 error analysis, so its v2 score is optimistic."}
        REPORT_V2.parent.mkdir(parents=True, exist_ok=True)
        REPORT_V2.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        _print(holdout, "holdout v2, UNSEEN")
        _print(reference, "v1 gold, SEEN reference")
        print(f"-> {_rel(REPORT_V2)}")
        ok = holdout["gold"]["hash_ok"] and reference["gold"]["hash_ok"]
    else:
        report = run_evaluation()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        _print(report, "v1 gold")
        print(f"-> {_rel(REPORT)}")
        ok = report["gold"]["hash_ok"]
    if not ok:
        print("WARNING: a gold file hash does not match its lock file", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
