"""Write-once snapshot store (owner: data-engineer, DESIGN 3.1, ADR 004, AC-1.1, AC-1.2).

Layout: ``<raw_root>/<folder>/records.json, meta.json, manifest.json``. The folder name is the snapshot date
(``YYYY-MM-DD``) for live pulls; fixture folders may use another name (``SYNTHETIC_D1``) because the date always comes
from ``manifest.json``. Anything that writes a valid folder in this format slots in on the next run (E4).

Rules:
* ``record_count != meta_total`` raises ``SnapshotIncomplete`` and leaves no folder (write to ``.tmp``, then rename).
* Files are set read-only after writing; an existing folder is never modified.
* ``--refresh`` writes a new folder and moves the old one to ``_superseded/YYYY-MM-DD-<fetched_at>/``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.config import load_settings
from pipeline.http import HttpClient

TOOL_VERSION = "0.1.0"
SUPERSEDED = "_superseded"
SOURCES = ("live", "backfill", "SYNTHETIC_fixture")
MANIFEST_KEYS = ("snapshot_date", "source", "fetched_at_utc", "record_count", "meta_total", "meta_last_updated",
                 "pages", "sha256", "tool_version")


class SnapshotIncomplete(RuntimeError):
    """The saved record count does not equal ``meta.results.total`` (AC-1.1)."""


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_date: dt.date
    path: Path
    manifest: dict = field(compare=False, hash=False)
    problem: str | None = None      # None = valid. Otherwise a short reason; the folder is skipped (ADR 004).

    @property
    def records_path(self) -> Path:
        return self.path / "records.json"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    def load_records(self) -> list[dict]:
        return json.loads(self.records_path.read_text(encoding="utf-8"))

    def load_meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))


def local_today(now_utc: dt.datetime | None = None) -> dt.date:
    """Today's date in the configured timezone (DESIGN T10)."""
    tz = ZoneInfo(load_settings()["timezone"])
    now = now_utc or dt.datetime.now(dt.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.UTC)
    return now.astimezone(tz).date()


def fetch_all(client: HttpClient, page_size: int = 1000) -> tuple[list[dict], dict]:
    """Page through the shortage endpoint with ``limit``/``skip``. Returns ``(records, meta of page 1)``.

    ``meta["_pages"]`` holds the number of requests made. Raises ``SnapshotIncomplete`` if the number of records
    does not equal ``meta.results.total``.
    """
    api = load_settings()["api"]
    page_size = min(int(page_size), int(api["page_size"]))
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    records: list[dict] = []
    meta: dict | None = None
    total = 0
    skip = 0
    pages = 0
    while True:
        if skip > int(api["max_skip"]):
            raise SnapshotIncomplete(f"skip {skip} exceeds the openFDA max_skip {api['max_skip']}")
        body = client.get_json(api["base_url"], {"limit": page_size, "skip": skip})
        pages += 1
        if meta is None:
            meta = body.get("meta") or {}
            total = int(((meta.get("results") or {}).get("total")) or 0)
        page = body.get("results") or []
        records.extend(page)
        if not page or len(records) >= total:
            break
        skip += page_size
    meta = dict(meta or {})
    meta["_pages"] = pages
    if len(records) != total:
        raise SnapshotIncomplete(f"saved {len(records)} records but meta.results.total is {total}")
    return records, meta


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def make_read_only(folder: Path) -> None:
    for p in Path(folder).iterdir():
        if p.is_file():
            os.chmod(p, stat.S_IREAD)


