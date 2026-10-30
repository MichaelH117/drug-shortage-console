# TEST PLAN: Drug Shortage Operations Console

Owner: QA / Test Engineer (SDET). Version 1, 2026-10-30 (QA-01). Inputs: SPEC.md ACs, DESIGN.md sections 4-6, TASKS.md.

Status key:
- **PASS**: the test exists and passes now.
- **XFAIL**: the test is written and waiting on code; it runs automatically once the code lands.
- **PLANNED**: the owning seat writes it under the named task.
- **LIVE**: runs only with `DSC_LIVE=1`.
- **MANUAL**: evidence is a file or a review, not pytest.

Status on 2026-10-30 (round 2, after REVIEW fixes and ADR 007): `pytest tests` gives **302 passed, 2 skipped (live), 0 failed, 0 xfail** (one run, 98 s).

Per-file counts (collected):

| File | Tests | Owner |
|---|---|---|
| tests/qa/test_action_edges.py | 34 | QA |
| tests/qa/test_review_attacks.py | 6 | QA (REVIEW 1, 3, 7) |
| tests/qa/test_queue_adr007.py | 10 | QA (ADR 007) |
| tests/test_gold_frozen.py | 8 | QA |
| tests/test_holdout_frozen.py | 7 | QA (v2, v3) |
| test_actions 13, test_ontokit_actions 9, test_ontology 6, test_ontokit_boundary 1, test_match 24, test_ndc 20, test_evaluate 8, test_objects 2 | 83 | ML |
| test_http 14, test_snapshots 8, test_ingest 16, test_dq 11, test_history 10, test_changes 19, test_run 9, test_generator 12, test_audit 16 | 115 | DE |
| test_queries 18, test_metrics 11, test_app 10 | 39 | AE |
| test_live | 2 (skipped unless DSC_LIVE=1) | ML |

Every owner file in the trace below now exists and is green. Rows still marked PLANNED mean "the owner's file passes, but QA has not re-audited that the named case is inside it". The audit is the next QA pass.

## 1. Offline proof (AC-1.4)
- `tests/conftest.py` (DE) patches `socket.socket.connect`, `connect_ex` and `socket.create_connection` to raise `NetworkBlocked` for every test that is not marked `live`.
- No QA test needs the network. The gold files are committed fixtures.
- **Proof test:** DE-01's test that the guard blocks a real connect.
- **Run:** `python -m pytest tests -q`, with Wi-Fi off once before sign-off (MANUAL, QA).

## 2. Acceptance criterion to test trace

