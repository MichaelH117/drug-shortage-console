"""ML-05 tests for console.actions and console.identity on a small hand-made DB (AC-6.1, AC-6.2).

The views are created by pipeline.objects.create_object_views from ontology.yaml, so this also exercises ML-06.
Uses the real ontokit.audit when DE-08 has landed, else the local fake from test_ontokit_actions.
"""
import datetime as dt
import importlib
import sys

import pytest

from console import actions as A
from console.identity import get_actor
from ontokit.actions import ActionPermissionError, ActionRefused
from pipeline.objects import create_object_views

NOW = dt.datetime(2026, 10, 30, 14, 0, 0)  # naive UTC = 10:00 New York
FUTURE = dt.date(2026, 11, 6)
E_SHORT, E_OK = "00409263405|2020-01-01", "00641614625|2021-01-01"

SEED_SQL = """
CREATE SCHEMA seed; CREATE SCHEMA core;
CREATE TABLE seed.app_user AS SELECT * FROM (VALUES
  ('U-dana','Dana Ruiz','pharmacist',true,true), ('U-sam','Sam Okafor','pharmacist',true,true),
  ('U-lee','Lee Chen','buyer',true,true), ('U-priya','Priya Nair','buyer',true,true),
  ('U-jordan','Jordan Blake','viewer',true,true), ('U-old','Old Buyer','buyer',false,true))
  v(user_id, display_name, role, active, is_synthetic);
CREATE TABLE seed.facility AS SELECT * FROM (VALUES ('F-1','SYNTHETIC Riverbend Main','hospital',300,true))
  v(facility_id, name, kind, beds, is_synthetic);
CREATE TABLE seed.facility_stock AS SELECT * FROM (VALUES ('F-1','I-1',3.0,true))
  v(facility_id,item_id,days_on_hand,is_synthetic);
CREATE TABLE core.shortage_event AS SELECT * FROM (VALUES
  ('00409263405|2020-01-01','00409263405','004092634','Hydromorphone HCl Injection','p',
   DATE '2026-10-28', DATE '2026-10-30', true, 'Current', 'Limited'),
  ('00641614625|2021-01-01','00641614625','006416146','Dexamethasone Injection','p',
   DATE '2026-10-28', DATE '2026-10-30', true, 'Current', 'Unavailable'))
  v(event_key, ndc11, product_ndc9, generic_name, presentation, first_seen, last_seen, present_in_latest,
    status, availability_normalized);
CREATE TABLE core.drug AS SELECT * FROM (VALUES
  ('00409263405','004092634','Hydromorphone','hydromorphone','injection','parenteral','10 mg/ml',
   NULL,NULL,'shortage'),
  ('55555011101','555550111','Hydromorphone','hydromorphone','injection','intravenous','10 mg/ml',
   NULL,NULL,'formulary'),
  ('00641614625','006416146','Dexamethasone','dexamethasone','injection','parenteral','4 mg/ml',NULL,NULL,'shortage'),
  ('00641999901','006416146','Dexamethasone','dexamethasone','injection','parenteral','10 mg/ml',NULL,NULL,'formulary'))
  v(ndc11, product_ndc9, generic_name, ingredient_norm, dosage_form_norm, route_norm, strength_norm, manufacturer,
    rxcui, source);
CREATE TABLE core.formulary_match AS SELECT 'I-1' item_id, '00409263405|2020-01-01' event_key, 1 match_tier,
  1.0 match_confidence, 'ndc11' matched_on;
CREATE TABLE core.substitute_candidate AS SELECT * FROM (VALUES
  ('S-x', '00409263405|2020-01-01', '55555011101', 'I-2', 'same ingredient'))
  v(substitute_id, event_key, alt_ndc11, alt_item_id, basis);
"""


@pytest.fixture()
def con(mem_db, monkeypatch):
    try:
        importlib.import_module("ontokit.audit")
    except ImportError:
        from tests.test_ontokit_actions import _fake_audit

        fake = _fake_audit()
        monkeypatch.setitem(sys.modules, "ontokit.audit", fake)
        import ontokit
        monkeypatch.setattr(ontokit, "audit", fake, raising=False)
    mem_db.execute(SEED_SQL)
    create_object_views(mem_db, A.ontology(), include_marts=False)
    return mem_db


def _log(con):
    return con.execute("SELECT action_type, actor_id, outcome, failed_criterion FROM audit.action_log "
                       "ORDER BY seq").fetchall()


def _state(con, sid):
    return con.execute("SELECT state FROM obj.substitute WHERE substitute_id = ?", [sid]).fetchone()[0]


def test_identity_role_comes_from_seed(con):
    assert get_actor(con, "U-lee").role == "buyer"
    a = get_actor(con, "U-nobody")
    assert a.active is False


