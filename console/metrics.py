"""Impact metrics: primary metric, baseline, guardrails (a)-(e), and the secondary operational metric
(AE-04 / AE-05, AC-8.1 to 8.4). Definitions live in docs/METRICS.md; this module is their only implementation.

* Primary and guardrail (a) come from ``reports/match_quality.json`` (never typed by hand).
* Guardrails (b)-(e) and the secondary metric are computed from the database passed in.
* The secondary metric uses only ``[SIMULATED]`` audit rows and is labeled "SIMULATED demo usage, not real users".

``python -m console.metrics`` recomputes ``reports/impact_metrics.json`` from the demo DB (plus the live DB,
read-only, for guardrail (b) when it exists) and re-renders the marker block in ``docs/impact.md``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

from console.queries import queue_for_snapshot

ROOT = Path(__file__).resolve().parents[1]
IMPACT_MD = ROOT / "docs" / "impact.md"
REPORTS = ROOT / "reports"
MARK_START, MARK_END = "<!-- impact:start -->", "<!-- impact:end -->"
SIM_LABEL = "SIMULATED demo usage, not real users"

QUEUE_PRECISION_MIN = 0.90      # guardrail (a), SPEC AC-8.2 / EM decision E2
QUEUE_LENGTH_MAX = 40           # guardrail (b)
DQ_PASS_RATE_MIN = 0.80         # guardrail (e): and zero FAIL rows on the latest snapshot


def _status(ok: bool | None) -> str:
    return "N/A" if ok is None else ("PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------------------------- quality reports
def _overall(section: dict | None, key: str = "matcher") -> dict | None:
    if not section or key not in section:
        return None
    o = section[key]["overall"]
    return {k: o.get(k) for k in ("precision", "recall", "f1", "tp", "fp", "fn")}


# Post-hoc product category of each POSITIVE pair, by formulary item text. COPIED from the Principal Engineer's
# docs/img/make_charts.py (COMBO / PREMIX); not a frozen label, never used for tuning. Everything else is
# "Single ingredient". tests/test_metrics.py checks this copy still equals the source file.
CATEGORY_COMBO = {
    "v1": {"GP-036", "GP-037", "GP-049", "GP-050"},
    "v2": {"HP-005", "HP-013", "HP-051"},
    "v3": {"VP-001", "VP-004", "VP-006", "VP-009", "VP-010", "VP-013", "VP-014", "VP-016", "VP-022", "VP-037",
           "VP-043", "VP-045", "VP-048", "VP-050", "VP-051", "VP-054", "VP-056", "VP-058", "VP-059", "VP-062",
           "VP-064", "VP-088", "VP-090"},
}
CATEGORY_PREMIX = {"v1": set(), "v2": set(), "v3": {"VP-024", "VP-030", "VP-032", "VP-035"}}
CATEGORIES = ("Single ingredient", "Combination", "Premix (drug in D5W / saline)")
FAIL_SAFE_PRECISION = 0.95       # "fails safe" claim: precision at or above this on every scored set


def _categories(section: dict, version: str) -> list[dict] | None:
    """Recall per product category for one scored set: found / positives, from the frozen CSV and the report's
    failing pairs. None when the category list does not cover that version or the CSV is missing."""
    if version not in CATEGORY_COMBO:
        return None
    gold = ROOT / section["gold"]["file"]
    if not gold.exists():
        return None
    import csv

    lines = [ln for ln in gold.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    pos = [r["pair_id"] for r in csv.DictReader(lines) if r["label"] == "1"]
    fn = {f["pair_id"] for f in section["matcher"]["failing_pairs"] if f["error"] == "FN"}
    out = []
    for cat in CATEGORIES:
        ids = [x for x in pos if (cat == "Combination" and x in CATEGORY_COMBO[version])
               or (cat.startswith("Premix") and x in CATEGORY_PREMIX[version])
               or (cat == "Single ingredient" and x not in CATEGORY_COMBO[version]
                   and x not in CATEGORY_PREMIX[version])]
        found = sum(1 for x in ids if x not in fn)
        out.append({"category": cat, "found": found, "positives": len(ids),
                    "recall": round(found / len(ids), 4) if ids else None})
    return out


def _versions(reports_dir: Path | None) -> dict[int, dict]:
    import re

    out = {}
    if reports_dir is None or not Path(reports_dir).exists():
        return out
    for f in Path(reports_dir).glob("match_quality_v*.json"):
        m = re.fullmatch(r"match_quality_v(\d+)\.json", f.name)
        if m:
            out[int(m.group(1))] = json.loads(f.read_text(encoding="utf-8"))
    return out


def _unseen_block(rep: dict) -> tuple[str | None, dict]:
    """The block of a per-round report scored on a set its author never opened; v1's file IS that block."""
    for k, v in rep.items():
        if k.endswith("_unseen") and isinstance(v, dict):
            return k, v
    return None, rep


