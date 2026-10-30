"""ML-02 tests for ontokit.ontology and ontology/ontology.yaml (AC-3.1)."""
import copy
from pathlib import Path

import duckdb
import pytest
import yaml

from ontokit.ontology import OntologyError, check_backing, load_ontology, parse_ontology

ONTO = Path(__file__).resolve().parents[1] / "ontology" / "ontology.yaml"


@pytest.fixture()
def raw():
    return yaml.safe_load(ONTO.read_text(encoding="utf-8"))


def test_project_ontology_has_5_objects_6_links_6_actions():
    onto = load_ontology(ONTO)
    assert set(onto.objects) == {"Drug", "ShortageEvent", "Substitute", "MitigationTask", "Facility"}
    assert len(onto.links) == 6
    assert set(onto.actions) == {"propose_substitute", "approve_substitute", "reject_substitute",
                                 "open_mitigation_task", "close_mitigation_task", "reassign_task"}
    assert onto.actions["approve_substitute"].roles == frozenset({"pharmacist"})
    assert all("viewer" not in a.roles for a in onto.actions.values())
    assert "alt_not_short" in onto.actions["approve_substitute"].criteria


def test_bad_link_reference_raises(raw):
    bad = copy.deepcopy(raw)
    bad["links"]["drug_shortage_event"]["to"] = "Nope"
    with pytest.raises(OntologyError, match="unknown object type"):
        parse_ontology(bad)


def test_bad_action_reference_and_role_raise(raw):
    bad = copy.deepcopy(raw)
    bad["actions"]["approve_substitute"]["object_type"] = "Ghost"
    with pytest.raises(OntologyError):
        parse_ontology(bad)
    bad = copy.deepcopy(raw)
    bad["actions"]["approve_substitute"]["roles"] = ["pharmacist", "admin"]
    with pytest.raises(OntologyError, match="unknown roles"):
        parse_ontology(bad)


def test_missing_key_raises(raw):
    bad = copy.deepcopy(raw)
    del bad["objects"]["Drug"]["key"]
    with pytest.raises(OntologyError, match="key"):
        parse_ontology(bad)


def _toy():
    return parse_ontology({
        "roles": ["r"],
        "objects": {"Thing": {"key": ["id"], "view": "obj.thing", "source": "t", "properties": {"id": "v"}},
                    "Other": {"key": ["oid"], "view": "obj.other", "source": "o", "properties": {"oid": "v"}}},
        "links": {"thing_other": {"from": "Thing", "to": "Other", "cardinality": "1-*",
                                  "view": "link.thing_other", "from_key": "id", "to_key": "oid"}},
        "actions": {},
    })


def test_check_backing_reports_missing_view_and_duplicate_key():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA obj; CREATE SCHEMA link")
    con.execute("CREATE VIEW obj.thing AS SELECT * FROM (VALUES ('a'), ('a'), ('b')) v(id)")
    problems = check_backing(con, _toy())
    assert any("duplicate keys in obj.thing" in p for p in problems)
    assert any("obj.other is missing" in p for p in problems)
    assert any("link.thing_other is missing" in p for p in problems)


def test_check_backing_clean():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA obj; CREATE SCHEMA link")
    con.execute("CREATE VIEW obj.thing AS SELECT * FROM (VALUES ('a'), ('b')) v(id)")
    con.execute("CREATE VIEW obj.other AS SELECT * FROM (VALUES ('x')) v(oid)")
    con.execute("CREATE VIEW link.thing_other AS SELECT 'a' AS id, 'x' AS oid")
    assert check_backing(con, _toy()) == []