def test_propose_then_approve_by_pharmacist(con):
    r = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "same drug other labeler", now=NOW)
    sid = r.object_key
    assert sid == A.substitute_id_for(E_SHORT, "55555011101") and _state(con, sid) == "proposed"
    A.approve_substitute(con, "U-dana", sid, "checked stock", now=NOW)
    assert _state(con, sid) == "approved"
    assert _state(con, "S-x") == "candidate"  # base-only row keeps the default


def test_propose_refusals(con):
    with pytest.raises(ActionRefused) as e:
        A.propose_substitute(con, "U-lee", E_SHORT, "00409263405", "same ndc", now=NOW)
    assert e.value.criterion == "alt_not_same_ndc"
    with pytest.raises(ActionRefused) as e:
        A.propose_substitute(con, "U-lee", E_SHORT, "99999999999", "x", now=NOW)
    assert e.value.criterion == "alt_drug_exists"
    with pytest.raises(ActionRefused) as e:
        A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "  ", now=NOW)
    assert e.value.criterion == "reason_given"
    assert con.execute("SELECT count(*) FROM ops.substitute_edit").fetchone()[0] == 0
    assert [r[2:] for r in _log(con)] == [("REFUSED", "alt_not_same_ndc"), ("REFUSED", "alt_drug_exists"),
                                          ("REFUSED", "reason_given")]


def test_buyer_cannot_approve_permission_error(con):
    sid = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "r", now=NOW).object_key
    with pytest.raises(ActionPermissionError) as e:
        A.approve_substitute(con, "U-lee", sid, "I want to", now=NOW)
    assert isinstance(e.value, PermissionError) and e.value.criterion == "role"
    assert _state(con, sid) == "proposed"
    assert _log(con)[-1] == ("approve_substitute", "U-lee", "REFUSED", "role")


def test_approve_refused_when_alternative_is_itself_short(con):
    # alt 00641999901 shares product 006416146 with an event that is Current/Unavailable
    sid = A.propose_substitute(con, "U-dana", E_SHORT, "00641999901", "r", now=NOW).object_key
    with pytest.raises(ActionRefused) as e:
        A.approve_substitute(con, "U-dana", sid, "ok", now=NOW)
    assert e.value.criterion == "alt_not_short"


def test_double_approve_and_reject(con):
    sid = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "r", now=NOW).object_key
    A.approve_substitute(con, "U-dana", sid, "ok", now=NOW)
    with pytest.raises(ActionRefused) as e:
        A.approve_substitute(con, "U-sam", sid, "again", now=NOW)
    assert e.value.criterion == "state_is_proposed"
    sid2 = A.propose_substitute(con, "U-lee", E_OK, "00641999901", "r", now=NOW).object_key
    A.reject_substitute(con, "U-sam", sid2, "not equivalent", now=NOW)
    assert _state(con, sid2) == "rejected"
    with pytest.raises(ActionPermissionError):
        A.reject_substitute(con, "U-lee", sid2, "buyer", now=NOW)


def test_task_lifecycle_and_refusals(con):
    r = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "call wholesaler", now=NOW)
    tid = r.object_key
    assert tid == "T-00001"
    assert con.execute("SELECT count(*) FROM ops.outbox").fetchone()[0] == 1
    for kwargs, crit in [(dict(facility_id="F-9"), "facility_valid"), (dict(assignee_id="U-jordan"),
                         "assignee_active_staff"), (dict(assignee_id="U-old"), "assignee_active_staff"),
                         (dict(due_date=dt.date(2026, 10, 29)), "due_not_past")]:
        args = dict(event_key=E_OK, facility_id="F-1", assignee_id="U-lee", due_date=FUTURE, reason="r") | kwargs
        with pytest.raises(ActionRefused) as e:
            A.open_mitigation_task(con, "U-lee", now=NOW, **args)
        assert e.value.criterion == crit
    with pytest.raises(ActionRefused) as e:
        A.reassign_task(con, "U-lee", tid, "U-lee", "same", now=NOW)
    assert e.value.criterion == "assignee_differs"
    A.reassign_task(con, "U-lee", tid, "U-priya", "vacation", now=NOW)
    with pytest.raises(ActionRefused) as e:  # Lee is no longer the assignee and is not a pharmacist
        A.close_mitigation_task(con, "U-lee", tid, "resolved by wholesaler", now=NOW)
    assert e.value.criterion == "actor_is_assignee_or_pharmacist"
    with pytest.raises(ActionRefused) as e:
        A.close_mitigation_task(con, "U-priya", tid, "too short", now=NOW)
    assert e.value.criterion == "note_min_10_chars"
    A.close_mitigation_task(con, "U-priya", tid, "resolved via wholesaler", now=NOW)
    row = con.execute("SELECT state, assignee_id FROM obj.mitigation_task WHERE task_id=?", [tid]).fetchone()
    assert row == ("closed", "U-priya")
    with pytest.raises(ActionRefused) as e:
        A.close_mitigation_task(con, "U-dana", tid, "closing it twice", now=NOW)
    assert e.value.criterion == "task_is_open"


