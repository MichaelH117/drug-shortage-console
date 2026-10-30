"""ontokit: an in-repo, domain-free library (ADR 005).

Modules: ontology (YAML loader + backing check), actions (runner with submission criteria),
writeback (edit tables + latest-edit views), audit (hash-chained action log, owned by DE).
It must not import pipeline, console or app (tests/test_ontokit_boundary.py).
"""
