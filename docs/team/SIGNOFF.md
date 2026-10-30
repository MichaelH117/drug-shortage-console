# DEMO SIGN-OFF: Drug Shortage Operations Console

Signed by: Deployment Strategist (product-owner seat). Date: 2026-10-30. Build checked: local commit b6efd87, run `run-35e141f54e33`.

**How this was checked.** I read outputs only. I did not run the pipeline or the tests. Sources:
- `docs/results.json`
- `reports/match_quality.json`, `match_quality_v1/v2/v3.json`, `impact_metrics.json`
- the six screenshots in `docs/demo/`
- `docs/team/TEST_PLAN.md`, `BUGS.md`, `REVIEW.md`, `HANDOFF.md`
- `docs/impact.md`, `METRICS.md`, `OPERATING_MODEL.md`, `decomposition.md`, `KNOWN_ISSUES.md`
- test names, found by grep (to confirm that a named test exists)

Test run on record: **307 passed, 2 skipped (live API), 0 failed** (`docs/results.json` `tests`, 2026-10-30).

---

## 1. Verdict

**ACCEPTED FOR THE PORTFOLIO DEMO, WITH EXCEPTIONS.** Of 44 acceptance criteria: **38 MET, 4 PARTIAL, 2 NOT MET.**

- **The matcher misses its recall target. AC-4.5 is NOT MET.**
  - On the newest blind set (v3), recall is **0.605** (26 of 43) against a target of 0.95.
  - Precision is 0.963, which passes its 0.90 target.
  - The project reports this plainly everywhere I looked, and nobody tuned the labels to pass. That honesty is the part of AC-4.5 that holds. The target itself does not.
- **AC-9.2 is NOT MET.** The Foundry concept map exists in the README, but nothing has a palantir.com/docs link and nothing says "open-source stand-in, not Foundry". The fix is about 15 minutes of doc work.
- **The core product works as specified, based on tests, JSON and screenshots:**
  - daily snapshots and history
  - the ontology
  - role-checked actions with write-back
  - the hash-chained log
  - the morning queue
  - honest metrics with guardrails
- **Before Michael pushes:** fix AC-9.2 and the four wording items in section 3. None of them needs a code change.

---

## 2. Acceptance criteria

### US-1 Ingest and cache
| AC | Verdict | Evidence |
|---|---|---|
| 1.1 | MET | `tests/test_snapshots.py::test_count_mismatch_raises_and_leaves_no_folder`. The live pull wrote 1614 records = `meta.results.total` 1614 in 2 pages (HANDOFF, DE entry). |
| 1.2 | MET | `test_snapshots.py::test_cache_hit_makes_zero_calls`, `test_refresh_keeps_the_old_folder` |
| 1.3 | MET | `test_http.py::test_other_hosts_are_refused`. REVIEW #12 is fixed: `pipeline/http.py` requires port 443 and uses `ProxyHandler({})`. |
| 1.4 | MET | The conftest socket guard plus `test_http.py::test_socket_guard_blocks_urllib_even_for_the_allowed_host`. The suite runs 307 passed offline. The TEST_PLAN "Wi-Fi off" manual run is not recorded. |
| 1.5 | MET | `results.json` `dq` shows PASS 26, WARN 5, SKIPPED 2, FAIL 0 (33 `core.dq_results` rows). Tests: `test_dq.py::test_renamed_field_fails_schema_drift_and_names_it` and `test_volume_drop_of_25_percent_fails`. |
| 1.6 | MET | HANDOFF (DE): a live pull on 2026-10-30 saved 1614 = `meta.results.total`. `test_live.py` (ML-08) was not run; it is one of the 2 skips. |

### US-2 Snapshots and history
| AC | Verdict | Evidence |
|---|---|---|
| 2.1 | MET | `core.shortage_history` has 96 rows. Screenshot 02 shows valid_from / valid_to rows. Test: `test_history.py`. |
| 2.2 | MET | `test_run.py::test_rebuild_is_idempotent`. FLAW_HUNT: 20 of 21 tables are byte-identical; the 21st differs only in run id and timestamp. |
| 2.3 | MET | `test_changes.py::test_counts_per_type`. The latest snapshot shows all 6 types: NEW 3, WORSENED 4, IMPROVED 2, RESOLVED 1, DROPPED 1, UNCHANGED 66. |
| 2.4 | MET | `test_history.py::test_out_of_order_load_gives_the_same_result`. The chosen behavior is "same final history". |
| 2.5 | MET | `test_ingest.py::test_unmapped_text_goes_to_unknown`, `test_dq.py::test_unmapped_availability_and_move_to_unknown_are_reported` |

