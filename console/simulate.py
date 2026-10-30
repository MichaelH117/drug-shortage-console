"""SIMULATED demo usage, not real users (AE-04, AC-8.3).

Replays a fixed script of morning-huddle actions against the demo database, through ``console.actions`` (the only
write path), with fixed ``now`` values so the secondary metric (time from detection to first action) is
deterministic. Actors are the SYNTHETIC users in ``seed.app_user``. Every reason starts with ``[SIMULATED]`` so the
rows are easy to tell apart from clicks made in the app.

The script targets the queue of each fixture day by position in the sorted queue, not by hard-coded event keys,
and checks the drug name it expects, so a fixture change fails loudly instead of acting on the wrong event.

Run:  python -m console.simulate            (demo DB; refuses to run on the live DB)
It is idempotent: if any ``[SIMULATED]`` audit row exists, nothing is written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys

from console import actions
from console.queries import drug_object
from ontokit.actions import ActionRefused

LABEL = "SIMULATED demo usage, not real users"
PREFIX = "[SIMULATED] "
SEED = 42

# (day, minutes after 11:30 UTC = 07:30 America/New_York, actor, verb, args, expect)
# Targets: (old queue position, name fragment); only the fragment is used (see _event).
# "sub" targets the first substitute option of that queue row; "task" = the task opened earlier under that label.
SCRIPT: list[dict] = [
    # Day 2 (2026-10-29): first "since yesterday" queue
    {"day": "2026-10-29", "after_min": 22, "actor": "dana.ruiz", "verb": "open_task", "target": (0, "morphine"),
     "facility": "F-MAIN", "assignee": "lee.chen", "due_days": 2, "label": "t_morphine",
     "reason": "1 day on hand at Main; buyer to source from a secondary wholesaler"},
    {"day": "2026-10-29", "after_min": 40, "actor": "lee.chen", "verb": "propose", "target": (1, "fentanyl"),
     "reason": "Alternate labeler, same route and form, in stock at the wholesaler"},
    {"day": "2026-10-29", "after_min": 71, "actor": "lee.chen", "verb": "approve", "target": (1, "fentanyl"),
     "reason": "Trying to approve my own proposal", "expect": "role"},
    {"day": "2026-10-29", "after_min": 95, "actor": "dana.ruiz", "verb": "approve", "target": (1, "fentanyl"),
     "reason": "Reviewed concentration and labeling with P&T chair; approved for Main and North"},
    {"day": "2026-10-29", "after_min": 170, "actor": "sam.okafor", "verb": "open_task",
     "target": (5, "dexmedetomidine"),
     "facility": "F-NORTH", "assignee": "priya.nair", "due_days": 1, "label": "t_dexmed",
     "reason": "1 day on hand at North; request allocation"},
    {"day": "2026-10-29", "after_min": 210, "actor": "dana.ruiz", "verb": "open_task", "target": (4, "leuprolide"),
     "facility": "F-MAIN", "assignee": "alex.kim", "due_days": 4, "label": "t_leuprolide",
     "reason": "New posting; confirm open orders with the distributor"},
    {"day": "2026-10-29", "after_min": 270, "actor": "priya.nair", "verb": "propose", "target": (3, "potassium"),
     "reason": "Alternate labeler ER tablet; pharmacy to confirm the strength"},
    {"day": "2026-10-29", "after_min": 345, "actor": "sam.okafor", "verb": "approve", "target": (3, "potassium"),
     "reason": "Checked strength and ER form with P&T; approved with a dosing note"},
    {"day": "2026-10-30", "after_min": 60, "actor": "priya.nair", "verb": "close_task", "task": "t_dexmed",
     "note": "Allocation confirmed: 3-day supply received at North"},
    # Day 3 (2026-10-30): today's queue. Some rows are left untouched on purpose for the live demo.
    {"day": "2026-10-30", "after_min": 18, "actor": "dana.ruiz", "verb": "open_task", "target": (0, "furosemide"),
     "facility": "F-MAIN", "assignee": "lee.chen", "due_days": 2, "label": "t_furosemide",
     "reason": "Now unavailable; 5 days on hand at Main"},
    {"day": "2026-10-30", "after_min": 32, "actor": "lee.chen", "verb": "propose", "target": (1, "ketorolac"),
     "reason": "Alternate labeler in stock; strength to be confirmed by pharmacy"},
    {"day": "2026-10-30", "after_min": 45, "actor": "lee.chen", "verb": "reassign_task", "task": "t_furosemide",
     "assignee": "priya.nair", "reason": "Priya owns the North and Main injectables contract"},
    {"day": "2026-10-30", "after_min": 50, "actor": "jordan.blake", "verb": "open_task", "target": (3, "morphine"),
     "facility": "F-KIDS", "assignee": "alex.kim", "due_days": 3, "reason": "Viewer trying to open a task",
     "expect": "role"},
    {"day": "2026-10-30", "after_min": 80, "actor": "sam.okafor", "verb": "open_task", "target": (3, "morphine"),
     "facility": "F-KIDS", "assignee": "alex.kim", "due_days": 3, "label": "t_morphine_kids",
     "reason": "New posting; Kids has 5 days on hand"},
]


def already_simulated(con) -> bool:
    return bool(con.execute("SELECT count(*) FROM audit.action_log WHERE reason LIKE '[SIMULATED]%'").fetchone()[0])


def _now(day: str, after_min: int, rng: random.Random) -> dt.datetime:
    base = dt.datetime.fromisoformat(day + "T11:30:00")
    return base + dt.timedelta(minutes=after_min, seconds=rng.randint(0, 59))


def _event(con, day: str, target: tuple[int, str]) -> str:
    """The one stocked NEW/WORSENED event of that day whose name contains the fragment.

    Targets by name, not queue position, so an ADR 007 filter or rank change cannot point a step at the wrong
    event. Some steps act on Watch-list items (for example a NEW event still Available); that is realistic.
    """
    _, fragment = target
    rows = con.execute("""
        SELECT f.event_key FROM core.change_feed f JOIN obj.shortage_event e USING (event_key)
        WHERE f.snapshot_date = ?::DATE AND f.change_type IN ('NEW', 'WORSENED')
          AND f.prev_snapshot_date IS NOT NULL AND lower(e.generic_name) LIKE ?
          AND EXISTS (SELECT 1 FROM link.shortage_event_facility l WHERE l.event_key = f.event_key)""",
                       [day, f"%{fragment}%"]).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"simulation script out of date: {len(rows)} events match '{fragment}' on {day}")
    return str(rows[0][0])


def _substitute(con, event_key: str, state: str) -> dict:
    """First substitute of the event in ``state``; a 'candidate' must not itself be short (approve rule)."""
    for s in drug_object(con, event_key)["substitutes"]:
        if s["state"] == state and not (state == "candidate" and s["alt_is_short"]):
            return s
    raise RuntimeError(f"simulation: no {state} substitute for {event_key}")


def run_simulation(con, *, force_db_check: bool = True) -> dict:
    """Apply the script. Returns counts. Does nothing if the DB already has simulated rows."""
    if force_db_check:
        src = con.execute("SELECT count(*) FILTER (WHERE source = 'live') FROM stage.snapshot").fetchone()[0]
        if src:
            raise RuntimeError("refusing to simulate on a database with live snapshots; use the demo DB")
    if already_simulated(con):
        return {"skipped": True, "reason": "already simulated", "label": LABEL}
    rng = random.Random(SEED)
    tasks: dict[str, str] = {}
    out = {"skipped": False, "success": 0, "refused": 0, "label": LABEL}
    for step in SCRIPT:
        now = _now(step["day"], step["after_min"], rng)
        reason = PREFIX + step.get("reason", "")
        actor, verb = step["actor"], step["verb"]
        try:
            if verb == "open_task":
                ek = _event(con, step["day"], step["target"])
                due = (now.date() + dt.timedelta(days=step["due_days"])).isoformat()
                res = actions.open_mitigation_task(con, actor, ek, step["facility"], step["assignee"], due, reason,
                                                   now=now)
                if step.get("label"):
                    tasks[step["label"]] = res.object_key
            elif verb == "propose":
                ek = _event(con, step["day"], step["target"])
                alt = _substitute(con, ek, "candidate")["alt_ndc11"]
                actions.propose_substitute(con, actor, ek, alt, reason, now=now)
            elif verb == "approve":
                ek = _event(con, step["day"], step["target"])
                actions.approve_substitute(con, actor, _substitute(con, ek, "proposed")["substitute_id"], reason,
                                           now=now)
            elif verb == "close_task":
                actions.close_mitigation_task(con, actor, tasks[step["task"]], PREFIX + step["note"], now=now)
            elif verb == "reassign_task":
                actions.reassign_task(con, actor, tasks[step["task"]], step["assignee"], reason, now=now)
            else:
                raise ValueError(f"unknown verb {verb}")
        except ActionRefused as exc:
            if step.get("expect") != exc.criterion:
                raise RuntimeError(f"simulation step refused unexpectedly: {step} -> {exc.criterion}") from exc
            out["refused"] += 1
            continue
        if step.get("expect"):
            raise RuntimeError(f"simulation step should have been refused ({step['expect']}): {step}")
        out["success"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Replay {LABEL} into the demo DB, then refresh the impact metrics.")
    ap.parse_args(argv)
    from pipeline.config import connect, paths_for

    paths = paths_for("demo")
    con = connect(paths.db)
    try:
        result = run_simulation(con)
    finally:
        con.close()
    print(f"{LABEL}: {result}")
    from console.metrics import main as metrics_main

    return metrics_main([])


if __name__ == "__main__":
    sys.exit(main())