def load_quality(match_quality: dict, reports_dir: Path | None = None) -> dict:
    """Pick the headline match-quality run in ONE place (app pages, docs and metrics all use this).

    Headline = the NEWEST round scored on a blind set: the highest ``match_quality_vN.json`` in ``reports_dir``,
    its ``*_unseen`` block (v1's whole file was blind when it ran). History = every round's blind score, oldest
    first, plus the headline file's ``*_seen_reference`` blocks labeled "seen, optimistic". Without per-round
    files, the report passed in is the headline.
    """
    rounds = _versions(reports_dir)
    if not rounds:
        if any(k.endswith("_unseen") for k in match_quality):
            rounds = {0: match_quality}
        else:
            rounds = {1: match_quality}
    latest = max(rounds)
    history = []
    for n in sorted(rounds):
        key, block = _unseen_block(rounds[n])
        what = "gold set (blind at the time; frozen)" if key is None else \
            f"{block['gold']['file'].split('/')[-1].removesuffix('.csv')} (unseen)"
        history.append({"run": f"v{n} matcher, {what}" + (" HEADLINE" if n == latest else ""), "version": n,
                        "set": block["gold"]["file"], "seen": False, "headline": n == latest,
                        **_overall(block), "categories": _categories(block, f"v{n}")})
    head_rep = rounds[latest]
    head_key, head = _unseen_block(head_rep)
    for k, v in head_rep.items():
        if k.endswith("_seen_reference") and isinstance(v, dict):
            history.append({"run": f"v{latest} matcher, {v['gold']['file'].split('/')[-1].removesuffix('.csv')} "
                                   "(seen, optimistic)", "version": latest, "set": v["gold"]["file"],
                            "seen": True, "headline": False, **_overall(v), "categories": None})
    label = (f"v{latest} matcher on the unseen {head['gold']['file'].split('/')[-1].removesuffix('.csv')}"
             if head_key else f"v{latest} matcher on the gold set")
    baselines = []
    if "baseline_v2" in head:
        baselines.append({"name": "Fairer baseline: base-name equality",
                          "definition": head["baseline_v2"]["definition"], **_overall(head, "baseline_v2")})
    baselines.append({"name": "Naive baseline: exact name", "definition": head["baseline"].get("definition", ""),
                      **_overall(head, "baseline")})
    precisions = [h["precision"] for h in history if h["precision"] is not None]
    fail_safe = {"min_precision": min(precisions) if precisions else None,
                 "max_precision": max(precisions) if precisions else None,
                 "sets": len(precisions),
                 "holds": bool(precisions) and min(precisions) >= FAIL_SAFE_PRECISION}
    src = f"reports/match_quality_v{latest}.json" if latest in _versions(reports_dir) else "reports/match_quality.json"
    return {"head": head, "label": label, "source": src, "unseen": head_key is not None or latest == 1,
            "version": latest, "history": history, "baselines": baselines,
            "categories": _categories(head, f"v{latest}"), "fail_safe": fail_safe, "note": head_rep.get("note")}


