# HANDOFF log: drug-shortage-console

## 2026-10-30 | product-owner (Deployment Strategist)
- **Did:** Created the project folder and docs/team/. Wrote SPEC.md: verified facts about the openFDA Drug Shortages API, persona (Dana Ruiz, PharmD, shortage coordinator at the synthetic Riverbend Health) and her morning decision, PR/FAQ, ontology (5 object types, 6 links), 9 user stories with testable ACs, the Streamlit rationale, out of scope, 14 risks, and 5 open decisions.
- **Files:** docs/team/SPEC.md (new), docs/team/HANDOFF.md (new).
- **Checks run (live, 2026-10-30):**
  - `api.fda.gov/drug/shortages.json?limit=2` returned 1614 records total, `last_updated` 2026-10-29.
  - `count=status`: Current 1153, To Be Discontinued 442, Resolved 19.
  - `count=availability` returned typo values ("Unvailable", "Limited Availalbility").
  - `_missing_:openfda` matched 323 records.
  - No code exists yet, so no tests were run.
- **Open issues:**
  - The `openfda.package_ndc` vs top-level `package_ndc` mismatch was seen in 1 record only (UNVERIFIED).
  - The persona workflow is not validated with a real pharmacist (UNVERIFIED).
  - Five decisions are waiting on Michael (SPEC section 9).
- **Next:** tech-lead (DESIGN.md, ADRs, TASKS.md).

## 2026-10-30 | tech-lead (Tech Lead / Solution Architect, also Product Engineer)
- **Did:**
  - Wrote DESIGN.md: architecture, folder layout with file ownership, table contracts, change-classification rules, Python interface signatures, matching tiers, gold-set labeling rule and freeze, test strategy, cross-cutting concerns, 14 design risks (T1-T14), and a story-to-component map.
  - Wrote 5 ADRs: 001 Streamlit, 002 DuckDB + YAML ontology + generated views (no dbt in v1), 003 actions / write-back / hash-chained audit, 004 write-once snapshots with full rebuild, 005 the in-repo `ontokit` library.
  - Wrote TASKS.md: 31 tasks across DE, ML, AE, QA, PE, OPS and TL, in 4 phases with parallel lanes and a story-coverage table. All of US-1 to US-9 are covered.
  - Applied the EM decisions E1-E5:
    - QA drafts the gold set with `reviewed_by: pending`, so the matcher author never labels it.
    - The spec targets are kept.
    - The snapshot store's slot-in contract covers backfills and future scheduled pulls.
    - The Developer Tier is an optional phase in DESIGN section 11.
- **Files:** docs/team/DESIGN.md, docs/team/TASKS.md, docs/team/adr/001-005 (all new); HANDOFF.md (appended).
- **Checks run:** documents only. No product code exists, so no tests were run. I read vaccine-assay-pipeline audit.py, ADR 001 and the TASKS format to mirror their conventions.
- **Open issues:**
  - Leaving out dbt is a deliberate break from Michael's usual stack (ADR 002); he may override it.
  - On the first live day every event is NEW, so the live queue guardrail is n/a until there are 2 live snapshots (T2).
  - The gold set is team-labeled until Michael reviews it (T4).
  - The fixture history days are synthetic (T5).
- **Next:** in parallel, data-engineer (DE-01, DE-02, DE-08), ml-engineer (ML-01, ML-02), analytics-engineer (AE-01), qa-engineer (QA-01), principal-engineer (PE-01).

## 2026-10-30 | ml-engineer (FDSE-2) | hand-over point ML-02
- `ontology/ontology.yaml` and `ontokit/ontology.py` exist (5 objects, 6 links, 6 actions). AE can query the `obj.*`/`link.*` names from DESIGN 3.4. `pipeline/ndc.py` (ML-01) is also done: `ndc11` is 11 plain digits, `product_ndc9` its first 9. Tests: test_ndc 20 passed, test_ontology + boundary 7 passed.

## 2026-10-30 | qa-engineer (QA / Test Engineer) | QA-01, QA-02 (frozen), QA-03 (action edges, xfail)
- **Did:**
  - **QA-02 gold set, LOCKED.** One polite openFDA pull (`limit=1000`, one call, kept in scratch only; `meta.total` 1614, `last_updated` 2026-10-29). DE-02/DE-06 outputs were not usable yet, so the gold set is **self-contained**:
    - `tests/fixtures/gold_events.json`: 30 real records, verbatim, 8 with no `openfda`.
    - `tests/fixtures/SYNTHETIC_gold_formulary.csv`: 86 hand-written SYNTHETIC items, with the same columns as `seed.formulary_item`.
    - `tests/fixtures/gold_matches.csv`: 102 pairs, 57 positive and 45 hard negative. Every row has a rationale, `labeled_by=qa-engineer` and `reviewed_by=pending`. **Not reviewed by Michael.**
  - **Lock:** `tests/fixtures/gold_matches.sha256` holds the hashes of all 3 files, computed on LF-normalized bytes so a CRLF checkout does not break them. `tests/test_gold_frozen.py` enforces them. No matcher was run before the freeze.
  - **QA-01:** wrote `docs/team/TEST_PLAN.md`, which maps every AC to a test and status, plus the planted-bug list (PB-1 to PB-5) and the concurrency section.
  - **QA-03 (part):** wrote `tests/qa/test_action_edges.py`, 34 tests. They are xfail(run=False) until `console.actions` and `pipeline.run` exist, and then they run automatically. They cover:
    - wrong role, viewer, inactive and unknown users
    - closing a task twice, approving twice
    - objects that don't exist
    - reason, note and due-date boundaries, including the New York day boundary
    - concurrent approve, close and open on separate cursors (chain must verify, no duplicate task_id)
    - the edit-to-audit reconcile
    - edits surviving a full rebuild
- **@ml-engineer: the gold set is FROZEN. ML-07 may now evaluate.**
  - Evaluate `pipeline.match` on SYNTHETIC_gold_formulary.csv plus gold_events.json. Event keys use the DESIGN 3.2 rule.
  - Do not edit the 3 gold files. If you think a label is wrong, log it here and QA will issue a new version.
- **Contract notes for TL:**
  1. The gold pairs use their own item_ids (`QG-xxx`), not generator ids. This avoids T3 (overfitting to the generator).
  2. The openFDA `strength` field was empty on all 1000 records, so strength comes from the presentation text (written in the file header).
  3. Risk for DESIGN tier 2: `openfda.product_ndc` lists sibling **strengths** (for example, 0378-6860 lists 0378-6855 = 20 mg). Tier 2 through openfda will produce false positives. The gold rows of type `openfda_sibling_strength` measure this.
  4. Two rule-literal labels are flagged for owner review: Saxenda vs Victoza = 1, and multi-route lidocaine = 0.
