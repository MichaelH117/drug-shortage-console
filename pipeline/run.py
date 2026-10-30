"""One command: snapshots -> stage -> DQ -> history / change feed / events -> SYNTHETIC hospital -> matching ->
ontology views -> evaluation -> results (owner: data-engineer, DE-07, DESIGN section 4, ADR 004).

    python -m pipeline.run --fixtures          offline demo DB from the SYNTHETIC fixture days (data/demo/)
    python -m pipeline.run                     live DB from data/live/raw (uses today's cache, else pulls api.fda.gov)
    python -m pipeline.run --pull-only         save today's snapshot only (what a scheduler would call); no DB change
    python -m pipeline.run --refresh           re-pull today even if cached (old folder kept in raw/_superseded/)
    python -m pipeline.run --fixtures --test   also run pytest and record the counts in docs/results.json

Every run is a full deterministic rebuild of the pipeline-owned schemas (stage, core, seed, obj, link, mart) from all
snapshot folders. ops.* and audit.* (user edits and the action log) are never dropped (AC-6.4).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from generator.hospital import generate
from generator.hospital import write as write_seed
from pipeline.changes import build_change_feed
from pipeline.config import ROOT, DatabaseInUse, Paths, connect, load_availability_map, load_settings, paths_for
from pipeline.dq import run_dq
from pipeline.history import build_events, build_history
from pipeline.http import HttpClient, default_client
from pipeline.ingest import parse_all, snapshot_rows
from pipeline.schema import DQ_DDL, init_action_schemas, load_table, reset_pipeline_schemas
from pipeline.snapshots import ensure_snapshot, list_snapshots, local_today, valid_snapshots

RESULTS = ROOT / "docs" / "results.json"
# Columns that legitimately change between identical runs (wall-clock times). Excluded from content hashes only.
VOLATILE_COLUMNS = {"checked_at_utc"}


def run_id_for(refs) -> str:
    """Deterministic run id: a hash of the snapshot set (dates + records sha256)."""
    basis = "|".join(f"{r.snapshot_date}:{r.manifest['sha256']['records.json']}" for r in refs)
    return "run-" + hashlib.sha256(basis.encode()).hexdigest()[:12]


def table_fingerprints(con, schemas=("stage", "core", "seed")) -> dict[str, dict]:
    """``{schema.table: {"rows": n, "sha256": h}}`` over sorted content (volatile columns excluded). AC-2.2."""
    out = {}
    tables = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' "
        f"AND table_schema IN ({', '.join('?' * len(schemas))}) ORDER BY 1, 2", list(schemas)).fetchall()
    for schema, name in tables:
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position", [schema, name]).fetchall() if r[0] not in VOLATILE_COLUMNS]
        df = con.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM {schema}.{name}').df()
        df = df.astype(str).sort_values(cols).reset_index(drop=True) if len(df) else df
        h = hashlib.sha256(df.to_csv(index=False, lineterminator="\n").encode()).hexdigest()
        out[f"{schema}.{name}"] = {"rows": len(df), "sha256": h}
    return out


def _generator_detection(con) -> dict | None:
    """Score core.formulary_match against the generator's KNOWN injected variants (seed.variant_manifest).
    This is a generator-based check, separate from the QA gold set in reports/match_quality.json."""
    try:
        df = con.execute("""
            WITH src AS (
                SELECT DISTINCT m.item_id, m.variant_type, m.expected_match, e.event_key
                FROM seed.variant_manifest m
                LEFT JOIN core.shortage_event e
                  ON e.package_ndc_raw = m.source_package_ndc
                 AND strftime(e.initial_posting_date, '%m/%d/%Y') = m.source_initial_posting_date)
            SELECT s.variant_type, s.expected_match,
                   count(DISTINCT s.item_id) AS items,
                   count(DISTINCT CASE WHEN s.expected_match = 1 AND fm.event_key IS NOT NULL THEN s.item_id END)
                       AS detected,
                   count(DISTINCT CASE WHEN s.expected_match = 0 AND any_m.item_id IS NOT NULL THEN s.item_id END)
                       AS false_matches
            FROM src s
            LEFT JOIN core.formulary_match fm ON fm.item_id = s.item_id AND fm.event_key = s.event_key
            LEFT JOIN (SELECT DISTINCT item_id FROM core.formulary_match) any_m ON any_m.item_id = s.item_id
            GROUP BY 1, 2 ORDER BY 1""").df()
    except Exception as exc:  # matching tables not built (ML stage missing)
        return {"skipped": str(exc)[:200]}
    pos = df[df["expected_match"] == 1]
    neg = df[df["expected_match"] == 0]
    return {
        "note": "generator-based (known injected variants), not the QA gold set",
        "positives": int(pos["items"].sum()), "detected": int(pos["detected"].sum()),
        "recall": round(float(pos["detected"].sum() / pos["items"].sum()), 4) if len(pos) else None,
        "negatives": int(neg["items"].sum()), "false_matches": int(neg["false_matches"].sum()),
        "by_variant": {r.variant_type: {"items": int(r.items), "detected": int(r.detected),
                                        "false_matches": int(r.false_matches)} for r in df.itertuples()},
    }


def _build_ml(con, summary: dict) -> None:
    """ML-owned stage: matching tables and ontology views (ML-06)."""
    from console.actions import init_ops
    from ontokit.ontology import load_ontology
    from pipeline.objects import build_matching, create_object_views

    init_ops(con)                      # ops.* and audit.* IF NOT EXISTS, before the views that read them
    summary["matching"] = build_matching(con)
    onto = load_ontology(ROOT / "ontology" / "ontology.yaml")
    create_object_views(con, onto)
    from ontokit.ontology import check_backing

    summary["ontology_problems"] = check_backing(con, onto)


def build(paths: Paths, *, client: HttpClient | None, refresh: bool = False, seed: int = 42,
          run_date: dt.date | None = None, evaluate: bool = True) -> dict:
    """Full rebuild of ``paths.db``. Returns a summary dict (counts, DQ, change counts, fingerprints).

    ``client`` is used only when the snapshot for ``run_date`` (default: today, America/New_York) is missing from
    a live raw folder, or ``refresh`` is set. Demo mode (raw = fixture snapshots) never calls it.
    """
    t0 = time.perf_counter()
    from pipeline.config import FIXTURE_SNAPSHOTS

    demo = Path(paths.raw).resolve() == FIXTURE_SNAPSHOTS.resolve()
    if not demo:
        ensure_snapshot(paths.raw, run_date or local_today(), client, refresh=refresh)
    refs = list_snapshots(paths.raw)
    valid = valid_snapshots(refs)
    if not valid:
        raise RuntimeError(f"no valid snapshot folders in {paths.raw}")
    mapping = load_availability_map()
    stage = parse_all(valid, mapping)
    snaps = snapshot_rows(valid)
    dates = [r.snapshot_date for r in valid]
    history = build_history(stage, dates)
    feed = build_change_feed(stage, dates)
    events = build_events(stage, dates)
    tables = generate(seed)
    run_id = run_id_for(valid)

    con = connect(paths.db)
    summary: dict = {"run_id": run_id, "mode": "demo" if demo else "live", "db": str(paths.db), "seed": seed}
    try:
        con.begin()
        reset_pipeline_schemas(con)
        init_action_schemas(con)
        load_table(con, "stage.snapshot", snaps)
        load_table(con, "stage.shortage_record", stage)
        con.execute(DQ_DDL)
        dq = run_dq(con, run_id, refs, stage)
        load_table(con, "core.shortage_history", history)
        load_table(con, "core.change_feed", feed)
        load_table(con, "core.shortage_event", events)
        for name, df in tables.items():
            load_table(con, f"seed.{name}", df)
        # REVIEW #5: matching and view creation run in the SAME transaction, so a failure there rolls back the
        # whole rebuild and the previous build (tables and obj/link/mart views) stays intact.
        _build_ml(con, summary)
        con.commit()
        write_seed(tables, paths.seed)     # seed CSVs only after a successful commit
        latest = max(dates)
        summary.update({
            "snapshots": [{"date": r.snapshot_date.isoformat(), "folder": r.path.name, "source": r.manifest["source"],
                           "records": r.manifest["record_count"]} for r in valid],
            "skipped_snapshots": [{"folder": r.path.name, "problem": r.problem} for r in refs if r.problem],
            "latest_snapshot": latest.isoformat(),
            "meta_last_updated": valid[-1].manifest.get("meta_last_updated"),
            "change_counts_latest": feed[feed["snapshot_date"] == latest]["change_type"].value_counts()
            .sort_index().to_dict(),
            "dq": dq.groupby("status").size().to_dict(),
            "dq_fail": dq[dq["status"] == "FAIL"][["snapshot_date", "check_name", "detail"]]
            .astype(str).to_dict("records"),
            "generator_detection": _generator_detection(con),
        })
        if evaluate:
            summary["match_quality"] = _write_match_quality(paths)
        summary["tables"] = table_fingerprints(con)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    summary["seconds"] = round(time.perf_counter() - t0, 2)
    return summary


def _write_match_quality(paths: Paths) -> dict:
    """ML-07 evaluation on the frozen gold set -> ``<reports>/match_quality.json``. Returns the headline."""
    from pipeline.evaluate import run_evaluation

    report = run_evaluation()
    paths.reports.mkdir(parents=True, exist_ok=True)
    (paths.reports / "match_quality.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8",
                                                      newline="\n")
    try:
        from console.metrics import compute_impact_metrics  # AE-04; optional until delivered

        con = connect(paths.db)
        try:
            metrics = compute_impact_metrics(con, report)
        finally:
            con.close()
        (paths.reports / "impact_metrics.json").write_text(json.dumps(metrics, indent=2, default=str) + "\n",
                                                           encoding="utf-8", newline="\n")
    except ImportError:
        pass
    m, b = report["matcher"]["overall"], report["baseline"]["overall"]
    # BUG-PE-02: the gold set drove error analysis, so its score is a regression check ("seen, optimistic"), never
    # the headline. The headline is the newest UNSEEN holdout from reports/match_quality_v*.json.
    return {"holdout_unseen": latest_unseen_holdout(ROOT / "reports"),
            "gold_seen": {"label": "seen, optimistic (regression check only)",
                          "matcher": {k: m[k] for k in ("precision", "recall", "f1")},
                          "baseline": {k: b[k] for k in ("precision", "recall", "f1")},
                          "gold_hash_ok": report["gold"]["hash_ok"]}}


def latest_unseen_holdout(reports_dir: Path) -> dict | None:
    """Headline matcher score: the newest ``match_quality_v<N>.json`` (highest N) that has a section whose key
    contains ``unseen``. Returns None if there is none (then the README shows no headline, not the seen score)."""
    found = []
    for f in Path(reports_dir).glob("match_quality_v*.json"):
        m = re.fullmatch(r"match_quality_v(\d+)\.json", f.name)
        if m:
            found.append((int(m.group(1)), f))
    for version, f in sorted(found, reverse=True):
        data = json.loads(f.read_text(encoding="utf-8"))
        keys = [k for k in data if "unseen" in k and isinstance(data[k], dict)]
        if not keys:
            continue
        sec = data[keys[0]]
        mo, bo = sec["matcher"]["overall"], (sec.get("baseline") or {}).get("overall") or {}
        return {"version": f"v{version}", "file": f"reports/{f.name}", "section": keys[0],
                "pairs": (sec.get("gold") or {}).get("pairs", mo.get("pairs")),
                "matcher": {k: mo.get(k) for k in ("precision", "recall", "f1")},
                "baseline": {k: bo.get(k) for k in ("precision", "recall", "f1")}}
    return None


def run_pytest() -> dict:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                          capture_output=True, text=True)
    lines = proc.stdout.strip().splitlines()
    tail = lines[-1] if lines else ""
    counts = {k: int(m.group(1)) if (m := re.search(rf"(\d+) {k}", tail)) else 0
              for k in ("passed", "failed", "skipped", "error")}
    if proc.returncode not in (0, 1):
        print(proc.stdout[-3000:], proc.stderr[-2000:])
    print("pytest:", tail)
    return {**counts, "summary": tail, "date": time.strftime("%Y-%m-%d")}


def _fmt(v) -> str:
    return "n/a (no matches)" if v is None else str(v)


def write_readme_results(r: dict) -> None:
    """Rewrite the README block between the results markers (the README itself is owned by PE/OPS)."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8")
    if "<!-- results:start -->" not in text:
        return
    s, t = r["summary"], r.get("tests")
    lines = ["| Result | Value |", "|---|---|",
             f"| Snapshots (latest) | **{len(s['snapshots'])}** ({s['latest_snapshot']}, {s['mode']}) |",
             f"| Changes in latest snapshot | {', '.join(f'{k} {v}' for k, v in s['change_counts_latest'].items())} |",
             f"| DQ checks | {', '.join(f'{k} {v}' for k, v in sorted(s['dq'].items()))} |"]
    mq = s.get("match_quality")
    if mq:
        h, g = mq.get("holdout_unseen"), mq["gold_seen"]
        if h:
            lines.append(f"| **Matcher P / R, unseen holdout {h['version']}** ({h['pairs']} pairs, team-labeled) | "
                         f"**{h['matcher']['precision']} / {h['matcher']['recall']}** vs baseline "
                         f"{_fmt(h['baseline']['precision'])} / {_fmt(h['baseline']['recall'])} ({h['file']}) |")
        lines.append(f"| Gold set (seen, optimistic; regression check only) | {g['matcher']['precision']} / "
                     f"{g['matcher']['recall']} |")
    if t:
        lines.append(f"| pytest | **{t['passed']} passed**, {t['failed']} failed, {t['skipped']} skipped "
                     f"({t['date']}) |")
    note = ("\nNumbers from `python -m pipeline.run --fixtures --test`, stored in "
            "[docs/results.json](docs/results.json). Shortage records are public openFDA data; "
            "the demo history days and the hospital are SYNTHETIC.\n")
    new = re.sub(r"<!-- results:start -->.*<!-- results:end -->",
                 lambda _: "<!-- results:start -->\n" + "\n".join(lines) + f"\n{note}<!-- results:end -->",
                 text, flags=re.S)
    readme.write_text(new, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", action="store_true", help="offline demo build from the SYNTHETIC fixture days")
    ap.add_argument("--refresh", action="store_true", help="re-pull today's snapshot even if it is cached")
    ap.add_argument("--pull-only", action="store_true", help="save today's snapshot and stop (no DB changes)")
    ap.add_argument("--seed", type=int, default=int(load_settings()["seed"]))
    ap.add_argument("--test", action="store_true", help="also run pytest and record the counts")
    a = ap.parse_args(argv)
    if a.fixtures and (a.refresh or a.pull_only):
        ap.error("--refresh and --pull-only need the live API; they cannot be combined with --fixtures")
    paths = paths_for("demo" if a.fixtures else "live")
    if a.pull_only:
        folder = ensure_snapshot(paths.raw, local_today(), default_client(), refresh=a.refresh)
        print(f"snapshot ready: {folder}")
        return 0
    try:
        summary = build(paths, client=None if a.fixtures else default_client(), refresh=a.refresh, seed=a.seed)
    except DatabaseInUse as exc:
        print(f"error: {exc}")
        return 2
    tests = run_pytest() if a.test else None
    if a.fixtures:   # docs/results.json describes the reproducible demo build only
        old = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
        results = {"summary": {k: v for k, v in summary.items() if k not in ("tables", "db", "seconds")},
                   "tables": {k: v["rows"] for k, v in summary["tables"].items()},
                   "tests": tests or old.get("tests")}
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
        write_readme_results(results)
    short = {k: summary.get(k) for k in ("mode", "latest_snapshot", "change_counts_latest", "dq", "matching",
                                         "match_quality", "ontology_problems", "seconds")}
    print(json.dumps(short, indent=1, default=str))
    if summary.get("dq_fail"):
        print("DQ FAIL rows:", json.dumps(summary["dq_fail"], indent=1))
    if tests and (tests["failed"] or tests["error"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
