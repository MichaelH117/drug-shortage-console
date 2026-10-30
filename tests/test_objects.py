"""ML-06 tests: substitute candidates (pure) and ontology backing on the demo build (AC-3.1)."""
import pandas as pd
import pytest

from pipeline.candidates import substitute_candidates, substitute_id_for


def test_candidates_same_ingredient_route_not_short_deterministic():
    drugs = pd.DataFrame([
        {"ndc11": "A0000000001", "product_ndc9": "A00000000", "ingredient_norm": "x", "dosage_form_norm": "injection",
         "route_norm": "intravenous", "strength_norm": "1 mg/ml"},
        {"ndc11": "B0000000001", "product_ndc9": "B00000000", "ingredient_norm": "x", "dosage_form_norm": "injection",
         "route_norm": "intravenous", "strength_norm": "1 mg/ml"},      # good candidate
        {"ndc11": "C0000000001", "product_ndc9": "C00000000", "ingredient_norm": "x", "dosage_form_norm": "tablet",
         "route_norm": "oral", "strength_norm": "5 mg"},                 # wrong route
        {"ndc11": "D0000000001", "product_ndc9": "D00000000", "ingredient_norm": "x", "dosage_form_norm": "injection",
         "route_norm": "intravenous", "strength_norm": "1 mg/ml"},      # itself short
        {"ndc11": "E0000000001", "product_ndc9": "E00000000", "ingredient_norm": "y", "dosage_form_norm": "injection",
         "route_norm": "intravenous", "strength_norm": "1 mg/ml"},      # other ingredient
    ])
    events = pd.DataFrame([
        {"event_key": "A0000000001|d", "ndc11": "A0000000001", "product_ndc9": "A00000000", "present_in_latest": True,
         "status": "Current", "availability_normalized": "Limited"},
        {"event_key": "D0000000001|d", "ndc11": "D0000000001", "product_ndc9": "D00000000", "present_in_latest": True,
         "status": "Current", "availability_normalized": "Unavailable"},
    ])
    matches = pd.DataFrame([{"item_id": "I", "event_key": "A0000000001|d"}])
    c = substitute_candidates(drugs, events, matches)
    assert list(c.alt_ndc11) == ["B0000000001"]
    assert c.iloc[0].substitute_id == substitute_id_for("A0000000001|d", "B0000000001")
    assert substitute_candidates(drugs, events, matches).equals(c)


def test_check_backing_on_demo_db(request):
    """AC-3.1: every object and link type has a backing view with a unique, non-null key."""
    pytest.importorskip("pipeline.run", reason="DE-07 (pipeline.run) not built yet")
    demo_db = request.getfixturevalue("demo_db")
    from console.actions import ontology
    from ontokit.ontology import check_backing

    assert check_backing(demo_db, ontology()) == []
