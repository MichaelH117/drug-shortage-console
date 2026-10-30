# ADR 007: First snapshot is a baseline; the queue shows only actionable, ranked, capped items

Status: Accepted 2026-10-30 (Tech Lead). Needs product-owner sign-off on the SPEC wording (AC-5.1, AC-8.2 b) and Michael's OK on the thresholds. Replaces the DESIGN T2 mitigation ("guardrail b = n/a until 2 live days"), which hid the flaw instead of fixing it.

## Context
The queue (AC-5.1) is "events that are NEW or WORSENED in the latest snapshot and matched to a formulary item". The change feed (DESIGN 3.5, rule 2) calls every event on the first snapshot NEW, because there is nothing to compare with. On the first live day (2026-10-30, live DB) that gives **663 events** in the queue against a guardrail of **40**. A pharmacist would close the app on day one. Measured on the live DB (read-only copy):

| Cut | Events | Formulary items | Item x facility |
|---|---|---|---|
| Matched and stocked (today's queue rule) | 663 | 83 | 153 |
| ...and status not Resolved, availability Limited/Unavailable or status To Be Discontinued | 262 | 73 | 132 |
| ...and Current + Limited/Unavailable only | 178 | 49 | 82 |
| Row 2 filter and min days-on-hand <= 14 (synthetic stock) | not measured | 37 | not measured |

Three things drive the number:
1. **Day one is not news.** 1609 "NEW" events are a backlog, not changes since yesterday.
2. **Wrong grain.** Tier 3 matches the ingredient+form, so one formulary item matches every labeler's event for that generic (581 of 663 events are tier 3). The pharmacist acts per formulary item, not per labeler record.
3. **Not everything is actionable.** 386 of the 663 are `Current` but availability `Available`.

The queue already requires a stocked formulary item (`link.shortage_event_facility` joins `seed.facility_stock`), so "limit to stocked items" alone does not help.

## Options
1. **Keep it; report guardrail (b) as n/a on day one** (current DESIGN T2). Honest but useless: the first real use fails.
2. **Cap at 40, sorted.** Simple, but it silently hides items, which is the metric-gaming path SPEC R8 warns about.
3. **Add a BASELINE change type to the change feed.** Clear, but it changes AC-2.3 (6 types), `expected_changes.csv` and DE's frozen classifier for no gain: `core.change_feed.prev_snapshot_date IS NULL` already marks a baseline row.
4. **Chosen: baseline + actionable filter + item grain for the backlog + transparent rank + visible cap.**

## Choice
1. **Baseline.** Rows with `prev_snapshot_date IS NULL` (the earliest valid snapshot) never enter the daily queue. The change feed is unchanged (no DE change, AC-2.3 stands). Because the baseline is "the earliest snapshot by date", a backfilled older day just moves it; this is order-independent under ADR 004.
2. **Baseline review worklist (one time).** On a DB whose latest snapshot is the baseline, Home shows a "Baseline review" list instead of "since yesterday": **one row per formulary item** (facilities and matching events aggregated), filtered as in 3. This is the onboarding backlog (73 items on the live day, 49 without discontinuations).
3. **Actionable filter (both lists).** `present_in_latest AND status <> 'Resolved' AND (availability_normalized IN ('Limited','Unavailable') OR status = 'To Be Discontinued')`. Matched events that fail it (for example Current + Available) go to a collapsed **Watch** list, counted, never dropped.
4. **Rank (a sort, not a score), deterministic:** availability Unavailable, then Limited, then To Be Discontinued / Unknown; then min synthetic days-on-hand ascending (NULL last); then best match tier ascending (a near-miss tier 3 sorts last within its group); then number of facilities descending; then `item_id` / `event_key`.
5. **Visible cap.** Show the top 40, with "N more" and a page control. Guardrail (b) is computed on the **uncapped** actionable daily queue, so the cap cannot make the metric look good. The baseline worklist length is reported separately and is not held to 40.

## Consequences
- The daily queue (snapshot 2 onward) stays event-grain, as AC-5.1 says, and is small because it holds only real changes. Only the one-time backlog switches to item grain.
- AC-5.1 wording changes (queue = actionable NEW/WORSENED since the previous snapshot; baseline snapshot excluded). AC-8.2 (b) is measured on the uncapped daily queue. Product-owner owns that SPEC edit.
- Precision of the queue rises (Available events leave it). Recall on the gold set (primary metric) is unaffected; the gold set scores the matcher, not the filter. METRICS.md must add a gaming path: "tighten the actionable filter to shrink the queue"; caught by reporting the Watch-list size next to the queue length.
- The demo is unaffected in shape: fixture D1 becomes the baseline, D3 is still "since yesterday". `console/simulate.py` queue positions may shift (it fails loudly by design).
- Days-on-hand is synthetic, so the second rank key is demo-only. Said in the UI and impact.md.
- Thresholds (40, the 14-day hint) are Michael's call.

## Tasks created (TASKS.md, "ADR 007")
| ID | Seat | Task |
|---|---|---|
| PO-07 | product-owner | Amend AC-5.1 and AC-8.2 (b) as above; Michael confirms cap 40 |
| AE-07 | analytics-engineer | `mart.triage_queue`: exclude baseline rows, actionable filter, rank, `actionable_total`; new `mart.baseline_worklist` (item grain) and `mart.watch_list` |
| AE-08 | analytics-engineer | Home: baseline banner + worklist, Watch list, top-40 cap with "N more"; METRICS.md/impact.md guardrail (b) on uncapped queue + new gaming path |
| QA-05 | qa-engineer | Tests: single-snapshot DB gives 0 daily-queue rows and a non-empty worklist; Available excluded; rank deterministic; cap never changes guardrail (b); backfilling an older snapshot moves the baseline |
| DE-09 | data-engineer | `build_events` takes `snapshot_dates` so `present_in_latest` is right when the latest snapshot is empty (the filter depends on it) |
| PE-07 | principal-engineer | Replace RISK_REGISTER/KNOWN_ISSUES T2 entry with this ADR; README "first day" note |