| AC | What is proved | Test (file::name) | Owner | Status |
|---|---|---|---|---|
| 1.1 | Paging, count = meta.total, else fail | `test_snapshots.py` (2-page fake client; count mismatch raises `SnapshotIncomplete`, no folder left); `test_run.py` one command | DE-03, DE-07 | PLANNED |
| 1.2 | Cache hit = zero calls | `test_snapshots.py` (client raises on any call) | DE-03 | PLANNED |
| 1.3 | Only api.fda.gov | `test_http.py` (`HostNotAllowed`) | DE-03 | PLANNED |
| 1.4 | All tests offline | conftest socket guard + DE-01 guard test; section 1 | DE-01 | PLANNED |
| 1.5 | DQ rows: volume, freshness, drift, dates | `test_dq.py` (renamed field fails check c and names it; 25% drop fails) | DE-04 | PLANNED |
| 1.6 | Live count = meta.total | `test_live.py` | ML-08 | LIVE |
| 2.1 | SCD2 history | `test_history.py` | DE-05 | PLANNED |
| 2.2 | Idempotent reload | `test_run.py` (build twice, same counts and table hashes) | DE-07 | PLANNED |
| 2.3 | 6 change types match expected file | `test_changes.py` vs `expected_changes.csv`; one test per rule row | DE-05 | PLANNED |
| 2.4 | Out-of-order load | `test_history.py` (D1,D3,D2 == D1,D2,D3) | DE-05 | PLANNED |
| 2.5 | Availability buckets incl. typos | `test_ingest.py` (every distinct raw value maps; unmapped goes to Unknown + DQ row) | DE-04 | PLANNED |
| 3.1 | 5 objects, 6 links, keys unique | `test_ontology.py` (bad reference, missing view, duplicate key); `test_objects.py::check_backing` on demo_db | ML-02, ML-06 | PASS (ontology part) / PLANNED |
| 3.2 | Seeded, byte-identical seed | `test_generator.py` (sha256 same seed) | DE-06 | PLANNED |
| 3.3 | NDC + name variant mix, at least 30% overlap, manifest | `test_generator.py` | DE-06 | PLANNED |
| 3.4 | `is_synthetic` everywhere + UI label | `test_generator.py` (column); `SYNTHETIC_gold_formulary.csv` checked in `test_gold_frozen.py::test_references_resolve`; UI label in screenshots | DE-06, AE-06 | PARTIAL PASS / MANUAL |
| 4.1 | NDC formats + invalid input | `test_ndc.py` | ML-01 | PASS |
| 4.2 | Tiers + tier/confidence columns | `test_match.py` (tier 1 uses top-level NDC, best tier wins) | ML-03 | PLANNED |
| 4.3 | 60+ gold pairs + header rule | `test_gold_frozen.py` (all 8 tests): 102 pairs, 57 positive, 45 hard negative | QA-02 | PASS |
| 4.4 | P/R/F1 for matcher and baseline, overall and per tier | `test_evaluate.py`; `reports/match_quality.json` | ML-07 | PLANNED |
| 4.5 | Target or honest miss; gold not tuned | `test_gold_frozen.py::test_hashes_match_lock_file` (freeze); `test_evaluate.py` (failing pairs listed) | QA-02, ML-07 | PASS (freeze) / PLANNED |
| 4.6 | No-openfda records match via tiers 1 and 3 | `test_match.py` (one record); gold rows GP with `variant_type=no_openfda` and `name_salt` on no-openfda events (QG-014 vs nebivolol) | ML-03, QA | PLANNED |
| 5.1 | One command; queue filter and sort (ADR 007: baseline excluded, actionable only, ranked, uncapped) | `test_queries.py`; QA `test_queue_adr007.py`: `test_single_snapshot_gives_empty_queue_and_a_worklist`, `test_backfilling_an_older_snapshot_moves_the_baseline`, `test_queue_rows_are_actionable_and_watch_rows_are_not`, `test_queue_excludes_baseline_rows`, `test_queue_rank_is_deterministic_and_follows_adr`, `test_worklist_rank_is_deterministic` | AE-02/07, QA-05 | PASS |
| 5.2 | Row columns | `test_queries.py` | AE-02 | PLANNED |
| 5.3 | Object view parts | `test_queries.py::drug_object` | AE-02 | PLANNED |
| 5.4 | Stale banner at the 1-day boundary | `test_queries.py` (`is_stale`) | AE-02 | PLANNED |
| 5.5 | User picker labeled not-auth | screenshot; `test_action_edges.py::test_role_cannot_be_passed_in` covers the server-side part | AE-03, QA | PASS / MANUAL |
| 5.6 | Click-through screenshots | `docs/demo/` | AE-06 | MANUAL |
| 6.1 | Allowed + refused per action; buyer approve raises permission error; viewer refused on all 6 | `test_actions.py` (ML-05); QA: `test_buyer_cannot_approve`, `test_buyer_cannot_reject`, `test_viewer_refused_on_all_six`, `test_inactive_pharmacist_refused`, `test_unknown_user_changes_nothing`, `test_close_by_other_buyer_refused_assignee_ok`; REVIEW 1: `test_buyer_cannot_undo_decision_by_reproposing` x2, `test_pharmacist_reproposing_approved_is_also_refused`; REVIEW 7: `test_double_open_same_event_facility_refused`, `test_open_allowed_for_other_facility_and_after_close` | ML-05/09/10, QA-03/05 | PASS |
| 6.2 | Refused = no change + 1 REFUSED row with criterion | every QA refusal goes through `_assert_refused` (counts edits and audit rows before and after, checks outcome and criterion id). Also covered: not-found objects (`test_approve_missing_substitute`, `test_reject_missing_substitute`, `test_close_missing_task`, `test_reassign_missing_task`, `test_open_task_missing_event`, `test_open_task_missing_facility`, `test_propose_missing_event_and_missing_alt`) and boundaries (`test_blank_reason_refused` x3, `test_note_9_vs_10_chars`, `test_whitespace_padded_note_refused`, `test_due_date_today_ok_yesterday_refused`, `test_due_date_uses_new_york_day`, `test_reassign_edges`, `test_open_task_assignee_must_be_active_staff`, `test_propose_same_ndc_refused`, `test_approve_alt_that_is_itself_short`) | QA-03 | PASS |
| 6.3 | One transaction (forced error leaves nothing) | `test_ontokit_actions.py` (monkeypatched `audit.append` raises) | ML-04 | PLANNED |
| 6.4 | Edits survive re-ingest | `test_action_edges.py::test_actions_survive_pipeline_rebuild` (counts, head hash, object state, chain OK); `test_run.py` | QA-03, DE-07 | PASS / see test_run.py |
| 6.5 | New state without restart | views not tables (DESIGN 3.4); screenshot | AE-03 | MANUAL |
| 7.1 | Every attempt = 1 row, hash rule | `test_audit.py`; `test_action_edges.py::test_reconcile_edits_and_success_rows`; REVIEW 3: `test_review_attacks.py::test_refused_open_is_not_logged_under_next_task_id` | DE-08, QA-03/05 | PASS |
| 7.2 | No update/delete path | `test_audit.py` (`update()`/`delete()` raise) | DE-08 | PLANNED |
| 7.3 | Tamper row k gives k; delete newest caught | `test_audit.py`; QA tamper suite with `generator/tamper.py` | DE-08, QA-03 | PLANNED |
| 7.4 | Audit page filters + verify result | screenshot | AE-03 | MANUAL |
| 7.5 | Copied code names its source; no cross-project import | `test_ontokit_boundary.py`; header check in `test_audit.py` | ML-02, DE-08 | PASS (boundary) / PLANNED |
| 8.1 | Metric + baseline come from the JSON | `test_metrics.py` (impact.md marker block equals rendered values) | AE-04/05 | PLANNED |
| 8.2 | Guardrails a-e computed by code; (b) on the uncapped queue | `test_metrics.py`; QA `test_cap_never_changes_guardrail_b`, `test_queue_function_is_uncapped` x3 | AE-04/08, QA-05 | PASS |
| 8.3 | Time-to-first-action labeled simulated | `test_metrics.py` (label string present) | AE-04 | PLANNED |
| 8.4 | 2+ gaming paths per metric | review of `docs/METRICS.md` | AE-01, QA | MANUAL |
| 9.1-9.3 | Decomposition doc | review of `docs/decomposition.md` (URL or UNVERIFIED on every claim) | PE-01, QA | MANUAL |

