# BUGS: drug-shortage-console

Owner: QA / Test Engineer (SDET). Format: id, date, steps, expected, actual, severity, owner, status.

## Run log
### 2026-10-30: action edge tests re-run after DE-07 and ML-05
- `pytest tests/qa`: 34 passed, 0 failed. The xfail guard was removed from `tests/qa/test_action_edges.py`.
- `pytest tests` (full suite, one run): 219 passed, 2 skipped (live), 0 failed.
- **No product bugs found in this run.** Covered: role and inactive-user refusals, double close and double approve, objects that don't exist, reason, note and due-date boundaries (including the New York day), three concurrent races (chain verified, no duplicate task_id or seq), the edit-to-audit reconcile, and edits surviving a full rebuild.

### 2026-10-30: round 2 (QA-05 attack tests for REVIEW 1, 3, 7 and ADR 007 queue tests)
- The new attack tests were written to be xfail until ML fixed the findings. They **passed on first run**: ML-09/ML-10 had already landed (`not_already_decided`, refused opens filed on the ShortageEvent, `no_open_task_for_event_facility`). So no xfail marks were needed.
- ADR 007 queue tests: 10 of 10 pass (AE-07 has landed).
- Full suite: 302 passed, 2 skipped, 0 failed.
- One test-harness note, not a product bug: `build()` on a non-fixture raw folder calls `ensure_snapshot(today)`. Offline tests must name folders by ISO date and pass `run_date`. QA's helper does this.

## Open bugs
QA-04 planted-bug proofs will be recorded here (PB-1 to PB-5 in TEST_PLAN.md section 4). PE ran PB-2, PB-5 and its own PB-6/PB-7 on a scratch copy; results in docs/FLAW_HUNT.md.

### BUG-PE-01 (2026-10-30, principal-engineer) S2: the app headline ignores the newest unseen run (v3)
- **Steps:** open the Metrics or Match Quality page, or read `docs/impact.md` / `reports/impact_metrics.json`.
- **Expected:** METRICS.md 1a: "Headline = the newest matcher scored once on a set its author never opened." That is now v3 on `holdout_v3` (P 0.963, R 0.605, `reports/match_quality_v3.json` `holdout_v3_unseen`).
- **Actual:** headline is v2 on holdout_v2 (P 0.957, R 0.936). `console/metrics.py:47-87` `load_quality` only looks for `match_quality_v2.json`. Guardrail (a) is also computed from v2.
- **Owner:** analytics-engineer. Fix: pick the newest `match_quality_v*.json` with an unseen block; show v2 and v1 as history. Re-render impact.md and retake screenshots 05/06.
- **Status:** FIXED 2026-10-30 (analytics-engineer). `console.metrics.load_quality` now picks the highest `reports/match_quality_vN.json` and its `*_unseen` block. The Metrics and Match Quality pages, `docs/impact.md` and `reports/impact_metrics.json` headline v3 on holdout_v3 (P 0.963 / R 0.605, FAIL vs 0.95), and guardrail (a) uses v3 (fp 1 of 27). Screenshots 05 and 06 were retaken. Test: `tests/test_metrics.py::test_newest_blind_round_is_headline_and_seen_is_labeled`. Verified at demo sign-off by reading `reports/impact_metrics.json` and `docs/demo/05_metrics.png` / `06_match_quality.png` (product-owner, 2026-10-30).

### BUG-PE-02 (2026-10-30, principal-engineer) S2: the auto-written results block reports a seen-set score with no "seen" label
- **Steps:** `python -m pipeline.run --fixtures --test`; read `docs/results.json` `summary.match_quality` and the README block written by `pipeline/run.py::write_readme_results`.
- **Expected:** the gold set drove v1/v2 error analysis, so its score is a regression check, labeled "seen, optimistic", never shown as matcher quality.
- **Actual:** `docs/results.json` now holds P 0.9825 / R 0.9825 on the gold set, and the README row says "Matcher P / R (gold set, team-labeled)". A reader would take 0.98 recall as the result; the unseen v3 recall is 0.605.
- **Owner:** data-engineer (run.py), devops-engineer (README). Fix: rename the row to "Gold set (seen; regression check only)" and add the headline unseen P / R from the newest `match_quality_v*.json`.
- **Status:** FIXED 2026-10-30 (data-engineer). `pipeline/run.py::latest_unseen_holdout` reads the newest `reports/match_quality_v*.json` with an `*unseen*` section; the README headline is now "Matcher P / R, unseen holdout v3" (0.963 / 0.6047) and the gold row says "Gold set (seen, optimistic; regression check only)". `docs/results.json` `summary.match_quality` now has `holdout_unseen` and `gold_seen`. Tests: `test_headline_is_the_newest_unseen_holdout`, `test_readme_block_headlines_unseen_and_labels_gold_as_seen`.

### BUG-PE-03 (2026-10-30, principal-engineer) S3: no committed test for the snapshot-date time zone
- **Steps:** `grep -rn "local_today(" tests` finds only a monkeypatch.
- **Expected:** a test that 2026-10-30 03:30Z maps to 2026-10-29 and that DST end (2026-11-01) is correct (DESIGN T10).
- **Actual:** behavior is correct when checked by hand (FLAW_HUNT row 8) but unguarded.
- **Owner:** data-engineer. Fix: add `tests/test_snapshots.py::test_local_today_late_night_utc_and_dst`.
- **Status:** FIXED 2026-10-30 (data-engineer). `tests/test_snapshots.py::test_local_today_late_night_utc_and_dst` covers 03:30Z -> previous day, both DST edges (2026-03-08, 2026-11-01) and an aware UTC input.

## Known non-bugs (label questions, not code)
- GP-024 (Saxenda vs Victoza) and GP-088 (multi-route lidocaine) are rule-literal gold labels flagged for Michael's review. They are not matcher bugs, and the gold set is not changed.
