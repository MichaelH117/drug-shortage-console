"""Shared page chrome for the Streamlit app (AE-03). Holds NO business logic (ADR 001).

Every page: ``with ui.page("Title") as con:``. That sets the page config and CSS, opens ONE DuckDB connection for
this rerun (closed at the end, never cached, so ``pipeline.run`` is not locked out between reruns), draws the
header (snapshot date, meta.last_updated, DB mode, stale banner, SYNTHETIC label) and the sidebar demo identity.
"""
from __future__ import annotations

import contextlib
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from console import queries  # noqa: E402
from console.freshness import is_stale, snapshot_age_days  # noqa: E402
from pipeline.config import DatabaseInUse, connect, load_settings, paths_for  # noqa: E402
from pipeline.snapshots import local_today  # noqa: E402

CSS = """
<style>
.block-container {padding-top: 3.4rem; padding-bottom: 2rem; max-width: 1500px;}
h1, h2, h3 {letter-spacing: .01em;}
.dsc-top {display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap;
  border-bottom:1px solid #383E47; padding-bottom:10px; margin-bottom:10px;}
.dsc-title {font-size:22px; font-weight:700; color:#F6F7F9;}
.dsc-title small {display:block; font-size:12px; font-weight:500; color:#ABB3BF; letter-spacing:.08em;
  text-transform:uppercase; margin-bottom:2px;}
.dsc-meta {display:flex; gap:18px; flex-wrap:wrap; font-size:12px; color:#ABB3BF;}
.dsc-meta b {color:#F6F7F9; font-family:ui-monospace,Consolas,monospace; font-weight:600;}
.dsc-strip {border-radius:3px; padding:7px 12px; font-size:13px; margin:6px 0;}
.dsc-synth {background:rgba(200,118,25,.15); border:1px solid #C87619; color:#FBB360;}
.dsc-stale {background:rgba(205,66,70,.18); border:1px solid #CD4246; color:#FA999C;}
.dsc-fresh {background:rgba(35,133,81,.14); border:1px solid #238551; color:#72CA9B;}
.badge {display:inline-block; padding:1px 8px; border-radius:3px; font-size:11px; font-weight:700;
  letter-spacing:.05em; text-transform:uppercase; line-height:18px; white-space:nowrap;}
.b-NEW {background:#2D72D2; color:#fff;} .b-WORSENED {background:#CD4246; color:#fff;}
.b-IMPROVED {background:#238551; color:#fff;} .b-RESOLVED {background:#238551; color:#fff;}
.b-DROPPED {background:#5F6B7C; color:#fff;} .b-UNCHANGED {background:#383E47; color:#ABB3BF;}
.b-Unavailable {background:rgba(205,66,70,.25); color:#FA999C; border:1px solid #CD4246;}
.b-Limited {background:rgba(200,118,25,.25); color:#FBB360; border:1px solid #C87619;}
.b-Available {background:rgba(35,133,81,.25); color:#72CA9B; border:1px solid #238551;}
.b-Unknown {background:#383E47; color:#ABB3BF; border:1px solid #5F6B7C;}
.b-PASS, .b-SUCCESS, .b-OK, .b-approved {background:#238551; color:#fff;}
.b-FAIL, .b-REFUSED, .b-rejected {background:#CD4246; color:#fff;}
.b-NA, .b-candidate, .b-closed {background:#383E47; color:#ABB3BF;}
.b-proposed, .b-open {background:#2D72D2; color:#fff;}
.b-role {background:#383E47; color:#F6F7F9; border:1px solid #5F6B7C;}
.kpi {background:#252A31; border:1px solid #383E47; border-radius:4px; padding:10px 14px; height:100%;}
.kpi .k {font-size:11px; color:#ABB3BF; text-transform:uppercase; letter-spacing:.08em;}
.kpi .v {font-size:26px; font-weight:700; color:#F6F7F9; font-variant-numeric:tabular-nums; line-height:1.25;}
.kpi .s {font-size:12px; color:#ABB3BF;}
.objcard {background:#252A31; border:1px solid #383E47; border-left:3px solid #4C90F0; border-radius:4px;
  padding:12px 16px; margin-bottom:8px;}
.objcard .type {font-size:11px; color:#8ABBFF; letter-spacing:.1em; text-transform:uppercase;}
.objcard .name {font-size:21px; font-weight:700; color:#F6F7F9; margin:2px 0 6px 0;}
.props {display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:6px 18px; font-size:12px;}
.props div span {display:block; color:#ABB3BF; font-size:11px; text-transform:uppercase; letter-spacing:.06em;}
.props div b {color:#F6F7F9; font-weight:600; font-family:ui-monospace,Consolas,monospace; font-size:12.5px;}
.links {font-size:12px; color:#ABB3BF; margin-top:8px;}
.links b {color:#8ABBFF;}
.lockmsg {font-size:12px; color:#FBB360; margin:-4px 0 8px 0;}
.feed {font-size:12.5px; border-left:2px solid #383E47; padding:2px 0 6px 10px; margin-bottom:4px;}
.feed .t {color:#ABB3BF; font-family:ui-monospace,Consolas,monospace; font-size:11px;}
.q {font-size:13px; color:#ABB3BF; margin:-6px 0 10px 0;}
</style>
"""

