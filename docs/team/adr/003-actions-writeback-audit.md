# ADR 003: Actions as one transaction over append-only edit tables, logged in a hash-chained action log

Status: Accepted 2026-10-30 (Tech Lead; Michael may override). Covers US-6 and US-7.

## Context
There are six actions, each with role rules and submission criteria (SPEC US-6). The design must meet these ACs:
- A refused action changes nothing but is logged (AC-6.2).
- An edit and its audit row are written together or not at all (AC-6.3).
- Re-running the pipeline never erases an edit (AC-6.4).
- The log is append-only and hash-chained with the vaccine-assay-pipeline rule `sha256(prev_hash + "|" + canonical_json)`, genesis `"0"*64`, and deleting the newest rows is detected (AC-7.1 to 7.3).

The GxP seat is benched, so we reuse its pattern rather than re-invent it. Foundry's model is the reference: action types, submission criteria, writeback datasets, and edits win.

## Options
**Write-back shape**
1. **Append-only edit event tables** (`ops.substitute_edit`, `ops.mitigation_task_edit`). The object view takes the latest edit per key over the pipeline base. History comes for free, and nothing is ever updated.
2. Mutable "current state" edit tables (UPDATE in place). The views are simpler, but object history is lost and lives only in the audit log.
3. Edits written into pipeline tables. This breaks AC-6.4, so it is rejected.

**Where rules run**
- A. **A generic runner (`ontokit.actions.run_action`) plus domain criteria as named predicates (`console.actions`).** The UI and the tests call the same functions.
- B. Rules in the Streamlit pages. A hidden button is not a control (SPEC R9), so this is rejected.

**Audit code**
- i. **Copy `vaccine-assay-pipeline/pipeline/audit.py` into `ontokit/audit.py`** and adapt the columns to AC-7.1. Keep `canonical_json`, `compute_row_hash`, `chain_checkpoint`, the external anchor, and the `update`/`delete` stubs that raise `AppendOnlyError`.
- ii. Import it across project folders. AC-7.5 forbids this.
- iii. A new implementation. Wasteful, and it would drift from the proven pattern.

## Choice
1 + A + i. The runner sequence (also in DESIGN section 4):
1. BEGIN.
2. Role check: the actor must be active, and their role must be in the action's roles. The role comes from `seed.app_user`, not from the caller.
3. Run the criteria in their declared order. The first failure is recorded by its id.
4. If refused: ROLLBACK. Then, in a separate transaction, append one `REFUSED` row (with `failed_criterion`). Then raise `ActionPermissionError` (role) or `ActionRefused` (criteria).
5. If allowed: `apply` writes the edit rows, then `audit.append(SUCCESS)`, then COMMIT.
6. Any exception between the edit and the commit: ROLLBACK and re-raise. No edit and no audit row remain. The test forces this by monkeypatching `audit.append` to raise.

Columns hashed, in this order: `seq, action_type, object_type, object_key, actor_id, role, reason, old_value, new_value, outcome, failed_criterion, ts_utc`. The copied file starts with a comment naming its source path and the date it was copied (AC-7.5).

Edit rows store the `action_seq` of their audit row, so every edit traces to exactly one SUCCESS log row. A QA reconcile test checks this both ways, applying the lesson from vaccine BUG-005.

## Consequences
- At the database level this is detective, not preventive. DuckDB has no triggers, so direct SQL can still edit the file. `verify_chain` finds it, and the checkpoint table plus the optional anchor file catch deletion of the newest row. KNOWN_ISSUES says so. The real-deployment fix is Postgres triggers plus REVOKE.
- REFUSED rows are written in a second transaction. If that write fails, the refusal still reaches the user but is not logged. We accept this and document it. A test covers the normal path.
- An exception (as opposed to a refusal) is not logged, because AC-6.3 requires both or neither. The UI shows the error. This is noted for the operating model.
- `ops.outbox` is a stub. Task-assignment rows are written to it, and nothing sends them.