- **Files:** tests/fixtures/{gold_matches.csv, gold_matches.sha256, SYNTHETIC_gold_formulary.csv, gold_events.json}, tests/test_gold_frozen.py, tests/qa/test_action_edges.py, docs/team/TEST_PLAN.md (all new).
- **Checks:**
  - `pytest tests -q` → 56 passed, 34 xfailed.
  - `pytest tests/test_gold_frozen.py` → 8 passed. The freeze test caught one coverage gap before the freeze; I fixed it before locking.
  - `ruff check` is clean on my files.
- **Open:**
  - The action-edge tests have not executed against real code yet.
  - Still to do: `generator/tamper.py`, the tamper and DQ edge tests (after DE-07), and QA-04 planted bugs.
  - Assumed column names `core.shortage_event.status` and `present_in_latest`.
- **Next:** ml-engineer (ML-07). QA re-runs after DE-07 and ML-05.

## 2026-10-30 | ml-engineer (Forward Deployed Software Engineer 2: matching, ontology, actions) | ML-01..ML-08
- **Did:** ML-01 NDC normalizer; ML-02 ontology YAML and loader; ML-03 normalize.yaml, names, tiered matcher and baseline; ML-04 action runner and write-back helpers; ML-05 identity and the 6 actions; ML-06 candidates, ML core tables and ontology-generated views; ML-07 evaluator and report; ML-08 live tests (written, not run). No LLM, stdlib only (no rapidfuzz, so no ADR for it).
- **ADR 006 (proposed, needs TL):** tier 2 through `openfda.product_ndc` is dropped on a dosage-form or strength conflict, and tiers 1/2 are dropped when the two names share no word. Written before the gold run. Release form (ER/DR/ODT) is part of the dosage form, following the gold header's rule, added before the run.
- **ML-07 result: one run against the frozen gold set** (sha256 c1308465..., hash OK, 102 pairs, reviewed_by pending). No tuning after the run.
  - Matcher: P 0.980, R 0.877, F1 0.926 (tp 50, fp 1, fn 7, tn 44). **Misses the recall target of 0.95** (precision target met). Tier 1: 26/0, tier 2: 6/0, tier 3: 18/1 (tp/fp). `openfda_sibling_strength` 4 pairs: 0 FP.
  - Baseline (exact lowercase generic_name): R 0.000 (0 of 57). The gold item names never equal the openFDA "X Injection"-style generic_name.
  - **Error analysis (8 failing pairs, in reports/match_quality.json):**
    - 3 FN caused by my own name guard blocking a correct NDC match: GP-004 (typo "Clonazapam", same product NDC), GP-063 ("Rifadin IV": "iv" is not a noise word), GP-098 ("Cleocin HCl": brand keys keep the salt, so it does not hit the "cleocin hydrochloride" map key).
    - 2 FN typos with no NDC (GP-014 Furosemid, GP-045 Hydroxychloroqine). Exact matching is by design; fuzzy matching would risk look-alike false matches.
    - 2 FN brands: GP-022 "Victoza pen" ("pen" not a noise word); GP-024 Saxenda never appears in the snapshot (QA flagged that label for owner review).
    - 1 FP GP-088: infiltration vs IV lidocaine. All parenteral routes are treated as compatible; QA flagged it as a rule-literal label for owner review.
  - Likely fixes, NOT applied (any re-score must be reported as "seen", not unseen): apply the name guard only when the dosage form also conflicts; strip salt and route words in brand keys; add "pen"/"iv" to noise words.
- **Files:** pipeline/{ndc,names,match,evaluate,candidates,objects}.py, config/normalize.yaml, ontology/ontology.yaml, ontokit/{__init__,ontology,actions,writeback}.py, console/{__init__,identity,actions}.py, docs/team/adr/006-matcher-guards.md, reports/match_quality.json, tests/test_{ndc,ontology,ontokit_boundary,match,ontokit_actions,actions,objects,evaluate,live}.py.
- **Checks:** my 9 test files in one pytest run: 59 passed, 3 skipped (2 live tests, plus check_backing on demo_db waiting for pipeline.run). Ruff is clean on my files. The ontokit tests use a local fake audit because ontokit/audit.py (DE-08) has not landed; they switch to the real one automatically.
- **For DE-07 (run.py):** after seed, call `pipeline.objects.build_matching(con)`, then `create_object_views(con, console.actions.ontology())`. That function calls `console.actions.init_ops` (ops/audit IF NOT EXISTS). Then call `pipeline.evaluate.run_evaluation()` or `main()`. Also writes `core.formulary_match_baseline`.
- **For AE:** the mart SQL is run from `console.queries.TRIAGE_QUEUE_SQL` (or `MART_TRIAGE_QUEUE_SQL`) if it exists.
- **Contract additions:** ObjectType/LinkType have optional `source`/`edits`/`defaults`/`sql`/`from_key`/`to_key`; `latest_edit_view_sql` has keyword-only column args; runner passes `_actor/_now/_action_seq` to criteria/apply. TL please log them in DESIGN 12.
- **Open:** ML-08 not run (needs DE-07); check_backing on demo_db not run yet; QA's 34 action edge tests not run by me.
- **Next:** qa-engineer (re-run tests/qa once DE-07 lands; decide on GP-024/GP-088 labels), tech-lead (ADR 006).

