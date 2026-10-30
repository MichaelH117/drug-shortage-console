"""Paths, settings and the DuckDB connection (owner: data-engineer, DESIGN section 4).

Two modes share one code path (DESIGN section 1):
* ``demo``: offline, built from the SYNTHETIC fixture snapshots in ``tests/fixtures/snapshots``.
* ``live``: built from ``data/live/raw/`` (today's cached openFDA pull, or a new pull).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.yaml"
AVAILABILITY_MAP_PATH = ROOT / "config" / "availability_map.yaml"
FIXTURE_SNAPSHOTS = ROOT / "tests" / "fixtures" / "snapshots"


class DatabaseInUse(RuntimeError):
    """The DuckDB file is locked by another process (usually the Streamlit app). DESIGN section 8."""


@dataclass(frozen=True)
class Paths:
    root: Path      # data/<mode>
    raw: Path       # snapshot folders (write-once). Demo mode reads the fixture snapshots in the repo.
    db: Path        # data/<mode>/console.duckdb
    seed: Path      # SYNTHETIC hospital CSVs written by generator.hospital
    reports: Path   # match_quality.json, impact_metrics.json


@lru_cache(maxsize=4)
def _load_settings_cached(path: str) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("version"):
        raise ValueError(f"{path}: settings has no `version` key")
    return data


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """Parsed ``config/settings.yaml`` (a fresh copy each call, so callers may not mutate the cache)."""
    import copy

    return copy.deepcopy(_load_settings_cached(str(path)))


def load_availability_map(path: Path = AVAILABILITY_MAP_PATH) -> dict:
    """Parsed ``config/availability_map.yaml`` (AC-2.5)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "buckets" not in data:
        raise ValueError(f"{path}: availability map has no `buckets` key")
    return data


def paths_for(mode: Literal["demo", "live"], base: Path | None = None) -> Paths:
    """Paths for one mode. ``base`` defaults to the repo root; tests pass a tmp folder.

    In demo mode ``raw`` always points at the committed fixture snapshots (read-only), so a test build into a tmp
    folder still uses the same offline input.
    """
    if mode not in ("demo", "live"):
        raise ValueError(f"mode must be 'demo' or 'live', got {mode!r}")
    s = load_settings()
    base = Path(base) if base is not None else ROOT
    root = base / s["paths"][f"{mode}_root"]
    raw = FIXTURE_SNAPSHOTS if mode == "demo" else root / "raw"
    return Paths(root=root, raw=raw, db=root / s["paths"]["db_file"], seed=root / "seed",
                 reports=base / s["paths"]["reports"])


def connect(db: Path | str, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open DuckDB with the RAM limits from settings (memory_limit 512MB, threads 2; DESIGN T1).

    ``":memory:"`` is allowed for unit tests. A locked file raises ``DatabaseInUse`` with a plain message.
    """
    s = load_settings()["duckdb"]
    target = str(db)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(target, read_only=read_only)
    except duckdb.IOException as exc:
        if "lock" in str(exc).lower() or "being used" in str(exc).lower():
            raise DatabaseInUse(f"close the app, the database is in use: {target}") from exc
        raise
    con.execute(f"SET memory_limit = '{s['memory_limit']}'")
    con.execute(f"SET threads = {int(s['threads'])}")
    return con
