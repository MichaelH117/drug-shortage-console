"""ML-03 tests for pipeline.names and pipeline.match (AC-4.2, AC-4.6).

All records here are hand-made dev examples in the shape of stage.shortage_record and seed.formulary_item.
The gold set is not used here (DESIGN 5: the matcher author does not grade their own work).
"""
import json

import pandas as pd
import pytest

from pipeline.match import (
    baseline_match,
    build_brand_map,
    match,
    normalize_events,
    normalize_formulary,
)
from pipeline.names import (
    normalize_dosage_form,
    normalize_ingredient,
    normalize_route,
    parse_strength,
    routes_compatible,
)


def ev(event_key, package_ndc, generic, presentation, dosage_form=None, *, routes=None,
       product_ndcs=None, package_ndcs=None, strength=None, proprietary=None):
    has = routes is not None or product_ndcs is not None
    from pipeline.ndc import normalize_ndc
    r = normalize_ndc(package_ndc)
    return {
        "event_key": event_key, "package_ndc_raw": package_ndc, "ndc11": r.ndc11, "product_ndc9": r.product_ndc9,
        "generic_name": generic, "proprietary_name": proprietary, "presentation": presentation,
        "dosage_form": dosage_form, "strength": json.dumps(strength) if strength else None,
        "has_openfda": has,
        "openfda_route": json.dumps(routes) if routes else None,
        "openfda_product_ndc": json.dumps(product_ndcs) if product_ndcs else None,
        "openfda_package_ndc": json.dumps(package_ndcs) if package_ndcs else None,
    }


def item(item_id, ndc, name, strength=None, form=None, route=None):
    return {"item_id": item_id, "ndc_raw": ndc, "name_raw": name, "strength_raw": strength,
            "dosage_form_raw": form, "route_raw": route, "is_synthetic": True}


def run(items, events):
    e = pd.DataFrame(events)
    bm = build_brand_map(e)
    return match(normalize_formulary(pd.DataFrame(items), bm), normalize_events(e, bm))


def pairs(m):
    return {(r.item_id, r.event_key): r.match_tier for r in m.itertuples()}


# ------------------------------------------------------------------ names
def test_ingredient_salt_case_whitespace_and_combo():
    assert normalize_ingredient("Lidocaine Hydrochloride Injection") == "lidocaine"
    assert normalize_ingredient("  LIDOCAINE   HCl  1% inj ") == "lidocaine"
    assert normalize_ingredient("Sodium Bicarbonate Injection") == "sodium bicarbonate"
    assert normalize_ingredient("Sodium Acetate") != normalize_ingredient("Sodium Bicarbonate")
    combo = ("Amphetamine Aspartate Monohydrate, Amphetamine Sulfate, Dextroamphetamine Saccharate, "
             "Dextroamphetamine Sulfate Tablet")
    assert normalize_ingredient(combo) == "amphetamine / dextroamphetamine"
    assert normalize_ingredient("Xylocaine", {"xylocaine": "Lidocaine Hydrochloride"}) == "lidocaine"
    assert normalize_ingredient(None) == ""


def test_lookalikes_stay_distinct():
    assert normalize_ingredient("Hydroxyzine HCl 25 mg tab") != normalize_ingredient("Hydralazine HCl 25 mg tab")


def test_dosage_form_buckets():
    assert normalize_dosage_form("Methylphenidate Hydrochloride Tablet, Extended Release") == "tablet_er"
    assert normalize_dosage_form("Tablet") == "tablet"
    assert normalize_dosage_form("methylphenidate ER 36 mg tab") == "tablet_er"
    assert normalize_dosage_form("Injection, solution") == "injection"
    assert normalize_dosage_form("") == ""


def test_strength_parsing():
    assert parse_strength("50 mg/5 mL") == (10.0, "mg/ml")
    assert parse_strength("10 mg/mL") == (10.0, "mg/ml")
    assert parse_strength("Injection, 1%, USP") == (10.0, "mg/ml")
    assert parse_strength("400 mcg/100 mL") == (0.004, "mg/ml")
    assert parse_strength(["2.5 mg", "2.5 mg"]) == (2.5, "mg")
    assert parse_strength("1.1 g/1 mL") == (1100.0, "mg/ml")
    assert parse_strength(None) is None


def test_routes():
    assert normalize_route("IV") == frozenset({"intravenous"})
    assert normalize_route(["INTRAMUSCULAR", "INTRAVENOUS"]) == frozenset({"intramuscular", "intravenous"})
    assert routes_compatible(frozenset({"parenteral"}), frozenset({"intravenous"}))
    assert not routes_compatible(frozenset({"oral"}), frozenset({"intravenous"}))
    assert routes_compatible(frozenset(), frozenset({"oral"}))