# ---------------------------------------------------------------------------------------------- primary
def primary_metric(q: dict) -> dict:
    head = q["head"]
    m = _overall(head)
    fair = q["baselines"][0]
    target = head.get("targets", {}).get("recall", 0.95)
    recall = m["recall"]
    return {
        "name": "Recall of affected formulary items surfaced in the morning queue",
        "run": q["label"],
        "measured_on": (f"{head['gold']['file']} ({head['gold']['pairs']} pairs, {head['gold']['positives']} "
                        f"positives); {head['gold'].get('reviewed_by')}"),
        "matcher_recall": recall, "matcher_precision": m["precision"], "matcher_f1": m["f1"],
        "matcher_tp": m["tp"], "matcher_fn": m["fn"],
        "baseline_name": fair["name"], "baseline_definition": fair["definition"],
        "baseline_recall": fair["recall"], "baseline_precision": fair["precision"], "baseline_tp": fair["tp"],
        "lift_vs_baseline": None if recall is None or fair["recall"] is None else round(recall - fair["recall"], 4),
        "target": target, "status": _status(None if recall is None else recall >= target),
        "gold_sha256": head["gold"]["sha256"], "gold_hash_ok": head["gold"]["hash_ok"],
        "source": q["source"], "history": q["history"], "baselines": q["baselines"],
        "categories": q["categories"], "fail_safe": q["fail_safe"],
    }


# ---------------------------------------------------------------------------------------------- guardrails
def _guard_a(q: dict) -> dict:
    o = _overall(q["head"])
    p = o["precision"]
    return {"id": "a", "name": f"Queue precision ({q['label']})", "protects": "alert fatigue",
            "value": p, "threshold": f">= {QUEUE_PRECISION_MIN}",
            "status": _status(None if p is None else p >= QUEUE_PRECISION_MIN),
            "detail": f"fp {o['fp']} of {o['tp'] + o['fp']} predicted matches"}


def _queue_length_stats(con) -> dict:
    snaps = con.execute("SELECT snapshot_date, source FROM stage.snapshot ORDER BY snapshot_date").fetchall()
    live = [d for d, s in snaps if s == "live"]
    latest = snaps[-1][0] if snaps else None
    return {"live_snapshots": len(live), "latest_live": live[-1] if live else None, "latest": latest,
            "latest_source": snaps[-1][1] if snaps else None}


def _queue_sizes(con, snapshot_date) -> dict:
    """Uncapped daily queue, Watch list and baseline worklist sizes for the latest snapshot of ``con``."""
    from console.queries import baseline_worklist, is_baseline_day, watch_list

    return {"queue": len(queue_for_snapshot(con, snapshot_date)), "watch": len(watch_list(con)),
            "worklist": len(baseline_worklist(con)), "baseline_day": is_baseline_day(con)}


def _guard_b(con, live_con=None) -> dict:
    """ADR 007: measured on the UNCAPPED actionable daily queue (the UI's top-40 cap cannot improve it), with the
    Watch-list size beside it so tightening the filter shows. A baseline day has no daily queue: N/A, and the
    one-time item-grain worklist is reported separately, not held to 40."""
    g = {"id": "b", "name": "Daily queue length, uncapped (latest live snapshot)",
         "protects": "a queue too long to use", "threshold": f"<= {QUEUE_LENGTH_MAX}"}
    own = _queue_length_stats(con)
    demo = _queue_sizes(con, own["latest"]) if own["latest"] else {"queue": 0, "watch": 0, "worklist": 0}
    g["demo_queue_length"], g["demo_watch_length"] = demo["queue"], demo["watch"]
    g["demo_note"] = f"SYNTHETIC fixture history, snapshot {own['latest']}" if own["latest_source"] != "live" else None
    src_con, st = (con, own) if own["live_snapshots"] else ((live_con, _queue_length_stats(live_con))
                                                            if live_con is not None else (None, None))
    if src_con is None or not st["live_snapshots"]:
        g.update(value=None, status="N/A", detail="n/a: no live snapshot available to this computation")
        return g
    z = _queue_sizes(src_con, st["latest_live"])
    g.update(live_queue_length=z["queue"], live_watch_length=z["watch"], live_worklist_length=z["worklist"])
    if z["baseline_day"]:
        g.update(value=None, status="N/A",
                 detail=(f"baseline day ({st['latest_live']}): no previous snapshot, so the daily queue is empty "
                         f"by design (ADR 007). One-time baseline worklist: {z['worklist']} formulary items "
                         f"(reported, not held to 40). Watch list: {z['watch']} events."))
        return g
    g.update(value=z["queue"], status=_status(z["queue"] <= QUEUE_LENGTH_MAX),
             detail=(f"live snapshot {st['latest_live']}; Watch list {z['watch']} events (filter check)"
                     + ("" if z["queue"] <= QUEUE_LENGTH_MAX else "; over 40: the queue is not usable as-is")))
    return g


