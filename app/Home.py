"""Morning triage queue (AE-03, AE-08; AC-5.1, 5.2, 5.4, 5.5; ADR 007). Start: streamlit run app/Home.py

Daily mode: actionable NEW/WORSENED events since the previous snapshot, ranked, top 40 shown with "N more".
Baseline mode (the latest snapshot is the first one): a one-time, item-grain review list instead.
Matched events that fail the actionable filter go to the Watch list: counted and shown, never dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import ui  # noqa: E402

from console import queries  # noqa: E402

QUESTION = ("Of the shortages that are new or got worse since yesterday, which ones hit drugs Riverbend stocks, "
            "and which need a decision first?")
CAP = queries.QUEUE_CAP
CHANGE_STYLE = {"NEW": ui.CELL["accent"], "WORSENED": ui.CELL["warn"]}
AVAIL_STYLE = {"Unavailable": ui.CELL["bad_text"], "Limited": ui.CELL["warn_text"],
               "Available": ui.CELL["ok_plain"], "Unknown": ui.CELL["muted"]}
TIER_SHORT = {1: "T1 NDC", 2: "T2 product", 3: "T3 ingredient"}
DAYS_COL = st.column_config.NumberColumn(format="%d d", width="small", help="Lowest SYNTHETIC days on hand")


def _substitute_text(r) -> str:
    if r.substitute_approved:
        return "Approved"
    if r.substitutes_proposed:
        return f"{r.substitutes_proposed} proposed"
    if r.substitute_options:
        return f"{r.substitute_options} candidate{'s' if r.substitute_options > 1 else ''}"
    return "none"


def _page(df: pd.DataFrame, key: str, noun: str) -> pd.DataFrame:
    """Visible cap (ADR 007 point 5): top 40 per page, with 'N more' and a page control. Never silently drops."""
    n = len(df)
    if n <= CAP:
        return df
    pages = (n + CAP - 1) // CAP
    page = st.selectbox(f"Page (showing {CAP} of {n} {noun})", list(range(1, pages + 1)), key=key,
                        format_func=lambda p: f"{(p - 1) * CAP + 1}-{min(p * CAP, n)} of {n}")
    start = (page - 1) * CAP
    st.markdown(f'<div class="dsc-more">{n - CAP} more beyond the top {CAP}; '
                f'nothing is hidden, use the page control.</div>', unsafe_allow_html=True)
    return df.iloc[start:start + CAP]


def _daily_table(q: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "#": q["queue_rank"],
        "Change": q["change_type"],
        "Drug": q["generic_name"],
        "Availability": q["availability_normalized"],
        "Was": q["prev_availability"].fillna("(new)"),
        "Days on hand": q["min_days_on_hand"],
        "Open tasks": q["open_tasks"],
        "Substitute": [_substitute_text(r) for r in q.itertuples()],
        "Sites affected": q["facilities"],
        "Match": q["best_match_tier"].map(TIER_SHORT),
        "FDA status": q["status"],
        "FDA availability text": q["availability_raw"].fillna(""),
        "NDC": q["ndc11"].map(ui.fmt_ndc),
    })


def _open_from(table_event, keys: list[str]) -> None:
    rows = table_event.selection.rows if table_event is not None else []
    if rows:
        ui.open_object(keys[rows[0]])


def _daily(con, info: dict, q: pd.DataFrame) -> None:
    st.markdown(f"#### Since yesterday &nbsp;{ui.badge(info['previous'], 'date')} &rarr; "
                f"{ui.badge(info['latest'], 'date')}", unsafe_allow_html=True)
    st.caption("Actionable only: Limited, Unavailable or To Be Discontinued, not Resolved. Ranked by availability, "
               "then lowest SYNTHETIC days on hand, then match tier, then sites affected. Click a row to open it.")
    if q.empty:
        st.info("Nothing actionable is new or worse for Riverbend stock in the latest snapshot.")
        return
    shown = _page(q, "queue_page", "actionable shortages")
    t = _daily_table(shown)
    styled = (t.style
              .map(lambda v: CHANGE_STYLE.get(v, ""), subset=["Change"])
              .map(lambda v: AVAIL_STYLE.get(v, ""), subset=["Availability", "Was"])
              .map(lambda v: ui.CELL["bad_text"] if isinstance(v, (int, float)) and v <= 7 else "",
                   subset=["Days on hand"])
              .map(lambda v: ui.CELL["ok_text"] if v == "Approved" else "", subset=["Substitute"]))
    ev = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="queue_table",
                      column_config={"#": st.column_config.NumberColumn(width="small"), "Days on hand": DAYS_COL,
                                     "Open tasks": st.column_config.NumberColumn(format="%d", width="small"),
                                     "Was": st.column_config.TextColumn(width="small")})
    _open_from(ev, shown["event_key"].tolist())
    names = dict(zip(q["event_key"], q["generic_name"], strict=True))
    pick = st.selectbox("Open object view", q["event_key"].tolist(), index=None,
                        placeholder="Choose a queue item to open", format_func=lambda k: f"{names[k]}  ({k})",
                        key="queue_pick")
    if pick:
        ui.open_object(pick)


def _worklist_table(w: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "#": w["worklist_rank"], "Formulary item": w["formulary_name"], "Item NDC as entered": w["ndc_raw"],
        "Worst availability": w["worst_availability"], "Days on hand": w["min_days_on_hand"],
        "Sites": w["facilities"], "Matching FDA records": w["n_events"],
        "Best match": w["best_match_tier"].map(TIER_SHORT), "Open worst record": w["worst_event_key"]})


def _worklist(w: pd.DataFrame, as_main: bool) -> None:
    if w.empty:
        st.caption("No actionable shortage touches the SYNTHETIC formulary.")
        return
    shown = _page(w, "worklist_page" if as_main else "worklist_page_bg", "formulary items")
    styled = _worklist_table(shown).style.map(
        lambda v: AVAIL_STYLE.get(v, ui.CELL["muted"]), subset=["Worst availability"])
    ev = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="worklist_table" if as_main else "worklist_table_bg",
                      column_config={"#": st.column_config.NumberColumn(width="small"), "Days on hand": DAYS_COL})
    _open_from(ev, shown["worst_event_key"].tolist())


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _overview_band(kicker: str, title: str, sub: str) -> None:
    """The takeaway for the whole page in one sentence. The optional moving background lives ONLY here, on the
    Home overview, behind a solid paper panel; it is OFF by default (audience rule: no motion while they work).
    The Drug object view, Audit and the action forms never load it."""
    paper = (f'<div class="paper"><div class="k">{ui.esc(kicker)}</div><div class="t">{ui.esc(title)}</div>'
             f'<div class="s">{ui.esc(sub)}</div></div>')
    if "wallpaper" not in st.session_state:  # off by default; ?bg=1 (a bookmark) turns it on for this session
        st.session_state["wallpaper"] = st.query_params.get("bg") == "1"
    if not st.session_state["wallpaper"]:
        st.markdown(f'<div class="dsc-band">{paper}</div>', unsafe_allow_html=True)
        return
    # Engine files are served by Streamlit's static route (server.enableStaticServing) as real files, because
    # st.html mangles a large inline script. Its layout CSS is part of app/assets/formulary.css.
    st.html(
        f'<div class="dsc-band" id="dsc-band-wall">{paper}</div>'
        '<script src="app/static/wallpaper/hl-wallpaper.js"></script>'
        """<script>
