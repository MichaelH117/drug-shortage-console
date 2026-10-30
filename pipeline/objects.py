"""ML-owned core tables and the ontology views (ML-06, AC-3.1, AC-6.4).

``build_matching(con)`` writes core.formulary_item_norm, core.drug, core.formulary_match,
core.formulary_match_baseline and core.substitute_candidate from stage.shortage_record, core.shortage_event and
seed.formulary_item. ``create_object_views(con, onto)`` regenerates obj.*, link.* (and mart.* when AE's SQL is
available) from ontology/ontology.yaml. Views read ops.* edit tables, which are created IF NOT EXISTS and never
dropped here.
"""
from __future__ import annotations

import json

import pandas as pd

from ontokit.ontology import Ontology
from ontokit.writeback import latest_edit_view_sql
from pipeline.candidates import substitute_candidates
from pipeline.match import (
    FORMULARY_NORM_COLUMNS,
    MATCH_COLUMNS,
    baseline_match,
    build_brand_map,
    match,
    normalize_events,
    normalize_formulary,
)

DRUG_COLUMNS = ["ndc11", "product_ndc9", "generic_name", "ingredient_norm", "dosage_form_norm", "route_norm",
                "strength_norm", "manufacturer", "rxcui", "source"]


def _first(v):
    if v is None:
        return None
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                lst = json.loads(t)
                return str(lst[0]) if lst else None
            except ValueError:
                return t
        return t or None
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else None
    return None


def latest_event_records(con) -> pd.DataFrame:
    """One row per event_key: the newest stage.shortage_record row, plus latest status from core.shortage_event."""
    rec = con.execute("""
        SELECT * FROM stage.shortage_record
        QUALIFY row_number() OVER (PARTITION BY event_key ORDER BY snapshot_date DESC) = 1
        ORDER BY event_key""").df()
    ev_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='core' AND table_name='shortage_event'"
    ).fetchall()}
    keep = [c for c in ("present_in_latest",) if c in ev_cols]
    if keep:
        ev = con.execute(f"SELECT event_key, {', '.join(keep)} FROM core.shortage_event").df()
        rec = rec.merge(ev, on="event_key", how="left")
    return rec


def build_drugs(enorm: pd.DataFrame, events_raw: pd.DataFrame, fnorm: pd.DataFrame,
                formulary: pd.DataFrame) -> pd.DataFrame:
    rows: dict[str, dict] = {}
    raw = events_raw.set_index("event_key")
    for e in enorm.sort_values("event_key").itertuples():
        if not isinstance(e.ndc11, str) or e.ndc11 in rows:
            continue
        r = raw.loc[e.event_key]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
        rows[e.ndc11] = {"ndc11": e.ndc11, "product_ndc9": e.product_ndc9, "generic_name": e.generic_name,
                         "ingredient_norm": e.ingredient_norm, "dosage_form_norm": e.dosage_form_norm,
                         "route_norm": e.route_norm, "strength_norm": e.strength_norm,
                         "manufacturer": r.get("company_name"), "rxcui": _first(r.get("openfda_rxcui")),
                         "source": "shortage"}
    names = dict(zip(formulary["item_id"], formulary["name_raw"], strict=False))
    for f in fnorm.sort_values("item_id").itertuples():
        if not isinstance(f.ndc11, str):
            continue
        if f.ndc11 in rows:
            if rows[f.ndc11]["source"] == "shortage":
                rows[f.ndc11]["source"] = "both"
            continue
        rows[f.ndc11] = {"ndc11": f.ndc11, "product_ndc9": f.product_ndc9, "generic_name": names.get(f.item_id),
                         "ingredient_norm": f.ingredient_norm, "dosage_form_norm": f.dosage_form_norm,
                         "route_norm": f.route_norm, "strength_norm": f.strength_norm, "manufacturer": None,
                         "rxcui": None, "source": "formulary"}
    return pd.DataFrame(list(rows.values()), columns=DRUG_COLUMNS).sort_values("ndc11").reset_index(drop=True)


def _write(con, table: str, df: pd.DataFrame) -> None:
    schema = table.split(".")[0]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    tmp = "_ml_tmp_" + table.replace(".", "_")
    con.register(tmp, df)
    try:
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {tmp}")
    finally:
        con.unregister(tmp)


def build_matching(con) -> dict:
    """Write the ML-owned core tables. Returns row counts for the run summary."""
    events_raw = latest_event_records(con)
    formulary = con.execute("SELECT * FROM seed.formulary_item ORDER BY item_id").df()
    bm = build_brand_map(events_raw)
    fnorm = normalize_formulary(formulary, bm)
    enorm = normalize_events(events_raw, bm)
    matches = match(fnorm, enorm)
    base = baseline_match(formulary, events_raw)
    drugs = build_drugs(enorm, events_raw, fnorm, formulary)
    ev_status = con.execute("SELECT * FROM core.shortage_event").df()
    cands = substitute_candidates(drugs, ev_status, matches, fnorm)

    _write(con, "core.formulary_item_norm", fnorm[FORMULARY_NORM_COLUMNS])
    _write(con, "core.drug", drugs)
    _write(con, "core.formulary_match", matches[MATCH_COLUMNS])
    _write(con, "core.formulary_match_baseline", base[MATCH_COLUMNS])
    _write(con, "core.substitute_candidate", cands)
    return {"formulary_items": len(fnorm), "drugs": len(drugs), "matches": len(matches),
            "baseline_matches": len(base), "substitute_candidates": len(cands), "brand_map": len(bm)}


def _cols(con, table: str) -> list[str]:
    schema, name = table.split(".")
    return [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? "
        "ORDER BY ordinal_position", [schema, name]).fetchall()]


def create_object_views(con, ontology: Ontology, *, include_marts: bool = True) -> None:
    """(Re)create obj.* and link.* views from the ontology; ops/audit tables are created IF NOT EXISTS first."""
    from console.actions import init_ops

    init_ops(con)
    for schema in ("obj", "link", "mart"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for o in ontology.objects.values():
        key = o.key[0]
        if o.edits:
            sql = latest_edit_view_sql(o.source, o.edits, key, o.view,
                                       base_columns=_cols(con, o.source) if o.source else None,
                                       edit_columns=_cols(con, o.edits), defaults=o.defaults)
        else:
            sql = f"CREATE OR REPLACE VIEW {o.view} AS SELECT * FROM {o.source}"
        con.execute(sql)
    for ln in ontology.links.values():
        con.execute(f"CREATE OR REPLACE VIEW {ln.view} AS {ln.sql}")
    if include_marts:
        create_mart_views(con)


def create_mart_views(con) -> list[str]:
    """Run AE's mart SQL (AE owns the text in console/queries.py; ML runs it). Returns the views created."""
    try:
        from console import queries
    except ImportError:
        return []
    created = []
    for name in ("TRIAGE_QUEUE_SQL", "MART_TRIAGE_QUEUE_SQL"):
        sql = getattr(queries, name, None)
        if sql:
            con.execute(sql)
            created.append("mart.triage_queue")
            break
    return created
