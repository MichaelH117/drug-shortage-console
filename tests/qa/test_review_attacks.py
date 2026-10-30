"""QA-05 (part): attack tests for REVIEW.md findings 1, 3 and 7 (S1/S2 action-layer holes).

Owner: qa-engineer. Each test tries the attack the Tech Lead reproduced and asserts it is now blocked.
Tests that still fail against the current code carry an xfail mark naming the ML task that fixes them.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime

import pytest

from console import actions as A
from ontokit.actions import ActionRefused
from pipeline.config import connect, paths_for
from pipeline.run import build

NOW = datetime(2026, 10, 30, 14, 0, 0)
DUE = date(2026, 11, 6)


@pytest.fixture(scope="module")
def base_build(tmp_path_factory):
    paths = paths_for("demo", tmp_path_factory.mktemp("qa_attacks"))
    build(paths, client=None, refresh=False, seed=42)
    return paths


@pytest.fixture
def con(base_build, tmp_path):
    dst = tmp_path / "console.duckdb"
    shutil.copy(base_build.db, dst)
    c = connect(dst)
    yield c
    c.close()


def _users(con):
    out = {"pharmacist": [], "buyer": [], "viewer": [], "inactive": []}
    for uid, role, active in con.execute("SELECT user_id, role, active FROM seed.app_user ORDER BY user_id").fetchall():
        out[role if active else "inactive"].append(uid)
    return out


def _candidate(con):
    return con.execute("SELECT substitute_id, event_key, alt_ndc11 FROM obj.substitute WHERE state = 'candidate' "
                       "ORDER BY substitute_id LIMIT 1").fetchone()


def _ev_fac(con):
    ek = con.execute("SELECT event_key FROM obj.shortage_event ORDER BY event_key LIMIT 1").fetchone()[0]
    fids = [r[0] for r in con.execute("SELECT facility_id FROM seed.facility ORDER BY facility_id").fetchall()]
    return ek, fids


def _last(con):
    return con.execute("SELECT outcome, failed_criterion, object_type, object_key FROM audit.action_log "
                       "ORDER BY seq DESC LIMIT 1").fetchone()


def _state(con, sid):
    return con.execute("SELECT state FROM obj.substitute WHERE substitute_id = ?", [sid]).fetchone()[0]


# --------------------------------------------------------------------------- REVIEW 1 (S1): undo by re-propose
@pytest.mark.parametrize("decision", ["approve_substitute", "reject_substitute"])
def test_buyer_cannot_undo_decision_by_reproposing(con, decision):
    u = _users(con)
    sid, ek, alt = _candidate(con)
    A.propose_substitute(con, u["pharmacist"][0], ek, alt, "QA propose", now=NOW)
    getattr(A, decision)(con, u["pharmacist"][0], sid, "QA decide", now=NOW)
    decided = _state(con, sid)
    assert decided in ("approved", "rejected")
    with pytest.raises(ActionRefused):
        A.propose_substitute(con, u["buyer"][0], ek, alt, "QA buyer re-proposes", now=NOW)
    assert _last(con)[:2] == ("REFUSED", "not_already_decided")
    assert _state(con, sid) == decided, "a re-propose must not move a decided substitute back to proposed"


def test_pharmacist_reproposing_approved_is_also_refused(con):
    """The control is on the object state, not on who asks."""
    u = _users(con)
    sid, ek, alt = _candidate(con)
    A.propose_substitute(con, u["pharmacist"][0], ek, alt, "QA propose", now=NOW)
    A.approve_substitute(con, u["pharmacist"][0], sid, "QA approve", now=NOW)
    with pytest.raises(ActionRefused):
        A.propose_substitute(con, u["pharmacist"][-1], ek, alt, "QA again", now=NOW)
    assert _state(con, sid) == "approved"


# ------------------------------------------------------------- REVIEW 3 (S2): refusal filed under a future task id
def test_refused_open_is_not_logged_under_next_task_id(con):
    u = _users(con)
    ek, fids = _ev_fac(con)
    with pytest.raises(ActionRefused):
        A.open_mitigation_task(con, u["pharmacist"][0], ek, "F-NOPE", u["buyer"][0], DUE, "QA bad facility", now=NOW)
    outcome, crit, otype, okey = _last(con)
    assert (outcome, crit) == ("REFUSED", "facility_valid")
    assert (otype, okey) == ("ShortageEvent", ek), "a refused open must be filed on the event, not a task id"

    tid = A.open_mitigation_task(con, u["pharmacist"][0], ek, fids[0], u["buyer"][0], DUE, "QA real open",
                                 now=NOW).object_key
    rows = con.execute("SELECT outcome FROM audit.action_log WHERE object_type = 'MitigationTask' "
                       "AND object_key = ?", [tid]).fetchall()
    assert rows == [("SUCCESS",)], f"task {tid} history must start with its own open, got {rows}"


# ----------------------------------------------------------------- REVIEW 7 (S2): double submit opens two tasks
def test_double_open_same_event_facility_refused(con):
    u = _users(con)
    ek, fids = _ev_fac(con)
    args = (u["pharmacist"][0], ek, fids[0], u["buyer"][0], DUE, "QA open")
    A.open_mitigation_task(con, *args, now=NOW)
    with pytest.raises(ActionRefused):
        A.open_mitigation_task(con, *args, now=NOW)
    assert _last(con)[:2] == ("REFUSED", "no_open_task_for_event_facility")
    n = con.execute("SELECT count(*) FROM obj.mitigation_task WHERE event_key = ? AND facility_id = ? "
                    "AND state = 'open'", [ek, fids[0]]).fetchone()[0]
    assert n == 1


def test_open_allowed_for_other_facility_and_after_close(con):
    """The duplicate guard must not over-block: another facility, or a fresh task after closing, is fine."""
    u = _users(con)
    ek, fids = _ev_fac(con)
    assert len(fids) >= 2
    t1 = A.open_mitigation_task(con, u["pharmacist"][0], ek, fids[0], u["buyer"][0], DUE, "QA a", now=NOW).object_key
    assert A.open_mitigation_task(con, u["pharmacist"][0], ek, fids[1], u["buyer"][0], DUE, "QA b",
                                  now=NOW).outcome == "SUCCESS"
    A.close_mitigation_task(con, u["pharmacist"][0], t1, "QA: stock arrived", now=NOW)
    assert A.open_mitigation_task(con, u["pharmacist"][0], ek, fids[0], u["buyer"][0], DUE, "QA c",
                                  now=NOW).outcome == "SUCCESS"
