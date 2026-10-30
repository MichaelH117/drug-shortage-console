"""QA-03 (part): action-rule edge tests against the DESIGN section 4 interfaces.

Owner: qa-engineer. Written before console/actions.py existed; the xfail guard was removed on 2026-10-30 once
ML-05 and DE-07 landed and all 34 tests passed.

Every test acts on its own copy of a fixtures-mode build, so tests never share state.
Criterion ids come from ontology/ontology.yaml (ML-02); "role" comes from DESIGN section 4 step 2.
SPEC refs: AC-6.1, 6.2, 6.3, 6.4, 7.1, 7.3; DESIGN T9 (double submit), section 8 (concurrency).
"""

from __future__ import annotations

import inspect
import shutil
import threading
from datetime import date, datetime

import duckdb
import pytest

from console import actions as A
from console.identity import get_actor  # noqa: F401  (import proves the module exists)
from ontokit import audit
from ontokit.actions import ActionPermissionError, ActionRefused
from pipeline.config import connect, paths_for
from pipeline.run import build

NOW = datetime(2026, 10, 30, 14, 0, 0)          # naive UTC = 10:00 New York
LATE_NIGHT = datetime(2026, 10, 30, 3, 0, 0)    # naive UTC = 2026-10-29 23:00 New York
FUTURE = date(2026, 11, 6)


# ----------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def base_build(tmp_path_factory):
    base = tmp_path_factory.mktemp("qa_actions")
    paths = paths_for("demo", base)
    build(paths, client=None, refresh=False, seed=42)
    return paths


@pytest.fixture
def con(base_build, tmp_path):
    """A private, writable copy of the built DB."""
    dst = tmp_path / "console.duckdb"
    shutil.copy(base_build.db, dst)
    wal = base_build.db.with_name(base_build.db.name + ".wal")
    if wal.exists():
        shutil.copy(wal, dst.with_name(dst.name + ".wal"))
    c = connect(dst)
    yield c
    c.close()


def _one(con, sql, *params):
    row = con.execute(sql, list(params)).fetchone()
    return None if row is None else (row[0] if len(row) == 1 else row)


def _users(con):
    rows = con.execute("SELECT user_id, role, active FROM seed.app_user ORDER BY user_id").fetchall()
    out = {"pharmacist": [], "buyer": [], "viewer": [], "inactive": []}
    for uid, role, active in rows:
        out[role if active else "inactive"].append(uid)
    assert out["pharmacist"] and len(out["buyer"]) >= 2 and out["viewer"] and out["inactive"], out
    return out


def _counts(con):
    return {
        "audit": _one(con, "SELECT count(*) FROM audit.action_log"),
        "sub_edit": _one(con, "SELECT count(*) FROM ops.substitute_edit"),
        "task_edit": _one(con, "SELECT count(*) FROM ops.mitigation_task_edit"),
    }


