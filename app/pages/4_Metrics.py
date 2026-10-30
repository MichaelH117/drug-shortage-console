"""Success metric, baseline and guardrails (AE-03/AE-04, AC-8.1 to 8.3). Computed live by console.metrics from
reports/match_quality.json and this database, so it always matches the audit log on screen."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import ui  # noqa: E402

from console.metrics import compute_impact_metrics  # noqa: E402
from pipeline.config import connect, paths_for  # noqa: E402

QUESTION = ("Does the console surface more of the shortages that hit us than a manual name search, without "
            "flooding the team or breaking the controls?")


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


def _f(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


with ui.page("Success metric and guardrails", QUESTION) as con:
    mode = ui.db_mode()
    path = paths_for(mode).reports / "match_quality.json"
    if not path.exists():
        st.error(f"{path} not found. Run python -m pipeline.run --fixtures")
        st.stop()
    mq = json.loads(path.read_text(encoding="utf-8"))
    live_db = paths_for("live").db
    live_con = None
    if mode == "demo" and live_db.exists():
        try:
            live_con = connect(live_db, read_only=True)
        except Exception:  # live DB busy: guardrail (b) shows n/a
            live_con = None
    try:
        m = compute_impact_metrics(con, mq, live_con=live_con, reports_dir=paths_for(mode).reports)
    finally:
        if live_con is not None:
            live_con.close()
    p, s = m["primary"], m["secondary"]

    st.markdown("#### Primary metric: recall of affected formulary items surfaced in the queue")
    c = st.columns(4)
    ui.kpi(c[0], "Console recall", _f(p["matcher_recall"]),
           f"{p['matcher_tp']} of {p['matcher_tp'] + p['matcher_fn']} found; {p['run']}")
    ui.kpi(c[1], "Fairer baseline recall", _f(p["baseline_recall"]), "base-name search (simulated manual)")
    ui.kpi(c[2], "Target", f">= {p['target']}", "EM decision E2")
    ui.kpi(c[3], "Status", p["status"], "recall below target" if p["status"] == "FAIL" else "meets target")
    if p["status"] == "FAIL":
        st.markdown(f'<div class="dsc-strip dsc-stale"><b>Primary metric misses its target.</b> Headline: '
                    f'{ui.esc(p["run"])}, recall {_f(p["matcher_recall"])} vs {p["target"]}.</div>',
                    unsafe_allow_html=True)
    st.caption(f"Measured on {p['measured_on']}. Source: {p['source']} (sha256 {p['gold_sha256'][:12]}..., "
               f"hash ok {p['gold_hash_ok']}).")
    hist = pd.DataFrame(p["history"])
    if not hist.empty:
        hist["Label"] = hist["seen"].map({True: "seen, optimistic", False: "unseen"})
        st.dataframe(hist[["run", "Label", "precision", "recall", "f1"]]
                     .rename(columns={"run": "History", "precision": "Precision", "recall": "Recall", "f1": "F1"})
                     .style.map(lambda v: "color:#FBB360;font-weight:700" if v == "seen, optimistic" else "",
                                subset=["Label"]),
                     hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.3f")
                                    for k in ("Precision", "Recall", "F1")})
    _categories_and_fail_safe(p, "cat_metrics")

    st.markdown("#### Guardrails")
    g = pd.DataFrame(m["guardrails"])
    g_view = pd.DataFrame({"": "(" + g["id"] + ")", "Guardrail": g["name"], "Protects against": g["protects"],
                           "Value": g["value"].map(_f), "Threshold": g["threshold"], "Status": g["status"],
                           "Detail": g["detail"]})
    st.dataframe(g_view.style.map(lambda v: {"PASS": "background-color:#238551;color:#fff;font-weight:700",
                                             "FAIL": "background-color:#CD4246;color:#fff;font-weight:700",
                                             "N/A": "background-color:#383E47;color:#ABB3BF;font-weight:700"}
                                  .get(v, ""), subset=["Status"]),
                 hide_index=True, width="stretch")
    b = next(x for x in m["guardrails"] if x["id"] == "b")
    st.caption(f"Demo queue length today: {b['demo_queue_length']} items ({b.get('demo_note') or 'live data'}).")

    st.markdown("#### Secondary metric: time from detection to first action")
    st.markdown(f'<div class="dsc-strip dsc-synth"><b>{ui.esc(s["label"])}.</b> Scripted by console.simulate '
                'with fixed timestamps; app clicks are excluded. It shows the metric works end to end, not how '
                'fast real pharmacists are.</div>', unsafe_allow_html=True)
    c = st.columns(4)
    ui.kpi(c[0], "Affected events", s["affected_events"], "entered the queue after day 1")
    ui.kpi(c[1], "Actioned", s["actioned_events"], f"{s['not_actioned_events']} not yet")
    med = s["median_minutes"]
    ui.kpi(c[2], "Median", f"{med:.0f} min" if med is not None else "n/a", "detection to first action")
    ui.kpi(c[3], "p90", f"{s['p90_minutes']:.0f} min" if s['p90_minutes'] is not None else "n/a",
           f"max {_f(s['max_minutes'])} min")
    per = pd.DataFrame(s["per_event"])
    if not per.empty and per["minutes"].notna().any():
        names = dict(con.execute("SELECT event_key, generic_name FROM obj.shortage_event").fetchall())
        d = per.dropna(subset=["minutes"]).assign(drug=lambda x: x["event_key"].map(names))
        chart = (alt.Chart(d, title=alt.Title("Minutes from detection to first action (SIMULATED)",
                                              subtitle="One bar per affected shortage event; detection = "
                                                       "snapshot time that put it in the queue",
                                              color="#F6F7F9", subtitleColor="#ABB3BF"))
                 .mark_bar(color="#4C90F0", cornerRadiusEnd=4, height=14)
                 .encode(x=alt.X("minutes:Q", title="Minutes (detection to first SUCCESS action)"),
                         y=alt.Y("drug:N", sort="x", title=None),
                         tooltip=["drug", "event_key", "detected_utc", "first_action_utc", "minutes"])
                 .properties(height=36 * len(d)))
        st.altair_chart(chart, width="stretch")
    if s.get("detail"):
        st.info(s["detail"])
    with st.expander("Per-event table"):
        st.dataframe(per, hide_index=True, width="stretch")
    st.caption("Definitions, grain and gaming paths: docs/METRICS.md. Written summary: docs/impact.md.")