# ------------------------------------------------------------------ matcher
def test_tier1_uses_top_level_package_ndc_not_openfda_package_ndc():
    """Regression for the observed 0378-6860-77 vs 0378-6854-77 case (SPEC R4, DESIGN T6)."""
    e = ev("E1", "0378-6860-77", "Fluvoxamine Maleate Tablet", "Fluvoxamine Maleate, Tablet, 50 mg (NDC 0378-6860-77)",
           "Tablet", routes=["ORAL"], product_ndcs=["0378-6860"], package_ndcs=["0378-6854-77"])
    items = [item("I-top", "0378-6860-77", "Fluvoxamine 50 mg tablet"),
             item("I-openfda", "0378-6854-77", "Zz Unrelated Product")]
    p = pairs(run(items, [e]))
    assert p[("I-top", "E1")] == 1
    assert ("I-openfda", "E1") not in p


def test_record_without_openfda_matches_through_tier3():
    """AC-4.6: no openfda block, different labeler, still matched on ingredient + form + default route."""
    e = ev("E2", "17478-050-02", "Sufentanil Citrate Injection",
           "Sufentanil Citrate, Injection, 50 mcg/1 mL (NDC 17478-050-02)", "Injection")
    assert e["has_openfda"] is False
    items = [item("I-suf", "99999-123-45", "SUFENTANIL citrate", "50 mcg/mL", "injection", "IV")]
    m = run(items, [e])
    assert pairs(m) == {("I-suf", "E2"): 3}
    assert m.iloc[0].match_confidence == 0.80


def test_best_tier_wins_and_tier2_product():
    e = ev("E3", "43066-013-10", "Rocuronium Bromide Injection",
           "Rocuronium Bromide, Injection, 100 mg/10 mL (NDC 43066-013-10)", "Injection",
           routes=["INTRAVENOUS"], product_ndcs=["43066-013", "43066-007"])
    items = [item("I-same", "43066-0013-10", "Rocuronium Bromide 10 mg/mL inj"),     # tier 1 and 3
             item("I-otherpkg", "43066-013-25", "Rocuronium 10 mg/mL"),               # tier 2 (same product)
             item("I-family", "43066-007-10", "Rocuronium bromide 50 mg/5 mL", None, "Injection")]  # 2b
    m = run(items, [e])
    p = pairs(m)
    assert p[("I-same", "E3")] == 1
    assert p[("I-otherpkg", "E3")] == 2
    assert p[("I-family", "E3")] == 2
    assert len(m) == 3  # one row per pair
    assert set(m.match_confidence) == {1.0, 0.9}


def test_hydroxyzine_does_not_match_hydralazine():
    e = ev("E4", "0641-6146-25", "Hydralazine Hydrochloride Injection",
           "Hydralazine Hydrochloride, Injection, 20 mg/1 mL (NDC 0641-6146-25)", "Injection")
    items = [item("I-hydroxyzine", "11111-222-33", "Hydroxyzine HCl 20 mg/mL injection")]
    assert run(items, [e]).empty


def test_hard_negatives_route_strength_release_and_bad_ndc():
    e = ev("E5", "0409-2634-05", "Hydromorphone Hydrochloride Injection",
           "Hydromorphone Hydrochloride, Injection, 50 mg/5 mL (10 mg/mL) (NDC 0409-2634-05)", "Injection",
           routes=["INTRAMUSCULAR", "INTRAVENOUS", "SUBCUTANEOUS"], product_ndcs=["0409-2634"])
    e6 = ev("E6", "62175-312-37", "Methylphenidate Hydrochloride Tablet, Extended Release",
            "Methylphenidate Hydrochloride, Tablet, Extended Release, 36 mg (NDC 62175-312-37)", "Tablet",
            routes=["ORAL"], product_ndcs=["62175-312"])
    items = [
        item("I-pos", "55555-111-01", "hydromorphone hcl 10 mg/mL", None, "Injection", "IV"),
        item("I-oral", "55555-111-02", "Hydromorphone 2 mg tablet", None, None, "PO"),        # route/form
        item("I-str", "55555-111-03", "Hydromorphone HCl 2 mg/mL injection"),                  # strength
        item("I-ir", "55555-111-04", "Methylphenidate HCl 36 mg tablet"),                      # IR vs ER
        item("I-erpos", "55555-111-05", "methylphenidate ER 36 mg tab"),
        # NDC points at the hydromorphone package, but the item is a different drug (typo'd NDC)
        item("I-badndc", "0409-2634-05", "Ondansetron 2 mg/mL injection"),
    ]
    p = pairs(run(items, [e, e6]))
    assert p == {("I-pos", "E5"): 3, ("I-erpos", "E6"): 3}