(function start(tries) {
  var b = document.getElementById("dsc-band-wall");
  if (!b || b.__hlw) return;
  if (!window.HansonWallpaper) { if (tries < 40) setTimeout(function () { start(tries + 1); }, 100); return; }
  if (window.__dscWall) { try { window.__dscWall.destroy(); } catch (e) {} }
  var w = HansonWallpaper.mount({ theme: "formulary", container: b, mode: "story", scope: "dsc-home", fps: 30 });
  b.__hlw = w; window.__dscWall = w;
  var mo = new MutationObserver(function () {   /* leaving Home or hiding it removes the box: stop drawing */
    if (document.body.contains(b)) return;
    w.destroy(); mo.disconnect(); if (window.__dscWall === w) window.__dscWall = null;
  });
  mo.observe(document.body, { childList: true, subtree: true });
})(0);</script>""", unsafe_allow_javascript=True)


def _wallpaper_toggle() -> None:
    st.toggle("Moving background (this overview only)", key="wallpaper",
              help="Off by default. A slow, low-contrast pharmacy-label pattern behind the summary above, with a "
                   "Pause motion button. It is never shown on the Drug, Audit or action screens.")
    if st.session_state["wallpaper"]:
        st.query_params["bg"] = "1"
    elif "bg" in st.query_params:
        del st.query_params["bg"]


with ui.page("Morning triage queue", QUESTION) as con:
    info = queries.snapshot_info(con)
    baseline = queries.is_baseline_day(con)
    q = queries.triage_queue(con)
    w = queries.baseline_worklist(con)
    watch = queries.watch_list(con)
    counts = queries.change_counts(con)

    needs = int(((q["open_tasks"] == 0) & (~q["substitute_approved"])).sum())
    low = "n/a" if q.empty else int(q["min_days_on_hand"].min())
    if baseline:
        items = _plural(len(w), "formulary item has", "formulary items have")
        _overview_band(f"Baseline day {info['latest']}", f"{items} an actionable shortage. Review them once.",
                       f"The daily since-yesterday queue starts with the next snapshot. Watch list: {len(watch)}.")
    else:
        _overview_band(f"Since yesterday, {info['previous']} to {info['latest']}",
                       f"{_plural(len(q), 'shortage hits', 'shortages hit')} drugs Riverbend stocks. "
                       f"{needs} {'needs' if needs == 1 else 'need'} a decision.",
                       f"Lowest SYNTHETIC days on hand: {low}. Watch list (new or worse, still available): "
                       f"{len(watch)}.")
    _wallpaper_toggle()

    c = st.columns([1.35, 1, 1, 1, 1, 1])
    if baseline:
        ui.kpi(c[0], "Baseline review", len(w), "formulary items, one time", lead=True)
        ui.kpi(c[1], "Daily queue", 0, "starts with the next snapshot")
        ui.kpi(c[2], "Unavailable items", int((w["worst_availability"] == "Unavailable").sum()), "in the review")
        ui.kpi(c[3], "FDA records matched", int(w["n_events"].sum()) if len(w) else 0, "actionable, stocked")
        ui.kpi(c[4], "Lowest days on hand", "n/a" if w.empty else int(w["min_days_on_hand"].min()), "SYNTHETIC stock")
        ui.kpi(c[5], "Watch list", len(watch), "matched, not actionable")
    else:
        ui.kpi(c[0], "In queue", len(q), f"actionable; top {CAP} shown" if len(q) > CAP else "actionable, stocked",
               lead=True)
        ui.kpi(c[1], "New", int((q["change_type"] == "NEW").sum()), f"{counts.get('NEW', 0)} on the FDA list")
        ui.kpi(c[2], "Worsened", int((q["change_type"] == "WORSENED").sum()),
               f"{counts.get('WORSENED', 0)} on the FDA list")
        ui.kpi(c[3], "Lowest days on hand", low, "SYNTHETIC stock", tone="bad" if low != "n/a" and low <= 7 else "")
        ui.kpi(c[4], "Needs a decision", needs, "no open task, no approved substitute", tone="warn" if needs else "")
        ui.kpi(c[5], "Watch list", len(watch), "new/worse but still available")

    st.write("")
    left, right = st.columns([3.3, 1.2])
    with left:
        if baseline:
            st.markdown(
                f'<div class="dsc-strip dsc-info"><div><b>Baseline day ({ui.esc(info["latest"])}).</b> '
                f'This is the first snapshot, so '
                f'there is no "since yesterday" yet: every FDA record would look NEW. Instead, review this '
                f'one-time list once, one row per formulary item. The daily queue starts with the next '
                f'snapshot (ADR 007).</div></div>', unsafe_allow_html=True)
            st.markdown("#### Baseline review: formulary items with an actionable shortage")
            st.caption("Ranked by worst availability, then lowest SYNTHETIC days on hand, then match tier. Click a "
                       "row to open its worst FDA record.")
            _worklist(w, as_main=True)
        else:
            _daily(con, info, q)
            with st.expander(f"Baseline backlog (onboarding view): {len(w)} formulary items with an actionable "
                             "shortage today"):
                st.caption("Item-grain view of everything actionable that Riverbend stocks, not just today's "
                           "changes. Used once at go-live; kept here for reference.")
                _worklist(w, as_main=False)

        with st.expander(f"Watch list: {len(watch)} matched {'records' if baseline else 'changes'} that are not "
                         "actionable (for example NEW but still Available)"):
            st.caption("Counted and shown so the actionable filter cannot hide anything. Guardrail (b) reports "
                       "this count next to the queue length.")
            if not watch.empty:
                st.dataframe(pd.DataFrame({
                    "Change": watch["change_type"], "Drug": watch["generic_name"],
                    "Availability": watch["availability_normalized"], "FDA status": watch["status"],
                    "Sites": watch["facilities"], "Days on hand": watch["min_days_on_hand"],
                    "Event": watch["event_key"]}), hide_index=True, width="stretch",
                    column_config={"Days on hand": DAYS_COL})
        others = queries.other_changes(con)
        if not baseline:
            with st.expander(f"Other changes in this snapshot: {len(others)} (not stocked, or improving)"):
                st.caption("NEW or WORSENED items that Riverbend does not stock, plus IMPROVED, RESOLVED and "
                           "DROPPED events.")
                st.dataframe(others.rename(columns={"change_type": "Change", "generic_name": "Drug",
                                                    "prev_availability": "Was", "curr_availability": "Now",
                                                    "prev_status": "Status was", "curr_status": "Status now",
                                                    "stocked": "Stocked"}),
                             hide_index=True, width="stretch")

    with right:
        from ontokit.audit import verify_chain

        chain = verify_chain(con)
        st.markdown("#### Activity")
        st.markdown(f"Audit chain {ui.badge('OK' if chain.ok else 'BROKEN', 'OK' if chain.ok else 'FAIL')} "
                    f"<span class='dsc-muted'>{chain.rows_checked} rows</span>",
                    unsafe_allow_html=True)
        recent = queries.audit_entries(con).head(7)
        if recent.empty:
            st.caption("No actions yet.")
        for r in recent.itertuples():
            crit = f" ({r.failed_criterion})" if isinstance(r.failed_criterion, str) else ""
            st.markdown(
                f'<div class="feed"><div class="t">#{r.seq} {str(r.ts_utc)[:16]} UTC</div>'
                f'{ui.badge(r.outcome)} <b>{ui.esc(r.actor or r.actor_id)}</b> '
                f'{ui.esc(r.action_type.replace("_", " "))}{ui.esc(crit)}<br>'
                f'<span class="o">{ui.esc(r.object_key)}</span></div>', unsafe_allow_html=True)
        # NEXT (not built, out of scope for the restyle): the #1 build-next item in research/voc/voc-drug-shortage.md,
        # an allocation and backorder board (ordered vs received per wholesaler, "expected back" date, days-on-hand
        # gauge). It would sit here as a second rail panel and as its own page. See docs/team/DESIGN_REVIEW.md.
        if st.button("Open audit log", width="stretch"):
            st.switch_page("pages/2_Audit.py")
