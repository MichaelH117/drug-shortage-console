# Postmortem: a "flaky test" that was a real write race in the action log

Blameless. Owner of this write-up: Principal Engineer. Fix owner: data-engineer (FDSE-1). Date: 2026-10-30.
Sources: [HANDOFF.md](team/HANDOFF.md) (analytics-engineer and data-engineer entries), [REVIEW.md](team/REVIEW.md) finding 14, [TEST_PLAN.md](team/TEST_PLAN.md) section 3.
All data involved is SYNTHETIC (demo users, demo hospital).

## Summary
`tests/qa/test_action_edges.py::test_concurrent_close_only_one_wins` failed once in a full run and passed on 3 reruns, so it was reported as **flaky**. Root cause: two concurrent action transactions could read the same audit-chain tail and pick the same `seq`. The primary key stopped a forked chain, but the losing user got a raw database error instead of a logged refusal, and an unrelated race could hand two opens the same `task_id` check. Fixed with a process-wide writer lock. Before: 11 of 15 runs of the file failed. After: 0 of 20.

## Impact
- No wrong data was ever committed: the primary key on `audit.action_log.seq` held, and `verify_chain` stayed OK.
- But the control was weaker than it looked. A losing writer died with `ConstraintException`, so the attempt was **not logged**, which breaks the "every attempt is recorded" promise (AC-7.1) and ALCOA "complete".
- In a real deployment this is the morning huddle: two pharmacists approve or close the same item within a second. Streamlit opens one connection per rerun (ADR 001), so two browser sessions in one server process hit exactly this path.

## Timeline (2026-10-30)
| When | What |
|---|---|
| AE full-suite run | 256 passed, **1 failed**, 2 skipped. The failure passed alone and in 3 reruns. Logged as "looks flaky" for QA. |
| TL review | REVIEW finding 14: `MAX()+1` ids for `edit_seq`, `outbox_seq`, `task_id` depend on PK conflicts under concurrent writers. Root cause left to DE. |
| DE investigation | Ran the file 15 times: **11 of 15 failed**. Not flaky: a race whose window depends on thread timing. |
| DE fix | `ontokit/audit.py`: `writer_lock()` (re-entrant, process-wide) and `ChainConflict`. `run_action` runs under the lock; `open_mitigation_task` picks `task_id` under it. |
| DE proof | 20 sequential runs: the 3 concurrent tests failed **0 of 20**. |
| PE re-check (this doc) | Planted the bug back on a scratch copy (lock body replaced with `yield`): the concurrent tests failed in **5 of 5** runs. Restored lock: **10 of 10** passed. Full suite: 302 passed, 2 skipped, 0 failed. |

## Root cause
1. Each thread began a transaction and read the chain tail. DuckDB's MVCC gave both the same snapshot, so both computed `seq = n + 1` and the same `prev_hash`.
2. The first commit won. The second hit the primary key on `seq` and raised `ConstraintException` inside the runner, which rolled back and re-raised. Refusals are logged; exceptions are not (ADR 003, AC-6.3), so the attempt vanished from the log.
3. Separately, `open_mitigation_task` computed `task_id` **before** the transaction, so two opens could pick the same id.

Why it looked flaky: the window is the few milliseconds between reading the tail and committing. A test barrier makes it likely but not certain.

## Why the tests did not catch it sooner
They did, which is the good part: QA wrote the barrier-based race tests in TEST_PLAN section 3 before anyone saw a failure. The miss was in triage: one red run that passed on rerun was labeled "flaky" instead of "intermittent, unexplained". The rule we take from this is below.

## Fix
- `ontokit.audit.writer_lock()` serializes action transactions from BEGIN to COMMIT/ROLLBACK. The loser now waits, reads committed state, and gets a normal `ActionRefused` (for example `state_is_proposed`), which **is** logged.
- `ChainConflict` wraps a duplicate-seq insert so it can never surface as a raw database error.
- `task_id` is chosen under the lock.

## Regression tests that guard it
- `tests/qa/test_action_edges.py::test_concurrent_double_approve_only_one_wins`
- `tests/qa/test_action_edges.py::test_concurrent_close_only_one_wins`
- `tests/qa/test_action_edges.py::test_concurrent_opens_keep_chain_and_task_ids_unique`
- `tests/test_audit.py::test_concurrent_appends_under_writer_lock_keep_one_chain`

Planted-bug proof that these can fail: see [FLAW_HUNT.md](FLAW_HUNT.md), PB-7.

## What is still true (limits)
- The lock is **in-process**. Two server processes on one DuckDB file are still unsafe; DuckDB itself allows only one read-write process (ADR 002). The real fix for multi-user is Postgres with row locks or `SERIALIZABLE` plus a sequence (see [OPERATING_MODEL](OPERATING_MODEL.md) and [KNOWN_ISSUES](KNOWN_ISSUES.md) KI-07).
- An exception, as opposed to a refusal, still leaves no audit row (REVIEW 8, KNOWN_ISSUES KI-08).

## Lessons
1. **"Flaky" is a hypothesis, not a diagnosis.** A test that fails once is run 10 to 20 times before anyone calls it flaky. One unexplained failure blocks sign-off.
2. Concurrency tests with a barrier earn their cost. Keep them in the default suite.
3. Ids derived from `MAX()+1` are a smell the reviewer should flag (TL did, finding 14).
