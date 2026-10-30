"""Generic action runner with role rules, submission criteria and one transaction (ML-04, ADR 003).

Sequence (DESIGN section 4):
1. BEGIN. 2. Role check (actor active and role allowed), else criterion "role".
3. Criteria in declared order; the first failure is recorded by its id.
4. Refused: ROLLBACK, then a separate transaction appends one REFUSED audit row, then raise.
5. Allowed: ``apply`` writes edit rows, then ``audit.append(SUCCESS)``, then COMMIT.
6. Any exception in 5: ROLLBACK and re-raise. No edit and no audit row remain (AC-6.3).

``apply`` and the criteria receive ``params`` plus three runner keys: ``_actor`` (Actor), ``_now`` (naive UTC
datetime) and ``_action_seq`` (the seq the SUCCESS audit row will get, so edit rows can point at it).

Domain-free: must not import pipeline, console or app.
"""
from __future__ import annotations

import datetime as dt
import decimal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Criterion = tuple[str, Callable[[Any, dict], bool]]


class ActionRefused(Exception):
    """A submission criterion failed. ``criterion`` is its id (or "role")."""

    def __init__(self, criterion: str, message: str | None = None):
        self.criterion = criterion
        super().__init__(message or f"refused: {criterion}")


class ActionPermissionError(ActionRefused, PermissionError):
    """The actor is inactive or their role may not run this action."""


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    active: bool


@dataclass(frozen=True)
class ActionResult:
    outcome: str
    seq: int
    object_key: str
    new_value: dict


def _audit():
    from ontokit import audit  # imported late so tests can swap in a fake before DE-08 lands

    return audit


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def jsonable(v):
    """Make params/old/new values JSON-safe for the audit row (dates -> ISO strings)."""
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items() if not str(k).startswith("_")}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _next_seq(con) -> int:
    """Next audit seq. Only a missing log table is tolerated (REVIEW 9); any other error raises."""
    import duckdb

    try:
        return int(con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit.action_log").fetchone()[0])
    except duckdb.CatalogException:
        return 1


def run_action(con, spec, actor: Actor, params: dict, *, object_key: str,
               criteria: list[Criterion], apply: Callable[[Any, dict], tuple[dict, dict]],
               now: dt.datetime | None = None, reason: str | None = None,
               refused_object: tuple[str, str] | None = None) -> ActionResult:
    """Run one action as one transaction. Raises ActionPermissionError / ActionRefused when refused.

    ``reason`` is the text for the audit row's reason column; the caller names it (REVIEW 10). If omitted,
    ``params["reason"]`` is used. ``refused_object`` = (object_type, object_key) to log a REFUSED row against
    when ``object_key`` only exists on success (REVIEW 3, e.g. a task id that is assigned only if the open works).

    The whole action runs under ``audit.writer_lock()`` (DE fix 2026-10-30, flaky concurrent QA tests): without it
    two threads read the same chain tail and the loser died with a raw ConstraintException instead of a REFUSED row.
    """
    with _audit().writer_lock():
        return _run_action_locked(con, spec, actor, params, object_key=object_key, criteria=criteria, apply=apply,
                                  now=now, reason=reason, refused_object=refused_object)


def _run_action_locked(con, spec, actor: Actor, params: dict, *, object_key: str,
                       criteria: list[Criterion], apply: Callable[[Any, dict], tuple[dict, dict]],
                       now: dt.datetime | None = None, reason: str | None = None,
                       refused_object: tuple[str, str] | None = None) -> ActionResult:
    now = now or _utcnow()
    audit = _audit()
    p = {**params, "_actor": actor, "_now": now}
    if reason is None:
        reason = params.get("reason")
    common = dict(action_type=spec.name, object_type=spec.object_type, object_key=str(object_key),
                  actor_id=actor.user_id, role=actor.role, reason=None if reason is None else str(reason), now=now)

    con.execute("BEGIN TRANSACTION")
    failed: str | None = None
    try:
        if not actor.active or actor.role not in spec.roles:
            failed = "role"
        else:
            for cid, pred in criteria:
                if not pred(con, p):
                    failed = cid
                    break
    except BaseException:
        con.execute("ROLLBACK")
        raise

    if failed is not None:
        con.execute("ROLLBACK")
        refused = dict(common)
        if refused_object is not None:
            refused["object_type"], refused["object_key"] = refused_object[0], str(refused_object[1])
        con.execute("BEGIN TRANSACTION")
        try:
            seq = audit.append(con, old_value=None, new_value=jsonable(params), outcome="REFUSED",
                               failed_criterion=failed, **refused)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        why = ("inactive user" if not actor.active else f"role {actor.role!r} may not run {spec.name}") \
            if failed == "role" else f"{spec.name} refused: criterion {failed!r} not met"
        exc = ActionPermissionError if failed == "role" else ActionRefused
        err = exc(failed, why)
        err.seq = seq
        raise err

    try:
        expected = _next_seq(con)
        p["_action_seq"] = expected
        old, new = apply(con, p)
        seq = audit.append(con, old_value=jsonable(old), new_value=jsonable(new), outcome="SUCCESS",
                           failed_criterion=None, **common)
        if int(seq) != expected:
            raise RuntimeError(f"audit seq {seq} != expected {expected}; edit rows would point at the wrong row")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return ActionResult("SUCCESS", int(seq), str(object_key), jsonable(new))
