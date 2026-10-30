# ADR 002: One DuckDB file, a YAML ontology, generated SQL views; no dbt in v1

Status: Accepted 2026-10-30 (Tech Lead; Michael may override). Covers US-2, US-3 (AC-3.1), US-6 (AC-6.4, 6.5).

## Context
- The spec asks for one ontology file as the single source for tables and models (SPEC section 3).
- Object views must merge pipeline data with user edits and show a new edit at once, without a rebuild (AC-6.5).
- The data is small: about 1,600 records a day.
- RAM is limited to about 2 GB.
- Michael's usual stack is DuckDB/PostgreSQL + dbt + pandas.

## Options
**Storage**
1. **DuckDB, one file per mode** (`data/demo/`, `data/live/`). No server, and already proven in vaccine-assay-pipeline and clinical-data-review. Allows one writer process at a time, with no triggers and no REVOKE.
2. **PostgreSQL.** Real triggers and REVOKE for append-only, and multi-user. But it needs a server (about 100-300 MB resident on Windows), credentials, and a harder one-command run.
3. **SQLite.** Light, but weaker analytics SQL and nothing to reuse from the other projects.

**Ontology representation**
- A. **YAML file -> generated DuckDB views + frozen dataclasses (`ontokit.ontology`).** Readable, easy to diff, and it maps onto Foundry object, link and action types.
- B. **Python classes as the source** (pydantic/SQLModel). Adds a dependency, and YAML is easier to show next to Foundry.
- C. **dbt models with `schema.yml` as the ontology.** dbt materializes at build time, so an action's edit would not show until the next `dbt run`, which breaks AC-6.5. It also adds a second process and about 200 MB at build.

## Choice
Storage option 1 with ontology option A, and **no dbt in v1**. Transforms are pandas plus SQL in `pipeline/`. The object and link views are plain `CREATE VIEW`s, generated from the YAML on every run.

Schema ownership (the AC-6.4 test relies on this):
- **Pipeline-owned:** `stage, core, seed, obj, link, mart`. These are dropped and rebuilt every run.
- **Action-owned:** `ops, audit`. These are created `IF NOT EXISTS` only. The pipeline never drops them or writes to them.

`ontokit.ontology.check_backing` verifies that each declared view exists and that its key is unique. That is the AC-3.1 test, driven by the YAML, so adding an object type without a backing view fails the tests.

## Consequences
- The DuckDB limits (one writer, no DB-level append-only) carry into ADR 003 and KNOWN_ISSUES.
- Dropping dbt is a visible break from Michael's usual stack. The reason is AC-6.5 plus RAM, not taste. If a later version needs heavier marts, dbt can own `mart.*` only, and the ontology views stay in Python.
- The YAML is the contract for the optional Palantir Developer Tier rebuild (DESIGN section 11).
