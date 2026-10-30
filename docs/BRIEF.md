# BRIEF: Drug Shortage Operations Console

Owner: Principal Engineer. 2026-10-30. One page. Sources: [SPEC](team/SPEC.md), [METRICS](METRICS.md), [impact](impact.md), `reports/match_quality_v1/v2/v3.json`.

**Data rule.** Shortage records are real **public** openFDA data. The hospital (Riverbend Health, 3 sites), its formulary, stock, users, tasks and all usage are **SYNTHETIC**. Nothing here was deployed or used by a real pharmacy.

## Who the user is
Dana Ruiz, PharmD (fictional), drug shortage coordinator at a 3-hospital system. The persona is grounded in the ASHP shortage guidelines (a pharmacy-led shortage team with a named lead) and a published hospital case study of a daily shortage huddle. Her exact workflow is a design assumption that **no real coordinator has confirmed** (SPEC section 1).

## The decision it supports
Every morning at 7:30, before the shortage huddle:
> Of the shortages that are new or got worse since yesterday, which hit drugs we stock? For each one: approve a substitute, open a mitigation task for a buyer, or mark it "no action".

Today (assumed): she searches the FDA site one drug name at a time against a spreadsheet. That misses NDC format and name variants, shows no "since yesterday", and leaves no proof of who approved what.

## Goals
1. One ranked queue of **actionable** changes since the previous snapshot that touch the formulary (ADR 007).
2. Match FDA records to formulary items across NDC formats, salts, brands and typos, with **measured** precision and recall.
3. Actions with server-side role rules (only a pharmacist approves a substitute) and write-back that survives every rebuild.
4. An append-only, hash-chained action log that names the first tampered row.
5. History that the API does not keep: daily write-once snapshots, fully rebuilt (ADR 004).

## Non-goals
No shortage prediction. No clinical substitution advice (candidates are mechanical; a pharmacist decides). No real authentication. No LLM. No cloud or Docker in v1. No real hospital data.

## Success metric, baseline, guardrails
| | Definition | Baseline | Latest measured | Target |
|---|---|---|---|---|
| **Primary** | Recall of affected formulary items surfaced (matcher recall on a labeled set its author never opened) | Base-name search: recall 0.093 on the v3 set, 0.234 on the v2 set | **v3 (newest, harder set): 0.605** (26 of 43). v2: 0.936. v1: 0.877 | >= 0.95. **Not met.** |
| Guardrail (a) | Queue precision | Base-name search: 0.571 (v3) | **v3: 0.963** (1 false match in 27). v2: 0.957. v1: 0.980 | >= 0.90. Met on all three sets |
| Guardrail (b) | Daily queue length, uncapped | 663 events on day one under the original rule | Live: N/A (baseline day; one-time worklist of 73 items). Demo: 3 | <= 40 |
| Guardrail (c) | Audit chain verifies | | OK | OK |
| Guardrail (d) | Actions executed by a role that is not allowed | | 0 | 0 |
| Guardrail (e) | DQ pass rate, latest snapshot | | 0.909, 0 FAIL | >= 0.80 and 0 FAIL |

**How to read the primary metric.** Precision held at about 0.96 on all three unseen sets; recall moved from 0.94 to 0.61 when the set changed from mostly single-ingredient drugs to mostly combinations and premixes. The matcher fails **safe**: a miss means the item is not in the queue and the pharmacist's normal review catches it; a false match could put a wrong substitute in front of her. Per-category recall is in [CASE_STUDY](CASE_STUDY.md#what-the-matcher-can-and-cannot-do). The labels are team-drafted (qa-engineer); `reviewed_by` is pending on every row.

## Decision taken (2026-10-30, Engineering Manager)
Stop the matcher rounds at v3. Ship with recall reported as missed. v4 fixes and a fresh blind set are on the [ROADMAP](ROADMAP.md).
