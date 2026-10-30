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

ASSETS = Path(__file__).resolve().parent / "assets"
CSS = "<style>\n" + (ASSETS / "formulary.css").read_text(encoding="utf-8") + "\n</style>"

# Light FORMULARY palette for places CSS cannot reach (pandas Styler cells in st.dataframe, Altair).
# Same hex values as app/assets/formulary.css; every text colour is >= 4.5:1 on its tint and on white.
INK, MUTED, ACCENT, OK, WARN, BAD = "#1B1B1B", "#5F6670", "#1D4ED8", "#14532D", "#A14A07", "#9A3412"
CELL = {
    "accent": f"color:{ACCENT};background-color:#EDF2FD;font-weight:700",
    "ok": f"color:{OK};background-color:#EAF3EE;font-weight:700",
    "warn": f"color:{WARN};background-color:#FCF1E6;font-weight:700",
    "bad": f"color:{BAD};background-color:#FBEDE7;font-weight:700",
    "muted": f"color:{MUTED}",
    "bad_text": f"color:{BAD};font-weight:700", "warn_text": f"color:{WARN};font-weight:700",
    "ok_text": f"color:{OK};font-weight:700", "ok_plain": f"color:{OK}",
}

# Icons: simple hand-drawn 24 px stroke geometry (no icon font, no emoji). The shape carries the status with the
# word, so nothing depends on colour alone.
_PATHS = {
    "plus": "M12 5v14M5 12h14",
    "down": "M3 7l6 6 4-4 8 8M21 11v6h-6",
    "stop": "M8 3h8l5 5v8l-5 5H8l-5-5V8zM9 9l6 6M15 9l-6 6",
    "warn": "M12 3l10 18H2zM12 10v5M12 18v.5",
    "check": "M5 12.5l4.5 4.5L19 7.5",
    "dash": "M6 12h12",
    "user": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c1.5-5 14.5-5 16 0",
    "lock": "M6 11h12v10H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
}
_ICON_FOR = {
    "NEW": "plus", "proposed": "plus", "open": "clock",
    "WORSENED": "down", "Limited": "warn", "SYNTHETIC": "warn",
    "Unavailable": "stop", "FAIL": "stop", "REFUSED": "stop", "rejected": "stop", "BROKEN": "stop",
    "RESOLVED": "check", "IMPROVED": "check", "Available": "check", "PASS": "check", "SUCCESS": "check",
    "OK": "check", "approved": "check",
    "role": "user",
}

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


def icon(name: str) -> str:
    d = _PATHS.get(name, _PATHS["dash"])
    return (f'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" '
            f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="{d}"/></svg>')


def badge(value, cls: str | None = None) -> str:
    """A status sticker: an icon band (the shape) plus the word. Colour is the third cue, never the only one."""
    v = "n/a" if value is None or value == "" else str(value)
    c = cls or v.replace(" ", "").replace("/", "")
    band = "" if c == "date" else f'<span class="ic">{icon(_ICON_FOR.get(c, "dash"))}</span>'
    return f'<span class="badge b-{esc(c)}">{band}<span class="tx">{esc(v)}</span></span>'


def aux_label(value, instruction: str) -> str:
    """The big pharmacy auxiliary label for the object view: a bold band with icon + word, the instruction below."""
    v = "n/a" if value is None or value == "" else str(value)
    c = v.replace(" ", "").replace("/", "")
    return (f'<span class="aux b-{esc(c)}"><span class="band">{icon(_ICON_FOR.get(c, "dash"))}{esc(v)}</span>'
            f'<span class="body">{esc(instruction)}</span></span>')


def kpi(col, label: str, value, sub: str = "", tone: str = "", lead: bool = False) -> None:
    """One figure. ``lead`` = the one big number per page; ``tone`` (bad/warn/ok) only when the number is a status."""
    cls = " ".join(x for x in ("kpi", "lead" if lead else "", tone) if x)
    col.markdown(f'<div class="{cls}"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>'
                 f'<div class="s">{esc(sub)}</div></div>', unsafe_allow_html=True)


def lock_html(msg: str) -> str:
    return f'<div class="lockmsg">{icon("lock")}<span>Locked: {esc(msg)}</span></div>'


LOCKUP = ('<span class="hl-lockup"><svg viewBox="0 0 64 64" aria-hidden="true" focusable="false">'
          '<path d="M4 12V4H12M52 4H60V12M60 52V60H52M12 60H4V52" fill="none" stroke="currentColor" stroke-width="2"/>'
          '<path d="M16 16V48M16 32H36M36 16V48H47" fill="none" stroke="currentColor" stroke-width="6.5" '
          'stroke-linecap="square"/><circle class="hl-dot" cx="47" cy="21" r="4.6"/></svg>'
          '<span>A <b>Hanson Labs</b> project</span></span>')


def footer() -> None:
    st.markdown(f'<div class="dsc-foot">{LOCKUP}<span>Shortage records: public openFDA data. Riverbend Health and '
                f'everything about it: SYNTHETIC. Not for medical-care decisions.</span></div>',
                unsafe_allow_html=True)


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
                       initial_sidebar_state="auto", page_icon=str(ASSETS / "favicon.svg"))
    st.markdown(CSS, unsafe_allow_html=True)
    st.logo(str(ASSETS / "wordmark-light.svg"), size="large", icon_image=str(ASSETS / "monogram-light.svg"))
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
        st.markdown(f'<div class="q"><b>Question this page answers:</b> {esc(question)}</div>',
                    unsafe_allow_html=True)
        show_flash()
        yield con
        footer()
    finally:
        con.close()


