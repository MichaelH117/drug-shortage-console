"""ML-04 tests for ontokit.actions and ontokit.writeback on mem_db with a toy object (AC-6.2, 6.3, 7.1).

Uses the real ontokit.audit (DE-08) when it exists, else a minimal local fake with the DESIGN 4 signature.
"""
import importlib
import json
import sys
import types

import pytest

from ontokit.actions import ActionPermissionError, ActionRefused, Actor, run_action
from ontokit.ontology import ActionType
from ontokit.writeback import init_edit_tables, latest_edit_view_sql


def _fake_audit():
    m = types.ModuleType("ontokit.audit")

    def init_audit(con):
        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        con.execute("""CREATE TABLE IF NOT EXISTS audit.action_log (seq BIGINT PRIMARY KEY, action_type VARCHAR,
            object_type VARCHAR, object_key VARCHAR, actor_id VARCHAR, role VARCHAR, reason VARCHAR,
            old_value VARCHAR, new_value VARCHAR, outcome VARCHAR, failed_criterion VARCHAR, ts_utc TIMESTAMP,
            prev_hash VARCHAR, row_hash VARCHAR)""")

    def append(con, *, action_type, object_type, object_key, actor_id, role, reason, old_value, new_value,
               outcome, failed_criterion=None, now=None):
        seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit.action_log").fetchone()[0]
        con.execute("INSERT INTO audit.action_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [seq, action_type, object_type, object_key, actor_id, role, reason, json.dumps(old_value),
                     json.dumps(new_value), outcome, failed_criterion, now, "x", "y"])
        return seq

    m.init_audit, m.append = init_audit, append
    return m


@pytest.fixture()
def audit_mod(monkeypatch):
    try:
        mod = importlib.import_module("ontokit.audit")
    except ImportError:
        mod = _fake_audit()
        monkeypatch.setitem(sys.modules, "ontokit.audit", mod)
        import ontokit
        monkeypatch.setattr(ontokit, "audit", mod, raising=False)
    return mod


TOY_DDL = [
    "CREATE SCHEMA IF NOT EXISTS ops",
    "CREATE TABLE IF NOT EXISTS ops.thing_edit (edit_seq BIGINT PRIMARY KEY, thing_id VARCHAR, state VARCHAR, "
    "actor_id VARCHAR, action_seq BIGINT)",
]
SPEC = ActionType("set_thing", "Thing", frozenset({"boss"}), ["value_ok"])


@pytest.fixture()
def db(mem_db, audit_mod):
    audit_mod.init_audit(mem_db)
    init_edit_tables(mem_db, TOY_DDL)
    mem_db.execute("CREATE TABLE thing AS SELECT * FROM (VALUES ('t1', 'a'), ('t2', 'b')) v(thing_id, label)")
    mem_db.execute(latest_edit_view_sql("thing", "ops.thing_edit", "thing_id", "obj_thing",
                                        base_columns=["thing_id", "label"],
                                        edit_columns=["edit_seq", "thing_id", "state", "actor_id", "action_seq"],
                                        defaults={"state": "candidate"}))
    return mem_db


def _apply(con, p):
    seq = con.execute("SELECT COALESCE(MAX(edit_seq), 0) + 1 FROM ops.thing_edit").fetchone()[0]
    con.execute("INSERT INTO ops.thing_edit VALUES (?, ?, ?, ?, ?)",
                [seq, p["thing_id"], p["state"], p["_actor"].user_id, p["_action_seq"]])
    return {"state": None}, {"state": p["state"]}


CRITERIA = [("value_ok", lambda con, p: p["state"] in ("on", "off"))]
BOSS = Actor("u1", "boss", True)


def _counts(con):
    edits = con.execute("SELECT count(*) FROM ops.thing_edit").fetchone()[0]
    rows = con.execute("SELECT outcome, failed_criterion FROM audit.action_log ORDER BY seq").fetchall()
    return edits, rows


def test_role_refused_raises_permission_error_and_logs_one_row(db):
    with pytest.raises(ActionPermissionError) as ei:
        run_action(db, SPEC, Actor("u2", "clerk", True), {"thing_id": "t1", "state": "on", "reason": "r"},
                   object_key="t1", criteria=CRITERIA, apply=_apply)
    assert isinstance(ei.value, PermissionError) and ei.value.criterion == "role"
    assert _counts(db) == (0, [("REFUSED", "role")])