def test_brand_from_presentation_maps_to_generic():
    e = ev("E7", "63323-486-17", "Lidocaine Hydrochloride Injection",
           "Xylocaine, Injection, 20 mg/1 mL (NDC 63323-486-17)", "Injection",
           routes=["INFILTRATION", "PERINEURAL"], product_ndcs=["63323-486"])
    items = [item("I-brand", "12345-678-90", "XYLOCAINE 2% injection")]
    assert pairs(run(items, [e])) == {("I-brand", "E7"): 3}


def test_bad_ndc_formats_do_not_crash():
    e = ev("E8", "0378686077", "Fluvoxamine Maleate Tablet", "Fluvoxamine Maleate, Tablet, 50 mg", "Tablet")
    items = [item("I-x", "abc", "Fluvoxamine 50 mg tablet"), item("I-y", None, None)]
    m = run(items, [e])
    assert pairs(m) == {("I-x", "E8"): 3}


@pytest.mark.parametrize("name, expect", [("rocuronium bromide injection", True), ("Rocuronium", False)])
def test_baseline_is_exact_lowercase_generic_name(name, expect):
    events = pd.DataFrame([{"event_key": "E", "generic_name": "Rocuronium Bromide Injection"}])
    f = pd.DataFrame([{"item_id": "I", "name_raw": name}])
    assert (len(baseline_match(f, events)) == 1) is expect


# ------------------------------------------------------------------ v2 (ADR 006 addendum), dev examples only
def test_v2_misspelling_matches_only_with_equal_strength():
    e = ev("E9", "0409-6102-04", "Furosemide Injection",
           "Furosemide, Injection, 40 mg/4 mL (10 mg/1 mL) (NDC 0409-6102-04)", "Injection",
           routes=["INTRAMUSCULAR", "INTRAVENOUS"], product_ndcs=["0409-6102"])
    items = [item("I-typo", None, "Furosemyde", "10 mg/mL", "Injection", "IV"),
             item("I-typo-nostr", None, "Furosemyde", None, "Injection", "IV"),
             item("I-typo-otherstr", None, "Furosemyde", "20 mg/mL", "Injection", "IV")]
    m = run(items, [e])
    assert pairs(m) == {("I-typo", "E9"): 3}
    assert m.iloc[0].match_confidence == 0.5 and m.iloc[0].matched_on.startswith("near-miss")


def test_v2_lookalikes_and_known_names_never_fuzzy():
    e = ev("E10", "0641-6146-25", "Prednisolone Tablet", "Prednisolone, Tablet, 5 mg (NDC 0641-6146-25)", "Tablet")
    e2 = ev("E11", "0641-6147-25", "Prednisone Tablet", "Prednisone, Tablet, 5 mg (NDC 0641-6147-25)", "Tablet")
    items = [item("I-pred", "11111-222-33", "Prednisone 5 mg tablet")]
    assert pairs(run(items, [e])) == {}          # listed look-alike pair
    assert pairs(run(items, [e, e2])) == {("I-pred", "E11"): 3}  # exact name wins, no fuzzy to E10


def test_v2_ndc_name_guard_needs_corroboration():
    e = ev("E12", "0068-0597-01", "Rifampin Injection", "Rifampin, Injection, 600 mg (NDC 0068-0597-01)", "Injection")
    items = [item("I-brand-unknown", "0068-0597-01", "Zzbrand IV", "600 mg", "Injection"),  # names disagree only
             item("I-typo-ndc", "0068-0597-01", "Ondansetron 2 mg/mL injection")]            # + strength conflict
    assert pairs(run(items, [e])) == {("I-brand-unknown", "E12"): 1}


def test_v2_brand_key_strips_salt_route_and_device_words():
    e = ev("E13", "0009-0395-14", "Clindamycin Hydrochloride Capsule",
           "Cleocin Hydrochloride, Capsule, 300 mg (NDC 0009-0395-14)", "Capsule")
    e2 = ev("E14", "0169-4060-12", "Liraglutide Injection", "Victoza, Injection, 6 mg/1 mL (NDC 0169-4060-12)",
            "Injection", routes=["SUBCUTANEOUS"], product_ndcs=["0169-4060"])
    items = [item("I-cleo", "77777-111-22", "Cleocin HCl", "300 mg", "Capsule", "Oral"),
             item("I-vic", None, "Victoza pen", "6 mg/mL", "Injection", "Subcutaneous")]
    assert pairs(run(items, [e, e2])) == {("I-cleo", "E13"): 3, ("I-vic", "E14"): 3}


