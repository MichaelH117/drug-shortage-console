# OPERATING MODEL: how I would deploy this at a 3-hospital system

Owner: Principal Engineer. 2026-10-30. Structure: clarify, decompose, integrate, roll out, run, measure. Riverbend Health is **SYNTHETIC**; this is a plan, not a record of a deployment. It also serves as the SPEC's `docs/decomposition.md` (US-9).

## 1. Clarify the problem before building
| Question | Why it matters | v1 assumption |
|---|---|---|
| Who decides, and when? | Sets the queue's grain and timing | Shortage coordinator (pharmacist), 7:30 huddle, daily |
| What is "affected"? | Defines recall | Item is on our formulary **and** stocked at a site |
| What does a miss cost vs a false alarm? | Sets the precision/recall trade | A miss falls back to today's manual review; a false match risks a wrong substitute discussion. So **precision first**, recall measured and improved |
| What is the system of record for stock? | Days-on-hand ranking | SYNTHETIC CSV today; real: the ERP / pharmacy inventory system |
| Who can approve a substitute? | Control design | Pharmacist only, server-side, logged |

## 2. Decompose
| Piece | Today in this repo | At Riverbend (real) |
|---|---|---|
| Shortage feed | openFDA daily pull, write-once snapshot | Same, plus the ASHP list and the wholesaler's allocation feed |
| Formulary | SYNTHETIC generator | Nightly export from the pharmacy information system (NDC-11, name, form, route, strength) |
| Stock / days on hand | SYNTHETIC | ERP or carousel inventory, per site, nightly |
| Identity / roles | Demo picker + `seed.app_user` | SSO; roles from IdP groups (pharmacist, buyer, viewer) |
| Ontology | `ontology/ontology.yaml` -> DuckDB views | Same YAML -> Postgres views, or Foundry object types (mapping in the README) |
| Actions + audit | `ontokit`, DuckDB, in-process lock | Same rules; Postgres with sequences, row locks, triggers + REVOKE on the log |
| Notifications | `ops.outbox` stub | Teams / email worker reading the outbox |

## 3. Integrations and data owners
| Feed | Owner (role) | Contract | Failure behavior |
|---|---|---|---|
| openFDA | Engineering (pull job) | Daily by 06:00 local | Stale banner after 1 day; app runs on the last snapshot |
| Formulary export | Pharmacy informatics | Nightly CSV, fixed columns | DQ FAIL blocks the rebuild; yesterday's views stay |
| Inventory | Supply chain analytics | Nightly, per site | Ranking falls back to "unknown days on hand"; never hides a row |
| Roles | IT identity team | IdP groups | Unknown user = inactive = no actions |

## 4. Roles (RACI, one line each)
- **Shortage coordinator (pharmacist):** owns the daily decision; Accountable for approvals.
- **Buyers:** own mitigation tasks; Responsible for closing them.
- **Pharmacy informatics analyst:** owns the brand map, normalization lists and label reviews; first responder for match quality.
- **Data/platform engineer:** owns the pull, rebuild, DB and on-call.
- **P&T committee / pharmacy director:** Informed weekly; reviews the audit export monthly.

## 5. Rollout
| Phase | Scope | Exit criteria |
|---|---|---|
| 0. Shadow (2 weeks) | Coordinator runs her old process **and** reviews the console; no actions | Pharmacist reviews the frozen label sets; misses logged against the queue |
| 1. Pilot, Riverbend Main | Real approvals and tasks at one site | Guardrails (a) to (e) green for 10 straight days; no refused-role SUCCESS rows |
| 2. All 3 sites | North and Kids added; site filter | Queue <= 40 on 90% of days; time to first action measured on real users |
| 3. Steady state | Monthly match-quality review on a new blind sample | Recall trend reported; label changes versioned |

## 6. On-call and SLAs
| Service | SLA (proposal) | Alert | First response |
|---|---|---|---|
| Daily snapshot present | By 06:30 local, 99% of business days | No folder for today at 06:30 | Re-run `--pull-only`; if openFDA is down, banner is automatic |
| Rebuild | Done by 07:00 | Non-zero exit or DQ FAIL | Old views stay (atomic rebuild); fix input; re-run |
| App availability | 07:00 to 18:00 local, business days | Health check | Restart; DB is the state, so no session loss |
| Audit chain | `verify_chain` OK every run | Not OK | Freeze writes; compare to the anchor; incident review |

On-call is business-hours only. A shortage console is a decision aid, not a dispensing system; outside hours the pharmacist uses the FDA site directly.

## 7. Change control
- **Code:** PR + review + full test suite green; any change to `pipeline/match.py`, `names.py` or `normalize.yaml` requires a match-quality run on a set the author has not seen, reported even if worse.
- **Ontology or action rules:** ADR + pharmacy director sign-off; YAML-vs-code consistency check blocks drift (FLAW_HUNT PB-6b).
- **Label sets:** never edited; a corrected set is a new version with a new hash.
- **Releases:** weekly, after the huddle; rollback = previous build + the same DB (edits and audit are never touched by a rebuild).

## 8. What I would measure in production
Primary: recall of affected items on a monthly blind sample reviewed by a pharmacist. Guardrails (a) to (e) as in [METRICS](METRICS.md). Adoption: share of huddle items actioned in the console; time to first action (real, not SIMULATED).