def test_inactive_actor_refused(db):
    with pytest.raises(ActionPermissionError):
        run_action(db, SPEC, Actor("u1", "boss", False), {"thing_id": "t1", "state": "on"},
                   object_key="t1", criteria=CRITERIA, apply=_apply)
    assert _counts(db) == (0, [("REFUSED", "role")])


def test_failed_criterion_records_its_id(db):
    with pytest.raises(ActionRefused) as ei:
        run_action(db, SPEC, BOSS, {"thing_id": "t1", "state": "maybe"}, object_key="t1",
                   criteria=CRITERIA, apply=_apply)
    assert not isinstance(ei.value, PermissionError)
    assert ei.value.criterion == "value_ok"
    assert _counts(db) == (0, [("REFUSED", "value_ok")])
    assert db.execute("SELECT state FROM obj_thing WHERE thing_id='t1'").fetchone()[0] == "candidate"


def test_forced_exception_after_edit_leaves_nothing(db, audit_mod, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("forced failure after the edit")

    monkeypatch.setattr(audit_mod, "append", boom)
    with pytest.raises(RuntimeError, match="forced"):
        run_action(db, SPEC, BOSS, {"thing_id": "t1", "state": "on"}, object_key="t1",
                   criteria=CRITERIA, apply=_apply)
    assert _counts(db) == (0, [])


def test_success_writes_one_edit_and_one_audit_row_with_same_seq(db):
    res = run_action(db, SPEC, BOSS, {"thing_id": "t1", "state": "on", "reason": "go"}, object_key="t1",
                     criteria=CRITERIA, apply=_apply)
    assert res.outcome == "SUCCESS"
    edits, rows = _counts(db)
    assert edits == 1 and rows == [("SUCCESS", None)]
    edit_seq = db.execute("SELECT action_seq FROM ops.thing_edit").fetchone()[0]
    audit_seq = db.execute("SELECT seq FROM audit.action_log WHERE outcome='SUCCESS'").fetchone()[0]
    assert edit_seq == audit_seq == res.seq
    # edits win in the merged view; untouched base rows keep the default
    got = dict(db.execute("SELECT thing_id, state FROM obj_thing ORDER BY thing_id").fetchall())
    assert got == {"t1": "on", "t2": "candidate"}


def test_init_edit_tables_refuses_destructive_ddl(mem_db):
    with pytest.raises(ValueError):
        init_edit_tables(mem_db, ["DROP TABLE ops.thing_edit"])
    with pytest.raises(ValueError):
        init_edit_tables(mem_db, ["CREATE OR REPLACE TABLE x (a INT)"])
    with pytest.raises(ValueError):
        init_edit_tables(mem_db, ["CREATE TABLE x (a INT)"])  # no IF NOT EXISTS


def test_edit_only_view(mem_db):
    init_edit_tables(mem_db, TOY_DDL)
    mem_db.execute("INSERT INTO ops.thing_edit VALUES (1,'t9','open','u',1), (2,'t9','closed','u',2)")
    mem_db.execute(latest_edit_view_sql(None, "ops.thing_edit", "thing_id", "obj_t"))
    assert mem_db.execute("SELECT state FROM obj_t").fetchall() == [("closed",)]


# ------------------------------------------------------------------ round 3 (ML-12: REVIEW 9, 10)
def test_r3_next_seq_tolerates_only_a_missing_table(mem_db):
    from ontokit.actions import _next_seq

    assert _next_seq(mem_db) == 1  # no audit schema yet: CatalogException -> 1

    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("disk on fire")

    with pytest.raises(RuntimeError, match="disk on fire"):
        _next_seq(Broken())


def test_r3_reason_comes_from_caller_and_refused_object_override(db):
    run_action(db, SPEC, BOSS, {"thing_id": "t1", "state": "on", "note": "x"}, object_key="t1",
               criteria=CRITERIA, apply=_apply, reason="caller reason")
    with pytest.raises(ActionRefused):
        run_action(db, SPEC, BOSS, {"thing_id": "t9", "state": "bad"}, object_key="T-next",
                   criteria=CRITERIA, apply=_apply, refused_object=("Parent", "p1"))
    rows = db.execute("SELECT object_type, object_key, reason, outcome FROM audit.action_log ORDER BY seq").fetchall()
    assert rows == [("Thing", "t1", "caller reason", "SUCCESS"), ("Parent", "p1", None, "REFUSED")]
