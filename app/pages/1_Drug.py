"""Drug object view with linked ShortageEvents, Facilities, Substitutes, MitigationTasks and audit rows, plus the
action forms (AE-03, AC-5.3, 6.5). Every write goes through console.actions; role checks shown here come from the
same ontology the action layer enforces."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import ui  # noqa: E402

from console import actions, queries  # noqa: E402
from ontokit.actions import ActionRefused  # noqa: E402
from pipeline.snapshots import local_today  # noqa: E402

QUESTION = ("For this shortage: which sites and items does it hit, is there a safe substitute, and who owns "
            "the follow-up?")


def _run(label: str, fn, *args) -> None:
    """Call one console.actions function as the acting user; flash the outcome and rerun so all views refresh."""
    try:
        res = fn(*args)
    except ActionRefused as exc:
        why = ui.CRITERION_TEXT.get(exc.criterion, exc.criterion)
        ui.flash("error", f"REFUSED by the action layer: {label}. Criterion `{exc.criterion}`: {why}. "
                          "A REFUSED row was written to the audit log.")
    else:
        ui.flash("success", f"{label}: SUCCESS (audit seq {res.seq}, object {res.object_key}).")
    st.rerun()


def _lock(msg: str) -> None:
    st.markdown(ui.lock_html(msg), unsafe_allow_html=True)


AVAIL_NOTE = {"Unavailable": "FDA lists it as unavailable. Substitutes are below; a pharmacist decides.",
              "Limited": "FDA lists limited supply. Check stock and the substitutes below.",
              "Available": "FDA lists it as available.", "Unknown": "FDA gives no availability."}
CHANGE_NOTE = {"NEW": "New on the FDA list since the previous snapshot.",
               "WORSENED": "Availability got worse since the previous snapshot."}


with ui.page("Drug object view", QUESTION) as con:
    user = ui.acting_user()
    choices = queries.event_choices(con)
    keys = choices["event_key"].tolist()
    qp = st.query_params.get("event")
    queue = queries.triage_queue(con)
    if not queue.empty:
        default = queue["event_key"].iloc[0]
    else:  # baseline day: open the top of the one-time review list
        wl = queries.baseline_worklist(con)
        default = wl["worst_event_key"].iloc[0] if not wl.empty else (keys[0] if keys else None)
    current = qp if qp in keys else st.session_state.get("event_key", default)
    if current not in keys:
        current = keys[0] if keys else None
    if current is None:
        st.info("No shortage event touches the SYNTHETIC formulary.")
        st.stop()
    labels = {r.event_key: f"{'[' + r.change_type + '] ' if r.change_type else ''}{r.generic_name}  ({r.event_key})"
              for r in choices.itertuples()}
    picked = st.selectbox("ShortageEvent", keys, index=keys.index(current), format_func=labels.get,
                          key="event_picker")
    if picked != current:
        st.session_state["event_key"] = picked
        st.query_params["event"] = picked
        st.rerun()
    st.session_state["event_key"] = current
    st.session_state["event_name"] = labels.get(current, current)  # for the sidebar's "Where you were" link

    obj = queries.drug_object(con, current)
    ev, drug = obj["event"], obj["drug"] or {}
    change = obj["changes"][-1]["change_type"] if obj["changes"] else None
    latest = queries.snapshot_info(con)["latest"]
    change_now = change if obj["changes"] and ui.d10(obj["changes"][-1]["snapshot_date"]) == ui.d10(latest) else None

    # ---------------------------------------------------------------- object card
    props = [("NDC-11 (package)", ui.fmt_ndc(ev["ndc11"])), ("Ingredient", drug.get("ingredient_norm")),
             ("Dosage form", drug.get("dosage_form_norm")), ("Route", drug.get("route_norm")),
             ("Strength", drug.get("strength_norm")), ("Manufacturer", drug.get("manufacturer") or ev["company_name"]),
             ("RxCUI", drug.get("rxcui")), ("FDA status", ev["status"]),
             ("Initial posting", ui.d10(ev["initial_posting_date"])), ("FDA update", ui.d10(ev["update_date"])),
             ("First seen", ui.d10(ev["first_seen"])), ("Last seen", ui.d10(ev["last_seen"]))]
    n_open = sum(1 for t in obj["tasks"] if t["state"] == "open")
    st.markdown(
        f'<div class="objcard"><div class="type">Object: Drug &nbsp;/&nbsp; ShortageEvent {ui.esc(current)}</div>'
        f'<div class="name">{ui.esc(ev["generic_name"])}</div>'
        f'<div class="pres">{ui.esc(ev["presentation"])}</div>'
        f'<div>{ui.aux_label(ev["availability_normalized"], AVAIL_NOTE.get(ev["availability_normalized"], ""))}'
        f'{ui.aux_label(change_now, CHANGE_NOTE[change_now]) if change_now in CHANGE_NOTE else ""}</div>'
        f'<div class="props">' + "".join(f'<div><span>{ui.esc(k)}</span><b>{ui.esc(v if v is not None else "n/a")}'
                                         f'</b></div>' for k, v in props) + '</div>'
        f'<div class="links">Links: <b>{1 + len(obj["sibling_events"])}</b> ShortageEvent &middot; '
        f'<b>{len({f["facility_id"] for f in obj["facilities"]})}</b> Facilities &middot; '
        f'<b>{len(obj["substitutes"])}</b> Substitutes &middot; <b>{len(obj["tasks"])}</b> MitigationTasks '
        f'({n_open} open) &middot; <b>{len(obj["audit"])}</b> audit rows</div></div>', unsafe_allow_html=True)
    if ev.get("shortage_reason"):
        st.caption(f"FDA shortage reason: {ev['shortage_reason']}. FDA availability text: "
                   f"{ev.get('availability_raw') or '(none)'}")

    c1, c2 = st.columns([1.25, 1])
    with c1:
        st.markdown("##### Affected sites (formulary matches)")
        fac = pd.DataFrame(obj["facilities"])
        if fac.empty:
            st.caption("No SYNTHETIC formulary item matches this event.")
        else:
            st.dataframe(pd.DataFrame({
                "Site": fac["facility"], "Formulary item": fac["formulary_name"], "Item NDC as entered": fac["ndc_raw"],
                "Match": fac["match_tier"].map(ui.TIER_TEXT), "Confidence": fac["match_confidence"],
                "Days on hand": fac["days_on_hand"]}), hide_index=True, width="stretch",
                column_config={"Confidence": st.column_config.NumberColumn(format="%.2f"),
                               "Days on hand": st.column_config.NumberColumn(format="%d d")})
    with c2:
        st.markdown("##### Status history (SCD2) and change feed")
        hist = pd.DataFrame(obj["history"])
        if not hist.empty:
            hist = hist.assign(valid_from=hist["valid_from"].map(ui.d10),
                               valid_to=hist["valid_to"].map(lambda v: "current" if ui.d10(v) == "n/a" else ui.d10(v)))
            st.dataframe(hist[["valid_from", "valid_to", "status", "availability_normalized", "present"]]
                         .rename(columns={"availability_normalized": "availability"}),
                         hide_index=True, width="stretch")
        ch = pd.DataFrame(obj["changes"])
        if not ch.empty:
            st.markdown(" ".join(f'{ui.esc(ui.d10(r.snapshot_date))} {ui.badge(r.change_type)}'
                                 for r in ch.itertuples()),
                        unsafe_allow_html=True)
        if obj["sibling_events"]:
            st.caption("Other ShortageEvents linked to this Drug: "
                       + ", ".join(s["event_key"] for s in obj["sibling_events"]))

    # ---------------------------------------------------------------- substitutes
    st.markdown("---")
    st.markdown("#### Substitutes")
    st.caption("Candidates are mechanical matches (same ingredient and route, different package, strength or "
               "manufacturer). They are not clinical advice; a pharmacist decides.")
    subs = pd.DataFrame(obj["substitutes"])
    if subs.empty:
        st.caption("No substitute candidates for this event.")
    else:
        st.dataframe(pd.DataFrame({
            "State": subs["state"], "Alternative": subs["alt_name"], "Alt NDC": subs["alt_ndc11"].map(ui.fmt_ndc),
            "Strength": subs["alt_strength"], "Basis": subs["basis"],
            "Alt itself short": subs["alt_is_short"].map({True: "YES (approve blocked)", False: "no"}),
            "Last reason": subs["reason"], "By": subs["actor_id"], "Substitute id": subs["substitute_id"]}),
            hide_index=True, width="stretch")

        s1, s2 = st.columns(2)
        with s1:
            ok, why = queries.action_permission(con, user, "propose_substitute")
            proposable = subs[subs["state"].isin(["candidate", "rejected"])]
            with st.form("propose_form", clear_on_submit=True):
                st.markdown("**Propose a substitute**")
                by_id = subs.set_index("substitute_id")
                sid = st.selectbox("Candidate", proposable["substitute_id"].tolist(),
                                   format_func=lambda s: f"{by_id.loc[s, 'alt_name']} "
                                                         f"({ui.fmt_ndc(by_id.loc[s, 'alt_ndc11'])})")
                reason = st.text_input("Reason", key="propose_reason")
                go = st.form_submit_button("Propose", disabled=not ok or proposable.empty, help=why)
            if not ok:
                _lock(why)
            if go and sid:
                alt = subs.set_index("substitute_id").loc[sid, "alt_ndc11"]
                _run("propose_substitute", actions.propose_substitute, con, user, current, alt, reason)
        with s2:
            ok_a, why_a = queries.action_permission(con, user, "approve_substitute")
            ok_r, why_r = queries.action_permission(con, user, "reject_substitute")
            proposed = subs[subs["state"] == "proposed"]
            with st.form("decide_form", clear_on_submit=True):
                st.markdown("**Decide on a proposed substitute** (pharmacist only)")
                sid = st.selectbox("Proposed", proposed["substitute_id"].tolist(),
                                   format_func=lambda s: f"{subs.set_index('substitute_id').loc[s, 'alt_name']} "
                                                         f"({s})")
                reason = st.text_input("Reason", key="decide_reason")
                b1, b2 = st.columns(2)
                approve = b1.form_submit_button("Approve substitute", type="primary",
                                                disabled=not ok_a or proposed.empty, help=why_a)
                reject = b2.form_submit_button("Reject", disabled=not ok_r or proposed.empty, help=why_r)
            if not ok_a:
                _lock(why_a)
            elif proposed.empty:
                st.caption("Nothing is in the 'proposed' state.")
            if approve and sid:
                _run("approve_substitute", actions.approve_substitute, con, user, sid, reason)
            if reject and sid:
                _run("reject_substitute", actions.reject_substitute, con, user, sid, reason)
            if not ok_a and not proposed.empty:
                with st.expander("Prove the rule is server-side: send the approval anyway"):
                    st.caption("A disabled button is not a control (SPEC R9). This calls "
                               "console.actions.approve_substitute directly as the current demo user. The action "
                               "layer refuses it and writes a REFUSED audit row.")
                    if st.button("Call approve_substitute as me", key="force_approve"):
                        _run("approve_substitute", actions.approve_substitute, con, user,
                             proposed["substitute_id"].iloc[0], "Attempt from the object view")

    # ---------------------------------------------------------------- tasks
    st.markdown("---")
    st.markdown("#### Mitigation tasks")
    tasks = pd.DataFrame(obj["tasks"])
    if tasks.empty:
        st.caption("No mitigation tasks yet.")
    else:
        st.dataframe(pd.DataFrame({
            "Task": tasks["task_id"], "State": tasks["state"], "Site": tasks["facility"],
            "Assignee": tasks["assignee"], "Due": tasks["due_date"], "Last note": tasks["note"],
            "Updated (UTC)": tasks["edited_at_utc"]}), hide_index=True, width="stretch")

    staff = queries.users(con)
    staff = staff[staff["active"] & staff["role"].isin(["pharmacist", "buyer"])]
    staff_label = dict(zip(staff["user_id"], staff["display_name"] + " (" + staff["role"] + ")", strict=True))
    site_ids = sorted({f["facility_id"] for f in obj["facilities"]}) or queries.facilities(con)["facility_id"].tolist()
    open_tasks = tasks[tasks["state"] == "open"]["task_id"].tolist() if not tasks.empty else []

    t1, t2, t3 = st.columns(3)
    with t1:
        ok, why = queries.action_permission(con, user, "open_mitigation_task")
        with st.form("open_task_form", clear_on_submit=True):
            st.markdown("**Open a task**")
            fac_id = st.selectbox("Site", site_ids)
            assignee = st.selectbox("Assignee", list(staff_label), format_func=staff_label.get)
            due = st.date_input("Due", value=local_today() + dt.timedelta(days=2))
            reason = st.text_input("What needs doing", key="task_reason")
            go = st.form_submit_button("Open task", disabled=not ok, help=why)
        if not ok:
            _lock(why)
        if go:
            _run("open_mitigation_task", actions.open_mitigation_task, con, user, current, fac_id, assignee,
                 due.isoformat(), reason)
    with t2:
        ok, why = queries.action_permission(con, user, "close_mitigation_task")
        with st.form("close_task_form", clear_on_submit=True):
            st.markdown("**Close a task**")
            tid = st.selectbox("Open task", open_tasks)
            note = st.text_input("Resolution note (10+ characters)", key="close_note")
            go = st.form_submit_button("Close task", disabled=not ok or not open_tasks, help=why)
        if ok:
            st.caption("Only the assignee or a pharmacist can close.")
        else:
            _lock(why)
        if go and tid:
            _run("close_mitigation_task", actions.close_mitigation_task, con, user, tid, note)
    with t3:
        ok, why = queries.action_permission(con, user, "reassign_task")
        with st.form("reassign_form", clear_on_submit=True):
            st.markdown("**Reassign a task**")
            tid = st.selectbox("Open task", open_tasks, key="reassign_tid")
            new_a = st.selectbox("New assignee", list(staff_label), format_func=staff_label.get)
            reason = st.text_input("Reason", key="reassign_reason")
            go = st.form_submit_button("Reassign", disabled=not ok or not open_tasks, help=why)
        if not ok:
            _lock(why)
        if go and tid:
            _run("reassign_task", actions.reassign_task, con, user, tid, new_a, reason)

    # ---------------------------------------------------------------- audit for this object
    st.markdown("---")
    st.markdown("#### Audit entries for this object")
    aud = obj["audit"]
    if aud.empty:
        st.caption("No actions on this event, its substitutes or its tasks yet.")
    else:
        st.dataframe(aud[["seq", "ts_utc", "action_type", "object_key", "actor", "role", "outcome",
                          "failed_criterion", "reason"]], hide_index=True, width="stretch")