def test_viewer_and_inactive_refused_on_all_six(con):
    sid = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "r", now=NOW).object_key
    tid = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "r", now=NOW).object_key
    for user in ("U-jordan", "U-old", "U-nobody"):
        calls = [
            lambda u: A.propose_substitute(con, u, E_SHORT, "55555011101", "r", now=NOW),
            lambda u: A.approve_substitute(con, u, sid, "r", now=NOW),
            lambda u: A.reject_substitute(con, u, sid, "r", now=NOW),
            lambda u: A.open_mitigation_task(con, u, E_SHORT, "F-1", "U-lee", FUTURE, "r", now=NOW),
            lambda u: A.close_mitigation_task(con, u, tid, "a long enough note", now=NOW),
            lambda u: A.reassign_task(con, u, tid, "U-priya", "r", now=NOW),
        ]
        for call in calls:
            with pytest.raises(ActionPermissionError):
                call(user)
    refused = [r for r in _log(con) if r[2] == "REFUSED"]
    assert len(refused) == 18 and all(r[3] == "role" for r in refused)


# ------------------------------------------------------------------ round 3 regressions (REVIEW 1, 3, 7; ML-12)
def _rows_for(con, object_key):
    return con.execute("SELECT object_type, outcome, failed_criterion FROM audit.action_log WHERE object_key = ? "
                       "ORDER BY seq", [object_key]).fetchall()


def test_r3_buyer_cannot_repropose_approved_substitute(con):
    """REVIEW 1: re-proposing must not undo a pharmacist's approval."""
    sid = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "r", now=NOW).object_key
    A.approve_substitute(con, "U-dana", sid, "ok", now=NOW)
    with pytest.raises(ActionRefused) as e:
        A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "again", now=NOW)
    assert e.value.criterion == "not_already_decided"
    assert _state(con, sid) == "approved"


def test_r3_repropose_rejected_refused_but_repropose_proposed_ok(con):
    sid = A.propose_substitute(con, "U-lee", E_SHORT, "55555011101", "r", now=NOW).object_key
    A.propose_substitute(con, "U-priya", E_SHORT, "55555011101", "still proposed: allowed", now=NOW)
    A.reject_substitute(con, "U-dana", sid, "no", now=NOW)
    with pytest.raises(ActionRefused) as e:
        A.propose_substitute(con, "U-dana", E_SHORT, "55555011101", "try again", now=NOW)
    assert e.value.criterion == "not_already_decided"
    assert _state(con, sid) == "rejected"


def test_r3_refused_open_is_logged_on_the_event_not_the_next_task_id(con):
    """REVIEW 3: the next real task's audit history must start with its own SUCCESS row."""
    with pytest.raises(ActionRefused):
        A.open_mitigation_task(con, "U-dana", E_SHORT, "F-9", "U-lee", FUTURE, "r", now=NOW)
    tid = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "r", now=NOW).object_key
    assert _rows_for(con, tid) == [("MitigationTask", "SUCCESS", None)]
    assert _rows_for(con, E_SHORT) == [("ShortageEvent", "REFUSED", "facility_valid")]


def test_r3_double_open_same_event_facility_refused(con):
    """REVIEW 7 / DESIGN T9: a double submit opens one task; after closing it a new one may be opened."""
    tid = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "r", now=NOW).object_key
    with pytest.raises(ActionRefused) as e:
        A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-priya", FUTURE, "double click", now=NOW)
    assert e.value.criterion == "no_open_task_for_event_facility"
    assert con.execute("SELECT count(*) FROM obj.mitigation_task").fetchone()[0] == 1
    A.close_mitigation_task(con, "U-lee", tid, "resolved with wholesaler", now=NOW)
    again = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "reopened", now=NOW)
    assert again.object_key == "T-00002"


def test_r3_close_audit_reason_is_the_resolution_note(con):
    """REVIEW 10: the caller names the reason field; ontokit no longer guesses."""
    tid = A.open_mitigation_task(con, "U-dana", E_SHORT, "F-1", "U-lee", FUTURE, "r", now=NOW).object_key
    A.close_mitigation_task(con, "U-lee", tid, "resolved with wholesaler", now=NOW)
    r = con.execute("SELECT reason FROM audit.action_log WHERE action_type='close_mitigation_task'").fetchone()
    assert r[0] == "resolved with wholesaler"