def make_writable(folder: Path) -> None:
    for p in Path(folder).rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def write_snapshot(raw_root: Path, folder_name: str, snapshot_date: dt.date, records: list[dict], meta: dict, *,
                   source: str, fetched_at_utc: dt.datetime, pages: int = 1) -> Path:
    """Write one snapshot folder atomically (``.tmp`` then rename) and set the files read-only.

    The target folder must not exist. Raises ``SnapshotIncomplete`` before writing anything if the count is wrong.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    meta = {k: v for k, v in meta.items() if k != "_pages"}
    total = int(((meta.get("results") or {}).get("total")) or 0)
    if len(records) != total:
        raise SnapshotIncomplete(f"saved {len(records)} records but meta.results.total is {total}")
    raw_root = Path(raw_root)
    final = raw_root / folder_name
    if final.exists():
        raise FileExistsError(f"{final} exists; snapshot folders are write-once")
    tmp = raw_root / f"{folder_name}.tmp"
    if tmp.exists():
        make_writable(tmp)
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        (tmp / "records.json").write_text(_dump(records), encoding="utf-8", newline="\n")
        (tmp / "meta.json").write_text(_dump(meta), encoding="utf-8", newline="\n")
        manifest = {
            "snapshot_date": snapshot_date.isoformat(),
            "source": source,
            "fetched_at_utc": fetched_at_utc.replace(tzinfo=None, microsecond=0).isoformat(),
            "record_count": len(records),
            "meta_total": total,
            "meta_last_updated": meta.get("last_updated"),
            "pages": pages,
            "sha256": {"records.json": _sha256(tmp / "records.json"), "meta.json": _sha256(tmp / "meta.json")},
            "tool_version": TOOL_VERSION,
        }
        (tmp / "manifest.json").write_text(_dump(manifest), encoding="utf-8", newline="\n")
        make_read_only(tmp)
        tmp.rename(final)
    except BaseException:
        if tmp.exists():
            make_writable(tmp)
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def ensure_snapshot(raw_root: Path, run_date: dt.date, client: HttpClient | None, refresh: bool = False) -> Path:
    """Return the snapshot folder for ``run_date``, pulling it only if needed.

    * Cache hit and ``refresh=False``: zero client calls (AC-1.2).
    * Missing, or ``refresh=True``: pull all pages; on refresh the old folder moves to ``_superseded/``.
    """
    raw_root = Path(raw_root)
    name = run_date.isoformat()
    folder = raw_root / name
    if folder.exists() and not refresh:
        return folder
    if client is None:
        raise RuntimeError(f"no snapshot for {run_date} in {raw_root} and no HTTP client (offline run)")
    records, meta = fetch_all(client, int(load_settings()["api"]["page_size"]))
    fetched = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    pages = int(meta.get("_pages", 1))
    if not folder.exists():
        return write_snapshot(raw_root, name, run_date, records, meta, source="live", fetched_at_utc=fetched,
                              pages=pages)
    # Refresh: write the new folder first, so a failed pull keeps the old folder in place.
    new = write_snapshot(raw_root, f"{name}.new", run_date, records, meta, source="live", fetched_at_utc=fetched,
                         pages=pages)
    old_manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    stamp = str(old_manifest.get("fetched_at_utc", "unknown")).replace(":", "").replace("-", "")
    dest = raw_root / SUPERSEDED / f"{name}-{stamp}"
    n = 1
    while dest.exists():
        dest = raw_root / SUPERSEDED / f"{name}-{stamp}-{n}"
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(dest)
    new.rename(folder)
    return folder


def _read_ref(folder: Path) -> SnapshotRef | None:
    mpath = folder / "manifest.json"
    if not mpath.exists():
        return None
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        snap_date = dt.date.fromisoformat(manifest["snapshot_date"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return SnapshotRef(dt.date.min, folder, {}, f"bad_manifest: {exc}")
    missing = [k for k in MANIFEST_KEYS if k not in manifest]
    if missing:
        return SnapshotRef(snap_date, folder, manifest, f"bad_manifest: missing {missing}")
    for fname in ("records.json", "meta.json"):
        f = folder / fname
        if not f.exists():
            return SnapshotRef(snap_date, folder, manifest, f"missing_file: {fname}")
        if _sha256(f) != (manifest.get("sha256") or {}).get(fname):
            return SnapshotRef(snap_date, folder, manifest, f"sha256_mismatch: {fname}")
    return SnapshotRef(snap_date, folder, manifest, None)


def list_snapshots(raw_root: Path) -> list[SnapshotRef]:
    """Every snapshot folder under ``raw_root`` with a manifest, sorted by date then folder name.

    Each manifest and sha256 is re-checked. Invalid folders are returned with ``problem`` set, so the DQ step can
    report them; callers use ``valid_snapshots`` to skip them (ADR 004). ``_superseded`` and ``.tmp``/``.new``
    folders are ignored. If two valid folders claim the same date, the later folder name wins and the other is
    marked ``duplicate_date``.
    """
    raw_root = Path(raw_root)
    if not raw_root.exists():
        return []
    refs = []
    for folder in sorted(p for p in raw_root.iterdir() if p.is_dir()):
        if folder.name == SUPERSEDED or folder.name.endswith((".tmp", ".new")):
            continue
        ref = _read_ref(folder)
        if ref is not None:
            refs.append(ref)
    refs.sort(key=lambda r: (r.snapshot_date, r.path.name))
    seen: dict[dt.date, int] = {}
    for i, r in enumerate(refs):
        if r.problem is None:
            if r.snapshot_date in seen:
                j = seen[r.snapshot_date]
                refs[j] = SnapshotRef(refs[j].snapshot_date, refs[j].path, refs[j].manifest, "duplicate_date")
            seen[r.snapshot_date] = i
    return refs


def valid_snapshots(refs: list[SnapshotRef]) -> list[SnapshotRef]:
    return [r for r in refs if r.problem is None]
