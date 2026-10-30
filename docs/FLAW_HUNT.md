# FLAW HUNT

Owner: Principal Engineer. Run on 2026-10-30. Each known failure mode is **Found** (a real defect, logged), **Fixed** (was a defect, now guarded by a named test), **Guarded** (never broke; a test or check proves it), or **N/A** (with the reason). There is no "not checked" row.

Planted bugs were run on a **scratch copy** of the repo (session scratchpad), never on the project files. Each was reverted and the revert was diffed.

## Runs behind this document

| Check | Command | Result |
|---|---|---|
| Full suite, one run | `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider` | **302 passed, 2 skipped (live API), 0 failed** in 80.17 s |
| Full build + suite (writes `docs/results.json`) | `python -m pipeline.run --fixtures --test` | exit 0; **302 passed, 2 skipped** in 74.76 s; `run_id run-35e141f54e33` |
| Repeat-run of the fixtures build | `pipeline.run --fixtures` twice on a fresh scratch DB, then a per-table hash of every row | see 1 below |
| Concurrency, lock in place | `pytest tests/qa/test_action_edges.py -k concurrent`, 10 sequential runs | **10 of 10** passed (3 tests each) |

## Results

| # | Failure mode | Verdict | Evidence |
|---|---|---|---|
| 1 | **Duplicates on re-run (idempotency)** | Guarded | Built twice on a fresh scratch DB: **20 of 21 tables identical** in row count and full-row hash (`core.change_feed` 225, `core.shortage_history` 96, `core.shortage_event` 78, `stage.shortage_record` 223, `core.formulary_match` 83, `seed.*` byte-equal ...). `core.dq_results` (33 rows both times) differs only in `run_id`/`checked_at_utc`; with those two columns dropped the hash is identical (`b0e4417d9b6724c4`). Then ran `console.simulate` (12 SUCCESS, 2 REFUSED actions) and rebuilt again: `ops.*` and `audit.*` unchanged (14 / 7 / 6 / 5 rows, same hashes), `verify_chain` OK (14 rows). Committed tests: `tests/test_run.py::test_rebuild_is_idempotent`, `tests/test_metrics.py::test_simulation_is_idempotent`, `tests/qa/test_action_edges.py::test_actions_survive_pipeline_rebuild`. |
| 2 | **Duplicate actions (double submit, double approve)** | Fixed | REVIEW 7: no idempotency on open-task. Now criterion `no_open_task_for_event_facility`; `tests/qa/test_review_attacks.py` (double open refused), `test_action_edges.py::test_task_closed_twice`. |
| 3 | **Late-arriving data (backfill, out of order)** | Guarded, one accepted limit | Full deterministic rebuild (ADR 004). `tests/test_history.py::test_out_of_order_load_gives_the_same_result`, `tests/qa/test_queue_adr007.py::test_backfilling_an_older_snapshot_moves_the_baseline`, `tests/test_history.py::test_present_in_latest_is_false_when_the_latest_snapshot_is_empty` (REVIEW 6, fixed). Accepted: a backfill can change a past day's queue (KNOWN_ISSUES KI-13). |
| 4 | **Silent schema drift** | Guarded | DQ check by field name: `tests/test_dq.py::test_renamed_field_fails_schema_drift_and_names_it` (fixture `SYNTHETIC_renamed_field.json`), `test_real_sample_has_no_schema_drift`. Snapshot tamper: `tests/test_snapshots.py::test_sha_mismatch_is_flagged_not_used`. |
| 5 | **Tests that cannot fail (planted bugs)** | Guarded | 4 planted bugs, all caught; table below. QA's PB-1, PB-3, PB-4 (TEST_PLAN 4) were not re-run here. |
| 6 | **Metric gaming** | **Found** (2 defects) | (a) **BUG-PE-01**: the app and `docs/impact.md` still headline v2 (R 0.936). `console.metrics.load_quality` only knows `match_quality_v2.json`, so the newest unseen run (v3, R 0.605) is invisible in the app. METRICS 1a's own rule says the newest unseen run is the headline. (b) **BUG-PE-02**: `pipeline/run.py::write_readme_results` and `docs/results.json` report the build's gold-set score, which is now **0.9825 / 0.9825**, a **seen** set, labeled only "gold set, team-labeled". Guarded parts: frozen label hashes (`tests/test_gold_frozen.py::test_hashes_match_lock_file`, `tests/test_holdout_frozen.py`), sets disjoint, only `[SIMULATED]` rows count for the secondary metric (`tests/test_metrics.py::test_secondary_metric_ignores_non_simulated_rows`), cap never moves guardrail (b) (`tests/qa/test_queue_adr007.py::test_cap_never_changes_guardrail_b`). |
| 7 | **PII handling** | N/A by design, checked | No patient data exists. The 8 users in `seed.app_user` are fictional and `is_synthetic = True` (`tests/test_generator.py::test_every_table_is_marked_synthetic`). openFDA `contact_info` holds company contacts (90 of 1,614 live records include a company mailbox such as a product-availability address); it stays in the raw snapshot only and no file under `app/`, `console/` or `pipeline/ingest.py` references it (grep). No email patterns in code, CSV or YAML outside a test host list. |
| 8 | **Time zones** | Guarded, test gap logged | Snapshot date uses `America/New_York` (`config/settings.yaml`). Checked by hand with `pipeline.snapshots.local_today`: 2026-10-30 03:30Z gives 10-29; 04:30Z gives 10-30; DST end 2026-11-01 04:30Z and 05:30Z both give 11-01 (correct). Due dates: `tests/qa/test_action_edges.py::test_due_date_uses_new_york_day`. Stale banner: `tests/test_queries.py::test_is_stale_boundary_is_exactly_one_day`. **Gap**: no committed test calls `local_today` with a late-night UTC time (BUG-PE-03, S3). |
| 9 | **Floating point and edge values** | Guarded, one limit | Strengths are rounded to 6 places and compared with relative tolerance 1e-3 (`pipeline/names.py:281`). Probed by hand: `0.25%` = `2.5 mg/mL` (True); `500 mg/250 mL` = `2 mg/mL` (True); `0.30000000000000004 mg/3 mL` = `0.1 mg/mL` (True); `10 mg` vs `10.02 mg` (False); `0 mg` = `0 mg` (True); empty vs `5 mg` gives None (unknown never blocks). Limit: `1:200,000` parses to no strength (KI-06). NDC edges: `tests/test_ndc.py::test_invalid_inputs_return_reason`, `test_ten_digit_unhyphenated_is_ambiguous_not_guessed`, `tests/test_match.py::test_bad_ndc_formats_do_not_crash`. Text edges: `test_whitespace_padded_note_refused`. |
| 10 | **Concurrency / race** | Fixed | [POSTMORTEM_concurrency_race.md](POSTMORTEM_concurrency_race.md). PB-7 below. |
| 11 | **Control bypass (role / state)** | Fixed | [POSTMORTEM_repropose_undoes_approval.md](POSTMORTEM_repropose_undoes_approval.md). PB-2, PB-6 below. |
| 12 | **Non-atomic rebuild** | Fixed | REVIEW 5; DE test: rollback when matching fails leaves the old views (see HANDOFF, data-engineer). |
| 13 | **Network allow-list bypass** | Fixed | REVIEW 12: port and proxy env. `tests/test_http.py` (port cases, proxy env ignored). |
| 14 | **Seen-set overfitting of the matcher** | Guarded by process | v1 gold, v2 holdout, v3 holdout each scored once by an author who never opened the file; code hashes frozen before scoring (ML handoff: match.py 4f009349, names.py 2d000af8). v3 is now seen; v4 needs a new set. |