def _header(con, title: str, mode: str) -> None:
    info = queries.snapshot_info(con)
    today = local_today()
    stale = is_stale(info["latest"], today, int(load_settings().get("stale_days", 1)))
    meta = [("Snapshot", info["latest"]), ("vs previous", info["previous"]),
            ("openFDA meta.last_updated", info["meta_last_updated"]), ("DB", mode), ("Snapshots", info["snapshots"])]
    st.markdown(
        f'<div class="dsc-top"><div><div class="dsc-eyebrow">Drug Shortage Console &middot; Riverbend Health '
        f'(SYNTHETIC)</div><div class="dsc-title">{esc(title)}</div></div><div class="dsc-meta">'
        + "".join(f'<span>{esc(k)}<b>{esc(v)}</b></span>' for k, v in meta) + '</div></div>',
        unsafe_allow_html=True)
    synth = ("SYNTHETIC hospital data. Riverbend Health, its 3 sites, formulary, stock, users and tasks are "
             "fictional. Shortage records are public openFDA data and are not for medical-care decisions.")
    if info.get("synthetic_history"):
        synth += " Demo history days are SYNTHETIC fixture snapshots, not real FDA history."
    st.markdown(f'<div class="dsc-strip dsc-synth">{badge("SYNTHETIC")}<div>{esc(synth)}</div></div>',
                unsafe_allow_html=True)
    if stale:
        age = "no snapshot" if info["latest"] is None else f"{snapshot_age_days(info['latest'], today)} days old"
        st.markdown(f'<div class="dsc-strip dsc-stale">{badge("STALE", "FAIL")}<div>STALE DATA: the newest '
                    f'snapshot is {esc(age)} (today {today}). Showing the last cached snapshot; run the daily '
                    f'pull.</div></div>', unsafe_allow_html=True)


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
        st.query_params["as"] = choice  # a reload or a bookmarked tab keeps the same demo identity
        role = users.set_index("user_id").loc[choice, "role"]
        st.markdown(f"Role {badge(role, 'role')}", unsafe_allow_html=True)
        st.caption("Demo identity, not authentication. The role comes from seed.app_user inside the action "
                   "layer; the UI cannot pass a role in.")
        last = st.session_state.get("event_key")
        if last:  # "where was I": one click back to the last shortage opened, after any interruption
            st.divider()
            st.markdown("**Where you were**")
            if st.button(st.session_state.get("event_name") or last, key="where_was_i", width="stretch",
                         help="Reopen the last shortage you looked at"):
                open_object(last)
        st.divider()
        st.caption("All hospital data is SYNTHETIC.")
    return choice


def acting_user() -> str:
    return st.session_state.get("acting_user", "")


def open_object(event_key: str) -> None:
    st.session_state["event_key"] = event_key
    st.switch_page("pages/1_Drug.py", query_params={"event": event_key})
