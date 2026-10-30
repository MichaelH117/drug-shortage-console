"""Tiered formulary-to-shortage matcher and the name-only baseline (ML-03, AC-4.2, AC-4.6). Pure DataFrames.

Tiers (DESIGN section 5):
1. formulary ndc11 == event ndc11. The event ndc11 comes from the TOP-LEVEL ``package_ndc``, never from
   ``openfda.package_ndc`` (SPEC R4, the 0378-6860-77 case).
2. formulary product_ndc9 == event product_ndc9, or == any normalized ``openfda.product_ndc`` (only when
   the record has an openfda block).
3. ingredient_norm + dosage_form_norm equal, routes compatible, and strength equal when both sides have one.
   The event route is openfda.route, else ``default_route(dosage_form)`` (AC-4.6: no openfda block needed).

Guards (a deliberate addition, logged in HANDOFF): an NDC match (tier 1 or 2) is dropped when both sides
have an ingredient name and the names share no word, which catches a typo'd or mis-padded NDC that points
at another product. A tier 2 match that comes only from the openfda product list must also not conflict
on dosage form or strength, because that list spans every strength of the product family.

Confidence: t1 1.00, t2 0.90, t3 with strength 0.80, t3 without strength 0.60, t3 near-miss name 0.50.

v2 (ADR 006 addendum): the NDC name guard now needs corroboration. A name disagreement alone no longer drops an
NDC match; it is dropped only if the names disagree AND the dosage form or strength also conflicts. Tier 3 also
accepts a near-miss spelling under strict conditions (names.near_miss plus equal strength on both sides).
"""
from __future__ import annotations

import json

import pandas as pd

from pipeline.names import (
    brand_key,
    default_route,
    format_strength,
    load_normalize_config,
    near_miss,
    normalize_dosage_form,
    normalize_ingredient,
    normalize_route,
    parse_strength,
    routes_compatible,
    strength_forms,
    strengths_equal,
)
from pipeline.ndc import extract_ndc_from_presentation, normalize_ndc, normalize_product_ndc

MATCH_COLUMNS = ["item_id", "event_key", "match_tier", "match_confidence", "matched_on"]
FORMULARY_NORM_COLUMNS = ["item_id", "ndc11", "ndc_reason", "product_ndc9", "ingredient_norm",
                          "dosage_form_norm", "route_norm", "strength_norm"]
CONFIDENCE = {1: 1.00, 2: 0.90, "3s": 0.80, "3": 0.60}


