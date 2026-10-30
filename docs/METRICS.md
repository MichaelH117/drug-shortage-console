# METRICS: Drug Shortage Console

Owner: analytics-engineer. 2026-10-30. Implements SPEC US-8 (AC-8.1 to 8.4) and the queue sort in DESIGN 3.4.
Implementation: `console/metrics.py` (the only code that computes these), `console/queries.py` (queue).
Reconciliation tests: `tests/test_metrics.py`, `tests/test_queries.py`. Rendered numbers: `docs/impact.md`, app page **Metrics**.

All hospital data (facilities, formulary, stock, users, tasks) is **SYNTHETIC**. The gold set is **team-labeled (qa-engineer), reviewed_by pending**.

## 1. Metric catalog

| Metric | Formula | Grain | Source | Owner | Threshold |
|---|---|---|---|---|---|
| **Primary: recall of affected formulary items surfaced** | TP / (TP + FN) of the matcher on the **headline set**, where a positive is a (formulary item, shortage event) pair labeled 1 | one labeled pair | headline run (section 1a): today `reports/match_quality_v3.json` `holdout_v3_unseen.matcher.overall.recall` | AE (definition), ML (matcher), QA (labels) | >= 0.95 (E2) |
| Baseline (comparison): fairer manual search | same formula for `baseline_v2` = base-name equality (TEST_PLAN 6.2: no salts, brands, NDCs, routes, strengths or fuzzy matching) | one labeled pair | headline run `baseline_v2.overall` | AE / ML | reported, no threshold |
| Naive baseline (reported, not the comparison) | exact lowercase `name_raw == generic_name` (AC-4.4) | one labeled pair | headline run `baseline.overall` | AE / ML | reported; scores 0 by construction |
| (a) Queue precision | TP / (TP + FP) of the matcher on the headline set (the queue is matcher output, so this is the share of queue rows that truly hit us) | one labeled pair | headline run `matcher.overall.precision` | AE | >= 0.90 |
| (b) Daily queue length | count of rows of the **uncapped** actionable daily queue (ADR 007) on the latest **live** snapshot; Watch-list size reported beside it | one shortage event in the queue | `console.queries.queue_for_snapshot` on the live DB | AE | <= 40; **N/A on a baseline day** (no previous snapshot), when the one-time worklist size is reported instead and not held to 40 |
| (c) Audit chain verify | `ontokit.audit.verify_chain(con).ok` | whole log | `audit.action_log`, `audit.chain_checkpoint` | AE (reports), DE (audit code) | OK |
| (d) Disallowed-role executions | count of `SUCCESS` rows whose `role` is not in the ontology's `roles` for that `action_type` | one audit row | `audit.action_log` + `ontology/ontology.yaml` | AE | = 0 |
| (e) DQ pass rate | PASS / (PASS + WARN + FAIL) for the latest snapshot's `dq_results` (SKIPPED excluded) | one DQ check | `core.dq_results` | AE (reports), DE (checks) | >= 0.80 **and** 0 FAIL |
| **Secondary: time from detection to first action** | per affected event: `min(ts_utc of SUCCESS action on the event, its substitutes or tasks) - fetched_at_utc of the snapshot that first put it in the queue`; report n, median, p90, max in minutes | one affected shortage event | `stage.snapshot`, queue per snapshot, `audit.action_log`, `ops.*_edit` | AE | none; **SIMULATED demo usage, not real users** |

### 1a. Which run is the headline (`console.metrics.load_quality`, the only place this is decided)
- **Headline = the newest round scored once on a set its author never opened**: the highest `reports/match_quality_vN.json`, its `*_unseen` block. Today: v3 on `tests/fixtures/holdout_v3.csv` (90 pairs, 43 positives), P 0.963 / R 0.605.
- **History** is shown next to it, never instead of it: every round's blind score (v1 on the gold set, v2 on holdout_v2, v3 on holdout_v3), plus the headline file's `*_seen_reference` blocks labeled **"seen, optimistic"**, because those sets drove earlier error analyses.
- **Per-category recall** (headline and history): single ingredient / combination / premix, from the Principal Engineer's post-hoc pair lists in `docs/img/make_charts.py`. They are copied into `console.metrics` and a test checks the copy is identical. Counts come from the frozen CSV positives minus the report's FN pairs. This is not a frozen label and is never used for tuning.
- **Fail-safe check:** the min and max precision over every scored set. It "holds" if the min is >= 0.95. Today it is 0.957 to 0.983 over 5 sets.
- Only unseen runs are compared with the 0.95 / 0.90 targets. If a v3 is tuned on the holdout, the holdout becomes "seen" too and needs a new blind set.
- If no per-round file exists, the headline falls back to `reports/match_quality.json` and says so.

Definitions in detail:
- **Affected** = the event is in that day's daily queue (ADR 007: NEW or WORSENED since the previous snapshot, matched to a SYNTHETIC facility's formulary, and actionable on that day's own status and availability).
- **Detection** uses the snapshot that first put the event *in the queue*, not `core.shortage_event.first_seen` as TASKS AE-01 wrote. Reason: a WORSENED event can be first seen days before it needs action; counting from first_seen would charge the team for days when the event was not in the queue. Logged for TL.
- The first snapshot ever is excluded from detection (every event is NEW there; same reason as T2).
- Only audit rows whose reason starts with `[SIMULATED]` count, so clicks during a demo never move the number.
- Action-to-event mapping: substitute ids via `obj.substitute`, task ids via `ops.mitigation_task_edit`, otherwise the object key itself.
- p90 uses `statistics.quantiles(n=10, method="inclusive")`.
- **Backfill caveat (REVIEW 13).** History is rebuilt from all snapshot folders (ADR 004). Backfilling an older snapshot recomputes the change feed, which can change a past day's queue, the baseline day, and therefore past "detection" times. The rebuild is deterministic, but historic values of the secondary metric and of guardrail (b) are only stable while the set of snapshots is unchanged. Compare runs on the same snapshot set.

