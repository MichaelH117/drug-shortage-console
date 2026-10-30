"""Append-only, hash-chained action log (owner: data-engineer, DE-08; AC-7.1, 7.2, 7.3, 7.5; ADR 003).

SOURCE (AC-7.5): copied on 2026-10-30 from
``C:\\Users\\stimp\\projects\\vaccine-assay-pipeline\\pipeline\\audit.py`` (GxP seat) and adapted. Not imported across
project folders. Kept from the source: ``canonical_json`` / ``_jsonable``, ``compute_row_hash`` (hash rule
``sha256(prev_hash + "|" + canonical_json)``, genesis ``"0" * 64``), the ``chain_checkpoint`` table (one row per
append), the external anchor file, and ``update``/``delete`` that only raise ``AppendOnlyError``.
Changed: the columns follow AC-7.1 (one row per action attempt, SUCCESS or REFUSED); ``append`` does NOT open or
commit a transaction (the action runner owns it, ADR 003 step 5); ``verify_chain`` returns ``reason`` and
``checked_at_utc``. Dropped: ``insert_records``, ``correct_record``, ``reconcile`` and ``audited_tables`` (this project
writes edit rows through ``ontokit.actions`` instead).

ontokit is domain-free (ADR 005): this module imports nothing from pipeline, console or app.

Known limitation (KNOWN_ISSUES): DuckDB has no triggers or REVOKE, so anyone with the database file can run SQL.
These checks DETECT that; they do not prevent it. A forger who rewrites rows, hashes and checkpoints is caught only by
the external anchor, and only if the anchor file is kept out of their reach.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

GENESIS_HASH = "0" * 64
ACTION_LOG = "audit.action_log"
CHECKPOINT = "audit.chain_checkpoint"
OUTCOMES = ("SUCCESS", "REFUSED")

# Columns that go into the hash, in this order (ADR 003). The two hashes are excluded.
HASHED_COLUMNS = ["seq", "action_type", "object_type", "object_key", "actor_id", "role", "reason", "old_value",
                  "new_value", "outcome", "failed_criterion", "ts_utc"]
LOG_COLUMNS = HASHED_COLUMNS + ["prev_hash", "row_hash"]

DDL = [
    "CREATE SCHEMA IF NOT EXISTS audit",
    f"""
    CREATE TABLE IF NOT EXISTS {ACTION_LOG} (
        seq              BIGINT PRIMARY KEY,
        action_type      VARCHAR NOT NULL,
        object_type      VARCHAR NOT NULL,
        object_key       VARCHAR NOT NULL,
        actor_id         VARCHAR NOT NULL,
        role             VARCHAR,
        reason           VARCHAR,
        old_value        VARCHAR,
        new_value        VARCHAR,
        outcome          VARCHAR NOT NULL CHECK (outcome IN ('SUCCESS', 'REFUSED')),
        failed_criterion VARCHAR,
        ts_utc           TIMESTAMP NOT NULL,
        prev_hash        VARCHAR NOT NULL,
        row_hash         VARCHAR NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {CHECKPOINT} (
        checkpoint_seq BIGINT PRIMARY KEY,
        head_seq       BIGINT NOT NULL,
        head_hash      VARCHAR NOT NULL,
        ts_utc         TIMESTAMP NOT NULL
    )
    """,
]


class AppendOnlyError(RuntimeError):
    """Raised for any attempt to change or remove an action-log row (AC-7.2)."""


class ChainConflict(RuntimeError):
    """Two writers tried to append the same seq (they read the same chain tail). The caller must roll back.
    With ``writer_lock`` held around the whole action this cannot happen inside one process."""


# DuckDB allows one read-write PROCESS per file, but many connections/threads inside it (Streamlit runs each session
# in its own thread). Under MVCC two concurrent transactions read the same chain tail and pick the same seq, so the
# whole action (criteria check + edit + append) must run under one lock. Process-wide on purpose: actions are tiny.
_WRITER_LOCK = threading.RLock()


@contextmanager
def writer_lock(timeout_s: float = 30):
    """Serialize action transactions in this process. Hold it from BEGIN to COMMIT/ROLLBACK."""
    if not _WRITER_LOCK.acquire(timeout=timeout_s):
        raise TimeoutError(f"another action held the audit writer lock for more than {timeout_s} s")
    try:
        yield
    finally:
        _WRITER_LOCK.release()


@dataclass
class ChainResult:
    ok: bool
    rows_checked: int
    first_broken_seq: int | None = None
    reason: str | None = None          # CHAIN / CHECKPOINT / ANCHOR plus a short explanation
    checked_at_utc: dt.datetime = field(default_factory=lambda: _utc_now())


# ---------------------------------------------------------------------------------------------------------------
# Canonical serialisation and hashing (kept from the source)
# ---------------------------------------------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Turn one value into a JSON-safe, deterministic Python value."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC).replace(tzinfo=None)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):   # numpy / pandas scalars
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "to_pydatetime"):                                    # pandas Timestamp
        return _jsonable(value.to_pydatetime())
    return value if isinstance(value, (str, int)) else str(value)


def canonical_json(obj: Any) -> str:
    """JSON with sorted keys, no spaces, ISO timestamps, NaN as null. Stable across runs."""
    return json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def compute_row_hash(prev_hash: str, row: dict) -> str:
    content = canonical_json({c: row.get(c) for c in HASHED_COLUMNS})
    return hashlib.sha256(f"{prev_hash}|{content}".encode()).hexdigest()


def _utc_now() -> dt.datetime:
    # Naive UTC (DESIGN 3: TIMESTAMP in UTC with no time zone). Microseconds dropped so DuckDB round-trips exactly.
    return dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)


def _as_json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return canonical_json(value)


# ---------------------------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------------------------

def init_audit(con) -> None:
    """Create schema ``audit`` and its tables if they do not exist. Never drops anything."""
    for stmt in DDL:
        con.execute(stmt)


def _chain_tail(con) -> tuple[int, str]:
    row = con.execute(f"SELECT seq, row_hash FROM {ACTION_LOG} ORDER BY seq DESC LIMIT 1").fetchone()
    return (0, GENESIS_HASH) if row is None else (int(row[0]), row[1])


def append(con, *, action_type: str, object_type: str, object_key: str, actor_id: str, role: str | None,
           reason: str | None, old_value: Any, new_value: Any, outcome: str, failed_criterion: str | None = None,
           now: dt.datetime | None = None) -> int:
    """Append one action-log row and one checkpoint row. Returns the new ``seq``.

    The CALLER owns the transaction (ADR 003) and must hold ``writer_lock()`` from BEGIN to COMMIT: this function
    never begins, commits or rolls back, so the edit and its log row are written together or not at all.
    ``old_value``/``new_value`` may be dicts (stored as canonical JSON) or None.
    ``now`` defaults to UTC now; a timezone-aware value is converted to naive UTC.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    for name, value in (("action_type", action_type), ("object_type", object_type), ("object_key", object_key),
                        ("actor_id", actor_id)):
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required (attributable: who did what to which object)")
    if outcome == "REFUSED" and not failed_criterion:
        raise ValueError("a REFUSED row must name its failed_criterion")
    ts = now or _utc_now()
    if ts.tzinfo is not None:
        ts = ts.astimezone(dt.UTC).replace(tzinfo=None)
    last_seq, prev_hash = _chain_tail(con)
    row = {
        "seq": last_seq + 1, "action_type": action_type, "object_type": object_type, "object_key": str(object_key),
        "actor_id": actor_id, "role": role, "reason": reason, "old_value": _as_json_text(old_value),
        "new_value": _as_json_text(new_value), "outcome": outcome, "failed_criterion": failed_criterion,
        "ts_utc": ts,
    }
    row["prev_hash"] = prev_hash
    row["row_hash"] = compute_row_hash(prev_hash, row)
    try:
        con.execute(f"INSERT INTO {ACTION_LOG} ({', '.join(LOG_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(LOG_COLUMNS))})", [row[c] for c in LOG_COLUMNS])
        con.execute(f"INSERT INTO {CHECKPOINT} SELECT coalesce(max(checkpoint_seq), 0) + 1, ?, ?, ? "
                    f"FROM {CHECKPOINT}", [row["seq"], row["row_hash"], ts])
    except Exception as exc:  # duckdb.ConstraintException / TransactionException: a concurrent writer won
        if "Duplicate key" in str(exc) or "Conflict" in str(exc) or "conflict" in str(exc):
            raise ChainConflict(f"concurrent append: seq {row['seq']} already taken; retry the action") from exc
        raise
    return int(row["seq"])


def update(*args, **kwargs) -> NoReturn:
    """There is no update path (AC-7.2)."""
    raise AppendOnlyError("UPDATE is not allowed on the action log; it is append-only")


def delete(*args, **kwargs) -> NoReturn:
    """There is no delete path (AC-7.2)."""
    raise AppendOnlyError("DELETE is not allowed on the action log; it is append-only")


def _load_anchor(anchor: Path | dict | str) -> dict:
    if isinstance(anchor, dict):
        return anchor
    return json.loads(Path(anchor).read_text(encoding="utf-8"))


def write_anchor(con, path: Path) -> dict:
    """Write the chain head (seq + hash) to an external JSON file and make it read-only. Returns the anchor.

    Keep this file outside the database (and ideally out of reach of whoever can edit the database). An existing
    read-only anchor is replaced by a newer one.
    """
    head_seq, head_hash = _chain_tail(con)
    anchor = {"head_seq": head_seq, "head_hash": head_hash, "written_at_utc": _utc_now().isoformat()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(anchor, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.chmod(path, stat.S_IREAD)
    return anchor


def verify_chain(con, anchor: Path | dict | None = None) -> ChainResult:
    """Recompute the log and report the first problem. Checks, in order:

    1. ``CHAIN``: walk the rows in seq order. A row is broken if its seq is not the next integer, its prev_hash is
       not the previous row_hash, or its row_hash does not match its content. Editing row k gives
       ``first_broken_seq == k``.
    2. ``CHECKPOINT``: checkpoint numbers are gap-free, each one matches the row hash at its head_seq, and the newest
       one is the chain head. Deleting the newest row(s) is caught here.
    3. ``ANCHOR`` (if given): the row at the anchored head_seq exists with the anchored hash. This survives a forger
       who also rewrites the checkpoints.
    """
    cur = con.execute(f"SELECT {', '.join(LOG_COLUMNS)} FROM {ACTION_LOG} ORDER BY seq")
    prev_hash = GENESIS_HASH
    expected = 1
    checked = 0
    hashes: dict[int, str] = {}
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        for values in batch:
            row = dict(zip(LOG_COLUMNS, values, strict=True))
            checked += 1
            seq = int(row["seq"])
            if seq != expected:
                return ChainResult(False, checked, seq, f"CHAIN: seq {seq} where {expected} was expected")
            if row["prev_hash"] != prev_hash:
                return ChainResult(False, checked, seq, "CHAIN: prev_hash does not match the previous row")
            if compute_row_hash(prev_hash, row) != row["row_hash"]:
                return ChainResult(False, checked, seq, "CHAIN: row content does not match its row_hash")
            hashes[seq] = row["row_hash"]
            prev_hash = row["row_hash"]
            expected += 1

    cps = con.execute(f"SELECT checkpoint_seq, head_seq, head_hash FROM {CHECKPOINT} ORDER BY checkpoint_seq"
                      ).fetchall()
    if checked and not cps:
        return ChainResult(False, checked, 1, "CHECKPOINT: log rows exist but there are no checkpoints")
    for i, (cp_seq, head_seq, head_hash) in enumerate(cps, start=1):
        if cp_seq != i:
            return ChainResult(False, checked, int(head_seq), f"CHECKPOINT: checkpoint {cp_seq} where {i} expected")
        if hashes.get(int(head_seq)) != head_hash:
            return ChainResult(False, checked, int(head_seq),
                               f"CHECKPOINT: row {head_seq} is missing or its hash changed")
    if cps and int(cps[-1][1]) != checked:
        return ChainResult(False, checked, int(cps[-1][1]) + 1,
                           "CHECKPOINT: rows after the newest checkpoint were written outside append()")

    if anchor is not None:
        a = _load_anchor(anchor)
        head_seq = int(a["head_seq"])
        if head_seq and hashes.get(head_seq) != a["head_hash"]:
            return ChainResult(False, checked, head_seq, f"ANCHOR: anchored row {head_seq} is missing or changed")
    return ChainResult(True, checked)
