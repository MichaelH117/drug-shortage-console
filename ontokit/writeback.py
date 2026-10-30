"""Write-back helpers (ML-04, ADR 003): append-only edit tables and "latest edit wins" views.

Domain-free: must not import pipeline, console or app.
"""
from __future__ import annotations

import re

_FORBIDDEN = re.compile(r"\b(DROP|TRUNCATE|DELETE|UPDATE|ALTER|OR\s+REPLACE)\b", re.IGNORECASE)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def init_edit_tables(con, ddl: list[str]) -> None:
    """Run CREATE ... IF NOT EXISTS statements only. Never drops or rewrites an existing edit table (AC-6.4)."""
    for stmt in ddl:
        s = stmt.strip()
        if not s:
            continue
        if _FORBIDDEN.search(s):
            raise ValueError(f"edit-table DDL may not drop, alter or replace: {s[:60]!r}")
        if not re.match(r"^CREATE\s+(SCHEMA|TABLE|SEQUENCE|INDEX|UNIQUE\s+INDEX)\s+IF\s+NOT\s+EXISTS\b", s,
                        re.IGNORECASE):
            raise ValueError(f"edit-table DDL must be CREATE ... IF NOT EXISTS: {s[:60]!r}")
        con.execute(s)


def _q(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"bad identifier {name!r}")
    return name


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def latest_edit_view_sql(base: str | None, edits: str, key: str, view: str, *,
                         base_columns: list[str] | None = None, edit_columns: list[str] | None = None,
                         defaults: dict | None = None, order_by: str = "edit_seq") -> str:
    """SQL for a view that merges a base table with the latest edit row per key (edits win).

    - ``base`` None: the view is the latest edit per key (a write-back-only object).
    - otherwise: FULL OUTER JOIN, so base-only rows and edit-only rows (e.g. user proposals) both appear.
      Columns present on both sides become ``COALESCE(edit, base)``. ``defaults`` fill edit-only columns for
      rows that have no edit yet (e.g. ``state = 'candidate'``).

    Known limit (REVIEW 11): because shared columns are ``COALESCE(edit, base)``, an edit cannot set a column
    back to NULL (a NULL in the edit row falls through to the base value). No current action needs that. If one
    does, select the edit columns as-is when an edit row exists (``CASE WHEN e.<key> IS NOT NULL``).
    """
    defaults = defaults or {}
    for n in (edits, key, view, order_by) + ((base,) if base else ()):
        _q(n)
    latest = (f"latest AS (SELECT * FROM {edits} "
              f"QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY {order_by} DESC) = 1)")
    if base is None:
        if edit_columns:
            cols = ", ".join(
                f"COALESCE(e.{_q(c)}, {_lit(defaults[c])}) AS {c}" if c in defaults else f"e.{_q(c)}"
                for c in edit_columns)
        else:
            cols = "e.*"
        return f"CREATE OR REPLACE VIEW {view} AS WITH {latest} SELECT {cols} FROM latest e"
    if base_columns is None or edit_columns is None:
        raise ValueError("base_columns and edit_columns are required when base is given")
    parts = [f"COALESCE(e.{key}, b.{key}) AS {key}"]
    for c in base_columns:
        if c == key:
            continue
        _q(c)
        parts.append(f"COALESCE(e.{c}, b.{c}) AS {c}" if c in edit_columns else f"b.{c}")
    for c in edit_columns:
        if c == key or c in base_columns:
            continue
        _q(c)
        parts.append(f"COALESCE(e.{c}, {_lit(defaults[c])}) AS {c}" if c in defaults else f"e.{c}")
    return (f"CREATE OR REPLACE VIEW {view} AS WITH {latest} SELECT {', '.join(parts)} "
            f"FROM {base} b FULL OUTER JOIN latest e ON e.{key} = b.{key}")