def test_v2_strength_comes_from_presentation_when_field_empty():
    e = ev("E15", "0409-2634-05", "Hydromorphone Hydrochloride Injection",
           "Hydromorphone Hydrochloride, Injection, 50 mg/5 mL (10 mg/mL) (NDC 0409-2634-05)", "Injection")
    assert e["strength"] is None
    n = normalize_events(pd.DataFrame([e]), {})
    assert n.iloc[0].strength_norm == "10 mg/ml"



# ------------------------------------------------------------------ round 3 (REVIEW 2, 4; ADR 006 conditions)
def test_r3_tier2b_combination_never_matches_single_agent():
    """REVIEW 2: bupivacaine single agent vs bupivacaine + epinephrine listing it in openfda.product_ndc."""
    combo = ev("E20", "0409-1163-01", "Bupivacaine Hydrochloride and Epinephrine Injection",
               "Bupivacaine Hydrochloride and Epinephrine, Injection, 5 mg/1 mL; .005 mg/1 mL (NDC 0409-1163-01)",
               "Injection", routes=["EPIDURAL", "INFILTRATION"], product_ndcs=["0409-1163", "0409-1162"])
    single = ev("E21", "0409-1162-02", "Bupivacaine Hydrochloride Injection",
                "Bupivacaine Hydrochloride, Injection, 5 mg/1 mL (NDC 0409-1162-02)", "Injection",
                routes=["EPIDURAL", "INFILTRATION"], product_ndcs=["0409-1162", "0409-1163"])
    items = [item("I-single", "0409-1162-01", "Bupivacaine HCl 5 mg/mL Injection"),
             item("I-combo", "0409-1163-02", "Bupivacaine HCl and Epinephrine 0.5% injection")]
    p = pairs(run(items, [combo, single]))
    assert ("I-single", "E20") not in p and ("I-combo", "E21") not in p
    assert p[("I-single", "E21")] == 2 and p[("I-combo", "E20")] == 2


def test_r3_tier1_one_vs_two_components_dropped():
    e = ev("E22", "0409-1163-01", "Bupivacaine Hydrochloride and Epinephrine Injection",
           "Bupivacaine Hydrochloride and Epinephrine, Injection, 5 mg/1 mL (NDC 0409-1163-01)", "Injection")
    items = [item("I-wrong", "0409-1163-01", "Bupivacaine 5 mg/mL injection")]  # NDC says combo, name says single
    assert pairs(run(items, [e])) == {}


def test_r3_pinned_trade_name_only_disagreement_keeps_ndc_match():
    """ADR 006 condition 4: pins addendum item 1 ON PURPOSE. A name that shares no word with the event, with the
    same dosage form and strength, keeps its tier-1 NDC match. A change that breaks this must update the ADR."""
    e = ev("E23", "0068-0597-01", "Rifampin Injection", "Rifampin, Injection, 600 mg (NDC 0068-0597-01)", "Injection")
    items = [item("I-unknown-brand", "0068-0597-01", "Qqbrand 600 mg", "600 mg", "Injection")]
    assert pairs(run(items, [e])) == {("I-unknown-brand", "E23"): 1}


def test_r3_brand_key_is_never_an_ingredient_of_its_generic():
    """REVIEW 4 / HP-051 regression: a presentation segment naming one salt must not become a brand."""
    e = ev("E24", "70010-112-01", "Amphetamine Aspartate Monohydrate, Amphetamine Sulfate, Dextroamphetamine "
           "Saccharate, Dextroamphetamine Sulfate Tablet",
           "Amphetamine Aspartate Monohydrate, Amphetamine Sulfate, Tablet, 1.875 mg (NDC 70010-112-01)", "Tablet")
    df = pd.DataFrame([e])
    bm = build_brand_map(df)
    assert "amphetamine" not in bm
    assert normalize_events(df, bm).iloc[0].ingredient_norm == "amphetamine / dextroamphetamine"


def test_r3_transdermal_film_extended_release_form():
    assert normalize_dosage_form("Methylphenidate Film, Extended Release") == "patch"
    assert normalize_dosage_form("Tablet, Film Coated") == "tablet"