## Planted bugs (scratch copy, each reverted and diffed)

| ID | Planted change | Tests run | Result |
|---|---|---|---|
| PB-2 | `ontology.yaml` line 156: `approve_substitute` roles `[pharmacist, buyer]` | `tests/test_actions.py tests/qa/test_action_edges.py` | **4 failed**, 43 passed: `test_buyer_cannot_approve_permission_error`, `test_buyer_cannot_approve`, `test_reconcile_edits_and_success_rows`, `test_actions_survive_pipeline_rebuild` |
| PB-5 | `console/actions.py` line 291: note length uses `len(note)` not the stripped length | `tests/qa/test_action_edges.py` | **1 failed**, 33 passed: `test_whitespace_padded_note_refused` |
| PB-6 | `not_already_decided` forced to always pass (`... or True`) | `tests/qa/test_review_attacks.py tests/test_actions.py` | **5 failed**, 14 passed: both `test_buyer_cannot_undo_decision_by_reproposing` cases, `test_pharmacist_reproposing_approved_is_also_refused`, `test_r3_buyer_cannot_repropose_approved_substitute`, `test_r3_repropose_rejected_refused_but_repropose_proposed_ok` |
| PB-6b | Same criterion removed from `ontology.yaml` only (code unchanged) | same | **11 failed** with `RuntimeError`: the YAML-vs-code criterion consistency check fires first. Good (drift between the ontology and code cannot ship), but it means this variant does not prove the attack tests; PB-6 does. |
| PB-7 | `ontokit/audit.py` `writer_lock()` body replaced by a bare `yield` | `tests/qa/test_action_edges.py -k concurrent`, 5 runs | **Failed 5 of 5 runs** (2, 1, 2, 1, 1 failures of 3). With the lock restored: 10 of 10 passed. |

## Defects logged
In [team/BUGS.md](team/BUGS.md): BUG-PE-01 (analytics-engineer), BUG-PE-02 (data-engineer / devops), BUG-PE-03 (data-engineer, test gap). No product code was changed by this hunt.
