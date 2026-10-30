# CASE STUDY: a Foundry-style drug shortage console on live openFDA data

Portfolio project, built by a small agent-assisted team (product, tech lead, two forward-deployed engineers, analytics, QA, devops, principal engineer seats) on 2026-10-30. **Shortage records are real public openFDA data. The hospital, its formulary, stock, users, tasks and all usage are SYNTHETIC.** Nothing here was deployed or used by a real pharmacy.

## The problem
A hospital shortage coordinator has to answer every morning: *which shortages that are new or worse since yesterday hit drugs we stock, and what do we do about each?* openFDA publishes current state only, one record per package NDC, with free-text availability ("Unvailable", "Limited Availalbility"), 20% of records lacking harmonized ids, and names that rarely match a formulary's spelling. A name search misses most of it: on the newest blind set, a base-name search finds **4 of 43** true matches (recall 0.093).

## What we built
- **Write-once daily snapshots** and a full deterministic rebuild into SCD2 history and a change feed (NEW, WORSENED, IMPROVED, RESOLVED, DROPPED, UNCHANGED) (ADR 004).
- **An ontology in one YAML file**: 5 object types, 6 link types, 6 action types, generated into DuckDB views (ADR 002).
- **A tiered matcher**: exact package NDC, product NDC with form/strength/component guards, then ingredient + form + route + strength, with a guarded near-miss tier (ADR 006).
- **Actions with submission criteria and write-back**: role checked server-side, state criteria, one transaction per action, edits that survive every rebuild (ADR 003).
- **A hash-chained action log** that names the first tampered row and catches deletion of the newest row.
- **A queue that a person can use on day one**: first snapshot is a baseline; only actionable items; ranked; a visible cap (ADR 007).
- `ontokit`: the domain-free part (ontology loader, action runner, write-back, audit) as an in-repo library (ADR 005).

## What the matcher can and cannot do
Each matcher version was scored **once** on a labeled set its author never opened. Labels are team-drafted (qa-engineer); `reviewed_by` is pending on every row.

| Run | Pairs / positives | Precision | Recall | Base-name search recall |
|---|---|---|---|---|
| v1 matcher, gold set (blind at the time) | 102 / 57 | 0.980 | 0.877 | not computed in v1 |
| v2 matcher, unseen holdout | 87 / 47 | 0.957 | 0.936 | 0.234 |
| **v3 matcher, unseen harder set (newest)** | 90 / 43 | **0.963** | **0.605** | 0.093 |

Recall by product type (post-hoc category from the item text; the list is in `docs/img/make_charts.py`):

| Set | Single ingredient | Combination | Premix (drug in D5W/saline) |
|---|---|---|---|
| v1 gold | 46/53 = 0.87 | 4/4 = 1.00 | none in set |
| v2 holdout | 42/44 = 0.95 | 2/3 = 0.67 | none in set |
| v3 holdout | 14/16 = 0.88 | **12/23 = 0.52** | **0/4** |

**Reading.** v1 and v2 were 93% and 94% single-ingredient. v3 was 53% combinations plus 4 premixes, and recall fell to 0.605. Single-ingredient recall stayed between 0.87 and 0.95. Precision held between 0.957 and 0.980 on all three. The 17 v3 misses: about 5 multi-strength combinations, 4 premixes, 4 brands that appear nowhere in the FDA data, 1 brand-on-one-component bug, 3 small parsing gaps.

**Why we stopped at v3.** The matcher fails safe. A miss leaves the item off the queue, and the coordinator's existing manual review still applies. A false match could put a wrong substitute in front of a pharmacist. Holding precision near 0.96 while recall is reported honestly as missed is the right trade for patient safety. Another round tuned on v3's errors would only be meaningful with a new blind set, so v4 fixes and a v4 set are on the [ROADMAP](ROADMAP.md). The gold set now scores 0.982, but that set drove the fixes, so it is a regression check, not a result.

## What went wrong, and what we did
| Problem | How we found it | Fix | Guard |
|---|---|---|---|
| "Flaky" concurrency test was a real write race: losing writer got a raw DB error and was not logged | DE ran it 15x: 11 failed | Process-wide `writer_lock`, `ChainConflict` | 0/20 failures after; planted removal fails 5/5 ([postmortem](POSTMORTEM_concurrency_race.md)) |
| A buyer could undo a pharmacist's approval by re-proposing | Tech-lead review, reproduced | `not_already_decided` criterion | 5 tests fail when it is disabled ([postmortem](POSTMORTEM_repropose_undoes_approval.md)) |
| Day one put 663 events in the queue | Measured on the live DB | ADR 007: baseline + actionable + item grain + cap | 73-item worklist; `tests/qa/test_queue_adr007.py` |
| Combination matched single agent via openFDA product list | Review + holdout HP-006/007 | Component-set guard on NDC tiers | `test_r3_tier2b_combination_never_matches_single_agent` |
| Refused task-open logged under the next real task's id | Review | Refusal filed on the ShortageEvent | `tests/qa/test_review_attacks.py` |
| Rebuild not atomic around matching | Review | Matching inside the rebuild transaction | DE rollback test |
| App still headlines v2 after v3 was scored | PE flaw hunt | `console.metrics.load_quality` picks the newest `match_quality_vN.json` unseen block (BUG-PE-01, fixed) | `tests/test_metrics.py` |

## Evidence
307 tests pass, 2 skipped (live API), 0 failed (`docs/results.json`, 2026-10-30). Rebuild is idempotent: 20 of 21 tables byte-identical on a second run, the 21st differs only by run id and timestamp; edits and the audit chain survive a rebuild ([FLAW_HUNT](FLAW_HUNT.md)).

## What I would do differently
1. Stratify the first blind set by product type, so the first number is not flattered by an easy mix.
2. Treat any single unexplained test failure as a bug until it has run 20 times clean.
3. Review actions as a state machine from the start, not one action at a time.
