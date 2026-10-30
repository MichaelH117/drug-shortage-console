# ADR 001: Streamlit app over a separate action layer

Status: Accepted 2026-10-30 (Tech Lead; Michael may override). Covers US-5, US-6 (AC-6.5), SPEC section 5.

## Context
Dana needs one screen: the queue, then the object view, then an action, then the audit page. The build budget is about 2 days, with roughly 1 day of UI. Michael has shipped Streamlit before (QuantLab) and must be able to explain every line. The PC has about 2 GB of free RAM. The rules (roles, submission criteria) must hold even if the UI is bypassed (SPEC R9).

## Options
1. **Streamlit.** Python only, with built-in forms, tables, a sidebar and multipage support. Every click reruns the script. It has no real auth and no multi-user concurrency story. Uses about 150 MB of RAM.
2. **FastAPI + React.** A real API boundary and a closer match to production. But it means two languages, a Node build toolchain, more RAM, and it does not fit in 2 days.
3. **Dash.** Similar to Streamlit, but nobody on the team has used it.
4. **Notebooks.** No operational write-back flow, so not an app.

## Choice
Option 1, with one hard rule: **Streamlit holds no business logic.** Pages call `console.queries` (read) and `console.actions` (write) only. All rules live in `ontokit.actions` and `console.actions`, and they are tested without the UI.

Concretely:
- There is no `st.session_state` for domain data. Every rerun reads the `obj.*` views, which are live, so AC-6.5 holds.
- Each rerun opens one DuckDB connection and closes it. There is no `st.cache_resource` connection, because that would lock the file against `pipeline.run`.
- The user picker passes only `user_id`. The role is looked up in `seed.app_user` inside the action. The sidebar says "demo identity, not authentication."
- Action forms use `st.form`. A double submit is refused by the "state is proposed / task is open" criteria and logged as REFUSED, so it can never apply twice.

## Consequences
- There is no authentication. KNOWN_ISSUES and the decomposition doc say what replaces it (SSO plus server-side roles).
- Only one writer works at a time. That is fine for a demo, not for a hospital. Swapping the UI (for example to FastAPI) changes only `app/`.
- UI tests are thin. ACs are tested on the functions (`is_stale`, `triage_queue`, the actions), not on pixels (AC-5.4). The click-through is shown with screenshots (AC-5.6).
