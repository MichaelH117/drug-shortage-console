# TASKS: drug-shortage-console

Owner: Tech Lead seat. 2026-10-30. Read DESIGN.md (sections 2 to 5) and ADRs 001-005 first.

Each task lists its ACs, the files it owns (from DESIGN section 2), and what it needs. Do not edit files you do not own. If a contract has to change, ask TL and log it in HANDOFF.md.

**Seats:**
| Code | Seat | Role on this project |
|---|---|---|
| DE | data-engineer | FDE-1: ingestion, snapshots, history, synthetic hospital, audit copy, run |
| ML | ml-engineer | FDE-2: matching, ontology, actions, write-back |
| AE | analytics-engineer | metrics, triage views, app |
| QA | qa-engineer | gold set, test plan, edge and planted-bug tests |
| PE | principal-engineer | |
| OPS | devops-engineer | |
| TL | tech-lead | review |

The GxP seat is benched. Its audit.py pattern is reused through DE-08.

**Conventions (same as vaccine-assay-pipeline):**
- project `.venv`
- run with `python -m pipeline.run [--fixtures] [--test]`
- every test runs offline from `tests/fixtures/`
- the run writes `docs/results.json` and the README results block

**RAM rule:** about 2 GB is free. Do not start Postgres, Docker or dbt. Do not leave Streamlit running while pytest runs a full build.

## Build order and parallel lanes

| Phase | DE lane | ML lane | AE / QA / PE lane |
|---|---|---|---|
| 0 (now, no cross-deps) | DE-01, DE-02, DE-08 | ML-01, ML-02 | AE-01, QA-01, PE-01 |
| 1 (against contracts) | DE-03, DE-04, DE-05, DE-06 | ML-03, ML-04, ML-05 | QA-02 (after DE-02 + DE-06), AE-02 |
| 2 (integration) | DE-07 (wires run.py) | ML-06, ML-07, ML-08 | AE-03, AE-04 |
| 3 | fixes from review | fixes from review | TL-01 review, then QA-03/04, AE-05/06, OPS-01/02, PE-02/03, then product-owner sign-off |

**Hand-over points.** Log a line in HANDOFF.md when each one is done.
1. **DE-01:** skeleton, `pipeline/config.py`, `config/settings.yaml`, and `tests/conftest.py` with the socket guard and `mem_db` exist. Everyone can import them.
2. **DE-02:** fixtures exist. QA-02 and ML-04 can start on real record shapes.
3. **DE-08:** `ontokit/audit.py` `append` works. ML-04 needs it, but may code against the DESIGN section 4 signature with a local fake inside its own test file.
4. **ML-02:** `ontology.yaml` and `ontokit.ontology` exist. AE writes queries against the `obj.*`/`link.*` names in DESIGN 3.4.
5. **QA-02:** the gold set is frozen (sha256 committed). Only after that may ML-07 run the evaluation and tune the matcher.
6. **DE-07:** `python -m pipeline.run --fixtures` builds `data/demo/console.duckdb`. AE and QA switch to the real demo DB.

---