def _guard_c(con) -> dict:
    from ontokit.audit import verify_chain

    r = verify_chain(con)
    return {"id": "c", "name": "Audit chain verifies", "protects": "quiet edits to the action log",
            "value": "OK" if r.ok else f"BROKEN at seq {r.first_broken_seq}", "threshold": "OK",
            "status": _status(r.ok), "detail": f"{r.rows_checked} rows checked" + (f"; {r.reason}" if r.reason else "")}


def _guard_d(con) -> dict:
    from console.actions import ontology

    onto = ontology()
    rows = con.execute("SELECT action_type, role, outcome, failed_criterion FROM audit.action_log").fetchall()
    bad = [r for r in rows if r[2] == "SUCCESS" and (r[0] not in onto.actions or r[1] not in onto.actions[r[0]].roles)]
    refused_role = sum(1 for r in rows if r[2] == "REFUSED" and r[3] == "role")
    return {"id": "d", "name": "Actions executed by a role that is not allowed", "protects": "role rules bypassed",
            "value": len(bad), "threshold": "= 0", "status": _status(len(bad) == 0),
            "detail": f"{sum(1 for r in rows if r[2] == 'SUCCESS')} successful actions checked; "
                      f"{refused_role} attempts refused on role (blocked, not executed)"}


def _guard_e(con) -> dict:
    rows = con.execute("""
        SELECT status, count(*) FROM core.dq_results
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM core.dq_results) GROUP BY 1""").fetchall()
    c = dict(rows)
    checked = sum(v for k, v in c.items() if k != "SKIPPED")
    rate = round(c.get("PASS", 0) / checked, 4) if checked else None
    fails = c.get("FAIL", 0)
    ok = None if rate is None else (fails == 0 and rate >= DQ_PASS_RATE_MIN)
    return {"id": "e", "name": "Data-quality checks pass rate (latest snapshot)", "protects": "bad input data",
            "value": rate, "threshold": f">= {DQ_PASS_RATE_MIN} and 0 FAIL", "status": _status(ok),
            "detail": ", ".join(f"{k} {v}" for k, v in sorted(c.items()))}