## 2. Queue spec (DESIGN 3.4, AC-5.1, 5.2, ADR 007)

The SQL text lives in `console.queries.TRIAGE_QUEUE_SQL`, which creates three views; `pipeline/objects.py` executes it.

**Actionable** (on the snapshot's own values): `status <> 'Resolved' AND (availability IN ('Limited','Unavailable') OR status = 'To Be Discontinued')`.

| View | Grain | Rows | Rank |
|---|---|---|---|
| `mart.triage_queue` (daily queue) | shortage event | latest snapshot, `change_type IN ('NEW','WORSENED')`, `prev_snapshot_date IS NOT NULL` (baseline rows never enter), matched to a stocked item, actionable. Uncapped; carries `queue_rank` and `actionable_total` | availability Unavailable, Limited, then To Be Discontinued / Unknown; min SYNTHETIC days on hand (NULL last); best match tier; facilities desc; event_key |
| `mart.baseline_worklist` (one-time review) | formulary item | items with at least one actionable matched event in the latest snapshot; facilities, events and the worst event aggregated; `is_baseline_day` flag | worst availability; min days on hand; best tier; facilities desc; item_id |
| `mart.watch_list` | shortage event | matched events that fail the actionable filter: daily = NEW/WORSENED since the previous snapshot; baseline day = all matched baseline records (`change_type = 'BASELINE'`) | change type, name |

Home on a baseline day shows the worklist with a "Baseline day" banner; on later days it shows the daily queue, and the worklist moves to a collapsed onboarding expander. Both lists show the top **40** with "N more" and a page control. The cap is UI only: every metric uses the uncapped views. Watch and the remaining changes (not stocked, IMPROVED, RESOLVED, DROPPED) are listed in expanders, so nothing disappears silently. Days on hand is SYNTHETIC, so the second rank key is demo-only.

Measured: on the live day (1 snapshot) the daily queue is 0, the baseline worklist 73 items, and the Watch list 401 events. On the demo (D3 vs D2) the daily queue is 3 and the Watch list 3.

## 3. Gaming paths and which guardrail catches them (AC-8.4)

| Metric | Gaming path | Caught by |
|---|---|---|
| Primary recall | Loosen tier 3 (e.g. ignore route/strength) so everything matches | (a) precision drops; (b) queue length grows |
| Primary recall | Edit gold labels after seeing results | gold sha256 freeze test (`test_gold_frozen.py`); report prints the hash; (none of a-e by itself, so the freeze is the control) |
| Primary recall | Evaluate on the generator's own variants instead of the hand-written gold set (T3) | report names the gold file and hash; the app shows it |
| Baseline | Make the baseline a straw man so any lift looks large | The comparison is the fairer base-name baseline, not the exact-name one (which scores 0 because openFDA `generic_name` carries the dosage form). And the **target is absolute (0.95)**, so no baseline choice can make the primary metric pass |
| Primary recall | Quote the score on the set used for tuning ("seen") as the headline | `load_quality` picks only an unseen run as the headline; seen scores are labeled "seen, optimistic" in the app and the docs |
| (a) Queue precision | Drop hard negatives from the gold set to raise precision | freeze test (counts per kind are asserted) |
| (a) Queue precision | Show only tier-1 matches in the queue | primary recall falls |
| (b) Queue length | Tighten the actionable filter (for example Unavailable only) to shrink the queue | the Watch-list size is reported next to the queue length (in (b)'s detail and on Home), so a shrinking queue with a growing Watch list shows; the filter is fixed in ADR 007 and changes need a new ADR |
| (b) Queue length | Lower the UI cap to make the visible list short | (b) is computed on the uncapped view; a test sets the cap to 1 and checks (b) does not change |
| (b) Queue length | Report it on the demo fixture DB (3 items) instead of live | the metric is defined on live snapshots only; demo length is labeled SYNTHETIC |
| (b) Queue length | Call a crowded day a "baseline day" to get N/A | baseline = the earliest snapshot by date only (`prev_snapshot_date IS NULL`); it cannot be chosen |
| (c) Chain verify | Rewrite the whole chain after an edit | external anchor file (`write_anchor`) when used; KNOWN_ISSUES says DuckDB cannot prevent this |
| (c) Chain verify | Delete the newest rows | checkpoint table check |
| (d) Role executions | Widen roles in `ontology.yaml` | (d) reads the same YAML, so it would not catch this; caught by QA planted bug PB-2 and code review of the YAML |
| (d) Role executions | Write edits directly, bypassing actions | (c) breaks if the log is touched; QA reconcile test (edit row has exactly one SUCCESS audit row) |
| (e) DQ pass rate | Turn a FAIL check into WARN | the "0 FAIL" half needs FAIL; review of `pipeline/dq.py` thresholds; check count is fixed per snapshot |
| (e) DQ pass rate | Mark checks SKIPPED | SKIPPED is shown in the detail; volume of checks per snapshot is visible |
| Secondary time | Click "open task" immediately with no real follow-up | not prevented; paired with tasks closed and approval counts in the audit log; it is labeled simulated anyway |
| Secondary time | Count only events that were acted on | `not_actioned_events` is reported next to the median |

## 4. Known limits
- The headline is measured on a 90-pair holdout (43 positives). One FN moves recall by about 0.023. The category cells are small (premix n = 4, single n = 16), so read them as directions, not rates.
- Queue precision is a gold-set proxy. True queue precision on live data needs pharmacist review of a live queue sample.
- The secondary metric is SIMULATED demo usage, not real users.
