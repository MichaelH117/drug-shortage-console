# ADR 004: Write-once snapshot folders; history fully rebuilt from all of them every run

Status: Accepted 2026-10-30 (Tech Lead; Michael may override). Covers US-1 (AC-1.1, 1.2), US-2 (AC-2.2, 2.4), and EM decision E4.

## Context
The API returns current state only, so history exists only if we keep daily copies (SPEC R1). Daily real pulls wait until Michael approves a scheduled task (E4). That means history may arrive late, as a backfill, or with gaps. AC-2.4 allows two outcomes for out-of-order loads: produce the same history, or refuse with a clear error.

## Options
1. **Incremental SCD2 merge** for each new snapshot, using a watermark. This is the classic approach, but out-of-order or backfilled days need special repair logic. It is the watermark bug class from the research (section 4).
2. **Refuse out-of-order loads.** Simple, but a backfill (E4) could then never be added.
3. **Full deterministic rebuild.** Each run parses every snapshot folder, sorts by `snapshot_date`, and recomputes SCD2 and the change feed from scratch.

## Choice
Option 3. The raw folders are the system of record. `stage`, `core.shortage_history` and `core.change_feed` are pure functions of the set of folders. So:
- Loading D1, D3, D2 gives exactly the same result as D1, D2, D3 (AC-2.4). A test proves it.
- Running twice gives identical row counts and content hashes (AC-2.2).
- A future scheduled `--pull-only` job, or a folder marked `source: backfill`, slots in with no code change.

Gaps between snapshot dates are allowed. A state stays valid until the next snapshot where it differs. The change feed compares the latest snapshot with the previous one that exists, and it shows `prev_snapshot_date`, so a 3-day gap is visible.

## Consequences
- Cost grows with the number of days. At about 1,600 records a day, that is about 600k rows a year, which DuckDB handles in seconds. If it ever matters, add a per-snapshot parquet cache keyed by `records_sha256`. Behaviour does not change.
- `--refresh` on a day that already has a snapshot moves the old folder to `raw/_superseded/` instead of deleting it. History uses the newest folder, and the original evidence is kept.
- Snapshot integrity: `list_snapshots` re-checks the sha256 in each manifest. On a mismatch it writes a FAIL row to `dq_results` and skips that folder; it never uses it silently.