CRITERION_TEXT = {
    "role": "your role is not allowed to run this action",
    "event_exists": "the shortage event does not exist",
    "alt_drug_exists": "the alternative drug is not in the drug catalog",
    "alt_not_same_ndc": "the alternative is the same NDC as the short drug",
    "reason_given": "a reason is required",
    "substitute_exists": "the substitute does not exist",
    "state_is_proposed": "the substitute is not in the 'proposed' state",
    "alt_not_short": "the alternative is itself in a current, limited or unavailable shortage",
    "facility_valid": "the facility is not valid",
    "assignee_active_staff": "the assignee must be an active pharmacist or buyer",
    "due_not_past": "the due date is in the past",
    "task_exists": "the task does not exist",
    "actor_is_assignee_or_pharmacist": "only the assignee or a pharmacist can close this task",
    "task_is_open": "the task is not open",
    "note_min_10_chars": "the resolution note must be at least 10 characters",
    "assignee_differs": "the new assignee is the current assignee",
}
TIER_TEXT = {1: "T1 exact NDC", 2: "T2 same product", 3: "T3 ingredient+form+route"}


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def badge(value, cls: str | None = None) -> str:
    v = "n/a" if value is None or value == "" else str(value)
    c = cls or v.replace(" ", "").replace("/", "")
    return f'<span class="badge b-{esc(c)}">{esc(v)}</span>'


def kpi(col, label: str, value, sub: str = "") -> None:
    col.markdown(f'<div class="kpi"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>'
                 f'<div class="s">{esc(sub)}</div></div>', unsafe_allow_html=True)


def d10(v) -> str:
    """Date-only text for DATE/TIMESTAMP values that are dates."""
    if v is None or (isinstance(v, float) and v != v) or str(v) in ("NaT", "None"):
        return "n/a"
    return str(v)[:10]


def fmt_ndc(ndc11: str | None) -> str:
    if not ndc11 or len(str(ndc11)) != 11:
        return ndc11 or "n/a"
    s = str(ndc11)
    return f"{s[:5]}-{s[5:9]}-{s[9:]}"


def db_mode() -> str:
    mode = os.environ.get("DSC_DB", "demo").strip().lower()
    return mode if mode in ("demo", "live") else "demo"


def flash(kind: str, msg: str) -> None:
    """Queue a message for the next rerun (after an action we rerun so every table shows the new state)."""
    st.session_state["_flash"] = (kind, msg)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


