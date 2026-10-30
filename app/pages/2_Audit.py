"""Append-only, hash-chained action log with the chain-verify result at the top (AE-03, AC-7.4)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402
import ui  # noqa: E402

from console import queries  # noqa: E402
from ontokit.audit import verify_chain  # noqa: E402
from pipeline.config import paths_for  # noqa: E402

QUESTION = "Who did what to which object, when, and can we prove nobody quietly edited the record since?"

with ui.page("Audit log", QUESTION) as con:
    anchor = paths_for(ui.db_mode()).root / "anchor.json"
    r = verify_chain(con, anchor if anchor.exists() else None)
    status = "OK" if r.ok else f"BROKEN at seq {r.first_broken_seq}"
    cls = "dsc-fresh" if r.ok else "dsc-stale"
    st.markdown(
        f'<div class="dsc-strip {cls}" style="font-size:14px"><b>Chain verify: {ui.esc(status)}</b> &nbsp; '
        f'{r.rows_checked} rows recomputed at {ui.esc(r.checked_at_utc.strftime("%Y-%m-%d %H:%M:%S"))} UTC'
        f'{" against the external anchor" if anchor.exists() else " (checkpoints; no external anchor file)"}'
        f'{" &nbsp; Reason: " + ui.esc(r.reason) if r.reason else ""}</div>', unsafe_allow_html=True)
    st.caption("row_hash = sha256(prev_hash + '|' + canonical_json(row)), genesis '0'*64. There is no update or "
               "delete path; editing row k makes verify report seq k, and deleting the newest row is caught by "
               "the checkpoint table.")

    all_rows = queries.audit_entries(con)
    c = st.columns(4)
    ui.kpi(c[0], "Rows", len(all_rows))
    ui.kpi(c[1], "Success", int((all_rows["outcome"] == "SUCCESS").sum()) if len(all_rows) else 0)
    ui.kpi(c[2], "Refused", int((all_rows["outcome"] == "REFUSED").sum()) if len(all_rows) else 0,
           "logged, nothing changed")
    ui.kpi(c[3], "Simulated rows", int(all_rows["reason"].fillna("").str.startswith("[SIMULATED]").sum())
           if len(all_rows) else 0, "SIMULATED demo usage, not real users")
    st.write("")

    f = st.columns([2, 1.3, 1.5, 1])
    obj_key = f[0].text_input("Object key (event, substitute or task id)", value=st.query_params.get("object", ""))
    actors = sorted(all_rows["actor_id"].dropna().unique().tolist()) if len(all_rows) else []
    actor = f[1].selectbox("Actor", ["(all)"] + actors)
    types = sorted(all_rows["action_type"].dropna().unique().tolist()) if len(all_rows) else []
    atype = f[2].selectbox("Action", ["(all)"] + types)
    outcome = f[3].selectbox("Outcome", ["(all)", "SUCCESS", "REFUSED"])
    rows = queries.audit_entries(con, object_key=obj_key.strip() or None,
                                 actor_id=None if actor == "(all)" else actor,
                                 action_type=None if atype == "(all)" else atype,
                                 outcome=None if outcome == "(all)" else outcome)
    if rows.empty:
        st.info("No audit rows match.")
    else:
        view = rows.assign(prev_hash=rows["prev_hash"].str[:10] + "...", row_hash=rows["row_hash"].str[:10] + "...")
        st.dataframe(
            view[["seq", "ts_utc", "outcome", "action_type", "object_key", "actor", "role", "failed_criterion",
                  "reason", "old_value", "new_value", "prev_hash", "row_hash"]]
            .style.map(lambda v: "background-color:#238551;color:#fff;font-weight:700" if v == "SUCCESS" else
                       ("background-color:#CD4246;color:#fff;font-weight:700" if v == "REFUSED" else ""),
                       subset=["outcome"]),
            hide_index=True, width="stretch", height=560)