## data-engineer (DE), Forward Deployed Engineer 1

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| DE-01 | Repo skeleton: `.gitignore` (data/ except `data/demo/seed/`), `pyproject.toml`, `requirements.txt` (DESIGN 7), `.venv`, `pipeline/__init__.py`, `pipeline/config.py` (`Paths`, `paths_for`, `load_settings`, `connect` with memory_limit 512MB, threads 2), `config/settings.yaml`, `config/availability_map.yaml`, `tests/conftest.py` (socket guard on every test, a `live` marker skipped unless `DSC_LIVE=1`, `mem_db`). Test: the socket guard blocks a real connect. | AC-1.4 | listed files | nothing |
| DE-02 | Fixtures: one live pull of about 80 real records (allowed host only), trimmed into `tests/fixtures/openfda_page_sample.json`. It must include: the availability typos, at least 5 records with no `openfda`, the 0378-6860-77 / 0378-6854-77 case if it is still live, and a sentence-style availability. Then build `snapshots/SYNTHETIC_D1..D3/` (records, meta, manifest with `source: SYNTHETIC_fixture`) with planted changes covering all 6 change types. Hand-write `expected_changes.csv` **before** DE-05 code exists. Add `SYNTHETIC_renamed_field.json` (for example `availability` renamed to `availabilty`). Record the live pull in HANDOFF (AC-1.6 evidence). | AC-1.4, 1.6, 2.3 | `tests/fixtures/*` except gold files | DE-01 |
| DE-03 | `pipeline/http.py` (allow-list, `HostNotAllowed`, urllib client) and `pipeline/snapshots.py` (`fetch_all` with paging, `ensure_snapshot` with write-to-tmp-then-rename, read-only files, `_superseded/` on `--refresh`, `list_snapshots` with a sha256 re-check). Tests: a fake client returning 2 pages gives the right count; a count different from `meta.total` raises `SnapshotIncomplete` and leaves no folder; cache hit makes zero calls (the client raises on any call); a non-fda host is refused; refresh keeps the old folder. | AC-1.1, 1.2, 1.3 | `pipeline/http.py`, `pipeline/snapshots.py`, `tests/test_http.py`, `tests/test_snapshots.py` | DE-01 |
| DE-04 | `pipeline/availability.py`, `pipeline/ingest.py` (`parse_snapshot`, event_key rules from DESIGN 3.2, MM/DD/YYYY dates where an unparseable date becomes null and is counted, NDC via `pipeline.ndc.normalize_ndc`), and `pipeline/dq.py` (volume +/-20% with SKIPPED on the first snapshot, freshness 3 days, schema drift by field name, unparseable dates, unmapped availability, event_key fallbacks, duplicate keys, manifest sha mismatch). Tests: every distinct raw availability value in the fixtures maps to a bucket and unmapped values go to Unknown plus a DQ row; the renamed-field fixture FAILs check (c) and names the field; a 25% volume drop FAILs. | AC-1.5, 2.5 | `pipeline/availability.py`, `pipeline/ingest.py`, `pipeline/dq.py`, `tests/test_ingest.py`, `tests/test_dq.py` | DE-02; ML-01 for NDC (until then a local stub inside the test only) |
| DE-05 | `pipeline/history.py` (SCD2 on status, availability_normalized, shortage_reason and update_date, with a `present` flag) and `pipeline/changes.py` (the DESIGN 3.5 rule table, in order). Tests: D1-D3 counts equal `expected_changes.csv`; one test per rule row; Unknown never gives WORSENED; out-of-order D1, D3, D2 gives the same frames as D1, D2, D3. | AC-2.1, 2.3, 2.4 | `pipeline/history.py`, `pipeline/changes.py`, `tests/test_history.py`, `tests/test_changes.py` | DE-04 |
| DE-06 | `generator/hospital.py`: 3 SYNTHETIC facilities (DESIGN uses the spec names), 150-300 formulary items, `facility_stock` with days_on_hand, and 8 users (Dana Ruiz and Sam Okafor as pharmacists, Lee Chen and Priya Nair as buyers, Jordan Blake as viewer, plus 3 more, one of them inactive). At least 30% of items derive from fixture shortage records, using a variant mix of NDC formats (4-4-2, 5-3-2, 5-4-1, 11-digit, leading-zero error) and name variants (brand/generic, salt, case and whitespace). Write `variant_manifest.csv`. Everything has `is_synthetic`. Tests: same seed gives byte-identical CSVs (sha256); the share of items from the shortage list is at least 30%; every variant type is present. | AC-3.2, 3.3, 3.4 | `generator/__init__.py`, `generator/hospital.py`, `tests/test_generator.py`, `data/demo/seed/*` | DE-02 |
| DE-07 | `pipeline/schema.py` (drop and recreate only `stage, core, seed, obj, link, mart`; create `ops`/`audit` IF NOT EXISTS) and `pipeline/run.py` (`build()` in the DESIGN 4 order; flags `--fixtures --refresh --pull-only --seed --test`; lock error message; `docs/results.json` and the README results block). Add the `demo_db` fixture to conftest. Tests: build twice gives identical counts and per-table content hashes (AC-2.2); `--pull-only` with a cache makes no DB changes; act, then re-ingest, and the edit is still there (AC-6.4, written with ML-05's action). | AC-1.1, 2.2, 6.4, 5.1 (one command) | `pipeline/schema.py`, `pipeline/run.py`, `tests/conftest.py` (`demo_db`), `tests/test_run.py` | DE-03 to DE-06, ML-03 to ML-06 |
| DE-08 | `ontokit/audit.py`: copy `C:\Users\stimp\projects\vaccine-assay-pipeline\pipeline\audit.py` and adapt it to the AC-7.1 columns and ADR 003 (`append` where the caller owns the transaction, `verify_chain` with checkpoint and anchor, `write_anchor`, `update`/`delete` raising). Drop `insert_records`, `correct_record` and `reconcile` (not needed). The source comment goes at the top. Tests: a clean chain is OK; `update()` raises; a direct SQL edit of row k gives `first_broken_seq == k`; deleting the newest row is caught by the checkpoint; the anchor catches deletion even when the checkpoints are rewritten. **Deliver early: ML-04 needs it.** | AC-7.1, 7.2, 7.3, 7.5 | `ontokit/audit.py`, `tests/test_audit.py` | none (duckdb + pandas only) |

## ml-engineer (ML), Forward Deployed Engineer 2

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| ML-01 | `pipeline/ndc.py`: `normalize_ndc`, `extract_ndc_from_presentation`. Tests: one per format (4-4-2, 5-3-2, 5-4-1, 11 digits plain), letters, wrong length, empty or None, and the 10-digit-unhyphenated reason. It must never raise. | AC-4.1 | `pipeline/ndc.py`, `tests/test_ndc.py` | nothing |
| ML-02 | `ontology/ontology.yaml` (5 object types, 6 link types, 6 action types with roles and criterion ids from SPEC US-6) and `ontokit/ontology.py` (`load_ontology` that validates references; `check_backing`). `ontokit/__init__.py`. `tests/test_ontokit_boundary.py` (no imports from pipeline, console or app). Tests: a bad reference raises `OntologyError`; on a hand-made in-memory DB, a missing view or a duplicate key is reported. | AC-3.1, 7.5 | listed files, `tests/test_ontology.py` | nothing |
| ML-03 | `config/normalize.yaml`, `pipeline/names.py`, and `pipeline/match.py` (tiers 1-3 per DESIGN 5, `baseline_match`). Pure DataFrames. Tests: tier 1 uses the top-level package_ndc and **not** `openfda.package_ndc` (regression for the 0378-6860-77 case); a record with no `openfda` matches through tier 3 (AC-4.6); the best tier wins; hydroxyzine vs hydralazine does not match. Do **not** open the gold file until QA-02 is frozen. | AC-4.2, 4.6 | `config/normalize.yaml`, `pipeline/names.py`, `pipeline/match.py`, `tests/test_match.py` | ML-01, DE-02 |
| ML-04 | `ontokit/actions.py` (`run_action` per ADR 003, exceptions, `Actor`, `ActionResult`) and `ontokit/writeback.py`. Tests on `mem_db` with a toy object: role refused gives a permission error and 1 REFUSED row with no edit; a failed criterion records its id; a forced exception after the edit leaves no edit and no audit row; a successful action leaves 1 edit and 1 SUCCESS row with the same `action_seq`. | AC-6.2, 6.3, 7.1 | `ontokit/actions.py`, `ontokit/writeback.py`, `tests/test_ontokit_actions.py` | DE-08 (or a fake in the test file) |
| ML-05 | `console/identity.py` and `console/actions.py`: the 6 actions with exact SPEC US-6 criteria and the DESIGN 4 approve rule; `ops.*` DDL via `init_edit_tables`; outbox row on open and reassign. Tests: for each action, 1 allowed case and at least 1 refused case; a buyer calling `approve_substitute` raises a permission error; a viewer is refused on all 6; an inactive user is refused; approving an alternative that is itself short is refused; a double approve is refused. | AC-6.1, 6.2 | `console/__init__.py`, `console/identity.py`, `console/actions.py`, `tests/test_actions.py` | ML-02, ML-04, DE-06 (users) |
| ML-06 | `pipeline/candidates.py` (same ingredient and route, different ndc11, alternative not in an active shortage; deterministic substitute_id) and `pipeline/objects.py` (`core.drug`, `core.formulary_item_norm`, `core.formulary_match`, `core.substitute_candidate`, `create_object_views` from the YAML). Test: `check_backing` returns no problems on `demo_db` (AC-3.1). | AC-3.1, 6.4 | `pipeline/candidates.py`, `pipeline/objects.py`, `tests/test_objects.py` | ML-02, ML-03, DE-05, DE-06 |
| ML-07 | `pipeline/evaluate.py` (`python -m pipeline.evaluate`): P/R/F1 for the matcher and the baseline, overall, per tier and per variant_type; the gold sha256; pass or fail against E2 targets; the failing pairs listed; output in `reports/match_quality.json`. Then tune `config/normalize.yaml` and `names.py` only, never the gold set. Write the result and any miss honestly in HANDOFF. | AC-4.4, 4.5 | `pipeline/evaluate.py`, `tests/test_evaluate.py`, `reports/match_quality.json` | QA-02 frozen, ML-03 |
| ML-08 | Add `@pytest.mark.live` tests: a live pull count equals `meta.total` (AC-1.6), and the match rate on the live snapshot is reported (not asserted). Run them once with `DSC_LIVE=1` and paste the output into HANDOFF. | AC-1.6 | `tests/test_live.py` | DE-07 |

## analytics-engineer (AE): metrics and triage views

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| AE-01 | `docs/METRICS.md`: exact definitions of the primary metric (recall of affected formulary items surfaced, against the name-only baseline) and guardrails (a) to (e) with thresholds; time-from-detection-to-first-action, computed from `core.shortage_event.first_seen` and the first SUCCESS audit row per event_key; at least 2 gaming paths per metric and which guardrail catches each. Also the queue sort spec from DESIGN 3.4. | AC-8.1, 8.2, 8.3, 8.4 (definitions) | `docs/METRICS.md` | SPEC, DESIGN |
| AE-02 | `console/freshness.py` (`is_stale`) and `console/queries.py` (`triage_queue`, `drug_object`, `audit_entries`) plus the `mart.triage_queue` view SQL (a constant in queries.py that `pipeline/objects.py` executes; ML runs it, AE owns the text). Tests on a small hand-made DB: only NEW/WORSENED events with a facility match appear; sort order; `is_stale` boundary at exactly 1 day. | AC-5.1, 5.2, 5.3, 5.4 | `console/freshness.py`, `console/queries.py`, `tests/test_queries.py` | ML-02 names (DESIGN 3.4) |
| AE-03 | Streamlit: `app/Home.py` (the queue; header with snapshot date, `meta.last_updated`, DB mode, stale banner, SYNTHETIC label; sidebar user picker labeled "demo identity, not authentication"), `app/pages/1_Drug.py` (object view plus action forms that call `console.actions` and show `ActionRefused` messages), `app/pages/2_Audit.py` (filters and the chain-verify result with its time at the top), `app/pages/3_Match_Quality.py` (reads `reports/match_quality.json`, shows matcher vs baseline). One connection per rerun, no cached connection. | AC-3.4, 5.1-5.5, 6.5, 7.4, 4.4 (display) | `app/*` | AE-02, ML-05, DE-07 |
| AE-04 | `console/simulate.py`: replay a scripted, seeded set of demo actions with fixed `now` values, so AC-8.3 has data. Actor ids are the synthetic users. Every output and doc says "SIMULATED demo usage, not real users". `console/metrics.py`: `compute_impact_metrics` gives `reports/impact_metrics.json`; queue length on the latest live snapshot shows `n/a: 1 snapshot` when there is only one (DESIGN T2). | AC-8.2, 8.3 | `console/simulate.py`, `console/metrics.py`, `tests/test_metrics.py` | ML-05, ML-07 |
| AE-05 | `docs/impact.md`: metric, baseline and guardrails, with numbers rendered from the two report JSONs by `console.metrics` (a marker block in the file, never typed by hand). It states that the gold set is "team-labeled, reviewed_by pending". | AC-8.1, 8.2, 8.4 | `docs/impact.md` | AE-04 |
| AE-06 | Screenshots of queue -> object -> approve substitute -> audit page (with verify OK), plus one refused buyer attempt, saved to `docs/demo/`. | AC-5.6 | `docs/demo/*` | AE-03 |

## qa-engineer (QA)

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| QA-01 | `docs/team/TEST_PLAN.md`: a trace from every AC in SPEC to a test name (or LIVE/manual), the planted-bug list, and the offline proof. | all | `docs/team/TEST_PLAN.md` | DESIGN |
| QA-02 | **Gold set (EM decision E1).** Draft at least 60 pairs in `tests/fixtures/gold_matches.csv` using the DESIGN 5 rule and mix (at least 35 positives, at least 25 hard negatives, at least 3 per variant type, at least 10 hand-written cases outside the generator rules). Each row has a one-line `rationale`, `labeled_by = qa-engineer`, and `reviewed_by = pending`. The labeling rule goes in the file header. Commit `gold_matches.sha256` and `tests/test_gold_frozen.py`, which checks the hash, the column set, `reviewed_by` = `pending` on every row, and the counts. Do not run the matcher before freezing. **Never write Michael as the reviewer.** | AC-4.3, 4.5 | `tests/fixtures/gold_matches.csv`, `gold_matches.sha256`, `tests/test_gold_frozen.py` | DE-02, DE-06 |
| QA-03 | `tests/qa/`: edge tests (empty API page, `meta.total` = 0, duplicate event_key, a 10-digit unhyphenated NDC, a due date today versus yesterday, a resolution note of 9 versus 10 characters, reassigning to the same person, a refused-row write failure); a reconcile test (every edit row has exactly 1 SUCCESS audit row and the reverse); `generator/tamper.py` helpers. | AC-6.x, 7.3 | `tests/qa/*`, `generator/tamper.py` | DE-07 |
| QA-04 | Planted-bug proofs: (1) tier 1 switched to `openfda.package_ndc`, (2) `approve_substitute` roles widened to buyer, (3) the pipeline drops `ops`. Each must make at least one test fail. Record the output in `docs/team/BUGS.md`, then revert. | research signal 4 | `docs/team/BUGS.md` | QA-03 |

## principal-engineer (PE)

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| PE-01 | `docs/decomposition.md` (US-9: parts, real data sources marked as assumptions, data owners, rollout with one site and a shadow week, training, week 1/4/12 measures, top risks, a Foundry concept map with palantir.com/docs links, the "open-source stand-in, not Foundry" statement, a URL or UNVERIFIED on every claim). | AC-9.1, 9.2, 9.3 | `docs/decomposition.md` | SPEC, DESIGN |
| PE-02 | `docs/RISK_REGISTER.md` (SPEC R1-R14 plus DESIGN T1-T14, each with an owner and a status) and `docs/KNOWN_ISSUES.md` (no auth, single writer, DuckDB detect-not-prevent, team-labeled gold set, synthetic history days, openFDA not for clinical use). | SPEC R13 | listed files | TL-01 |
| PE-03 | README pass: wording check against overclaiming (research section 5), results block, screenshots, a roadmap including the optional Developer Tier phase. | SPEC R13 | `README.md` (shared with OPS: PE owns the prose, OPS owns the quickstart section) | AE-05, AE-06 |

## devops-engineer (OPS), light duty

| ID | Task | ACs | Owns | Needs |
|---|---|---|---|---|
| OPS-01 | Pin exact versions in `requirements.txt` from the `.venv`; `README.md` quickstart (`python -m venv .venv`, install, `python -m pipeline.run --fixtures --test`, `streamlit run app/Home.py`); `run_demo.ps1` doing both. Measure peak memory of the build plus the app once and write it in HANDOFF (DESIGN T1). | AC-5.1 | `requirements.txt` (pins), `run_demo.ps1`, README quickstart section | DE-07, AE-03 |
| OPS-02 | Optional light CI: a GitHub Actions workflow file that runs ruff and pytest offline. Write the file only; Michael pushes. A scheduled daily pull is **not** created (E4: Michael approves it first). Document the `--pull-only` command a scheduler would call. | E4 | `.github/workflows/ci.yml`, README "daily pulls" note | OPS-01 |

## tech-lead (TL)

| ID | Task | Owns |
|---|---|---|
| TL-01 | Review the Phase 1-2 diffs in priority order: correctness (event_key, tier 1 source, transaction boundaries, schema-drop scope), then needless complexity (ontokit has no feature without a caller), then missing tests. Output `docs/team/REVIEW.md`. Approve contract changes and log them in DESIGN section 12 (to be added). | REVIEW.md, DESIGN.md, adr/* |

---

## Story coverage (every story has at least one task)

| Story | Tasks |
|---|---|
| US-1 Ingest and cache | DE-01, DE-02, DE-03, DE-04, DE-07, ML-08 |
| US-2 Snapshots and history | DE-04, DE-05, DE-07 |
| US-3 Ontology and synthetic hospital | ML-02, ML-06, DE-06, AE-03 (label) |
| US-4 Matching quality | ML-01, ML-03, ML-07, QA-02, AE-03 (page) |
| US-5 Triage queue | AE-02, AE-03, AE-06, DE-07, OPS-01 |
| US-6 Actions and write-back | ML-04, ML-05, ML-06, DE-07, QA-03 |
| US-7 Hash-chained log | DE-08, ML-02 (boundary), AE-03 (audit page), QA-03 |
| US-8 Metric and guardrails | AE-01, AE-04, AE-05 |
| US-9 Decomposition | PE-01 |

## Cut list if late (SPEC R14)
These are cut in this order:
1. OPS-02
2. AE-06 cut to 3 screenshots
3. the anchor part of DE-08 (the checkpoint stays)
4. ML-08 (run by hand once instead)

Never cut these:
- the gold-set freeze
- the AC-6.3 transaction test
- the offline guard

---

## Round 2 (2026-10-30): review fixes (REVIEW.md) and ADR 007

| ID | Seat | Task | Covers | Needs |
|---|---|---|---|---|
| ML-09 | ml-engineer | REVIEW 1: propose refuses an already approved/rejected substitute (new criterion in `ontology.yaml` + code) | US-6 (AC-6.1, 6.2) | none |
| ML-10 | ml-engineer | REVIEW 3: refused open-task rows logged on the event, not a speculative task id; REVIEW 7: refuse a duplicate open task for the same event+facility | US-6, US-7 (AC-7.1) | ML-09 (same file) |
| ML-11 | ml-engineer | ADR 006 conditions: ingredient-set guard (REVIEW 2), brand key not a component (REVIEW 4), "Film, Extended Release" form, test pinning the name-only trade; score v3 once on QA's new held-out set | US-4 (AC-4.4, 4.5) | QA-06 |
| ML-12 | ml-engineer | REVIEW 9-11: narrow `_next_seq` except, `_reason` field from the caller, NULL-edit note in writeback | US-6, ADR 005 | none |
| DE-09 | data-engineer | REVIEW 6: `build_events(stage, snapshot_dates)`; REVIEW 5: `_build_ml` inside the rebuild transaction (or swap a temp DB); REVIEW 12: port check + no env proxy | US-1 (AC-1.3), US-2, US-6 (AC-6.4) | after the concurrency root cause |
| AE-07 | analytics-engineer | ADR 007: `mart.triage_queue` excludes baseline rows (`prev_snapshot_date IS NULL`), actionable filter, rank, `actionable_total`; new `mart.baseline_worklist` (item grain) and `mart.watch_list` | US-5 (AC-5.1, 5.2) | after the v2-metrics switch |
| AE-08 | analytics-engineer | ADR 007: Home baseline banner + worklist, Watch list, top-40 cap with "N more"; guardrail (b) on the uncapped daily queue; METRICS.md gaming path for the filter; REVIEW 13 note on backfill | US-5, US-8 (AC-8.2, 8.4) | AE-07 |
| QA-05 | qa-engineer | Tests for REVIEW 1, 3, 7 and ADR 007 (single-snapshot DB -> empty daily queue + worklist; Available excluded; deterministic rank; cap does not change guardrail b; backfill moves the baseline) | US-5, US-6, US-8 | ML-09, ML-10, AE-07 |
| QA-06 | qa-engineer | Freeze a new held-out set (holdout_v3) with combination-product and brand-component cases; do not show it to ML | US-4 (AC-4.5) | none |
| PO-07 | product-owner | Amend AC-5.1 and AC-8.2 (b) per ADR 007; Michael confirms cap 40 | US-5, US-8 | none |
| PE-07 | principal-engineer | RISK_REGISTER / KNOWN_ISSUES: replace T2 with ADR 007; add REVIEW 8 (failed attempts leave no audit row) | US-8 | AE-08 |

Story coverage of round 2: US-1 DE-09; US-2 DE-09; US-4 ML-11, QA-06; US-5 AE-07, AE-08, QA-05, PO-07; US-6 ML-09, ML-10, ML-12, DE-09, QA-05; US-7 ML-10; US-8 AE-08, PO-07, PE-07.
