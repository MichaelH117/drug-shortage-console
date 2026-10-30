"""The six domain actions (ML-05, SPEC US-6). This module is the ONLY write path for the app and the tests.

Each action = role rule (from ontology.yaml) + named submission criteria (ids from ontology.yaml) +
an ``apply`` that appends edit rows, all run by ``ontokit.actions.run_action`` in one transaction.
Edit tables are append-only event rows; ``obj.*`` views take the latest edit per key (edits win).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from console.identity import get_actor
from ontokit.actions import ActionResult, Criterion, run_action
from ontokit.ontology import Ontology, load_ontology
from ontokit.writeback import init_edit_tables

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "ontology" / "ontology.yaml"
LOCAL_TZ = ZoneInfo("America/New_York")
STAFF_ROLES = ("pharmacist", "buyer")
MIN_NOTE_CHARS = 10

OPS_DDL = [
    "CREATE SCHEMA IF NOT EXISTS ops",
    """CREATE TABLE IF NOT EXISTS ops.substitute_edit (
        edit_seq BIGINT PRIMARY KEY, substitute_id VARCHAR NOT NULL, event_key VARCHAR NOT NULL,
        alt_ndc11 VARCHAR NOT NULL, state VARCHAR NOT NULL, reason VARCHAR, actor_id VARCHAR NOT NULL,
        action_seq BIGINT NOT NULL, edited_at_utc TIMESTAMP NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS ops.mitigation_task_edit (
        edit_seq BIGINT PRIMARY KEY, task_id VARCHAR NOT NULL, event_key VARCHAR NOT NULL,
        facility_id VARCHAR NOT NULL, assignee_id VARCHAR NOT NULL, due_date DATE, state VARCHAR NOT NULL,
        note VARCHAR, actor_id VARCHAR NOT NULL, action_seq BIGINT NOT NULL, edited_at_utc TIMESTAMP NOT NULL,
        is_synthetic BOOLEAN NOT NULL DEFAULT true)""",
    """CREATE TABLE IF NOT EXISTS ops.outbox (
        outbox_seq BIGINT PRIMARY KEY, action_seq BIGINT NOT NULL, kind VARCHAR NOT NULL,
        payload_json VARCHAR NOT NULL, created_at_utc TIMESTAMP NOT NULL)""",
]
SUBSTITUTE_EDIT_COLUMNS = ["edit_seq", "substitute_id", "event_key", "alt_ndc11", "state", "reason", "actor_id",
                           "action_seq", "edited_at_utc"]
TASK_EDIT_COLUMNS = ["edit_seq", "task_id", "event_key", "facility_id", "assignee_id", "due_date", "state", "note",
                     "actor_id", "action_seq", "edited_at_utc", "is_synthetic"]


@lru_cache(maxsize=1)
def ontology() -> Ontology:
    return load_ontology(ONTOLOGY_PATH)


def init_ops(con) -> None:
    """Create ops.* and audit.* tables IF NOT EXISTS. Never drops anything (AC-6.4)."""
    from ontokit import audit

    init_edit_tables(con, OPS_DDL)
    audit.init_audit(con)


def substitute_id_for(event_key: str, alt_ndc11: str) -> str:
    """Deterministic id (DESIGN 3.2), so an edit still joins after re-ingest (AC-6.4)."""
    return "S-" + hashlib.sha1(f"{event_key}|{alt_ndc11}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------- small helpers
def _one(con, sql: str, params: list):
    return con.execute(sql, params).fetchone()


def _next(con, table: str, col: str) -> int:
    return int(con.execute(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table}").fetchone()[0])


def _given(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _local_today(now: dt.datetime) -> dt.date:
    return now.replace(tzinfo=dt.UTC).astimezone(LOCAL_TZ).date()


def _as_date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def _substitute(con, substitute_id: str):
    return _one(con, "SELECT substitute_id, event_key, alt_ndc11, state FROM obj.substitute WHERE substitute_id = ?",
                [substitute_id])


def _task(con, task_id: str):
    return _one(con, "SELECT task_id, event_key, facility_id, assignee_id, due_date, state FROM obj.mitigation_task "
                     "WHERE task_id = ?", [task_id])


def _active_staff(con, user_id) -> bool:
    r = _one(con, "SELECT role, active FROM seed.app_user WHERE user_id = ?", [user_id])
    return r is not None and bool(r[1]) and r[0] in STAFF_ROLES


def _event_exists(con, p) -> bool:
    return _one(con, "SELECT 1 FROM core.shortage_event WHERE event_key = ?", [p["event_key"]]) is not None


def alt_is_short(con, alt_ndc11: str) -> bool:
    """DESIGN 4 approve rule: some event in the latest snapshot has ndc11 = alt or product_ndc9 = alt's product,
    and is Current or Limited/Unavailable."""
    p9 = _one(con, "SELECT product_ndc9 FROM core.drug WHERE ndc11 = ?", [alt_ndc11])
    alt_p9 = (p9[0] if p9 and p9[0] else None) or (alt_ndc11[:9] if alt_ndc11 else None)
    r = _one(con, """SELECT count(*) FROM core.shortage_event
                     WHERE present_in_latest
                       AND (ndc11 = ? OR product_ndc9 = ?)
                       AND (status = 'Current' OR availability_normalized IN ('Limited', 'Unavailable'))""",
             [alt_ndc11, alt_p9])
    return bool(r[0])


def _write_sub_edit(con, p, sub_id, event_key, alt, state):
    row = [_next(con, "ops.substitute_edit", "edit_seq"), sub_id, event_key, alt, state, p.get("reason"),
           p["_actor"].user_id, p["_action_seq"], p["_now"]]
    con.execute(f"INSERT INTO ops.substitute_edit ({', '.join(SUBSTITUTE_EDIT_COLUMNS)}) VALUES "
                f"({', '.join('?' * len(row))})", row)


def _write_task_edit(con, p, task_id, event_key, facility_id, assignee_id, due_date, state, note):
    row = [_next(con, "ops.mitigation_task_edit", "edit_seq"), task_id, event_key, facility_id, assignee_id,
           due_date, state, note, p["_actor"].user_id, p["_action_seq"], p["_now"], True]
    con.execute(f"INSERT INTO ops.mitigation_task_edit ({', '.join(TASK_EDIT_COLUMNS)}) VALUES "
                f"({', '.join('?' * len(row))})", row)


def _outbox(con, p, kind: str, payload: dict):
    con.execute("INSERT INTO ops.outbox VALUES (?, ?, ?, ?, ?)",
                [_next(con, "ops.outbox", "outbox_seq"), p["_action_seq"], kind,
                 json.dumps(payload, sort_keys=True, default=str), p["_now"]])


def _run(con, user_id, name, params, object_key, criteria, apply, now, *, reason=None,
         refused_object=None) -> ActionResult:
    spec = ontology().actions[name]
    declared = [c for c, _ in criteria]
    if declared != spec.criteria:  # the YAML is the single source of criterion ids and order
        raise RuntimeError(f"{name}: criteria {declared} do not match ontology {spec.criteria}")
    return run_action(con, spec, get_actor(con, user_id), params, object_key=object_key, criteria=criteria,
                      apply=apply, now=now, reason=reason if reason is not None else params.get("reason"),
                      refused_object=refused_object)


# ---------------------------------------------------------------- substitutes
def propose_substitute(con, user_id, event_key, alt_ndc11, reason, now=None) -> ActionResult:
    sub_id = substitute_id_for(event_key, alt_ndc11)
    params = {"substitute_id": sub_id, "event_key": event_key, "alt_ndc11": alt_ndc11, "reason": reason}

    def alt_drug_exists(con, p):
        return _one(con, "SELECT 1 FROM core.drug WHERE ndc11 = ?", [p["alt_ndc11"]]) is not None

    def alt_not_same_ndc(con, p):
        r = _one(con, "SELECT ndc11 FROM core.shortage_event WHERE event_key = ?", [p["event_key"]])
        return r is not None and r[0] != p["alt_ndc11"]

    criteria: list[Criterion] = [
        ("event_exists", _event_exists),
        ("alt_drug_exists", alt_drug_exists),
        ("alt_not_same_ndc", alt_not_same_ndc),
        # REVIEW 1: a pharmacist's decision is final for this flow; re-proposing must not undo it
        ("not_already_decided", lambda con, p: (_substitute(con, p["substitute_id"]) or (None,) * 4)[3]
         not in ("approved", "rejected")),
        ("reason_given", lambda con, p: _given(p["reason"])),
    ]

    def apply(con, p):
        prev = _substitute(con, sub_id)
        _write_sub_edit(con, p, sub_id, event_key, alt_ndc11, "proposed")
        return {"state": prev[3] if prev else None}, {"substitute_id": sub_id, "state": "proposed",
                                                      "event_key": event_key, "alt_ndc11": alt_ndc11}

    return _run(con, user_id, "propose_substitute", params, sub_id, criteria, apply, now)


def _decide_substitute(con, user_id, name, substitute_id, reason, new_state, now) -> ActionResult:
    params = {"substitute_id": substitute_id, "reason": reason}

    def state_is_proposed(con, p):
        s = _substitute(con, p["substitute_id"])
        return s is not None and s[3] == "proposed"

    criteria: list[Criterion] = [
        ("substitute_exists", lambda con, p: _substitute(con, p["substitute_id"]) is not None),
        ("state_is_proposed", state_is_proposed),
    ]
    if name == "approve_substitute":
        criteria.append(("alt_not_short",
                         lambda con, p: not alt_is_short(con, _substitute(con, p["substitute_id"])[2])))
    criteria.append(("reason_given", lambda con, p: _given(p["reason"])))

    def apply(con, p):
        s = _substitute(con, substitute_id)
        _write_sub_edit(con, p, substitute_id, s[1], s[2], new_state)
        return {"state": s[3]}, {"substitute_id": substitute_id, "state": new_state}

    return _run(con, user_id, name, params, substitute_id, criteria, apply, now)


def approve_substitute(con, user_id, substitute_id, reason, now=None) -> ActionResult:
    return _decide_substitute(con, user_id, "approve_substitute", substitute_id, reason, "approved", now)


def reject_substitute(con, user_id, substitute_id, reason, now=None) -> ActionResult:
    return _decide_substitute(con, user_id, "reject_substitute", substitute_id, reason, "rejected", now)


# ---------------------------------------------------------------- mitigation tasks
def _audit_mod():
    from ontokit import audit

    return audit


def _task_id_next(con) -> str:
    r = _one(con, "SELECT COALESCE(MAX(TRY_CAST(substr(task_id, 3) AS INTEGER)), 0) + 1 "
                  "FROM ops.mitigation_task_edit", [])
    return f"T-{int(r[0]):05d}"


def open_mitigation_task(con, user_id, event_key, facility_id, assignee_id, due_date, reason,
                         now=None) -> ActionResult:
    params = {"event_key": event_key, "facility_id": facility_id, "assignee_id": assignee_id,
              "due_date": due_date, "reason": reason}

    def facility_valid(con, p):
        return _one(con, "SELECT 1 FROM seed.facility WHERE facility_id = ?", [p["facility_id"]]) is not None

    def due_not_past(con, p):
        d = _as_date(p["due_date"])
        return d is not None and d >= _local_today(p["_now"])

    def no_open_task(con, p):  # REVIEW 7: a double submit must not open a second task
        r = _one(con, "SELECT count(*) FROM obj.mitigation_task WHERE event_key = ? AND facility_id = ? "
                      "AND state = 'open'", [p["event_key"], p["facility_id"]])
        return r[0] == 0

    criteria: list[Criterion] = [
        ("event_exists", _event_exists),
        ("facility_valid", facility_valid),
        ("assignee_active_staff", lambda con, p: _active_staff(con, p["assignee_id"])),
        ("due_not_past", due_not_past),
        ("no_open_task_for_event_facility", no_open_task),  # inputs first, then state
    ]
    # The task_id is picked before run_action (it is the audit object_key), so the id choice must sit under the
    # same writer lock as the transaction (DE fix 2026-10-30; the lock is re-entrant).
    with _audit_mod().writer_lock():
        task_id = _task_id_next(con)  # single-writer DB; re-checked inside the transaction

        def apply(con, p):
            if _task_id_next(con) != task_id:
                raise RuntimeError("task id changed during the action; retry")
            d = _as_date(due_date)
            _write_task_edit(con, p, task_id, event_key, facility_id, assignee_id, d, "open", reason)
            _outbox(con, p, "task_assigned", {"task_id": task_id, "assignee_id": assignee_id, "event_key": event_key,
                                              "facility_id": facility_id, "due_date": d})
            return {"state": None}, {"task_id": task_id, "state": "open", "assignee_id": assignee_id,
                                     "facility_id": facility_id, "due_date": d}

        # REVIEW 3: the task id exists only if the open succeeds; a refusal is logged on the ShortageEvent
        return _run(con, user_id, "open_mitigation_task", params, task_id, criteria, apply, now,
                    refused_object=("ShortageEvent", event_key))


def close_mitigation_task(con, user_id, task_id, resolution_note, now=None) -> ActionResult:
    params = {"task_id": task_id, "resolution_note": resolution_note}

    def actor_ok(con, p):
        t = _task(con, p["task_id"])
        a = p["_actor"]
        return t is not None and (a.role == "pharmacist" or a.user_id == t[3])

    criteria: list[Criterion] = [
        ("task_exists", lambda con, p: _task(con, p["task_id"]) is not None),
        ("actor_is_assignee_or_pharmacist", actor_ok),
        ("task_is_open", lambda con, p: _task(con, p["task_id"])[5] == "open"),
        ("note_min_10_chars", lambda con, p: isinstance(p["resolution_note"], str)
         and len(p["resolution_note"].strip()) >= MIN_NOTE_CHARS),
    ]

    def apply(con, p):
        t = _task(con, task_id)
        _write_task_edit(con, p, task_id, t[1], t[2], t[3], t[4], "closed", resolution_note)
        return {"state": t[5]}, {"task_id": task_id, "state": "closed"}

    return _run(con, user_id, "close_mitigation_task", params, task_id, criteria, apply, now,
                reason=resolution_note)


def reassign_task(con, user_id, task_id, new_assignee_id, reason, now=None) -> ActionResult:
    params = {"task_id": task_id, "new_assignee_id": new_assignee_id, "reason": reason}
    criteria: list[Criterion] = [
        ("task_exists", lambda con, p: _task(con, p["task_id"]) is not None),
        ("task_is_open", lambda con, p: _task(con, p["task_id"])[5] == "open"),
        ("assignee_differs", lambda con, p: _task(con, p["task_id"])[3] != p["new_assignee_id"]),
        ("assignee_active_staff", lambda con, p: _active_staff(con, p["new_assignee_id"])),
        ("reason_given", lambda con, p: _given(p["reason"])),
    ]

    def apply(con, p):
        t = _task(con, task_id)
        _write_task_edit(con, p, task_id, t[1], t[2], new_assignee_id, t[4], "open", reason)
        _outbox(con, p, "task_reassigned", {"task_id": task_id, "from": t[3], "to": new_assignee_id})
        return {"assignee_id": t[3]}, {"task_id": task_id, "assignee_id": new_assignee_id}

    return _run(con, user_id, "reassign_task", params, task_id, criteria, apply, now)