## 2026-10-30 | data-engineer (Forward Deployed Software Engineer 1) | DE-01..DE-08
- **Did:** All DE tasks. Skeleton + config (`connect` = 512MB/2 threads, `DatabaseInUse` message), socket guard on every test, `mem_db`/`demo_build`/`demo_db` fixtures. urllib client with host allow-list (also checks redirects). Write-once snapshot store (tmp+rename, read-only, `_superseded/` on refresh, sha re-check, slot-in by manifest). Ingest (event_key rules, top-level NDC only, dedupe), availability map, 12 DQ checks, SCD2 history, change feed (DESIGN 3.5), shortage_event. SYNTHETIC hospital generator with `variant_manifest.csv` (12 variant types, expected tier). `ontokit/audit.py` copied from vaccine audit.py. `schema.py` + `run.py` wired to ML's `build_matching`/`create_object_views`/`run_evaluation` (AE metrics called if present).
- **AC-1.6 live evidence:** one pull on 2026-10-30 to `data/live/raw/2026-10-30/`: 1614 records = meta.results.total 1614, 2 pages, meta.last_updated 2026-10-29. All fixtures come from this one cached pull (`tests/fixtures/build_fixtures.py`, no network).
- **Fixtures:** 80 real records (23 without openfda, all 3 typo/sentence availabilities, 0378-6860-77 R4 case, 2 real duplicate keys). SYNTHETIC_D1..D3 = 75/76/78 records with planted changes; `expected_changes.csv` hand-written first, and the classifier matches it exactly (D3: NEW 3, WORSENED 4, IMPROVED 2, RESOLVED 1, DROPPED 1).
- **Files:** .gitignore, .gitattributes, pyproject.toml, requirements.txt, config/settings.yaml, config/availability_map.yaml, pipeline/{__init__,config,http,snapshots,availability,ingest,dq,history,changes,schema,run}.py, generator/{__init__,hospital}.py, ontokit/audit.py, data/demo/seed/*.csv, tests/conftest.py, tests/test_{http,snapshots,ingest,dq,changes,history,generator,audit,run}.py, tests/fixtures/{build_fixtures.py, openfda_page_sample.json, snapshots/SYNTHETIC_D1..D3, expected_changes.csv, SYNTHETIC_renamed_field.json}.
- **Checks:** DE tests 107 passed. `python -m pipeline.run --fixtures --test`: build OK (0.8 s), full pytest **209 passed, 2 skipped (live), 0 failed**. `python -m pipeline.run` (live, from cache, zero calls): 1609 events, all NEW (T2). Rebuild twice gives identical per-table hashes; edit survives re-ingest; ruff clean on DE files.
- **Open issues:** (1) openFDA `strength`, `proprietary_name`, `type` were absent on all 1614 live records; strength is only in `presentation`. (2) Generator-based detection: recall 0.936 (73/78), 0 false matches; misses in name_salt 2, alt_manufacturer 2, name_case_whitespace 1. Gold-set matcher R 0.877 < 0.95 target (ML's report). (3) Baseline gets 0 matches on both sets because formulary names never include the dosage form that openFDA `generic_name` carries; ML/AE should say so so it is not read as a straw man. (4) Live run also writes `reports/*.json` (shared path with demo). (5) The project folder has no own git repo (it sits in the home-folder repo); nothing committed.
- **Next:** analytics-engineer (mart SQL, app, metrics on `data/demo/console.duckdb`) and qa-engineer (re-run tests/qa on the real demo DB).

## 2026-10-30 | qa-engineer (QA / Test Engineer) | v2 round: held-out set, baseline_v2 spec, action re-run
- **Did:**
  - Locked a new held-out answer set for v2 scoring: `tests/fixtures/holdout_v2.csv` plus its event and formulary files, frozen by `holdout_v2.sha256`.
    - Its contents are deliberately not described here. **Matcher authors: do not open it before scoring.**
    - `reviewed_by = pending` on every row (not reviewed by Michael).
    - It shares no records or items with gold. I built it from the same single pull as the gold set, with no new network call.
  - Added the `baseline_v2` spec (normalized generic name with dosage-form words stripped) to TEST_PLAN.md section 6.2 for ml-engineer.
  - Re-ran the action edge tests on real code: 34 of 34 pass, and I removed their xfail guard.
  - Created BUGS.md. No product bugs found.
- **Files:**
  - New: tests/fixtures/{holdout_v2.csv, holdout_v2.sha256, SYNTHETIC_holdout_v2_formulary.csv, holdout_v2_events.json}, tests/test_holdout_frozen.py, docs/team/BUGS.md.
  - Edited: tests/qa/test_action_edges.py, docs/team/TEST_PLAN.md.
- **Checks:**
  - `pytest tests -q` → 219 passed, 2 skipped (live), 0 failed.
  - `ruff check` is clean on my files.
- **Open:**
  - The held-out files sit in the repo, so the "don't look" rule depends on discipline. Moving them outside ml-engineer's working area is an EM call.
  - Still to do: QA-04 planted bugs, `generator/tamper.py`, and the DQ edge tests.
- **Next:** ml-engineer (implement baseline_v2, then score v2 on gold as seen and on the held-out set as unseen).

## 2026-10-30 | ml-engineer (FDSE-2) | v2 matching round: fixes, baseline_v2, single scored run
- **Did:**
  - Froze v1 as `reports/match_quality_v1.json` (sha256 b20c565b...).
  - v2 matcher fixes (ADR 006 addendum):
    - The NDC/name guard now needs a form or strength conflict too.
    - Strict near-miss spelling in tier 3: stdlib only; needs ratio >= 0.88, at most 2 edits and equal strength; look-alike list.
    - Brand keys drop salt, route and device words.
    - Strength comes from `presentation` (test added).
  - `baseline_v2` implemented exactly per TEST_PLAN 6.2 (`pipeline.match.base_name`), with QA's 5 tests.
  - Code was final before scoring: sha256 prefixes match.py c70a98d9, names.py 2d000af8, normalize.yaml 359dfa19.
  - I did not open holdout_v2.csv. I ran `python -m pipeline.evaluate --v2 ...` ONCE and wrote `reports/match_quality_v2.json`.
- **Results (all gold labels team-drafted, reviewed_by pending):**

  | Set | Matcher P / R / F1 | baseline v1 R | baseline_v2 P / R |
  |---|---|---|---|
  | holdout_v2, UNSEEN, 87 pairs (hash OK) | **0.957 / 0.936 / 0.946** (tp 44, fp 2, fn 3). **Misses R >= 0.95.** | 0.000 | 0.579 / 0.234 |
  | v1 gold, SEEN, 102 pairs (optimistic: v2 fixes came from its errors) | 0.982 / 0.982 / 0.982 (passes) | 0.000 | 0.650 / 0.684 |

- **Holdout error analysis (post-run, nothing retuned):**
  - FP HP-006 and HP-007 (tier 2 via openfda product list): plain bupivacaine vs bupivacaine + epinephrine. The first strength (5 mg/mL) and form agree, so the tier-2b guard passes. It should also require the same ingredient components, so a combination never matches a single agent.
  - FN HP-051: **regression from my v2 brand-key change.** The presentation segment "Amphetamine Aspartate Monohydrate" became brand key "amphetamine", which expanded the mixed-salts generic into one garbled ingredient. Fix: never use a brand key that is itself an ingredient of its generic. The item name "Dextroamphetamine-Amphetamine Mixed Salts" (hyphen, "mixed salts") is also not split.
  - FN HP-050: dosage form "Film, Extended Release" (a transdermal methylphenidate) maps to no bucket, so tier 3 cannot run. It is a normalize.yaml gap.
  - FN HP-080: Vascepa does not appear in the snapshot. It can't be fixed without an external brand list (out of scope).
  - Seen set: GP-024 (Saxenda) and GP-088 (lidocaine route) remain; both are flagged by QA for owner review.
- **Files:** pipeline/{match,names,evaluate}.py, config/normalize.yaml, docs/team/adr/006-matcher-guards.md (addendum), tests/test_match.py, tests/test_evaluate.py, reports/match_quality_v1.json, reports/match_quality_v2.json.
- **Checks:** my 9 test files in one run: 71 passed, 2 skipped (live). Ruff is clean on pipeline/. `holdout_v2` and gold hashes are both OK in the report.
- **Open:**
  - Any v3 fix for the 4 classes above must be scored on a NEW held-out set; holdout_v2 is now seen.
  - The report key `baseline` is still the v1 exact baseline; AE should show `baseline_v2` next to it.
  - ML-08 live run not done yet.
- **Next:** qa-engineer (review the v2 numbers, BUGS.md for the HP-051 regression), tech-lead (ADR 006 + addendum).

## 2026-10-30 | analytics-engineer (metrics, triage views, app) | AE-01..AE-06
- **Did:**
  - AE-01 `docs/METRICS.md`: the catalog (formula, grain, source, owner, threshold) for the primary metric, the baseline, guardrails (a)-(e) and time-to-first-action; the queue sort spec; 2+ gaming paths per metric and which guardrail catches each. One deviation for TL: detection time = the snapshot that first put the event *in the queue*, not `first_seen` (a WORSENED event can be first seen days before it needs action).
  - AE-02 `console/freshness.py` (`is_stale`: exactly 1 day old is not stale) and `console/queries.py` (`TRIAGE_QUEUE_SQL` for `mart.triage_queue`, `triage_queue`, `queue_for_snapshot`, `drug_object`, `audit_entries`, `action_permission`, which reads roles from `console.actions.ontology()`).
  - AE-03 app: `app/Home.py` (since-yesterday queue with NEW/WORSENED badges, KPI strip, "filtered out" list, audit activity rail), `app/pages/1_Drug.py` (Drug/ShortageEvent object card, sites, SCD2 history, substitutes, tasks, object audit; all 6 actions as forms; buttons disabled with the reason when the role is not allowed; a "send anyway" button proves the server-side refusal), `2_Audit.py` (chain verify and time at the top, filters), `3_Match_Quality.py` (red BELOW TARGET banner, failing pairs), and `4_Metrics.py`. Shared chrome is in `app/ui.py`; the SYNTHETIC banner is on every page. Theme is in `.streamlit/config.toml` (localhost only, no telemetry).
  - AE-04 `console/simulate.py` (14 scripted steps with fixed `now`: 12 SUCCESS and 2 REFUSED on role; every reason is prefixed `[SIMULATED]`; idempotent; refuses a DB with live snapshots) and `console/metrics.py` (`compute_impact_metrics`, a pure read; CLI `python -m console.metrics`).
  - AE-05 `docs/impact.md`: the numbers block is rendered by code between the markers.
  - AE-06: 6 screenshots in `docs/demo/` (queue; buyer with approve locked; after the pharmacist approves; audit verify OK with the REFUSED row; metrics; match quality). Taken with the already-installed Chrome via its DevTools port, against a **scratch copy** of the demo DB. Server and Chrome were stopped afterward.
  - `app/README.md`: the business question for each page, the honest matcher numbers (R 0.877 misses 0.95; P 0.980), and the click-through.
- **Demo DB changes:** I ran `create_mart_views` (ML's function) and `python -m console.simulate` on `data/demo/console.duckdb`. I did NOT rebuild, so `reports/match_quality.json` was not rewritten while ML works.
- **Results:** primary metric FAIL (0.877 vs 0.95; baseline 0.000). Guardrails a/c/d/e PASS. (b) N/A: 1 live snapshot, and that first live day's queue would hold 663 items. Time to first action (SIMULATED): 11 affected events, 8 actioned, median 60 min.
- **Files:** console/{queries,freshness,metrics,simulate}.py, app/{Home.py,ui.py,README.md}, app/pages/{1_Drug,2_Audit,3_Match_Quality,4_Metrics}.py, .streamlit/config.toml, docs/{METRICS.md,impact.md}, docs/demo/01-06*.png, reports/impact_metrics.json, tests/test_{queries,metrics,app}.py.
- **Checks:**
  - AE tests: 34 passed. AppTest covers all 5 pages, the approve button per role, a real approve via the form, and a forced buyer approve refused (`role`). test_app allows loopback sockets only, because AppTest's asyncio loop on Windows needs a socketpair; external hosts stay blocked.
  - Full `pytest`: **256 passed, 1 failed, 2 skipped**. The failure was `tests/qa/test_action_edges.py::test_concurrent_close_only_one_wins` (QA's file). It passed alone and in 3 reruns of that file (34/34 each), so it looks **flaky**.
  - ruff is clean on my files.
- **Open issues:**
  - @qa-engineer: `test_concurrent_close_only_one_wins` is intermittent.
  - @devops: `run_demo.ps1` should call `python -m console.simulate` after the build so the demo has activity. The README quickstart can link app/README.md.
  - The `docs/results.json` and README results block were not regenerated (no `--test` build, to avoid touching ML's report mid-round).
  - When the v2 matcher and the new gold set land, rerun `python -m pipeline.run --fixtures` then `python -m console.metrics`. If the simulation's queue positions change, it fails loudly by design.
- **Next:** qa-engineer.

## 2026-10-30 | tech-lead (Tech Lead / Solution Architect) | TL-01 code review, ADR 006 decision, ADR 007
- **Did:** Reviewed the action layer, write-back, snapshot rebuild, matcher, ontokit and http.py. Wrote REVIEW.md: 14 findings, 2 of them S1. Approved ADR 006 + addendum **with conditions**. Wrote ADR 007 for the first-day queue flaw: the first snapshot is a baseline; the queue holds only actionable items, ranked, with a visible cap; there is a one-time item-grain baseline worklist. Added DESIGN section 12 (contract log) and the round-2 tasks to TASKS.md. I edited no code (DE and AE were working in parallel).
- **Top findings:**
  - (1) S1: a buyer can undo a pharmacist's approval by re-proposing (reproduced).
  - (2) S1: tier 2b matches bupivacaine to bupivacaine+epinephrine (reproduced).
  - (3) S2: refused open-task audit rows land on the next real task's id (reproduced).
  - (5) S2: the rebuild is not atomic around `_build_ml`.
  - (6) S2: `present_in_latest` is wrong when the latest snapshot is empty.
- **Live first-day numbers (read-only DB copy):** 663 queue events = 83 formulary items. Under the ADR 007 filter that becomes 73 items, or 49 without discontinuations. 386 of the 663 were Current + Available.
- **Files:** docs/team/REVIEW.md (new), docs/team/adr/007-baseline-snapshot-and-actionable-queue.md (new), docs/team/adr/006-matcher-guards.md (status + decision), docs/team/DESIGN.md (section 12), docs/team/TASKS.md (round 2), HANDOFF.md.
- **Checks:**
  - 7 test files (match, history, changes, http, ontokit_boundary, snapshots, ontokit_actions): **73 passed**.
  - I did not run the full suite, to avoid clashing with the DE and AE work.
  - Repro scripts and DB copies live in the session scratchpad only.
- **Open:**
  - The SPEC wording for ADR 007 needs product-owner and Michael (cap 40).
  - REVIEW 14 waits on DE's root cause.
- **Next:** ml-engineer (ML-09, ML-10, ML-11), qa-engineer (QA-06, then QA-05), analytics-engineer (AE-07/08 after the v2 switch), data-engineer (DE-09 after the flaky-test fix), product-owner (PO-07).

## 2026-10-30 | analytics-engineer | v2 correction (EM request)
- **Did:**
  - Added `console.metrics.load_quality`. It is the one place that picks the headline: the newest matcher on an unseen set, which today is `match_quality_v2.json` `holdout_unseen` (v2: P 0.957, R 0.936, F1 0.946). Recall still misses 0.95, so it stays red.
  - History: v1 on gold (frozen `_v1.json`, R 0.877) and v2 on gold, labeled "seen, optimistic" (R 0.983).
  - Comparison: the fairer base-name baseline (P 0.579, R 0.234), with the naive exact-name baseline also shown.
  - The Match Quality and Metrics pages, METRICS.md (new section 1a plus gaming rows), impact.md (re-rendered by code) and app/README.md were updated to match.
  - Retook screenshots 05 and 06 only. The server and Chrome are stopped.
- **Checks:** run separately, one at a time: test_metrics 11 passed (adds a v2-headline test that uses a constructed report, not ML's file), test_queries 15 passed, test_app 9 passed. ruff is clean on app/ and console/.
- **Note:** ML's evaluator now adds `baseline_v2` to the build's `match_quality.json` too. The metrics pick it up as the comparison automatically.
- **Next:** qa-engineer.

## 2026-10-30 | analytics-engineer | AE-07, AE-08 (ADR 007)
- **AE-07:** `TRIAGE_QUEUE_SQL` now creates 3 views; `pipeline/objects.py` runs the one constant, and DuckDB executes all three statements.
  - `mart.triage_queue`: baseline rows excluded (`prev_snapshot_date IS NULL`), actionable filter, deterministic rank, `queue_rank`, `actionable_total`, uncapped.
  - `mart.baseline_worklist`: item grain.
  - `mart.watch_list`.
  - Deviation from the ADR wording, for TL: the filter is evaluated on each snapshot's own values (`change_feed.curr_*`, `stage.shortage_record`), not `obj.shortage_event`. It gives identical results on the latest day, keeps past days' queues correct for the secondary metric, and does not depend on DE-09's `present_in_latest` fix.
- **AE-08:**
  - Home: a baseline banner and worklist on a single-snapshot DB; otherwise the daily queue. Both are capped at 40 with "N more" and a pager. Watch list, other changes and the onboarding backlog are in expanders.
  - Guardrail (b) is measured on the uncapped queue, with the Watch size beside it; on a baseline day it is N/A and the worklist size is reported.
  - METRICS.md: section 2 rewritten, 4 new gaming rows for (b), and the REVIEW 13 backfill caveat. impact.md re-rendered from code; app/README.md updated.
  - `console/simulate.py` now targets events by name, not queue position.
  - Retook screenshot 01 only; the server and Chrome are stopped.
- **Measured:**
  - Live DB (read-only and a scratch copy): daily queue 0, worklist 73 items, Watch list 401, matching the ADR's table.
  - Demo: queue 3, Watch list 3. Secondary metric (SIMULATED): 7 affected, 6 actioned.
- **Demo DB:** `create_mart_views` re-run (ML's function). No rebuild.
- **Checks:** run separately, one at a time: test_queries 18 passed, test_metrics 11 passed, test_app 10 passed. The new tests cover a baseline-day DB (empty queue, item-grain worklist, BASELINE watch rows), Available going to Watch with queue + watch = all stocked changes, a deterministic rank, a cap of 1 leaving (b) unchanged, and the Home baseline view. ruff is clean.
- **Open:** PO-07 (SPEC wording) and Michael's OK on the cap of 40. QA-05 can reuse the `_truncate_to_baseline` pattern in tests/test_queries.py. Backfill moving the baseline is left to QA-05.
- **Next:** qa-engineer.

## 2026-10-30 | qa-engineer (QA / Test Engineer) | Round 2: QA-06 (holdout_v3), QA-05 (review attacks + ADR 007)
- **QA-06:** `tests/fixtures/holdout_v3.csv` is **locked** (`holdout_v3.sha256`). No other details here, on purpose.
- **QA-05:**
  - `tests/qa/test_review_attacks.py`, 6 tests for REVIEW 1, 3 and 7:
    - re-proposing to undo an approval or a rejection
    - a refused open filed under a future task id
    - a double open-task, plus a check that the guard does not over-block
  - All 6 passed on first run, because ML-09/10 had already landed, so none are marked xfail.
  - `tests/qa/test_queue_adr007.py`, 10 tests:
    - a single-snapshot build gives an empty queue and an item-grain worklist
    - a backfilled older day moves the baseline
    - the queue holds only actionable rows, and the Watch list holds none
    - rank is deterministic and follows the ADR keys
    - the UI cap never moves guardrail (b)
  - `tests/test_holdout_frozen.py` now checks v2 and v3, including disjointness from all earlier sets.
- **Files:**
  - New: tests/fixtures/{holdout_v3.csv, holdout_v3.sha256, SYNTHETIC_holdout_v3_formulary.csv, holdout_v3_events.json}, tests/qa/test_review_attacks.py, tests/qa/test_queue_adr007.py.
  - Edited: tests/test_holdout_frozen.py, docs/team/TEST_PLAN.md (counts and trace), docs/team/BUGS.md.
- **Checks:**
  - `pytest tests -q` (one run) → 302 passed, 2 skipped (live), 0 failed, 0 xfail.
  - `ruff check` is clean on my files.
- **Open:**
  - QA has not re-audited the owner test files against the named AC cases (TEST_PLAN rows still marked PLANNED).
  - Still to do: QA-04 planted bugs and `generator/tamper.py`.
- **Next:** ml-engineer (ML-11: score v3 once, unseen). Then QA-04.

## 2026-10-30 | data-engineer (FDSE-1) | flaky concurrency test + REVIEW #5, #6/DE-09, #12
- **Flaky root cause (real race, not the test):** two threads in one process each started a transaction, read the same chain tail (DuckDB MVCC) and picked the same `audit.action_log.seq` / `chain_checkpoint.checkpoint_seq`. The primary key stopped a chain fork, but the loser died with a raw `ConstraintException` instead of being refused. A second race: `open_mitigation_task` picked `task_id` before the transaction ("task id changed; retry"). DESIGN 8 says Streamlit makes one connection per rerun, so two sessions can hit exactly this.
- **Fix:** `ontokit/audit.py` adds `writer_lock()` (a re-entrant lock for the whole process) and `ChainConflict`, which wraps a duplicate-seq insert. **Cross-seat edits, for ML/TL to review:** `ontokit/actions.py` now runs `run_action` under the lock (the body moved to `_run_action_locked`), and `console/actions.py` picks `task_id` under the lock. The loser now sees committed state and gets a logged REFUSED. QA's test file was not edited. REVIEW #14 (MAX()+1 ids) is covered by the same lock.
- **Proof:** `tests/qa/test_action_edges.py` ran 20 times, one at a time. The 3 concurrent tests failed 0 of 20 times; before the fix, 11 of 15 runs failed. Runs 1, 5 and 6 failed elsewhere while ML was editing the action files (`due_not_past` vs the new `no_open_task` criterion; build errors). Runs 7 to 20 were all 34 passed.
- **REVIEW #5:** `_build_ml` now runs inside the rebuild transaction. Seed CSVs are written only after the commit.
- **REVIEW #6 / DE-09:** `build_events(stage, snapshot_dates)`, and run.py passes the dates.
- **REVIEW #12:** `check_host` requires port None or 443. The opener uses `ProxyHandler({})`.
- **New tests:** a rollback when matching fails; an empty latest snapshot leaves `present_in_latest` false (unit and end to end); the port cases; proxy env vars ignored; concurrent appends under the lock.
- **Checks:** DE tests 115 passed; ruff clean on DE files.
- **Open for QA:** `test_due_date_today_ok_yesterday_refused` expects `due_not_past`, but ML's new criterion now fires first (run 1).

## 2026-10-30 | ml-engineer (FDSE-2) | Round 3: REVIEW 1/3/7, ADR 006 conditions, ML-09..ML-12, v3 scored once
- **Did:**
  - **ML-09 (REVIEW 1):** new criterion `not_already_decided` on `propose_substitute` (ontology.yaml + code). Re-proposing an approved or rejected substitute is refused.
  - **ML-10 (REVIEW 3, 7):** a refused open-task is logged with object_type ShortageEvent and the event_key, not a speculative task id; new runner option `refused_object`. New criterion `no_open_task_for_event_facility`, last in order so input errors report first; this kept QA's due-date test valid without editing it.
  - **ML-12 (REVIEW 9-11):**
    - `_next_seq` catches only `duckdb.CatalogException`.
    - The runner takes `reason=` from the caller; close passes the resolution note; ontokit no longer hard-codes field names.
    - The NULL-edit limit is documented in the writeback docstring.
  - **ML-11 (ADR 006 conditions 1-4):**
    - Component-set guard on tiers 1, 2a and 2b.
    - A brand key may not be an ingredient of its own generic.
    - "Film, Extended Release" maps to the patch form.
    - The name-only trade is pinned by a test.
    - Recorded under ADR 006 "Round 3 implementation".
  - `python -m pipeline.evaluate --v3` added. Code frozen before scoring: match.py 4f009349, names.py 2d000af8, normalize.yaml e487dc8a. v1/v2 reports untouched.
- **v3 result: ONE run.** holdout_v3 was not opened before scoring; hash OK; 90 pairs; reviewed_by pending.

  | Set | Matcher P / R / F1 | baseline_v2 P / R | baseline v1 R |
  |---|---|---|---|
  | **holdout_v3, UNSEEN** | **0.963 / 0.605 / 0.743** (tp 26, fp 1, fn 17). **Recall target badly missed.** | 0.571 / 0.093 | 0.000 |
  | gold v1, seen | 0.982 / 0.982 / 0.982 | 0.650 / 0.684 | 0.000 |
  | holdout_v2, seen | 0.978 / 0.936 / 0.957 (was 0.957 / 0.936) | 0.579 / 0.234 | 0.000 |

- **Honest reading:** v1/v2 scores over-represented single-ingredient, single-strength items. holdout_v3 is heavy on combinations, premixes and brands, and recall falls to 0.605. Precision holds (0.963).
- **v3 error analysis (post-run, nothing retuned):**
  1. **Multi-strength combinations (about 6 FN: VP-014, 043, 054, 058, 062, 088).** Strength is single-valued (the first strength in the text). "7.5 mg/325 mg" vs "325 mg; 7.5 mg" conflicts, and so does "10 mg" vs "2.5 mg x4". Fix: compare a per-component strength set, ignoring order.
  2. **Premix diluents (4 FN: VP-024, 030, 032, 035).** "in D5W" and "in 0.9% NaCl" stay in the ingredient ("lidocaine d5w"). Fix: diluent noise words.
  3. **Bug: brand replacement on one component of a combination does not split the mapped generic** ("Sensorcaine with Epinephrine" -> "bupivacaine epinephrine / epinephrine"). It causes VP-006, and on seen v2 the component guard then drops HP-013 (a new seen FN). Fix in `normalize_ingredient`.
  4. **Brand ambiguity:** Sensorcaine/Xylocaine name both the plain drug and the drug with epinephrine. One FP (VP-008: plain Sensorcaine became the combo) and misses where the ambiguous brand was dropped. Also seen HP-007: an unresolved brand vs a combo still passes on equal strength.
  5. **Brands absent from the snapshot (4 FN: Namzaric, Diclegis, Percocet, Accuretic).** Unfixable without an external brand list (out of scope).
  6. Smaller: no "oral powder" form (VP-041); "U-100" and "[iU]" units (VP-082); typo plus "w/ epi" abbreviation (VP-004).
- **Files:** console/actions.py, ontology/ontology.yaml, ontokit/actions.py, ontokit/writeback.py, pipeline/match.py, pipeline/evaluate.py, config/normalize.yaml, docs/team/adr/006-matcher-guards.md, tests/test_{actions,ontokit_actions,match,evaluate}.py, reports/match_quality_v3.json.
- **Checks:** my 9 test files + tests/qa in one run: **117 passed, 2 skipped (live), 0 failed** (before the v3 run; the v3 run changed no code). Ruff is clean on my files.
- **Open:**
  - Classes 1-3 and 6 are fixable, but any fix needs a v4 blind set (holdout_v3 is now seen).
  - Class 5 needs a product decision on an external brand list (for example RxNorm), which is out of scope today.
  - ML-08 live run still pending.
- **Next:** qa-engineer (review), tech-lead (decide whether to do a v4 round).

## 2026-10-30 | principal-engineer (Principal Engineer / Engineering Director) | documentation pass + README
- **Did:** Wrote BRIEF, RISK_REGISTER (17 risks), KNOWN_ISSUES (17), FLAW_HUNT (14 failure modes, 5 planted bugs), 2 postmortems (concurrency race; re-propose undid approval), SCALE_AND_COST, OPERATING_MODEL (3-hospital deployment, also serves as US-9 via docs/decomposition.md), ROADMAP (v4 matcher + new blind set under Next), CASE_STUDY, EXECUTIVE_ONE_PAGER, the README (results, 6 screenshots, 3 infographics, Mermaid, Foundry mapping, ADR table, Quickstart placeholder for devops). Matcher rounds documented as stopped at v3 (EM decision); headline is v3 unseen P 0.963 / R 0.605, never the seen gold score.
- **Per-category recall (post hoc, by item text; list in docs/img/make_charts.py):** v3 single 14/16, combination 12/23, premix 0/4; v2 single 42/44, combination 2/3; v1 single 46/53, combination 4/4.
- **Files:** README.md (new), docs/{BRIEF,RISK_REGISTER,KNOWN_ISSUES,FLAW_HUNT,POSTMORTEM_concurrency_race,POSTMORTEM_repropose_undoes_approval,SCALE_AND_COST,OPERATING_MODEL,ROADMAP,CASE_STUDY,EXECUTIVE_ONE_PAGER,decomposition}.md, docs/img/{make_charts.py,matcher_rounds.png,recall_by_category.png,day_one_queue.png}, docs/team/BUGS.md (3 bugs). No product code changed.
- **Run outputs regenerated:** `python -m pipeline.run --fixtures --test` on the repo (exit 0) rewrote docs/results.json, reports/match_quality.json (now v3 matcher on the seen gold set, 0.9825/0.9825) and reports/impact_metrics.json; demo DB rebuilt (edits and audit kept). README results block filled by `pipeline.run.write_readme_results`.
- **Checks:** full pytest 302 passed, 2 skipped, 0 failed (80.17 s; again 74.76 s inside the --test build). Fixture build twice on a scratch DB: 20/21 tables identical, dq_results differs only in run_id/checked_at_utc; edits + audit survive rebuild, chain OK. Planted bugs on a scratch copy: PB-2 4 failed, PB-5 1 failed, PB-6 5 failed, PB-7 (lock removed) failed 5/5 runs; lock restored 10/10 pass. Live build from cache 2.25 s. ruff clean on make_charts.py. All README/doc relative links resolve.
- **Open:** BUG-PE-01 (AE: app headline still v2), BUG-PE-02 (DE/OPS: results block shows seen gold score as "gold set"), BUG-PE-03 (DE: no local_today tz test). Charts need matplotlib, which is not in requirements.txt (rendered with system Python). QA PB-1/3/4 not re-run.
- **Next:** devops-engineer (Quickstart, pinned deps, CI), then analytics-engineer for BUG-PE-01.

## 2026-10-30 | analytics-engineer | BUG-PE-01 fixed (headline is v3)
- **Did:**
  - `console.metrics.load_quality` (still the only place the headline is chosen) now takes the highest `reports/match_quality_vN.json` and its `*_unseen` block. Headline: v3 on holdout_v3, P 0.963 / R 0.605, red against 0.95.
  - History lists every round's blind score (v1 0.877, v2 0.936, v3 0.605) plus the v3 file's seen references, labeled "seen, optimistic".
  - Added per-category recall from PE's post-hoc lists in `docs/img/make_charts.py`, copied into `console.metrics` with the source named: v3 single 14/16, combination 12/23, premix 0/4; v1 and v2 match PE's numbers too.
  - Added the fail-safe note: precision 0.957 to 0.983 on all 5 scored sets.
  - Updated the Match Quality and Metrics pages, impact.md (block re-rendered by code, bullets rewritten), METRICS.md section 1a, and app/README.md.
  - Retook screenshots 05 and 06; the server and Chrome are stopped.
- **Checks:** run separately, one at a time: test_metrics 13 passed, test_queries 18 passed, test_app 10 passed. New tests: the newest round is the headline (constructed v1/v2/v3 reports); the category lists equal PE's file (parsed with `ast`, not imported, because it needs matplotlib); the real v3 counts reconcile to 14/16, 12/23, 0/4 and sum to tp + fn. ruff is clean.
- **Open:** if PE edits COMBO/PREMIX, the parity test fails by design; update the copy in `console/metrics.py`.
- **Next:** qa-engineer.

## 2026-10-30 | devops-engineer (SRE / DevOps Engineer) | OPS-01, OPS-02: pins, quickstart, CI, .gitignore, fresh-clone proof, first commit
- **Did:**
  - Ran `python -m pipeline.run --fixtures --test` on the repo: exit 0, **307 passed, 2 skipped, 0 failed** (76.6 s). Synced README, CASE_STUDY and EXECUTIVE_ONE_PAGER to it (302 -> 307). Removed the stale BUG-PE-01/PE-02 "still open" notes in README and CASE_STUDY (both fixed per BUGS/HANDOFF). BRIEF already matched `docs/results.json` and `reports/*.json`.
  - `requirements.txt`: exact runtime pins from `.venv` (duckdb 2.0.0, pandas 3.1.0, PyYAML 6.0.3, streamlit 1.66.1, altair 6.3.0, tzdata 2026.6). New `requirements-dev.txt`: pytest 9.2.0, ruff 0.17.3, matplotlib 3.11.2 (chart script only).
  - README Quickstart for Windows and macOS/Linux, `run_demo.ps1`, `run_demo.sh`, daily-pull note (`--pull-only`; no schedule created, E4), CI wording "configured, has not run yet".
  - `.github/workflows/ci.yml`: ruff check, fixtures build, `pytest -m "not live"`. No secrets, no network. Parsed with `yaml.safe_load` (valid).
  - `.gitignore`: venvs, caches, `.env*`, `.streamlit/secrets.toml`, `*.duckdb`, all of `data/` except `data/demo/seed/` (46 KB CSVs). Decision: the demo DB is NOT committed (11 MB, rebuilt in about 2 s); `tests/fixtures` (705 KB) is committed.
  - `tests/test_live.py`: ruff import-order fix only (the repo's `.ruff_cache` had hidden it; `--no-cache` and the fresh clone both flagged it).
- **Fresh-clone proof** (staged files exported with `git checkout-index`, new venv in scratchpad, Python 3.13.0): venv 15 s; `pip install -r requirements-dev.txt` 3 min 9 s, `pip check` clean; fixtures build exit 0 in 2.3 s, peak 146 MiB; `console.simulate` 1.5 s (12 success, 2 refused); 4 test files (run, actions, audit, app headless) **50 passed** in 46 s, peak 235 MiB; `streamlit run` headless: `/_stcore/health` = ok, `/` = 200, idle server 61 MiB. Clone and venv deleted. Did not measure server memory with a browser session open.
- **git:** `git init -b main`, one local commit. No remote, no push. Staged-file check: no `data/live`, no `*.duckdb`, no file over 500 KB, no secret patterns.
- **Open:** (1) `console.simulate` before any build fails with a raw DuckDB "schema stage does not exist" error; a friendly message would help (owner: data-engineer). (2) BUGS.md still shows BUG-PE-01 Status OPEN although analytics-engineer fixed it (owner: qa-engineer). (3) FLAW_HUNT and SCALE_AND_COST keep 302 as the dated record of that run; not changed. (4) macOS/Linux commands not executed. (5) `ruff format` is not enforced (61 files would reformat).
- **Next:** product-owner (demo sign-off).

## 2026-10-30 | product-owner (Deployment Strategist) | demo sign-off
- **Did:** Checked all 44 SPEC acceptance criteria against outputs only (I did not run the pipeline or the tests):
  - `docs/results.json` and `reports/*.json`
  - the 6 screenshots in `docs/demo/`
  - TEST_PLAN, BUGS, REVIEW and the docs/ write-ups
  - test names, found by grep
- **Verdict: ACCEPTED FOR THE DEMO, WITH EXCEPTIONS.** 38 MET, 4 PARTIAL (5.2, 8.2, 9.1, 9.3), 2 NOT MET:
  - **AC-4.5:** v3 unseen recall 0.605 vs the 0.95 target, precision 0.963
  - **AC-9.2:** no palantir.com/docs links and no "stand-in, not Foundry" statement
- **Overclaim scan:** README and one-pager are clean on deployment, users, clinical advice, compliance and titles. Wording fixes needed:
  - "663 raw events" should be "matched events"
  - "about 0.96 on all three" (v1 was 0.980)
  - the README's "17 limits" (now 19)
  - stale v2 baseline numbers in an impact.md bullet
  - `reports/match_quality.json` shows the seen gold run as `"pass": true` with no seen label
- **Files:** `docs/team/SIGNOFF.md` (new); `docs/team/BUGS.md` (BUG-PE-01 marked FIXED); `docs/KNOWN_ISSUES.md` (KI-17 marked resolved; KI-18 added: `console.simulate` before a build gives a raw DuckDB error; KI-19 added: the macOS/Linux quickstart has not been run).
- **Checks:** reading only. Run on record: 307 passed, 2 skipped, 0 failed (`docs/results.json`). Every README and one-pager number matched the JSON except the 663/73 pair, which comes from ADR 007's live DB measurement and is in no report JSON.
- **Open:** AC-9.2 fix plus the training plan and week 1/4/12 measures for AC-9.1 (principal-engineer); the wording fixes above (principal-engineer / devops-engineer); the label in `match_quality.json` (data-engineer). Michael's open items: review the gold and holdout labels (GP-024, GP-088) and approve the daily live pull (E4).
- **Next:** Michael (approve the push), after the doc fixes, or as-is if he accepts the exceptions.

## 2026-10-30, Engineering Manager: sign-off wording fixes
- "663 raw events" changed to "663 matched events (from 1,614 raw records)" in the README and the one-pager.
- Precision is now stated as "between 0.957 and 0.980" on all three blind sets (README, BRIEF, CASE_STUDY).
- KNOWN_ISSUES count corrected from 17 to 19 in the README.
- impact.md baseline now labels v2 and adds the v3 numbers.
- reports/match_quality.json now has a set_label saying it is the SEEN gold set.
- AC-9.2: the Foundry concept map now says it is a stand-in and links to palantir.com/docs/foundry.
- AC-4.5 (recall 0.605 against 0.95) stays NOT MET on purpose; v4 is on the roadmap.

## 2026-10-30, Data Engineer: pytest run must fail loudly when it did not run cleanly
- Did: run_pytest now records `ok` and `returncode`. ok is False on any error (e.g. a collection error), an exit code other than 0/1 (2 interrupted, 5 no tests ran), or no summary line. New check_tests() makes main() return 1 with "error: pytest did not run cleanly (N errors, exit code X: ...)", and the README row says DID NOT RUN CLEANLY instead of "0 passed, 0 failed".
- Files: pipeline/run.py, tests/test_run_pytest.py (new).
- Checks: new test file against the old run.py: 4 failed (e.g. "a suite that never ran was recorded as ok: {... 'error': 1 ...}"); with the fix: 4 passed. Quick subset `pytest -x -q` on test_run_pytest, test_run, test_ndc, test_match, test_dq: 70 passed. Full suite not run (RAM brief).
- Open: pre-existing uncommitted edits to README.md, docs/impact.md, reports/impact_metrics.json, reports/match_quality.json were left untouched and NOT committed (not mine).
- Next: QA Engineer.

## 2026-10-30 | design-lead | Hanson Labs FORMULARY restyle (OVERNIGHT_PLAN step 4)
- **Did:** Light FORMULARY theme (`.streamlit/config.toml`) plus `app/assets/formulary.css`; Atkinson Hyperlegible Next 17 px / Source Serif 4 / IBM Plex Mono; pharmacy-label status stickers (icon + word); a large auxiliary label on the object view; a lead number with a ruled ledger in place of 6 identical cards; a takeaway band on Home; the Hanson Labs logo, favicon and footer lockup; the wallpaper only in the Home band, OFF by default behind a toggle, with a Pause button; `?as=` persisted and a sidebar "Where you were" button; the sidebar collapses on phones; the Metrics chart row height fixed (alt.Step).
- **Files:** .streamlit/config.toml, app/ui.py, app/Home.py, app/pages/1_Drug.py, 2_Audit.py, 3_Match_Quality.py, 4_Metrics.py, app/assets/*, app/static/wallpaper/hl-wallpaper.js, tests/test_app.py (+1 test), README.md (one line), docs/demo/*.png (6 retaken + 01b + mobile/*_375), docs/team/DESIGN_REVIEW.md (new).
- **Checks:** test_app 11 passed; full pytest **312 passed, 2 skipped**; ruff clean on app/. Screenshots were taken with headless Chrome on a scratch DB copy; the server and Chrome are stopped.
- **Open:** The brand Streamlit snippet's multi-family font URL is rejected by Streamlit 1.66 (brand owner to fix build_tokens.py). Not tested on a real phone. Backlog count shows 39 (old shot: 40); that comes from the demo DB, not this change.
- **Next:** qa-engineer (spot-check the screens). Bigger idea, not built: the allocation/backorder board (VoC #1), see DESIGN_REVIEW.md NEXT.

## 2026-10-30 principal-engineer (overnight step 7: cross-project consistency review)
- Did: added KI-20 (results.json records 307 passed; later full pytest run was 312 passed, 2 skipped) and KI-21 (backlog 39 vs 40).
- Files: docs/KNOWN_ISSUES.md
- Checks: README headline numbers compared with docs/results.json by hand; no code or numbers changed.
- Open: README baseline row cites baseline_v2 (recall 0.093) while the generated block shows the exact-match baseline (0.0): pick one.
- Full review: dev-team/brand/portfolio/CONSISTENCY_REVIEW.md. Next: qa-engineer: re-run pipeline.run --fixtures --test; confirm backlog count.