## 3. Concurrency (DESIGN section 8, T9)
In `tests/qa/test_action_edges.py`, two threads each take their own DuckDB cursor and start at a barrier:
- `test_concurrent_double_approve_only_one_wins`
- `test_concurrent_close_only_one_wins`
- `test_concurrent_opens_keep_chain_and_task_ids_unique`

For each race:
- **Acceptable outcomes:** one SUCCESS, and the other is `ActionRefused` or a DuckDB `TransactionException` (write conflict).
- **Never acceptable:** two approvals, two closes, a duplicate `task_id` or audit `seq`, or a forked hash chain (`verify_chain` must be OK).

## 4. Planted-bug list (QA-04, after QA-03)
Each bug is planted on a scratch copy, the failing test output is recorded in BUGS.md, and then the change is reverted.

| # | Planted change | Expected to fail |
|---|---|---|
| PB-1 | Tier 1 uses `openfda.package_ndc` | `test_match.py` R4 regression; gold rows GP with `openfda_sibling_strength` (QG-052 vs 0378-6860-77) in the eval |
| PB-2 | `approve_substitute` roles include buyer | `test_buyer_cannot_approve`, `test_actions.py` |
| PB-3 | `schema.py` drops `ops` | `test_actions_survive_pipeline_rebuild`, `test_run.py` |
| PB-4 (extra) | Tier 2 expands `openfda.product_ndc` without a strength check | gold `openfda_sibling_strength` rows (precision drop) |
| PB-5 (extra) | Note length uses `len(note)`, not the stripped length | `test_whitespace_padded_note_refused` |