### US-3 Ontology and synthetic hospital
| AC | Verdict | Evidence |
|---|---|---|
| 3.1 | MET | `results.json` `ontology_problems: []`. Tests: `test_ontology.py` and `check_backing`. |
| 3.2 | MET | `seed.facility` 3, `seed.formulary_item` 177 (within 150-300), `seed.app_user` 8. Test: `test_generator.py::test_same_seed_gives_byte_identical_csvs`. |
| 3.3 | MET | `test_generator.py::test_at_least_30_percent_of_items_come_from_the_shortage_list`. `seed.variant_manifest` has 177 rows; the variant mix is in `generator_detection.by_variant`. |
| 3.4 | MET | `test_generator.py::test_every_table_is_marked_synthetic`. All 6 screenshots show the orange "SYNTHETIC hospital data" banner. |

### US-4 Matching with measured quality
| AC | Verdict | Evidence |
|---|---|---|
| 4.1 | MET | `test_ndc.py` (20 tests, PASS in TEST_PLAN) |
| 4.2 | MET | `test_match.py::test_best_tier_wins_and_tier2_product` and `test_tier1_uses_top_level_package_ndc_not_openfda_package_ndc`. Screenshot 02 shows "T1 exact NDC". The per-tier results are in the reports. |
| 4.3 | MET | `gold_matches.csv` has 102 pairs (57 positive, 45 hard negative), with the header rule checked by `test_gold_frozen.py::test_header_states_rule_and_pending_review`. Its sha256 matches the frozen hash. |
| 4.4 | MET | Precision, recall and F1 are reported for the matcher, the naive baseline and the fairer baseline, overall and per tier, in `reports/match_quality*.json`. Screenshot 06 shows them in the app. Note: `reports/match_quality.json` (the file name the AC uses) now holds the **seen** gold run with `"pass": true` and no "seen" label. See section 3, item 5. |
| **4.5** | **NOT MET** | Target: recall >= 0.95 at precision >= 0.90. **v3 unseen holdout: recall 0.6047, precision 0.963** (`match_quality_v3.json` `holdout_v3_unseen`, `"pass": false`). The 17 FN and 1 FP are listed with rationales. The holdout hash is frozen (`hash_ok: true`). Earlier blind rounds: v1 0.877, v2 0.936. They also missed the target. The honesty part of the AC is met; the target is not. |
| 4.6 | MET | `test_match.py::test_record_without_openfda_matches_through_tier3`. On the gold set, `no_openfda` recall is 4/4. |

### US-5 Morning triage queue
| AC | Verdict | Evidence |
|---|---|---|
| 5.1 | MET | One command (`run_demo.ps1`). Screenshot 01 shows the 3 WORSENED, stocked, actionable rows ranked Unavailable first, then days on hand (5 d, 6 d, 19 d). Tests: `test_queries.py::test_queue_sort_order_and_rank` and the 10 ADR 007 queue tests. |
| 5.2 | PARTIAL | The queue query returns all 8 fields, including `availability_raw` and `best_match_tier` (`test_queries.py` line 145). But screenshot 01 shows no match-tier column and no raw-availability column; it shows normalized availability and "Was". The tier is visible only in the object view (screenshot 02). |
| 5.3 | MET | Screenshots 02 and 03 show status history (SCD2 and the change feed), substitutes, tasks and the object's audit entries. |
| 5.4 | MET | The screenshot header shows snapshot 2026-10-30 and meta.last_updated 2026-10-29. Test: `test_queries.py::test_is_stale_boundary_is_exactly_one_day`. |
| 5.5 | MET | Every screenshot sidebar says "Demo identity, not authentication". Test: `test_action_edges.py::test_role_cannot_be_passed_in`. |
| 5.6 | MET | `docs/demo/01`-`06`: queue -> object (buyer locked) -> approve -> audit -> metrics -> match quality |

### US-6 Actions and write-back
| AC | Verdict | Evidence |
|---|---|---|
| 6.1 | MET | TEST_PLAN marks it PASS: `test_buyer_cannot_approve`, `test_viewer_refused_on_all_six`, plus the allowed case for every action |
| 6.2 | MET | Every QA refusal goes through `_assert_refused`, which checks no edit and one REFUSED row with the criterion. Screenshot 04 shows REFUSED rows with failed_criterion `role`. |
| 6.3 | MET | `test_ontokit_actions.py::test_forced_exception_after_edit_leaves_nothing` |
| 6.4 | MET | `test_action_edges.py::test_actions_survive_pipeline_rebuild`. FLAW_HUNT row 1. |
| 6.5 | MET | Screenshot 03 shows the substitute "approved" and task T-00006 open without a restart. Test: `test_app.py::test_pharmacist_approves_and_views_update`. |