# ---------------------------------------------------------------- helpers
def _isnull(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _s(v) -> str:
    return "" if _isnull(v) else str(v)


def _as_list(v) -> list:
    """Accept a python list, a JSON array string (stage.* stores json as VARCHAR), or a scalar."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if hasattr(v, "tolist") and not isinstance(v, str):
        return list(v.tolist())
    if _isnull(v):
        return []
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                out = json.loads(t)
                return out if isinstance(out, list) else [out]
            except ValueError:
                return [t]
        return [t] if t else []
    return [v]


def _col(df: pd.DataFrame, name: str, default=None) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index, dtype=object)


def _words(ingredient_norm: str) -> set[str]:
    return {w for w in ingredient_norm.replace("/", " ").split() if w}


def _presentation_brand(presentation: str) -> str:
    """'Xylocaine, Injection, 20 mg/1 mL (NDC ...)' -> 'Xylocaine'."""
    return presentation.split(",")[0].strip() if presentation else ""


# ---------------------------------------------------------------- brand map
def build_brand_map(events: pd.DataFrame) -> dict[str, str]:
    """brand key -> generic name, from the snapshot only (no external lists).

    Sources per record: proprietary_name, openfda brand_name (if the column exists), and the first segment
    of ``presentation`` (often the brand). A brand that maps to two different ingredients is dropped.
    """
    seen: dict[str, set[str]] = {}
    generic_of: dict[str, str] = {}
    for _, r in events.iterrows():
        generic = _s(r.get("generic_name"))
        if not generic:
            continue
        g_norm = normalize_ingredient(generic, {})
        if not g_norm:
            continue
        candidates = [_s(r.get("proprietary_name")), _presentation_brand(_s(r.get("presentation")))]
        candidates += [_s(b) for b in _as_list(r.get("openfda_brand_name"))]
        components = set(g_norm.split(" / "))
        for c in candidates:
            key = brand_key(c)
            # ADR 006 condition 2 (REVIEW 4): "Amphetamine Aspartate" -> key "amphetamine" is an ingredient of
            # the mixed-salts generic, not a brand; mapping it would garble every name containing it.
            if not key or key in components or normalize_ingredient(c, {}) == g_norm:
                continue
            seen.setdefault(key, set()).add(g_norm)
            generic_of.setdefault(key, generic)
    return {k: generic_of[k] for k, v in seen.items() if len(v) == 1}


# ---------------------------------------------------------------- normalization
def normalize_formulary(formulary: pd.DataFrame, brand_map: dict[str, str] | None = None) -> pd.DataFrame:
    """seed.formulary_item -> core.formulary_item_norm (+ private ``_routes``/``_strengths`` columns)."""
    brand_map = brand_map or {}
    rows = []
    for _, r in formulary.iterrows():
        name = _s(r.get("name_raw"))
        res = normalize_ndc(r.get("ndc_raw"))
        form = normalize_dosage_form(_s(r.get("dosage_form_raw"))) or normalize_dosage_form(name)
        routes = normalize_route(_s(r.get("route_raw")) or None)
        if not routes and default_route(form):
            routes = frozenset({default_route(form)})
        strength_src = _s(r.get("strength_raw")) or name
        rows.append({
            "item_id": r.get("item_id"),
            "ndc11": res.ndc11,
            "ndc_reason": res.reason,
            "product_ndc9": res.product_ndc9,
            "ingredient_norm": normalize_ingredient(name, brand_map),
            "dosage_form_norm": form,
            "route_norm": "|".join(sorted(routes)),
            "strength_norm": format_strength(parse_strength(strength_src)),
            "_routes": routes,
            "_strengths": strength_forms(strength_src),
        })
    return pd.DataFrame(rows, columns=FORMULARY_NORM_COLUMNS + ["_routes", "_strengths"])


def normalize_events(events: pd.DataFrame, brand_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Shortage events (stage.shortage_record-like rows) -> normalized frame for the matcher.

    Uses ``ndc11``/``product_ndc9`` if present (DE computes them from the top-level package_ndc); otherwise
    normalizes ``package_ndc_raw``/``package_ndc``, falling back to the NDC in ``presentation``.
    """
    brand_map = brand_map if brand_map is not None else build_brand_map(events)
    rows = []
    for _, r in events.iterrows():
        ndc11 = _s(r.get("ndc11")) or None
        p9 = _s(r.get("product_ndc9")) or None
        if ndc11 is None:
            raw = r.get("package_ndc_raw") if "package_ndc_raw" in events.columns else r.get("package_ndc")
            res = normalize_ndc(raw)
            if res.ndc11 is None:
                res = normalize_ndc(extract_ndc_from_presentation(_s(r.get("presentation"))))
            ndc11, p9 = res.ndc11, res.product_ndc9
        if p9 is None and ndc11:
            p9 = ndc11[:9]
        generic = _s(r.get("generic_name"))
        presentation = _s(r.get("presentation"))
        form = (normalize_dosage_form(generic) or normalize_dosage_form(_s(r.get("dosage_form")))
                or normalize_dosage_form(", ".join(presentation.split(",")[1:2])))
        has_openfda = bool(r.get("has_openfda")) if not _isnull(r.get("has_openfda")) else False
        routes = normalize_route(_as_list(r.get("openfda_route")))
        if not routes and default_route(form):
            routes = frozenset({default_route(form)})
        strength_list = [x for x in _as_list(r.get("strength")) if _s(x)]
        strength_src = strength_list if strength_list else ", ".join(presentation.split(",")[1:])
        openfda_p9 = []
        if has_openfda:
            openfda_p9 = sorted({p for p in (normalize_product_ndc(x) for x in _as_list(r.get("openfda_product_ndc")))
                                 if p})
        rows.append({
            "event_key": r.get("event_key"),
            "ndc11": ndc11,
            "product_ndc9": p9,
            "generic_name": generic,
            "ingredient_norm": normalize_ingredient(generic, brand_map),
            "dosage_form_norm": form,
            "route_norm": "|".join(sorted(routes)),
            "strength_norm": format_strength(parse_strength(strength_src)),
            "has_openfda": has_openfda,
            "openfda_product_ndc9": openfda_p9,
            "_routes": routes,
            "_strengths": strength_forms(strength_src),
        })
    return pd.DataFrame(rows, columns=["event_key", "ndc11", "product_ndc9", "generic_name", "ingredient_norm",
                                       "dosage_form_norm", "route_norm", "strength_norm", "has_openfda",
                                       "openfda_product_ndc9", "_routes", "_strengths"])


def _ensure_private(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "_routes" not in df.columns:
        df["_routes"] = [frozenset(_s(v).split("|")) - {""} for v in _col(df, "route_norm")]
    if "_strengths" not in df.columns:
        df["_strengths"] = [strength_forms(_s(v)) for v in _col(df, "strength_norm")]
    return df


# ---------------------------------------------------------------- matching
def _name_conflict(a: str, b: str) -> bool:
    """Both names present, no shared word, and not a near-miss spelling of each other."""
    return bool(a) and bool(b) and not (_words(a) & _words(b)) and not near_miss(a, b)


def _component_conflict(a: str, b: str) -> bool:
    """ADR 006 condition 1 (REVIEW 2): both sides parse to ingredient sets that differ AND overlap (a combination
    vs one of its parts, e.g. bupivacaine vs bupivacaine / epinephrine), or are two different combinations.
    Disjoint single names are NOT a component conflict: that is a name-only disagreement, handled by the
    corroboration rule (addendum item 1, pinned by a test)."""
    if not a or not b:
        return False
    sa, sb = set(a.split(" / ")), set(b.split(" / "))
    if sa == sb:
        return False
    return bool(sa & sb) or (len(sa) > 1 and len(sb) > 1)


def _form_or_strength_conflict(r) -> bool:
    ff, ef = _s(r["f_dosage_form_norm"]), _s(r["e_dosage_form_norm"])
    if ff and ef and ff != ef:
        return True
    return strengths_equal(r["f__strengths"], r["e__strengths"]) is False


def match(formulary_norm: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Return core.formulary_match rows: the best tier per (item_id, event_key)."""
    if "ingredient_norm" not in events.columns:
        events = normalize_events(events)
    f = _ensure_private(formulary_norm)
    e = _ensure_private(events)
    if "openfda_product_ndc9" not in e.columns:
        e["openfda_product_ndc9"] = [[] for _ in range(len(e))]
    if f.empty or e.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)

    fs = f.add_prefix("f_")
    es = e.add_prefix("e_")
    out: list[dict] = []

    def emit(df: pd.DataFrame, tier: int, how: str, extra_guard: bool = False):
        for _, r in df.iterrows():
            fi, ei = _s(r["f_ingredient_norm"]), _s(r["e_ingredient_norm"])
            if _component_conflict(fi, ei):
                continue
            conflict = _form_or_strength_conflict(r)
            if _name_conflict(fi, ei) and conflict:
                continue
            if extra_guard and conflict:
                continue
            key = r["f_product_ndc9"] if tier == 2 else r["f_ndc11"]
            out.append({"item_id": r["f_item_id"], "event_key": r["e_event_key"], "match_tier": tier,
                        "match_confidence": CONFIDENCE[tier], "matched_on": f"{how}={key}"})

    # tier 1: package ndc11 (top-level package_ndc only)
    t1 = fs[fs.f_ndc11.notna()].merge(es[es.e_ndc11.notna()], left_on="f_ndc11", right_on="e_ndc11")
    emit(t1, 1, "ndc11")

    # tier 2a: same product (labeler + product), any package
    t2 = fs[fs.f_product_ndc9.notna()].merge(es[es.e_product_ndc9.notna()],
                                              left_on="f_product_ndc9", right_on="e_product_ndc9")
    emit(t2, 2, "product_ndc9")

    # tier 2b: product listed in the record's openfda.product_ndc (only when has_openfda)
    ex = es[es.e_has_openfda.fillna(False).astype(bool)].explode("e_openfda_product_ndc9")
    ex = ex[ex.e_openfda_product_ndc9.notna()]
    t2b = fs[fs.f_product_ndc9.notna()].merge(ex, left_on="f_product_ndc9", right_on="e_openfda_product_ndc9")
    t2b = t2b[t2b.f_product_ndc9 != t2b.e_product_ndc9]
    emit(t2b, 2, "openfda.product_ndc", extra_guard=True)

    # tier 3: ingredient + form + route (+ strength when both have one)
    fk = fs[(fs.f_ingredient_norm.fillna("") != "") & (fs.f_dosage_form_norm.fillna("") != "")]
    ek = es[(es.e_ingredient_norm.fillna("") != "") & (es.e_dosage_form_norm.fillna("") != "")]
    t3 = fk.merge(ek, left_on=["f_ingredient_norm", "f_dosage_form_norm"],
                  right_on=["e_ingredient_norm", "e_dosage_form_norm"])
    for _, r in t3.iterrows():
        if not routes_compatible(r["f__routes"], r["e__routes"]):
            continue
        same = strengths_equal(r["f__strengths"], r["e__strengths"])
        if same is False:
            continue
        conf = CONFIDENCE["3s"] if same else CONFIDENCE["3"]
        on = "ingredient+form+route" + ("+strength" if same else "")
        out.append({"item_id": r["f_item_id"], "event_key": r["e_event_key"], "match_tier": 3,
                    "match_confidence": conf, "matched_on": f"{on}={r['f_ingredient_norm']}"})

    # tier 3, near-miss spelling (v2): only for items whose spelling is not itself a known ingredient
    fuzzy_conf = float((load_normalize_config().get("fuzzy") or {}).get("confidence", 0.50))
    known = set(ek.e_ingredient_norm)
    done = {(o["item_id"], o["event_key"]) for o in out}
    fz = fk[~fk.f_ingredient_norm.isin(known)]
    for fr in fz.itertuples(index=False):
        cands = ek[ek.e_dosage_form_norm == fr.f_dosage_form_norm]
        for er in cands.itertuples(index=False):
            if (fr.f_item_id, er.e_event_key) in done or not near_miss(fr.f_ingredient_norm, er.e_ingredient_norm):
                continue
            if not routes_compatible(fr.f__routes, er.e__routes):
                continue
            if strengths_equal(fr.f__strengths, er.e__strengths) is not True:
                continue
            out.append({"item_id": fr.f_item_id, "event_key": er.e_event_key, "match_tier": 3,
                        "match_confidence": fuzzy_conf,
                        "matched_on": "near-miss name+form+route+strength="
                                      f"{fr.f_ingredient_norm}~{er.e_ingredient_norm}"})

    if not out:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    res = pd.DataFrame(out, columns=MATCH_COLUMNS)
    res = res.sort_values(["item_id", "event_key", "match_tier", "match_confidence"],
                          ascending=[True, True, True, False], kind="mergesort")
    res = res.drop_duplicates(["item_id", "event_key"], keep="first").reset_index(drop=True)
    res["match_tier"] = res["match_tier"].astype("int64")
    return res


# baseline_v2: spec owned by QA (TEST_PLAN.md section 6.2). The word list is fixed before scoring.
BASE_NAME_FORM_WORDS = frozenset("""
tablet tablets tab capsule capsules cap injection injectable inj solution suspension oral powder for film coated
extended delayed release er xr sr dr odt orally disintegrating chewable spray ointment cream gel liquid concentrate
suppository kit system transdermal ophthalmic topical inhalation aerosol metered irrigant vial syringe pen bag
""".split())
BASE_NAME_UNIT_WORDS = frozenset("mg mcg ug g ml l meq unit units percent".split())


def base_name(text) -> str:
    """TEST_PLAN 6.2: NFKC lowercase, non [a-z0-9 ] -> space, drop form words, digit tokens and unit words."""
    import re
    import unicodedata

    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    toks = [w for w in t.split() if w not in BASE_NAME_FORM_WORDS and w not in BASE_NAME_UNIT_WORDS
            and not any(c.isdigit() for c in w)]
    return " ".join(toks)


def baseline_v2_match(formulary: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Fairer name-only baseline (TEST_PLAN 6.2): base_name(item.name_raw) == base_name(event.generic_name),
    non-empty. No salt stripping, brand map, NDC, route, strength or fuzzy matching."""
    name_col = "name_raw" if "name_raw" in formulary.columns else "generic_name"
    f = pd.DataFrame({"item_id": formulary["item_id"], "k": [base_name(v) for v in formulary[name_col]]})
    e = pd.DataFrame({"event_key": events["event_key"], "k": [base_name(v) for v in events["generic_name"]]})
    m = f[f.k != ""].merge(e[e.k != ""], on="k")
    if m.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    m["match_tier"] = 0
    m["match_confidence"] = 1.0
    m["matched_on"] = "baseline_v2:base_name=" + m["k"]
    return m[MATCH_COLUMNS].drop_duplicates(["item_id", "event_key"]).reset_index(drop=True)


def baseline_match(formulary: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Naive baseline (AC-4.4): exact lowercase equality of the formulary name and the event generic_name."""
    name_col = "name_raw" if "name_raw" in formulary.columns else "generic_name"
    f = pd.DataFrame({"item_id": formulary["item_id"],
                      "k": [_s(v).strip().lower() for v in formulary[name_col]]})
    e = pd.DataFrame({"event_key": events["event_key"],
                      "k": [_s(v).strip().lower() for v in events["generic_name"]]})
    m = f[f.k != ""].merge(e[e.k != ""], on="k")
    if m.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    m["match_tier"] = 0
    m["match_confidence"] = 1.0
    m["matched_on"] = "baseline:lower(generic_name)=" + m["k"]
    return m[MATCH_COLUMNS].drop_duplicates(["item_id", "event_key"]).reset_index(drop=True)