# ---------------------------------------------------------------------------------------------- secondary
def detection_to_first_action(con) -> dict:
    """Minutes from the snapshot that first put an event in the queue to the first SUCCESS action on it.

    Only snapshots that have a previous snapshot count (on the first one every event is NEW). Only
    ``[SIMULATED]`` rows count, so app clicks during a demo never change the number.
    """
    snaps = con.execute("SELECT snapshot_date, fetched_at_utc FROM stage.snapshot ORDER BY snapshot_date").fetchall()
    entered: dict[str, dt.datetime] = {}
    for d, fetched in snaps[1:]:
        for ek in queue_for_snapshot(con, d)["event_key"]:
            entered.setdefault(ek, pd.Timestamp(fetched).to_pydatetime())
    first = dict(con.execute("""
        WITH acts AS (
            SELECT a.ts_utc, coalesce(s.event_key, t.event_key, a.object_key) AS event_key
            FROM audit.action_log a
            LEFT JOIN (SELECT DISTINCT substitute_id, event_key FROM obj.substitute) s ON s.substitute_id = a.object_key
            LEFT JOIN (SELECT DISTINCT task_id, event_key FROM ops.mitigation_task_edit) t ON t.task_id = a.object_key
            WHERE a.outcome = 'SUCCESS' AND a.reason LIKE '[SIMULATED]%')
        SELECT event_key, min(ts_utc) FROM acts GROUP BY 1""").fetchall())
    minutes = []
    per_event = []
    for ek, t0 in sorted(entered.items()):
        t1 = first.get(ek)
        m = None if t1 is None or t1 < t0 else round((pd.Timestamp(t1).to_pydatetime() - t0).total_seconds() / 60, 1)
        if m is not None:
            minutes.append(m)
        per_event.append({"event_key": ek, "detected_utc": t0.isoformat(),
                          "first_action_utc": None if t1 is None else pd.Timestamp(t1).isoformat(), "minutes": m})
    out = {"name": "Time from detection to first action per affected shortage", "label": SIM_LABEL,
           "affected_events": len(entered), "actioned_events": len(minutes),
           "not_actioned_events": len(entered) - len(minutes),
           "median_minutes": statistics.median(minutes) if minutes else None,
           "p90_minutes": (round(statistics.quantiles(minutes, n=10, method="inclusive")[-1], 1)
                           if len(minutes) >= 2 else (minutes[0] if minutes else None)),
           "max_minutes": max(minutes) if minutes else None, "per_event": per_event}
    if not first:
        out["detail"] = "n/a: no simulated actions in this database; run python -m console.simulate"
    return out


# ---------------------------------------------------------------------------------------------- entry points
def compute_impact_metrics(con, match_quality: dict, live_con=None,
                           reports_dir: Path | None = None) -> dict:
    """Everything in ``reports/impact_metrics.json``. Pure read: never writes to the database.

    ``reports_dir`` (default: the repo's reports/) is where the v1 and v2 match-quality files are looked up.
    """
    q = load_quality(match_quality, REPORTS if reports_dir is None else reports_dir)
    guards = [_guard_a(q), _guard_b(con, live_con), _guard_c(con), _guard_d(con), _guard_e(con)]
    snap = con.execute("SELECT max(snapshot_date) FROM stage.snapshot").fetchone()[0]
    return {
        "as_of_snapshot": None if snap is None else str(snap),
        "primary": primary_metric(q),
        "guardrails": guards,
        "guardrails_failing": [g["id"] for g in guards if g["status"] == "FAIL"],
        "secondary": detection_to_first_action(con),
        "notes": [
            "Hospital data (facilities, formulary, stock, users, tasks) is SYNTHETIC.",
            "Gold set and holdout are team-labeled (qa-engineer), reviewed_by pending; not owner-reviewed.",
            "Headline = newest matcher on a set its author never opened. Scores on the already-seen gold set "
            "are labeled 'seen, optimistic' and are history, not the headline.",
            "The naive exact-name baseline gets 0 because openFDA generic_name carries the dosage form; the "
            "fairer base-name baseline is the comparison.",
        ],
    }


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def render_impact_block(m: dict) -> str:
    p, s = m["primary"], m["secondary"]
    lines = [MARK_START,
             "<!-- generated by `python -m console.metrics` from reports/match_quality*.json and the demo DB; "
             "do not edit by hand -->",
             "",
             f"**Primary metric, headline: {p['run']}** ({p['measured_on']}, sha256 `{p['gold_sha256'][:12]}...`, "
             f"hash ok: {p['gold_hash_ok']}; source {p['source']})",
             "",
             "| | Precision | Recall | F1 | Recall target | Status |", "|---|---|---|---|---|---|",
             f"| Console matcher | {_fmt(p['matcher_precision'])} | **{_fmt(p['matcher_recall'])}** "
             f"({p['matcher_tp']} of {p['matcher_tp'] + p['matcher_fn']}) | {_fmt(p['matcher_f1'])} | "
             f">= {p['target']} | **{p['status']}** |"]
    for bl in p["baselines"]:
        lines.append(f"| {bl['name']} | {_fmt(bl['precision'])} | {_fmt(bl['recall'])} | {_fmt(bl['f1'])} | | |")
    if p.get("categories"):
        lines += ["", "**Headline recall by product category** (post-hoc split by item text, from "
                      "docs/img/make_charts.py)", "", "| Category | Found / positives | Recall |", "|---|---|---|"]
        for c in p["categories"]:
            lines.append(f"| {c['category']} | {c['found']} / {c['positives']} | {_fmt(c['recall'])} |")
    fs = p.get("fail_safe") or {}
    if fs.get("sets"):
        lines += ["", f"**Fails safe:** precision is {_fmt(fs['min_precision'])} to {_fmt(fs['max_precision'])} "
                      f"on all {fs['sets']} scored sets. When the matcher is unsure it misses (lower recall); it "
                      "rarely puts a wrong drug in the queue."]
    lines += ["", "**History** (only unseen runs count against the target)", "",
              "| Run | Precision | Recall | F1 |", "|---|---|---|---|"]
    for h in p["history"]:
        lines.append(f"| {h['run']} | {_fmt(h['precision'])} | {_fmt(h['recall'])} | {_fmt(h['f1'])} |")
    lines += ["", "**Guardrails**", "",
              "| | Guardrail | Value | Threshold | Status | Detail |", "|---|---|---|---|---|---|"]
    for g in m["guardrails"]:
        lines.append(f"| ({g['id']}) | {g['name']} | {_fmt(g['value'])} | {g['threshold']} | {g['status']} | "
                     f"{g['detail']} |")
    b = next(g for g in m["guardrails"] if g["id"] == "b")
    lines += ["", f"Demo daily queue (SYNTHETIC fixture history, snapshot {m['as_of_snapshot']}): "
                  f"{b['demo_queue_length']} actionable items, uncapped; Watch list {b['demo_watch_length']}.", "",
              f"**Secondary metric: {s['name']}** ({s['label']})", "",
              "| Affected events | Actioned | Not actioned | Median minutes | p90 minutes | Max minutes |",
              "|---|---|---|---|---|---|",
              f"| {s['affected_events']} | {s['actioned_events']} | {s['not_actioned_events']} | "
              f"{_fmt(s['median_minutes'])} | {_fmt(s['p90_minutes'])} | {_fmt(s['max_minutes'])} |",
              MARK_END]
    return "\n".join(lines)