### US-7 Hash-chained action log
| AC | Verdict | Evidence |
|---|---|---|
| 7.1 | MET | Screenshot 04 shows the columns. The hash rule is stated on the page. Tests: `test_audit.py`, `test_reconcile_edits_and_success_rows`. |
| 7.2 | MET | `test_audit.py::test_update_and_delete_raise` |
| 7.3 | MET | `test_audit.py::test_direct_sql_edit_of_row_k_is_found_at_k`, `test_deleting_the_newest_row_is_caught_by_the_checkpoint`, `test_anchor_catches_deletion_even_when_checkpoints_are_rewritten` |
| 7.4 | MET | Screenshot 04: filters for object key, actor, action and outcome, plus "Chain verify: OK, 17 rows recomputed at 2026-10-30 05:35:14 UTC". |
| 7.5 | MET | `test_audit.py::test_source_is_named_at_the_top`, `test_ontokit_boundary.py`. The tech lead chose the in-repo `ontokit` library (ADR 005). |

### US-8 Metric, baseline, guardrails
| AC | Verdict | Evidence |
|---|---|---|
| 8.1 | MET | The block in `docs/impact.md` is rendered by code from `reports/match_quality_v3.json` (`test_metrics.py::test_render_impact_block_round_trip`). It shows matcher R 0.605 against the fairer baseline R 0.093. Note: a narrative bullet below the block has stale numbers (section 3, item 4). |
| 8.2 | PARTIAL | Guardrails (a) 0.963 PASS, (c) OK PASS, (d) 0 PASS and (e) 0.909 PASS are all computed by code in `impact_metrics.json`. Guardrail (b) is `value: null`, status N/A. The JSON detail says "no live snapshot available to this computation", while `impact.md` says the live baseline worklist was 73 items. The 73 (and the 663) come from ADR 007's measurement on a read-only live DB copy, not from a report JSON. The SPEC asked for the queue length on the latest real snapshot. It is explained but not computed into a report. |
| 8.3 | MET | `impact_metrics.json` `secondary`: 7 affected events, 6 actioned, median 36.4 min, labeled "SIMULATED demo usage, not real users". Test: `test_simulation_runs_and_is_labeled`. |
| 8.4 | MET | `METRICS.md` section 3 lists at least 2 gaming paths for each metric, each with the control that catches it. |

### US-9 Deployment decomposition
| AC | Verdict | Evidence |
|---|---|---|
| 9.1 | PARTIAL | `docs/decomposition.md` is a 1-line pointer to `OPERATING_MODEL.md`. That file covers clarifying questions, the parts, real data sources, data owners, a rollout (2-week shadow, then one pilot site, then all 3 sites), on-call and what to measure. Missing: a **training plan** and **week 1 / 4 / 12 measures**. Top risks live in RISK_REGISTER and are not summarized here. |
| **9.2** | **NOT MET** | The concept map is in README "Foundry concept to this repo", but no row links to palantir.com/docs (grep found those links only in SPEC.md). No doc states "open-source stand-in, not Foundry"; the README says only "Foundry-style". |
| 9.3 | PARTIAL | OPERATING_MODEL names real sources and vendors (the ASHP list, wholesaler allocation feeds, Entra ID / Okta, Teams) with no URL and no UNVERIFIED mark. They are written as plan statements, not facts, so the risk is low, but the AC asks for a mark. |

---

## 3. Overclaim scan: README.md and docs/EXECUTIVE_ONE_PAGER.md

| Check | Result |
|---|---|
| Real deployment / real users | **Clean.** The README says "has not been deployed or used by a real pharmacy". The one-pager says "Not deployed". Usage is labeled SYNTHETIC/SIMULATED. |
| Clinical advice | **Clean in both docs.** Small app-side note: the Drug page's business question says "is there a **safe** substitute". Suggest "a candidate substitute", to match the "not clinical advice" text below it. |
| Compliance | **Clean.** No GxP, Part 11, HIPAA or "validated" claims. The log is correctly called tamper-**evident**. |
| Years / titles | **Clean.** "Two forward-deployed engineers" in "How it was built" describes agent seats, not Michael. Keep that title off the resume. |
| Seen-set score as headline | **Clean in the README, the one-pager and the app.** The headline is v3 unseen (0.963 / 0.605). The gold score 0.9825 is labeled "seen, optimistic; regression check only". |

Numbers I checked against the JSON (all match): 0.963 / 0.605 / 26 of 43 / 1 FP in 27; v2 0.957 / 0.936 (44 of 47); v1 0.980 / 0.877; fairer baseline 0.571 / 0.093 (v3) and 0.579 / 0.234 (v2); combinations 12/23 = 0.52; premixes 0/4; audit 14 rows; DQ 0.909; 307 / 0 / 2.

