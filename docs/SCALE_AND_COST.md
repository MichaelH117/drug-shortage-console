# SCALE AND COST

Owner: Principal Engineer. 2026-10-30. **Measured** numbers are marked as measured, with the command. Everything else is an **ESTIMATE** (reasoned, not run). Cloud prices are rough public list-price ranges from memory, not quotes, and were not checked today.

## Today (measured)
| Quantity | Value | Source |
|---|---|---|
| Live openFDA records per daily pull | 1,614 (2 API requests) | `data/live/raw/2026-10-30/meta.json`, SPEC section 0 |
| Raw snapshot size | 2.45 MB of JSON per day | `records.json` on disk |
| Live build from the cached snapshot (stage, history, matching, views) | **2.25 s** (pipeline's own timer) | `python -m pipeline.run` on a scratch copy, 2026-10-30 |
| Demo build (3 SYNTHETIC fixture days) | 0.92 s | `python -m pipeline.run --fixtures --test` |
| Live DB file / demo DB file | 5.0 MB / 10.8 MB | file sizes |
| DuckDB limits | `memory_limit 512MB`, `threads 2` | `config/settings.yaml` |
| Full test suite | 302 passed in 74.76 s to 80.17 s | FLAW_HUNT |

## Growth model
Full rebuild (ADR 004) re-parses **every** snapshot folder on every run, so cost grows with days x records per day.
- 1 year at 1x: 365 x 1,614 = about 589k stage rows and about 0.9 GB of raw JSON. ESTIMATE.
- The JSON parse dominates. If a snapshot costs roughly 0.5 to 1 s to parse and stage (ESTIMATE from the 2.25 s live build, which also includes matching), one year of history is a 3 to 6 minute rebuild.

| Scale | What it means here | What happens (ESTIMATE) | Breaks first |
|---|---|---|---|
| **1x** | 1,614 records/day, 1 health system, 177 formulary items | Seconds per day; minutes per year of history. Fine. | nothing |
| **10x** | 1 year of daily history, or ~10 sources (FDA + ASHP + wholesaler feeds), or ~1,800 formulary items | Rebuild 3 to 60 minutes; still under 512 MB if parsed one folder at a time. | **Full rebuild from raw JSON.** Fix already named in ADR 004: per-snapshot parquet cache keyed by `records_sha256`, so only new days are parsed. Behavior unchanged. |
| **100x** | Multi-year history across many health systems, or a regional GPO with ~20k items and many users | Rebuild in hours; matching candidate generation (Python, per item) becomes the hot loop; the single-writer DuckDB file cannot serve concurrent pharmacists. | **Concurrency before volume.** DuckDB allows one read-write process and `writer_lock` is in-process only (KI-07). Move ops/audit to Postgres; incremental SCD2 with a backfill repair job; matching as a set-based SQL join on normalized keys. |

Users break it before data does: one pharmacy team is 3 to 10 people, and the second app process is already unsafe on one DuckDB file.

## If it moved to a cloud (ESTIMATE, monthly, one 3-hospital system)
| Component | Choice | Rough cost |
|---|---|---|
| App + nightly job | 1 small VM or container (2 vCPU, 4 to 8 GB) | about $30 to $80 |
| Database for ops/audit (and views) | Small managed Postgres with backups | about $15 to $60 |
| Snapshot archive (write-once) | Object storage with object lock / versioning, ~1 GB/year | under $1 |
| Identity | The hospital's existing SSO (Entra ID / Okta) | $0 incremental |
| Logs and alerts | Cloud defaults | about $0 to $20 |
| **Total** | | **about $50 to $160 per month** |

The dominant cost is not infrastructure. It is people: a pharmacist reviewing labels, an analyst maintaining the brand and normalization lists, and an engineer on call for the daily pull (see [OPERATING_MODEL](OPERATING_MODEL.md)).

A Palantir Foundry deployment is priced by contract and is not public, so no number is given.
