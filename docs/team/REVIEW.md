# REVIEW: drug-shortage-console (TL-01)

Reviewer: Tech Lead. Date: 2026-10-30. Scope: action layer, write-back, snapshot rebuild, matcher, ontokit, http.
Not reviewed (seats working now): the flaky concurrency test (DE) and the v2-metrics app switch (AE). No code was
edited by this review.

Severity: **S1** wrong result or a control can be bypassed; **S2** wrong in an edge case or misleading audit;
**S3** robustness, clarity or a missing test. Ordered by priority.

Evidence: findings 1, 2 and 3 were reproduced with scripts on scratch copies of `data/demo/console.duckdb` and
pure DataFrames (outputs quoted). The other findings come from reading the code.

## Findings

| # | Sev | Where | Owner | Finding | Fix | Missing test |
|---|---|---|---|---|---|---|
| 1 | S1 | `console/actions.py:157-181` (criteria 168-173) | ml-engineer | **A buyer can undo a pharmacist's approval.** `propose_substitute` has no state criterion, so re-proposing an approved or rejected substitute writes a new `proposed` edit, and the latest edit wins. Reproduced: pharmacist approves -> state `approved`; buyer calls propose on the same event/alt -> SUCCESS, state `proposed`. A buyer cannot reject, but can get the same effect. | Add criterion `not_already_decided` (state is null/candidate, or proposed by the same flow; refuse `approved`/`rejected`) to `ontology.yaml` and the code; or make an approved row immutable except through a pharmacist-only action. | QA: `test_buyer_cannot_repropose_approved`, `test_repropose_rejected_refused` |
| 2 | S1 | `pipeline/match.py:266-271` (2b) and `:249` | ml-engineer | **Combination products match single agents on tier 2b.** The guard only checks "shares a word" and "any one strength equal". Reproduced: item "Bupivacaine HCl 5 mg/mL Injection" (product 0409-1162) vs event "Bupivacaine ... and Epinephrine" listing 0409-1162 in `openfda.product_ndc` -> tier 2, conf 0.90. This is holdout HP-006/HP-007. | ADR 006 condition 1: drop when both component sets (`split(" / ")`) exist and differ. | ML: unit test with the case above; also a 1-vs-2-component tier-1 case |
| 3 | S2 | `console/actions.py:241, 253` | ml-engineer | **REFUSED open-task rows are filed under the next real task's id.** `task_id` is computed before the action and used as `object_key` even when refused. Reproduced: refused open (`facility_valid`) logged on `T-00006`; the next successful open is also `T-00006`, so that task's audit history starts with an unrelated refusal (ALCOA "attributable/accurate"). | For a refused open, log `object_type=ShortageEvent, object_key=event_key` (or `T-pending`); keep the id only on SUCCESS. | QA: refused open then real open; task's audit rows = 1 SUCCESS |
| 4 | S2 | `pipeline/match.py:117-123` | ml-engineer | Brand-map key can be an ingredient of its own generic ("amphetamine" -> mixed salts). HP-051 regression. | ADR 006 condition 2. | ML: unit test |
| 5 | S2 | `pipeline/run.py:158-159` | data-engineer | **Rebuild is not atomic.** The pipeline schemas are committed (obj/link/mart dropped by CASCADE) before `_build_ml` runs in autocommit. If matching or view creation fails, the DB is left with no `obj.*`/`core.formulary_match` and the `rollback()` at :179 does nothing. Edits and audit survive (good), but the app is broken until a clean rebuild. | Move `_build_ml` inside the same transaction before `commit()` (DuckDB supports DDL in a txn), or build into a temp file and swap. | DE: monkeypatch `build_matching` to raise; assert obj views still exist and the old fingerprints are intact |
| 6 | S2 | `pipeline/history.py:87-91` | data-engineer | `build_events` ignores `snapshot_dates`; if the latest valid snapshot has 0 records, `latest_date` is the previous day, so `present_in_latest` is true for events that are gone. `alt_is_short` (approve rule) and the ADR 007 filter both read it. `build_history`/`build_change_feed` already take the dates. | Pass `dates` from `run.py:138`; use `max(snapshot_dates)`. (Tracked as DE-09.) | DE: empty latest snapshot -> all `present_in_latest` false |
| 7 | S2 | `ontology.yaml` actions + `console/actions.py:223-253` | ml-engineer | **No idempotency on `open_mitigation_task`.** A double submit (DESIGN T9) opens two tasks for the same event/facility/assignee. Approve/close are protected by state criteria; open is not. | Criterion `no_open_task_for_event_facility`, or an idempotency key from the form. | QA: same open twice -> second REFUSED |
| 8 | S3 | `ontokit/actions.py:110-112, 131-142` | ml-engineer | An exception inside a criterion or `apply` (including a DuckDB write conflict) rolls back and leaves **no audit row**. Correct for AC-6.3, but the attempt is invisible. Document it as an accepted gap (KNOWN_ISSUES) or log an `ERROR` outcome in a separate txn (needs ADR 003 change; not required for v1). | Document. | none |
| 9 | S3 | `ontokit/actions.py:76-80` | ml-engineer | `_next_seq` swallows every exception and returns 1. Only "table missing" should be tolerated; anything else is hidden until the seq-mismatch error. | Catch `duckdb.CatalogException` only, or let it raise. | none |
| 10 | S3 | `ontokit/actions.py:83-87` | ml-engineer | **ontokit domain leak (minor).** `_reason` hard-codes `resolution_note`/`note`. No forbidden imports (boundary test passes, grep finds no domain terms), but field names belong to the caller. | Add `reason_param` to the action spec or let the caller pass `reason=`. | none |
| 11 | S3 | `ontokit/writeback.py:216, 221` | ml-engineer | `COALESCE(edit, base)` means an edit can never set a column back to NULL (for example clearing a due date). Not used today. | Note it in the docstring, or select edit columns as-is when an edit row exists (`CASE WHEN e.key IS NOT NULL`). | none |
| 12 | S3 | `pipeline/http.py:29-32, 50` | data-engineer | Allow-list checks scheme and host but **not the port** (`https://api.fda.gov:8443` passes), and `build_opener` keeps the default `ProxyHandler`, so an `HTTPS_PROXY` env var routes traffic elsewhere. Redirect check is good. | Require `parts.port in (None, 443)`; build the opener with `ProxyHandler({})`. | DE: port case in `test_other_hosts_are_refused` |
| 13 | S3 | `pipeline/changes.py` + `console/queries.py:20-29` | analytics-engineer | Backfilling an older snapshot recomputes the feed, so a past day's queue (and "detection time") can change after the fact. Deterministic, but the historic metric is not stable. | One line in METRICS.md; no code change. | none |
| 14 | S3 | `console/actions.py:66-71`, `:241` | ml-engineer | `MAX()+1` ids for `edit_seq`, `outbox_seq`, `task_id`. Under concurrent writers, uniqueness depends on the PK conflict on `edit_seq`/`seq`. That is fine for single-writer DuckDB; it is related to DE's flaky test, so I leave the root cause to DE. | Wait for DE's finding. | already in tests/qa |