**Items to fix (wording only, no code):**
1. **"663 raw events" (README results table and one-pager) is misleading.** Per ADR 007, 663 is the **matched and stocked** events under the original queue rule; the live snapshot had 1614 raw records. Say "663 matched events". Neither 663 nor 73 is in any report JSON (`impact_metrics.json` guardrail (b) is null). Cite ADR 007 in the one-pager too.
2. **README: "Precision held at about 0.96 on all three blind sets."** v1 was 0.980. Say "between 0.957 and 0.980".
3. **README: "KNOWN_ISSUES: 17 honest limits."** It now has 19 rows (KI-17 resolved, KI-18 and KI-19 added today).
4. **`docs/impact.md`, "Reading the result":** "base-name search gets precision 0.579 and recall 0.234 on the holdout" are the **v2** numbers. On the v3 headline set they are 0.571 / 0.093, as the block above shows. Line 3 also says the numbers render from `reports/match_quality.json`; they now come from `_v3.json`.
5. **`reports/match_quality.json` reads like a pass.** It holds only the seen gold run (R 0.9825, `"pass": true`) with no "seen" label. Anyone who opens `reports/` first sees a passing score. Add `"seen": true` and a note, or rename the file to `match_quality_gold_seen.json`. This is the one place left where the seen-set score could pass for the headline.
6. Minor:
   - Screenshot 04 shows 17 audit rows while the Metrics page and the JSON say 14. The screenshots were taken on a scratch copy after 3 manual clicks, which the README discloses.
   - On the Match Quality page, `combination_product` (1 positive, a variant tag) sits next to "Combination 23" (post-hoc category). A reviewer may ask why; one caption line would answer it.

---

## 4. Interview talking points

**Palantir, Deployment Strategist and FDSE**

1. **Decomposition: start from the decision, not the data.**
   - The user's question: "At 7:30, of the shortages that are new or worse since yesterday, which hit what we stock, and for each one do I approve a substitute, open a buyer task, or mark it no-action?"
   - That question split the work into five parts: data in, identity matching, the decision workflow, audit, and change management. Each part solved a real constraint:
     - the API keeps no history, so the console saves write-once daily snapshots with SCD2 history
     - 20% of records have no `openfda` block, so a tier-3 match by ingredient, form and route is needed
     - a hidden button is not a control, so the role and submission-criteria checks sit in the action layer
   - **The decomposition story:** day one on live data put 663 events in a queue meant to hold 40. ADR 007 reframed day one:
     - treat the first snapshot as a baseline
     - show only actionable rows (Limited, Unavailable or To Be Discontinued, not Resolved)
     - one row per formulary item
     - the result was a 73-item onboarding worklist, with the daily queue measured from day two
2. **Learning: the number went down and I reported it.**
   - Blind recall went 0.877 (v1) -> 0.936 (v2) -> 0.605 (v3). v3 was scored on a harder blind set: 53% combination products.
   - Splitting by product type showed why:
     - single-ingredient recall stayed between 0.87 and 0.95
     - combinations fell to 12 of 23
     - premixed bags found 0 of 4
   - Precision stayed between 0.957 and 0.983 on every set, so the matcher fails safe: a miss falls back to the pharmacist's normal review, while a false match would put a wrong substitute in front of her.
   - We stopped tuning because v3 is now seen. Every round was scored once on a frozen, hashed set its author never opened.
   - We also caught our own overclaim: the app briefly headlined the older 0.936, which became BUG-PE-01 and was fixed.
3. **"Tell me about a project": a Foundry-style operational app with write-back, built on a live public API.**
   - One YAML ontology (5 object types, 6 link types, 6 action types) drives the views and the action rules.
   - "Approve substitute" is pharmacist-only and enforced server-side. A forced buyer approval is refused and logged.
   - Edits go to append-only tables that survive every full rebuild.
   - Two postmortems with numbers:
     - **a real concurrency race:** 11 of 15 runs failed before the writer lock, 0 of 20 after, and removing the lock fails 5 of 5
     - **a state-machine back door:** re-proposing undid an approval
   - 307 tests pass offline.
   - Be plain about scale: about 1,600 records a day. The hard part was messy identity data, not volume.

**Pfizer / Merck (pharma data and GxP-adjacent roles)**

- **Data integrity by design, without claiming compliance.**
  - Every action attempt, including refusals, writes one row: who, role, reason, old and new value, outcome and UTC time.
  - The rows are sha256 hash-chained. `verify` names the first edited row k. Checkpoints and an optional anchor catch deleted rows.
  - Label sets are frozen by hash and versioned, never edited, like a locked validation dataset.
  - This audit pattern is reused from the vaccine-assay project.
  - Know the gap and say it: DuckDB makes the log tamper-**evident**, not tamper-proof. The Part 11 path is Postgres triggers + REVOKE, WORM storage for the anchor, and real SSO (KNOWN_ISSUES KI-09, KI-10).
