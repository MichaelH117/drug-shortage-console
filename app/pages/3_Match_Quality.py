"""Match quality (AE-03, AC-4.4 display). Headline = the newest matcher on a set its author never opened
(console.metrics.load_quality picks it); history and the seen-gold score are labeled. Nothing typed by hand."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import ui  # noqa: E402

from console.metrics import load_quality  # noqa: E402
from pipeline.config import paths_for  # noqa: E402

QUESTION = "How often does the console catch a shortage that hits our formulary, and how often does it cry wolf?"
NUM3 = st.column_config.NumberColumn(format="%.3f")


def _f(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _categories_and_fail_safe(q: dict, key: str) -> None:
    cats = q.get("categories")
    if cats:
        st.markdown("##### Headline recall by product category")
        cdf = pd.DataFrame([{"Category": c["category"], "Found": c["found"], "Positives": c["positives"],
                             "Recall": c["recall"]} for c in cats])
        st.dataframe(cdf, hide_index=True, width="stretch", key=key,
                     column_config={"Recall": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0,
                                                                              format="%.2f")})
        st.caption("Post-hoc split by formulary item text (Principal Engineer, docs/img/make_charts.py); not a "
                   "frozen label and not used for tuning. The v3 set is 53% combinations.")
    fs = q.get("fail_safe") or {}
    if fs.get("sets"):
        st.markdown(
            f'<div class="dsc-strip dsc-fresh"><b>Fails safe.</b> Precision is {fs["min_precision"]:.3f} to '
            f'{fs["max_precision"]:.3f} on all {fs["sets"]} scored sets, seen and unseen. When the matcher is '
            f'unsure it misses (recall drops) instead of putting a wrong drug in the queue.</div>',
            unsafe_allow_html=True)



with ui.page("Match quality", QUESTION) as con:
    reports = paths_for(ui.db_mode()).reports
    path = reports / "match_quality.json"
    if not path.exists():
        st.error(f"{path} not found. Run python -m pipeline.run --fixtures")
        st.stop()
    q = load_quality(json.loads(path.read_text(encoding="utf-8")), reports)
    h = q["head"]
    m, tg, g = h["matcher"]["overall"], h["targets"], h["gold"]
    fair = q["baselines"][0]

    r_ok = (m["recall"] or 0) >= tg["recall"]
    p_ok = (m["precision"] or 0) >= tg["precision"]
    where = "an unseen holdout the matcher author never opened" if q["unseen"] else "the frozen gold set"
    if not (r_ok and p_ok):
        st.markdown(
            f'<div class="dsc-strip dsc-stale" style="font-size:14px"><b>BELOW TARGET. Headline: '
            f'{ui.esc(q["label"])}.</b> Recall {_f(m["recall"])} vs target {tg["recall"]} ({m["fn"]} of '
            f'{m["tp"] + m["fn"]} true matches missed). Precision {_f(m["precision"])} '
            f'{"meets" if p_ok else "misses"} its {tg["precision"]} target. Measured once on {where}; '
            f'nobody tuned the labels to pass.</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="dsc-strip dsc-fresh">Headline ({ui.esc(q["label"])}) meets both targets.</div>',
                    unsafe_allow_html=True)

    c = st.columns(5)
    ui.kpi(c[0], "Recall", _f(m["recall"]), f"target >= {tg['recall']}  {'PASS' if r_ok else 'FAIL'}")
    ui.kpi(c[1], "Precision", _f(m["precision"]), f"target >= {tg['precision']}  {'PASS' if p_ok else 'FAIL'}")
    ui.kpi(c[2], "F1", _f(m["f1"]), f"tp {m['tp']}  fp {m['fp']}  fn {m['fn']}")
    ui.kpi(c[3], "Fairer baseline recall", _f(fair["recall"]), f"precision {_f(fair['precision'])}")
    ui.kpi(c[4], "Pairs", g["pairs"], f"{g['positives']} positive, {g['hard_negatives']} hard negative")
    st.caption(f"Headline set: {g['file']}, sha256 {g['sha256'][:16]}..., hash ok: {g['hash_ok']}. "
               f"Labeled by {g['labeled_by']}; reviewed_by: {g['reviewed_by']}. Source: {q['source']}.")

    st.write("")
    left, right = st.columns([1, 1])
    with left:
        st.markdown("##### Console matcher vs baselines (headline set)")
        comp = pd.DataFrame([{"Method": "Console matcher (tiers 1-3)", "Precision": m["precision"],
                              "Recall": m["recall"], "F1": m["f1"]}]
                            + [{"Method": b["name"], "Precision": b["precision"], "Recall": b["recall"],
                                "F1": b["f1"]} for b in q["baselines"]])
        st.dataframe(comp, hide_index=True, width="stretch",
                     column_config={"Precision": NUM3, "Recall": NUM3, "F1": NUM3})
        st.caption("Fairer baseline: " + fair["definition"] + ". The naive exact-name baseline scores 0 because "
                   "openFDA generic_name carries the dosage form ('Furosemide Injection').")

        st.markdown("##### History: matcher rounds")
        hist = pd.DataFrame(q["history"])
        hist["Label"] = hist["seen"].map({True: "seen, optimistic", False: "unseen"})
        st.dataframe(hist[["run", "Label", "precision", "recall", "f1", "tp", "fp", "fn"]]
                     .rename(columns={"run": "Run", "precision": "Precision", "recall": "Recall", "f1": "F1"})
                     .style.map(lambda v: "color:#FBB360;font-weight:700" if v == "seen, optimistic" else "",
                                subset=["Label"]),
                     hide_index=True, width="stretch",
                     column_config={"Precision": NUM3, "Recall": NUM3, "F1": NUM3})
        st.caption("Only unseen runs count against the target. Seen scores are optimistic: those sets drove "
                   "earlier error analyses.")
        _categories_and_fail_safe(q, "cat_mq")

        st.markdown("##### By match tier (headline set, predicted)")
        tiers = pd.DataFrame([{"Tier": ui.TIER_TEXT.get(int(k), k), **v} for k, v in h["matcher"]["per_tier"].items()])
        st.dataframe(tiers, hide_index=True, width="stretch")
    with right:
        st.markdown("##### Recall by variant type (headline set, positives only)")
        rows = [{"Variant": k, "Positives": v.get("tp", 0) + v.get("fn", 0), "Recall": v.get("recall"),
                 "FP": v.get("fp", 0)} for k, v in h["matcher"]["per_variant_type"].items()]
        var = pd.DataFrame(rows).sort_values(["Recall", "Variant"], na_position="last")
        st.dataframe(var, hide_index=True, width="stretch", height=560,
                     column_config={"Recall": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0,
                                                                              format="%.2f")})

    st.markdown("##### Failing pairs on the headline set (every miss and false match, with the labeler's rationale)")
    fails = pd.DataFrame(h["matcher"]["failing_pairs"])
    if fails.empty:
        st.caption("None.")
    else:
        st.dataframe(fails[["pair_id", "error", "variant_type", "item_id", "event_key", "predicted_tier",
                            "matched_on", "rationale"]], hide_index=True, width="stretch")