## 5. Gold set (QA-02): what is locked
Files:
- `tests/fixtures/gold_matches.csv`: 102 pairs, 57 positive and 45 hard negative.
- `tests/fixtures/SYNTHETIC_gold_formulary.csv`: 86 hand-written SYNTHETIC items.
- `tests/fixtures/gold_events.json`: 30 real openFDA records, 8 of them with no `openfda` block.

Lock:
- `tests/fixtures/gold_matches.sha256` holds the hashes, computed on LF-normalized bytes so a CRLF checkout does not break them.
- `tests/test_gold_frozen.py` checks the hash, the columns, `reviewed_by = pending` on every row, the counts, at least 3 rows per generator variant type, at least 10 hand-written-type rows, and that every reference resolves.

Variant types:
- **Generator-style:** `ndc_442`, `ndc_532`, `ndc_541`, `ndc_11digit`, `ndc_leading_zero_error`, `name_brand`, `name_salt`, `name_case_ws`.
- **Hand-written:** `missing_ndc`, `typo`, `diff_strength`, `diff_dosage_form`, `diff_route`, `combination_product`, `lookalike_name`, `same_labeler_diff_product`, `true_non_match`, `openfda_sibling_strength`, `no_openfda`, `ndc_10digit_unhyphenated`, `same_ingredient_other_labeler`.

## 6. v2 matching round
### 6.1 Held-out set
- `tests/fixtures/holdout_v2.csv` is locked by `holdout_v2.sha256` and checked by `tests/test_holdout_frozen.py` (hash, review state, mix, no overlap with gold).
- Its contents are not described here or in HANDOFF, on purpose. The matcher author does not open it before scoring.
- After v2 scoring, report gold results as **seen** and held-out results as **unseen**.

### 6.2 Fairer baseline: `baseline_v2` (spec owned by QA, ml-engineer implements)
The v1 baseline (exact lowercase `generic_name`) scored 0 recall, because openFDA appends the dosage form ("Furosemide Injection"). A baseline that cannot win is a strawman, so v2 adds this one and reports both.

**Rule:** a pair (formulary item, event) is predicted as a match if and only if `base_name(item.name_raw) == base_name(event.generic_name)` and the result is not empty.

`base_name(text)`:
1. Lowercase. Unicode NFKC. Replace every character that is not a-z, 0-9 or a space with a space.
2. Remove dosage-form words: tablet, tablets, tab, capsule, capsules, cap, injection, injectable, inj, solution, suspension, oral, powder, for, film, coated, extended, delayed, release, er, xr, sr, dr, odt, orally, disintegrating, chewable, spray, ointment, cream, gel, liquid, concentrate, suppository, kit, system, transdermal, ophthalmic, topical, inhalation, aerosol, metered, irrigant, vial, syringe, pen, bag.
3. Remove strength tokens: any token that contains a digit, plus unit words (mg, mcg, ug, g, ml, l, meq, unit, units, percent).
4. Collapse spaces and strip.

Not allowed in the baseline (these are what the matcher adds): salt stripping, the brand map, NDCs, route, strength comparison, fuzzy or edit-distance matching.

Implementation must be pure and deterministic, and this word list is fixed before scoring: changing it after seeing the held-out results counts as tuning.

Tests ml-engineer writes in `tests/test_evaluate.py`:
- `base_name("Furosemide Injection") == "furosemide"`
- `base_name("Metoprolol Succinate Tablet, Extended Release") == "metoprolol succinate"`
- `base_name("Quinapril/Hydrochlorothiazide Tablet") == "quinapril hydrochlorothiazide"`
- `base_name("  CLONAZEPAM 0.5MG TAB ") == "clonazepam"`
- `base_name("Injection") == ""`, and an empty base never matches

Report: P/R/F1 for `baseline_v2` overall and per variant_type, on gold and on the held-out set, next to the v1 baseline and the matcher.

### 6.3 v3 held-out set (QA-06)
`tests/fixtures/holdout_v3.csv` is locked by `holdout_v3.sha256`. `tests/test_holdout_frozen.py` checks the hash, the review state, the mix, a minimum number of combination and brand traps, and that it is disjoint from gold and v2. Contents are not described here.

## 7. Out of scope for automated tests
- Streamlit pixels (AC-5.6, 6.5, 7.4): covered by screenshots.
- Real-pharmacist validation of the labels (DESIGN T4): this waits for Michael's review.
