# Drug Shortage Console: executive one-pager

Demonstration project, 2026-10-30. Real public FDA shortage data; **SYNTHETIC** hospital, users and usage. Not deployed.

**The problem.** A 3-hospital pharmacy team must decide each morning which new or worsening FDA shortages hit drugs it stocks, and act. Today (assumed) that is a one-name-at-a-time search with no "since yesterday" and no record of who approved what.

**What it does.** Pulls the FDA list daily, keeps history the FDA does not, matches records to the formulary despite NDC and name variants, and shows one ranked queue. A pharmacist approves substitutes, buyers own tasks, and every attempt lands in a tamper-evident log.

**Results (latest runs)**
| | Value |
|---|---|
| Matcher precision, newest blind set | **0.963** (1 false match in 27) |
| Matcher recall, newest blind set | **0.605** (26 of 43); target 0.95 **not met** |
| Same, previous blind set (mostly single-ingredient drugs) | precision 0.957, recall 0.936 |
| Name search on the newest set | recall 0.093 |
| Day-one queue | 663 matched events (from 1,614 raw records) reduced to a 73-item review list |
| Role violations executed | 0; audit chain verifies |
| Tests | 307 passed, 0 failed, 2 skipped (live API) |

**The trade we chose.** The matcher fails safe: when unsure it stays silent and the pharmacist's normal review applies; it rarely suggests a wrong match. Recall is weakest on combination products (0.52) and premixed bags (0 of 4). We stopped tuning at v3 and put the fixes on the roadmap, because more tuning on a set we have already seen would only inflate the number.

**Top risks.** Missed combination products (open, roadmap v4). Labels not yet reviewed by a pharmacist. Single-writer database and no real login (fine for a demo, replaced in any deployment).

**To go live (estimate).** About $50 to $160 per month of cloud for one system; the real cost is a pharmacist reviewing labels and an analyst owning the brand list. Shadow for 2 weeks, pilot one site, then all three. Details: [OPERATING_MODEL](OPERATING_MODEL.md), [SCALE_AND_COST](SCALE_AND_COST.md).
