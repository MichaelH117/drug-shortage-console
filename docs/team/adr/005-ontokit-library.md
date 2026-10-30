# ADR 005: `ontokit`, an in-repo, domain-free library for ontology, actions, write-back and audit

Status: Accepted 2026-10-30 (Tech Lead acting as Product Engineer; Michael may override). Covers AC-7.5 and the team rule: "pull reusable parts into a library."

## Context
A product team builds one capability for many customers, and a deployment team builds many capabilities for one customer. This project is a single-customer deployment, but four parts are generic:
- the YAML ontology loader
- the action runner with submission criteria
- the hash-chained audit log
- the edit-merge view helper

AC-7.5 forbids imports across project folders, and the vaccine project already holds one copy of the audit code.

## Options
1. **An in-repo package, `ontokit/`.** It must not import `pipeline`, `console` or `app`, a test enforces that, and it can be lifted into its own repo later.
2. **A separate shared repo** installed with `pip install -e ../ontokit`. This is a dependency across project folders, which goes against the intent of AC-7.5, and it means versioning work before there is a second user.
3. **No library.** Everything goes in `console/`. This is the simplest, but it mixes generic rules with domain rules.

## Choice
Option 1. `ontokit` contains `ontology.py`, `actions.py`, `writeback.py` and `audit.py`. Domain code (criteria predicates, the six actions, NDC logic) stays in `console/` and `pipeline/`. `tests/test_ontokit_boundary.py` parses the imports in `ontokit/*.py` and fails on any import from `pipeline`, `console` or `app`.

## Consequences
- The audit code now exists in two places (vaccine and here). AC-7.5 accepts that. When a third project needs it, `ontokit` moves to its own repo and every project pins a version.
- There is a small cost in indirection, because the runner takes `criteria` and `apply` callables. Rule: no new `ontokit` feature without a current caller in this repo.
