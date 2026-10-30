"""Schema ownership and typed table loads (owner: data-engineer, DE-07, ADR 002, AC-6.4).

* Pipeline-owned schemas ``stage, core, seed, obj, link, mart`` are dropped and recreated on every run.
* Action-owned schemas ``ops`` and ``audit`` are only ever created IF NOT EXISTS. Nothing here drops or writes them.
"""

from __future__ import annotations

import pandas as pd

PIPELINE_SCHEMAS = ("stage", "core", "seed", "obj", "link", "mart")
ACTION_SCHEMAS = ("ops", "audit")

# Column types for the DE-owned tables (DESIGN 3.2). Columns not listed load as VARCHAR.
TYPES: dict[str, dict[str, str]] = {
    "stage.snapshot": {"snapshot_date": "DATE", "fetched_at_utc": "TIMESTAMP", "meta_last_updated": "DATE",
                       "meta_total": "BIGINT", "record_count": "BIGINT"},
    "stage.shortage_record": {"snapshot_date": "DATE", "api_order": "INTEGER", "availability_mapped": "BOOLEAN",
                              "update_date": "DATE", "change_date": "DATE", "initial_posting_date": "DATE",
                              "discontinued_date": "DATE", "has_openfda": "BOOLEAN", "dup_count": "INTEGER"},
    "core.dq_results": {"snapshot_date": "DATE", "measured": "DOUBLE", "threshold": "DOUBLE",
                        "checked_at_utc": "TIMESTAMP"},
    "core.shortage_history": {"valid_from": "DATE", "valid_to": "DATE", "is_current": "BOOLEAN", "present": "BOOLEAN",
                              "update_date": "DATE"},
    "core.change_feed": {"snapshot_date": "DATE", "prev_snapshot_date": "DATE", "rule": "INTEGER"},
    "core.shortage_event": {"update_date": "DATE", "change_date": "DATE", "initial_posting_date": "DATE",
                            "discontinued_date": "DATE", "has_openfda": "BOOLEAN", "first_seen": "DATE",
                            "last_seen": "DATE", "present_in_latest": "BOOLEAN"},
    "seed.facility": {"beds": "INTEGER", "is_synthetic": "BOOLEAN"},
    "seed.formulary_item": {"is_synthetic": "BOOLEAN"},
    "seed.facility_stock": {"days_on_hand": "INTEGER", "is_synthetic": "BOOLEAN"},
    "seed.app_user": {"active": "BOOLEAN", "is_synthetic": "BOOLEAN"},
    "seed.variant_manifest": {"expected_match": "INTEGER", "expected_tier": "INTEGER", "is_synthetic": "BOOLEAN"},
}
DQ_DDL = """
CREATE TABLE core.dq_results (
    run_id VARCHAR NOT NULL, snapshot_date DATE, check_name VARCHAR NOT NULL, status VARCHAR NOT NULL
        CHECK (status IN ('PASS', 'WARN', 'FAIL', 'SKIPPED')),
    measured DOUBLE, threshold DOUBLE, detail VARCHAR, checked_at_utc TIMESTAMP NOT NULL)
"""


def reset_pipeline_schemas(con) -> None:
    """Drop and recreate ONLY the pipeline-owned schemas. ops/audit are never touched (AC-6.4)."""
    for s in PIPELINE_SCHEMAS:
        con.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
        con.execute(f"CREATE SCHEMA {s}")


def init_action_schemas(con) -> None:
    """Create the action-owned schemas IF NOT EXISTS. Their tables are created by ``console.actions.init_ops``
    (ops.*) and ``ontokit.audit.init_audit`` (audit.*), also IF NOT EXISTS."""
    for s in ACTION_SCHEMAS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")


def load_table(con, table: str, df: pd.DataFrame, types: dict[str, str] | None = None) -> int:
    """CREATE ``table`` with explicit column types and insert ``df``. Returns the row count."""
    types = TYPES.get(table, {}) if types is None else types
    cols = list(df.columns)
    if not cols:
        raise ValueError(f"{table}: no columns")
    ddl = ", ".join(f'"{c}" {types.get(c, "VARCHAR")}' for c in cols)
    con.execute(f"CREATE TABLE {table} ({ddl})")
    if df.empty:
        return 0
    frame = df.astype(object).where(df.notna(), None)
    con.register("_load_frame", frame)
    try:
        select = ", ".join(f'CAST("{c}" AS {types.get(c, "VARCHAR")})' for c in cols)
        con.execute(f"INSERT INTO {table} SELECT {select} FROM _load_frame")
    finally:
        con.unregister("_load_frame")
    return len(df)