@contextlib.contextmanager
def page(title: str, question: str):
    """Page chrome + one connection for this rerun. ``question`` is the business question the page answers."""
    st.set_page_config(page_title=f"{title} | Drug Shortage Console", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    mode = db_mode()
    path = Path(os.environ["DSC_DB_PATH"]) if os.environ.get("DSC_DB_PATH") else paths_for(mode).db  # test hook
    if not path.exists():
        st.error(f"No database at {path}. Build it first: python -m pipeline.run --fixtures")
        st.stop()
    try:
        con = connect(path)
    except DatabaseInUse as exc:
        st.error(f"{exc}. A pipeline run is probably writing it; reload in a few seconds.")
        st.stop()
    try:
        _header(con, title, mode)
        sidebar_identity(con)
        st.markdown(f'<div class="q">Business question: {esc(question)}</div>', unsafe_allow_html=True)
        show_flash()
        yield con
    finally:
        con.close()


def _header(con, title: str, mode: str) -> None:
    info = queries.snapshot_info(con)
    today = local_today()
    stale = is_stale(info["latest"], today, int(load_settings().get("stale_days", 1)))
    st.markdown(
        f'<div class="dsc-top"><div class="dsc-title"><small>Drug Shortage Console / Riverbend Health '
        f'(SYNTHETIC)</small>{esc(title)}</div><div class="dsc-meta">'
        f'<span>Snapshot <b>{esc(info["latest"])}</b></span>'
        f'<span>vs previous <b>{esc(info["previous"])}</b></span>'
        f'<span>openFDA meta.last_updated <b>{esc(info["meta_last_updated"])}</b></span>'
        f'<span>DB <b>{esc(mode)}</b></span>'
        f'<span>Snapshots <b>{esc(info["snapshots"])}</b></span></div></div>', unsafe_allow_html=True)
    synth = ("SYNTHETIC hospital data. Riverbend Health, its 3 sites, formulary, stock, users and tasks are "
             "fictional. Shortage records are public openFDA data and are not for medical-care decisions.")
    if info.get("synthetic_history"):
        synth += " Demo history days are SYNTHETIC fixture snapshots, not real FDA history."
    st.markdown(f'<div class="dsc-strip dsc-synth">{esc(synth)}</div>', unsafe_allow_html=True)
    if stale:
        age = "no snapshot" if info["latest"] is None else f"{snapshot_age_days(info['latest'], today)} days old"
        st.markdown(f'<div class="dsc-strip dsc-stale">STALE DATA: the newest snapshot is {esc(age)} '
                    f'(today {today}). Showing the last cached snapshot; run the daily pull.</div>',
                    unsafe_allow_html=True)


def sidebar_identity(con) -> str:
    """Demo identity picker. Only the user_id is kept; the role is always looked up server-side."""
    users = queries.users(con)
    ids = users["user_id"].tolist()
    labels = {r.user_id: f"{r.display_name} ({r.role}{'' if r.active else ', inactive'})"
              for r in users.itertuples()}
    if "acting_user" not in st.session_state:
        qp = st.query_params.get("as")
        st.session_state["acting_user"] = qp if qp in ids else ids[0]
    with st.sidebar:
        st.markdown("**Acting as**")
        choice = st.selectbox("Demo identity, not authentication", ids,
                              index=ids.index(st.session_state["acting_user"]),
                              format_func=lambda u: labels.get(u, u), key="_user_picker")
        st.session_state["acting_user"] = choice
        role = users.set_index("user_id").loc[choice, "role"]
        st.markdown(f"Role {badge(role, 'role')}", unsafe_allow_html=True)
        st.caption("Demo identity, not authentication. The role comes from seed.app_user inside the action "
                   "layer; the UI cannot pass a role in.")
        st.divider()
        st.caption("All hospital data is SYNTHETIC.")
    return choice


def acting_user() -> str:
    return st.session_state.get("acting_user", "")


def open_object(event_key: str) -> None:
    st.session_state["event_key"] = event_key
    st.switch_page("pages/1_Drug.py", query_params={"event": event_key})
