"""Mechanical substitute candidates (ML-06, SPEC FAQ). Pure DataFrames. Not clinical advice: a pharmacist
must approve (console.actions.approve_substitute).

A candidate for a shortage event is a Drug with the same ingredient and a compatible route, a different ndc11,
that is not itself in an active shortage in the latest snapshot (same rule as the approve criterion,
DESIGN section 4). Only events that touch the formulary (have a match) get candidates, which keeps it small.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from pipeline.names import normalize_route, routes_compatible

CANDIDATE_COLUMNS = ["substitute_id", "event_key", "alt_ndc11", "alt_item_id", "basis"]


def substitute_id_for(event_key: str, alt_ndc11: str) -> str:
    """Same rule as console.actions.substitute_id_for (DESIGN 3.2)."""
    return "S-" + hashlib.sha1(f"{event_key}|{alt_ndc11}".encode()).hexdigest()[:12]


def _short_keys(events: pd.DataFrame) -> tuple[set, set]:
    ev = events
    if "present_in_latest" in ev.columns:
        ev = ev[ev["present_in_latest"].fillna(False).astype(bool)]
    status = ev["status"] if "status" in ev.columns else pd.Series([None] * len(ev), index=ev.index)
    avail = (ev["availability_normalized"] if "availability_normalized" in ev.columns
             else pd.Series([None] * len(ev), index=ev.index))
    active = ev[(status == "Current") | avail.isin(["Limited", "Unavailable"])]
    return set(active["ndc11"].dropna()), set(active["product_ndc9"].dropna())


def substitute_candidates(drugs: pd.DataFrame, events: pd.DataFrame, matches: pd.DataFrame,
                          formulary_norm: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return core.substitute_candidate rows, sorted, with deterministic ids."""
    if drugs.empty or events.empty or matches.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    short_ndc, short_p9 = _short_keys(events)
    item_of: dict[str, str] = {}
    if formulary_norm is not None and not formulary_norm.empty:
        for r in formulary_norm.dropna(subset=["ndc11"]).sort_values("item_id").itertuples():
            item_of.setdefault(r.ndc11, r.item_id)

    d = drugs[drugs["ingredient_norm"].fillna("") != ""].copy()
    d = d[~d["ndc11"].isin(short_ndc) & ~d["product_ndc9"].isin(short_p9)]
    by_ing = {k: g for k, g in d.groupby("ingredient_norm")}

    drug_of = drugs.set_index("ndc11")
    matched = set(matches["event_key"])
    out = []
    for e in events[events["event_key"].isin(matched)].itertuples():
        if not isinstance(e.ndc11, str) or e.ndc11 not in drug_of.index:
            continue
        src = drug_of.loc[e.ndc11]
        if isinstance(src, pd.DataFrame):
            src = src.iloc[0]
        ing = src.get("ingredient_norm") or ""
        if not ing or ing not in by_ing:
            continue
        src_routes = normalize_route((src.get("route_norm") or "").split("|"))
        for alt in by_ing[ing].itertuples():
            if alt.ndc11 == e.ndc11:
                continue
            if not routes_compatible(src_routes, normalize_route((alt.route_norm or "").split("|"))):
                continue
            basis = ["same ingredient", "compatible route"]
            basis.append("same form" if alt.dosage_form_norm == src.get("dosage_form_norm") else "different form")
            basis.append("same strength" if alt.strength_norm and alt.strength_norm == src.get("strength_norm")
                         else "strength differs or unknown")
            out.append({"substitute_id": substitute_id_for(e.event_key, alt.ndc11), "event_key": e.event_key,
                        "alt_ndc11": alt.ndc11, "alt_item_id": item_of.get(alt.ndc11),
                        "basis": "; ".join(basis)})
    if not out:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    res = pd.DataFrame(out, columns=CANDIDATE_COLUMNS).drop_duplicates("substitute_id")
    return res.sort_values(["event_key", "alt_ndc11"]).reset_index(drop=True)