def _assert_refused(con, fn, criterion, exc=ActionRefused):
    """AC-6.2: refused -> raises, no edit row, exactly one REFUSED audit row with the criterion."""
    before = _counts(con)
    with pytest.raises(exc) as info:
        fn()
    after = _counts(con)
    assert after["sub_edit"] == before["sub_edit"] and after["task_edit"] == before["task_edit"]
    assert after["audit"] == before["audit"] + 1
    outcome, crit = con.execute(
        "SELECT outcome, failed_criterion FROM audit.action_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert outcome == "REFUSED"
    assert crit == criterion
    assert getattr(info.value, "criterion", criterion) == criterion
    return info.value


def _candidate(con):
    row = con.execute("SELECT substitute_id, event_key, alt_ndc11 FROM obj.substitute "
                      "WHERE state = 'candidate' ORDER BY substitute_id LIMIT 1").fetchone()
    assert row, "fixture build produced no substitute candidates"
    return row


def _proposed(con, users):
    sid, ek, alt = _candidate(con)
    res = A.propose_substitute(con, users["pharmacist"][0], ek, alt, "QA: candidate looks fine", now=NOW)
    assert res.outcome == "SUCCESS"
    assert res.object_key == sid, "substitute_id must be deterministic (DESIGN 3.2)"
    return sid


def _event_and_facility(con):
    ek = _one(con, "SELECT event_key FROM obj.shortage_event ORDER BY event_key LIMIT 1")
    fid = _one(con, "SELECT facility_id FROM seed.facility ORDER BY facility_id LIMIT 1")
    return ek, fid


def _open_task(con, users, assignee=None, now=NOW):
    ek, fid = _event_and_facility(con)
    res = A.open_mitigation_task(con, users["pharmacist"][0], ek, fid, assignee or users["buyer"][0], FUTURE,
                                 "QA: call wholesaler", now=now)
    assert res.outcome == "SUCCESS"
    return res.object_key


def _task_state(con, task_id):
    return _one(con, "SELECT state FROM obj.mitigation_task WHERE task_id = ?", task_id)


# ------------------------------------------------------------------------------------------------ roles
def test_buyer_cannot_approve(con):
    """AC-6.1: a buyer calling approve_substitute raises a permission error and is logged."""
    u = _users(con)
    sid = _proposed(con, u)
    err = _assert_refused(con, lambda: A.approve_substitute(con, u["buyer"][0], sid, "QA: buyer tries", now=NOW),
                          "role", ActionPermissionError)
    assert isinstance(err, PermissionError)
    assert _one(con, "SELECT state FROM obj.substitute WHERE substitute_id = ?", sid) == "proposed"


def test_buyer_cannot_reject(con):
    u = _users(con)
    sid = _proposed(con, u)
    _assert_refused(con, lambda: A.reject_substitute(con, u["buyer"][0], sid, "QA: buyer tries", now=NOW),
                    "role", ActionPermissionError)


def test_viewer_refused_on_all_six(con):
    u = _users(con)
    v = u["viewer"][0]
    sid = _proposed(con, u)
    tid = _open_task(con, u)
    ek, fid = _event_and_facility(con)
    _, _, alt = _candidate(con)
    calls = [
        lambda: A.propose_substitute(con, v, ek, alt, "QA viewer", now=NOW),
        lambda: A.approve_substitute(con, v, sid, "QA viewer", now=NOW),
        lambda: A.reject_substitute(con, v, sid, "QA viewer", now=NOW),
        lambda: A.open_mitigation_task(con, v, ek, fid, u["buyer"][0], FUTURE, "QA viewer", now=NOW),
        lambda: A.close_mitigation_task(con, v, tid, "QA viewer closing this", now=NOW),
        lambda: A.reassign_task(con, v, tid, u["buyer"][1], "QA viewer", now=NOW),
    ]
    for call in calls:
        _assert_refused(con, call, "role", ActionPermissionError)


def test_inactive_pharmacist_refused(con):
    """DESIGN 4 step 2: actor.active is part of the role check."""
    u = _users(con)
    sid = _proposed(con, u)
    _assert_refused(con, lambda: A.approve_substitute(con, u["inactive"][0], sid, "QA inactive", now=NOW),
                    "role", ActionPermissionError)


def test_unknown_user_changes_nothing(con):
    """A user id that is not in seed.app_user must not act. SPEC is silent on the exact error; any raise is OK,
    but no edit and no SUCCESS row may appear."""
    u = _users(con)
    sid = _proposed(con, u)
    before = _counts(con)
    with pytest.raises(Exception):  # noqa: B017  (exact type not specified by SPEC)
        A.approve_substitute(con, "U-DOES-NOT-EXIST", sid, "QA ghost", now=NOW)
    after = _counts(con)
    assert after["sub_edit"] == before["sub_edit"]
    assert _one(con, "SELECT count(*) FROM audit.action_log WHERE actor_id = 'U-DOES-NOT-EXIST' "
                     "AND outcome = 'SUCCESS'") == 0


def test_role_cannot_be_passed_in():
    """DESIGN 8: the role comes from seed.app_user, never from the caller."""
    for name in ("propose_substitute", "approve_substitute", "reject_substitute", "open_mitigation_task",
                 "close_mitigation_task", "reassign_task"):
        params = inspect.signature(getattr(A, name)).parameters
        assert "role" not in params and "actor" not in params, name


def test_close_by_other_buyer_refused_assignee_ok(con):
    u = _users(con)
    tid = _open_task(con, u, assignee=u["buyer"][0])
    _assert_refused(con, lambda: A.close_mitigation_task(con, u["buyer"][1], tid, "QA not my task", now=NOW),
                    "actor_is_assignee_or_pharmacist")
    assert A.close_mitigation_task(con, u["buyer"][0], tid, "QA closed by assignee", now=NOW).outcome == "SUCCESS"


# ----------------------------------------------------------------------------------- double submit / state
def test_task_closed_twice(con):
    u = _users(con)
    tid = _open_task(con, u)
    assert A.close_mitigation_task(con, u["pharmacist"][0], tid, "QA: stock arrived", now=NOW).outcome == "SUCCESS"
    _assert_refused(con, lambda: A.close_mitigation_task(con, u["pharmacist"][0], tid, "QA: second click", now=NOW),
                    "task_is_open")
    assert _task_state(con, tid) == "closed"
    closes = _one(con, "SELECT count(*) FROM ops.mitigation_task_edit WHERE task_id = ? AND state = 'closed'", tid)
    assert closes == 1


def test_double_approve_refused(con):
    """DESIGN T9: a double click must not approve twice."""
    u = _users(con)
    sid = _proposed(con, u)
    assert A.approve_substitute(con, u["pharmacist"][0], sid, "QA ok", now=NOW).outcome == "SUCCESS"
    _assert_refused(con, lambda: A.approve_substitute(con, u["pharmacist"][0], sid, "QA again", now=NOW),
                    "state_is_proposed")


def test_reject_after_approve_refused(con):
    u = _users(con)
    sid = _proposed(con, u)
    A.approve_substitute(con, u["pharmacist"][0], sid, "QA ok", now=NOW)
    _assert_refused(con, lambda: A.reject_substitute(con, u["pharmacist"][0], sid, "QA change mind", now=NOW),
                    "state_is_proposed")


def test_reassign_closed_task_refused(con):
    u = _users(con)
    tid = _open_task(con, u)
    A.close_mitigation_task(con, u["pharmacist"][0], tid, "QA: resolved now", now=NOW)
    _assert_refused(con, lambda: A.reassign_task(con, u["pharmacist"][0], tid, u["buyer"][1], "QA", now=NOW),
                    "task_is_open")


# --------------------------------------------------------------------------------- objects that don't exist
def test_approve_missing_substitute(con):
    u = _users(con)
    _assert_refused(con, lambda: A.approve_substitute(con, u["pharmacist"][0], "S-000000000000", "QA", now=NOW),
                    "substitute_exists")


def test_reject_missing_substitute(con):
    u = _users(con)
    _assert_refused(con, lambda: A.reject_substitute(con, u["pharmacist"][0], "S-000000000000", "QA", now=NOW),
                    "substitute_exists")


def test_close_missing_task(con):
    u = _users(con)
    _assert_refused(con, lambda: A.close_mitigation_task(con, u["pharmacist"][0], "T-99999", "QA: no such task",
                                                         now=NOW), "task_exists")


def test_reassign_missing_task(con):
    u = _users(con)
    _assert_refused(con, lambda: A.reassign_task(con, u["pharmacist"][0], "T-99999", u["buyer"][0], "QA", now=NOW),
                    "task_exists")


def test_open_task_missing_event(con):
    u = _users(con)
    _, fid = _event_and_facility(con)
    _assert_refused(con, lambda: A.open_mitigation_task(con, u["pharmacist"][0], "00000000000|1999-01-01", fid,
                                                        u["buyer"][0], FUTURE, "QA", now=NOW), "event_exists")


def test_open_task_missing_facility(con):
    u = _users(con)
    ek, _ = _event_and_facility(con)
    _assert_refused(con, lambda: A.open_mitigation_task(con, u["pharmacist"][0], ek, "F-NOPE", u["buyer"][0],
                                                        FUTURE, "QA", now=NOW), "facility_valid")


def test_propose_missing_event_and_missing_alt(con):
    u = _users(con)
    _, ek, alt = _candidate(con)
    _assert_refused(con, lambda: A.propose_substitute(con, u["pharmacist"][0], "00000000000|1999-01-01", alt,
                                                      "QA", now=NOW), "event_exists")
    _assert_refused(con, lambda: A.propose_substitute(con, u["pharmacist"][0], ek, "99999999999", "QA", now=NOW),
                    "alt_drug_exists")


def test_propose_same_ndc_refused(con):
    u = _users(con)
    ek, ndc = con.execute("SELECT event_key, ndc11 FROM obj.shortage_event WHERE ndc11 IS NOT NULL "
                          "ORDER BY event_key LIMIT 1").fetchone()
    _assert_refused(con, lambda: A.propose_substitute(con, u["pharmacist"][0], ek, ndc, "QA same", now=NOW),
                    "alt_not_same_ndc")


def test_approve_alt_that_is_itself_short(con):
    """DESIGN 4 approve rule: the alternative is short if a latest-snapshot event has its ndc11 and is Current."""
    u = _users(con)
    a, b = con.execute("SELECT event_key, ndc11 FROM core.shortage_event WHERE present_in_latest "
                       "AND status = 'Current' AND ndc11 IS NOT NULL ORDER BY event_key LIMIT 2").fetchall()
    res = A.propose_substitute(con, u["pharmacist"][0], a[0], b[1], "QA: alt is short", now=NOW)
    _assert_refused(con, lambda: A.approve_substitute(con, u["pharmacist"][0], res.object_key, "QA", now=NOW),
                    "alt_not_short")


# ------------------------------------------------------------------------------------- input boundaries
@pytest.mark.parametrize("reason", ["", "   ", None])
def test_blank_reason_refused(con, reason):
    u = _users(con)
    sid = _proposed(con, u)
    _assert_refused(con, lambda: A.approve_substitute(con, u["pharmacist"][0], sid, reason, now=NOW),
                    "reason_given")


def test_note_9_vs_10_chars(con):
    u = _users(con)
    tid = _open_task(con, u)
    _assert_refused(con, lambda: A.close_mitigation_task(con, u["pharmacist"][0], tid, "123456789", now=NOW),
                    "note_min_10_chars")
    assert A.close_mitigation_task(con, u["pharmacist"][0], tid, "1234567890", now=NOW).outcome == "SUCCESS"


def test_whitespace_padded_note_refused(con):
    """10 characters of padding is not a resolution note. If this fails, log it in BUGS.md (spec intent)."""
    u = _users(con)
    tid = _open_task(con, u)
    _assert_refused(con, lambda: A.close_mitigation_task(con, u["pharmacist"][0], tid, " " * 9 + "x", now=NOW),
                    "note_min_10_chars")


def test_due_date_today_ok_yesterday_refused(con):
    u = _users(con)
    ek, fid = _event_and_facility(con)
    ph, buyer = u["pharmacist"][0], u["buyer"][0]
    assert A.open_mitigation_task(con, ph, ek, fid, buyer, date(2026, 10, 30), "QA today", now=NOW).outcome \
        == "SUCCESS"
    _assert_refused(con, lambda: A.open_mitigation_task(con, ph, ek, fid, buyer, date(2026, 10, 29), "QA", now=NOW),
                    "due_not_past")


def test_due_date_uses_new_york_day(con):
    """03:00 UTC on Oct 30 is still Oct 29 in New York, so a due date of Oct 29 is today, not past (DESIGN 4)."""
    u = _users(con)
    ek, fid = _event_and_facility(con)
    res = A.open_mitigation_task(con, u["pharmacist"][0], ek, fid, u["buyer"][0], date(2026, 10, 29), "QA tz",
                                 now=LATE_NIGHT)
    assert res.outcome == "SUCCESS"


def test_reassign_edges(con):
    u = _users(con)
    ph = u["pharmacist"][0]
    tid = _open_task(con, u, assignee=u["buyer"][0])
    _assert_refused(con, lambda: A.reassign_task(con, ph, tid, u["buyer"][0], "QA same", now=NOW),
                    "assignee_differs")
    _assert_refused(con, lambda: A.reassign_task(con, ph, tid, u["viewer"][0], "QA viewer", now=NOW),
                    "assignee_active_staff")
    _assert_refused(con, lambda: A.reassign_task(con, ph, tid, u["inactive"][0], "QA inactive", now=NOW),
                    "assignee_active_staff")
    assert A.reassign_task(con, ph, tid, u["buyer"][1], "QA handover", now=NOW).outcome == "SUCCESS"


def test_open_task_assignee_must_be_active_staff(con):
    u = _users(con)
    ek, fid = _event_and_facility(con)
    for bad in (u["viewer"][0], u["inactive"][0], "U-DOES-NOT-EXIST"):
        _assert_refused(con, lambda b=bad: A.open_mitigation_task(con, u["pharmacist"][0], ek, fid, b, FUTURE, "QA",
                                                                  now=NOW), "assignee_active_staff")


# ------------------------------------------------------------------------------------------ concurrency
def _race(con, fns):
    """Run callables at the same moment, each on its own cursor (a separate DuckDB connection)."""
    barrier = threading.Barrier(len(fns))
    results: list = [None] * len(fns)

    def run(i, fn):
        cur = con.cursor()
        try:
            barrier.wait(timeout=10)
            results[i] = fn(cur)
        except Exception as exc:  # noqa: BLE001  (we classify below)
            results[i] = exc
        finally:
            cur.close()

    threads = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def _ok_or_expected_failure(r):
    return getattr(r, "outcome", None) == "SUCCESS" or isinstance(r, (ActionRefused, duckdb.TransactionException))


def test_concurrent_double_approve_only_one_wins(con):
    u = _users(con)
    sid = _proposed(con, u)
    ph = u["pharmacist"]
    other = ph[1] if len(ph) > 1 else ph[0]
    res = _race(con, [lambda c: A.approve_substitute(c, ph[0], sid, "QA race A", now=NOW),
                      lambda c: A.approve_substitute(c, other, sid, "QA race B", now=NOW)])
    assert all(_ok_or_expected_failure(r) for r in res), res
    approved = _one(con, "SELECT count(*) FROM ops.substitute_edit WHERE substitute_id = ? AND state = 'approved'",
                    sid)
    assert approved == 1, res
    assert audit.verify_chain(con).ok


def test_concurrent_close_only_one_wins(con):
    u = _users(con)
    tid = _open_task(con, u, assignee=u["buyer"][0])
    res = _race(con, [lambda c: A.close_mitigation_task(c, u["buyer"][0], tid, "QA race close A", now=NOW),
                      lambda c: A.close_mitigation_task(c, u["pharmacist"][0], tid, "QA race close B", now=NOW)])
    assert all(_ok_or_expected_failure(r) for r in res), res
    assert _one(con, "SELECT count(*) FROM ops.mitigation_task_edit WHERE task_id = ? AND state = 'closed'",
                tid) == 1
    assert audit.verify_chain(con).ok


def test_concurrent_opens_keep_chain_and_task_ids_unique(con):
    """Two different actions at once must not fork the hash chain or reuse a task_id (DESIGN 3.2 T-%05d)."""
    u = _users(con)
    ek, fid = _event_and_facility(con)
    res = _race(con, [lambda c: A.open_mitigation_task(c, u["pharmacist"][0], ek, fid, u["buyer"][0], FUTURE,
                                                       "QA race open A", now=NOW),
                      lambda c: A.open_mitigation_task(c, u["buyer"][1], ek, fid, u["buyer"][0], FUTURE,
                                                       "QA race open B", now=NOW)])
    assert all(_ok_or_expected_failure(r) for r in res), res
    ids = [r.object_key for r in res if getattr(r, "outcome", None) == "SUCCESS"]
    assert ids and len(ids) == len(set(ids))
    assert _one(con, "SELECT count(*) - count(DISTINCT seq) FROM audit.action_log") == 0
    assert audit.verify_chain(con).ok


# --------------------------------------------------------------------------- reconcile and rebuild survival
def _do_a_mix(con, u):
    sid = _proposed(con, u)
    A.approve_substitute(con, u["pharmacist"][0], sid, "QA approve", now=NOW)
    tid = _open_task(con, u, assignee=u["buyer"][0])
    A.reassign_task(con, u["pharmacist"][0], tid, u["buyer"][1], "QA handover", now=NOW)
    with pytest.raises(ActionPermissionError):
        A.approve_substitute(con, u["buyer"][0], sid, "QA buyer", now=NOW)
    return sid, tid


def test_reconcile_edits_and_success_rows(con):
    """ADR 003 + vaccine BUG-005: every edit row has exactly one SUCCESS audit row, and the reverse."""
    u = _users(con)
    _do_a_mix(con, u)
    edit_seqs = [r[0] for r in con.execute(
        "SELECT action_seq FROM ops.substitute_edit UNION ALL SELECT action_seq FROM ops.mitigation_task_edit"
    ).fetchall()]
    success = [r[0] for r in con.execute("SELECT seq FROM audit.action_log WHERE outcome = 'SUCCESS'").fetchall()]
    assert sorted(edit_seqs) == sorted(success)
    assert len(edit_seqs) == len(set(edit_seqs))
    refused_with_edits = _one(con, "SELECT count(*) FROM audit.action_log a JOIN ops.substitute_edit e "
                                   "ON e.action_seq = a.seq WHERE a.outcome = 'REFUSED'")
    assert refused_with_edits == 0
    assert audit.verify_chain(con).ok


def test_actions_survive_pipeline_rebuild(tmp_path):
    """AC-6.4: act, re-run the whole pipeline, and every edit and audit row is still there."""
    paths = paths_for("demo", tmp_path)
    build(paths, client=None, refresh=False, seed=42)
    c = connect(paths.db)
    try:
        u = _users(c)
        sid, tid = _do_a_mix(c, u)
        before = _counts(c)
        head = c.execute("SELECT max(seq), max_by(row_hash, seq) FROM audit.action_log").fetchone()
    finally:
        c.close()

    build(paths, client=None, refresh=False, seed=42)

    c = connect(paths.db)
    try:
        assert _counts(c) == before
        assert c.execute("SELECT max(seq), max_by(row_hash, seq) FROM audit.action_log").fetchone() == head
        assert _one(c, "SELECT state FROM obj.substitute WHERE substitute_id = ?", sid) == "approved"
        row = c.execute("SELECT state, assignee_id FROM obj.mitigation_task WHERE task_id = ?", [tid]).fetchone()
        assert row == ("open", u["buyer"][1])
        assert audit.verify_chain(c).ok
    finally:
        c.close()
