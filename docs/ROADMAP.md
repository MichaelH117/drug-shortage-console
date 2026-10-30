# ROADMAP

Owner: Principal Engineer. 2026-10-30. Decision on record: **matcher rounds stop at v3** (Engineering Manager, 2026-10-30). v4 is planned here, not started.

## Now (before showing the project)
| Item | Owner | Why |
|---|---|---|
| BUG-PE-01: app headline picks the newest unseen run (v3) | analytics-engineer | In-app numbers must match the README and the newest blind result |
| BUG-PE-02: label the gold-set row "seen, regression check" in `run.py` and `results.json` | data-engineer, devops-engineer | Stop a seen 0.98 from reading as the result |
| BUG-PE-03: committed time-zone test for `local_today` | data-engineer | Close the FLAW_HUNT gap |
| README Quickstart, pinned deps, CI | devops-engineer | One-command run for a reviewer |
| QA-04 remaining planted bugs (PB-1, PB-3, PB-4) and `generator/tamper.py` | qa-engineer | Complete TEST_PLAN section 4 |
| SPEC wording for ADR 007; Michael's OK on the cap of 40 | product-owner, Michael | Close the open decision |

## Next (v4 matcher, then a new blind set)
Fix only the classes found in the v3 error analysis, freeze the code hashes, then score once on a **new** set (`holdout_v4`) that QA writes and the matcher author never opens. v3 is now a seen set.
| Fix | v3 misses it addresses | Owner |
|---|---|---|
| Per-component strength set, order-insensitive | about 5 (multi-strength combinations) | ml-engineer |
| Diluent noise words ("in D5W", "in 0.9% NaCl"); active drug concentration is the strength | 4 (premixes) | ml-engineer |
| Split the mapped generic when a brand names one component of a combination | 1 FN (VP-006) and the seen v2 regression HP-013 | ml-engineer |
| Ambiguous brands (plain vs with epinephrine) judged by the item text | the only v3 FP (VP-008) | ml-engineer |
| Oral powder form; `U-100` and `[iU]` units; `w/ epi` abbreviation; parse `1:N` ratio strengths | 3 | ml-engineer |
| `holdout_v4`: at least 40 positives, stratified so single-ingredient, combination and premix each have at least 10, so per-category recall has a usable n | | qa-engineer |
| Report per-category recall in `pipeline.evaluate` (today it is computed post hoc in `docs/img/make_charts.py`) | | ml-engineer, analytics-engineer |

Expected effect: none claimed. v4 is reported as measured, including if it is worse.

## Later
| Item | Depends on |
|---|---|
| External brand-to-ingredient source (RxNorm) for brands absent from openFDA (4 v3 misses) | Product decision on an external dependency |
| Daily scheduled pull, so "since yesterday" runs on real consecutive days | Michael's OK (E4) |
| Postgres for ops/audit: sequences, row locks, triggers + REVOKE | Multi-user deployment |
| SSO and IdP roles | Real deployment |
| Outbox worker (Teams/email) for task assignments | Real deployment |
| Per-snapshot parquet cache (ADR 004) | About a year of history |
| Rebuild the ontology in the free Palantir AIP Developer Tier | Michael signs up himself |
| Pharmacist review of all frozen label sets | A pharmacist reviewer |