def render_impact_md(m: dict, path: Path = IMPACT_MD) -> bool:
    """Replace the marker block in docs/impact.md. Returns False if the file or the markers are missing."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if MARK_START not in text or MARK_END not in text:
        return False
    head, rest = text.split(MARK_START, 1)
    _, tail = rest.split(MARK_END, 1)
    path.write_text(head + render_impact_block(m) + tail, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Recompute reports/impact_metrics.json and docs/impact.md.")
    ap.add_argument("--db", choices=["demo", "live"], default="demo")
    args = ap.parse_args(argv)
    from pipeline.config import connect, paths_for

    paths = paths_for(args.db)
    mq = json.loads((paths.reports / "match_quality.json").read_text(encoding="utf-8"))
    con = connect(paths.db, read_only=True)
    live_con = None
    live_db = paths_for("live").db
    try:
        if args.db == "demo" and live_db.exists():
            live_con = connect(live_db, read_only=True)
        m = compute_impact_metrics(con, mq, live_con=live_con, reports_dir=paths.reports)
    finally:
        con.close()
        if live_con is not None:
            live_con.close()
    paths.reports.mkdir(parents=True, exist_ok=True)
    (paths.reports / "impact_metrics.json").write_text(json.dumps(m, indent=2, default=str) + "\n",
                                                       encoding="utf-8", newline="\n")
    rendered = render_impact_md(m)
    print(f"impact metrics: {m['primary']['run']}: {m['primary']['status']} (recall "
          f"{m['primary']['matcher_recall']} vs baseline "
          f"{m['primary']['baseline_recall']}); guardrails failing: {m['guardrails_failing'] or 'none'}; "
          f"docs/impact.md {'updated' if rendered else 'not updated (missing markers)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