## What is correct (checked)
- **Role checks are server-side.** `get_actor` reads `seed.app_user`; unknown users become inactive; the UI cannot pass a role (`console/identity.py:12-17`, `ontokit/actions.py:103`). The app has no write path except `console.actions`.
- **Transaction:** edit rows plus SUCCESS audit row commit together; `audit.append` never commits; the seq cross-check at `ontokit/actions.py:137` stops edits pointing at the wrong row.
- **REFUSED rows:** one row per refusal, in its own txn after rollback, with the criterion id (audit enforces it at `ontokit/audit.py:172`).
- **Write-back survives rebuilds:** `schema.py` drops only pipeline schemas; ops/audit are `IF NOT EXISTS`; `substitute_id` is deterministic; the FULL OUTER JOIN keeps edit-only rows even if an event_key disappears.
- **Out-of-order load:** every run sorts all valid folders by date (`snapshots.py:245`), duplicates by date are resolved by name, history/feed are pure functions of the set. Tested in `test_history.py:84`.
- **ontokit:** no imports from pipeline/console/app (AST test + grep).

## ADR decisions
- **ADR 006 + addendum: APPROVED WITH CONDITIONS** (findings 2 and 4, a new form mapping, a test pinning the name-only trade, and v3 scored on a new held-out set).
- **ADR 007 (new): first snapshot is a baseline; actionable, ranked, visibly capped queue.** Tasks PO-07, AE-07, AE-08, QA-05, DE-09, PE-07.

## Checks run
- `pytest tests/test_match.py test_history.py test_changes.py test_http.py test_ontokit_boundary.py test_snapshots.py test_ontokit_actions.py` -> **73 passed**. The full suite was not run, to avoid clashing with DE's concurrency work and AE's app change.
- Repro scripts (scratchpad, on DB copies): findings 1, 2, 3 confirmed. Live-DB queue profile for ADR 007.
