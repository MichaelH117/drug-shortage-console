"""DE-03: paging, write-once snapshot folders, cache hits, refresh (AC-1.1, AC-1.2, ADR 004)."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from pipeline.config import FIXTURE_SNAPSHOTS
from pipeline.snapshots import (
    SUPERSEDED,
    SnapshotIncomplete,
    ensure_snapshot,
    fetch_all,
    list_snapshots,
    make_writable,
    valid_snapshots,
)

DAY = dt.date(2026, 10, 30)


class FakeClient:
    """Serves ``total`` synthetic records in pages; can lie about meta.total."""

    def __init__(self, total=5, claimed_total=None, last_updated="2026-10-29"):
        self.records = [{"package_ndc": f"0000-0000-{i:02d}", "generic_name": "SYNTHETIC"} for i in range(total)]
        self.claimed = total if claimed_total is None else claimed_total
        self.last_updated = last_updated
        self.calls = []

    def get_json(self, url, params):
        self.calls.append(dict(params))
        skip, limit = params["skip"], params["limit"]
        return {"meta": {"last_updated": self.last_updated, "results": {"skip": skip, "limit": limit,
                                                                        "total": self.claimed}},
                "results": self.records[skip: skip + limit]}


class ExplodingClient:
    def get_json(self, url, params):
        raise AssertionError("no network call allowed on a cache hit")


def test_fetch_all_pages_until_total():
    c = FakeClient(total=5)
    records, meta = fetch_all(c, page_size=2)
    assert len(records) == 5
    assert [p["skip"] for p in c.calls] == [0, 2, 4]
    assert meta["_pages"] == 3


def test_count_mismatch_raises_and_leaves_no_folder(tmp_path):
    with pytest.raises(SnapshotIncomplete):
        ensure_snapshot(tmp_path, DAY, FakeClient(total=3, claimed_total=4))
    assert list(tmp_path.iterdir()) == []


def test_snapshot_folder_contents_and_read_only(tmp_path):
    folder = ensure_snapshot(tmp_path, DAY, FakeClient(total=3))
    assert folder.name == "2026-10-30"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["record_count"] == manifest["meta_total"] == 3
    assert manifest["source"] == "live"
    for f in folder.iterdir():
        assert not os.access(f, os.W_OK), f"{f.name} should be read-only"
    make_writable(folder)


def test_cache_hit_makes_zero_calls(tmp_path):
    ensure_snapshot(tmp_path, DAY, FakeClient(total=2))
    again = ensure_snapshot(tmp_path, DAY, ExplodingClient())
    assert again.name == "2026-10-30"
    make_writable(tmp_path)


def test_refresh_keeps_the_old_folder(tmp_path):
    ensure_snapshot(tmp_path, DAY, FakeClient(total=2))
    ensure_snapshot(tmp_path, DAY, FakeClient(total=3), refresh=True)
    refs = valid_snapshots(list_snapshots(tmp_path))
    assert len(refs) == 1 and refs[0].manifest["record_count"] == 3
    superseded = list((tmp_path / SUPERSEDED).iterdir())
    assert len(superseded) == 1
    old = json.loads((superseded[0] / "manifest.json").read_text(encoding="utf-8"))
    assert old["record_count"] == 2
    make_writable(tmp_path)


def test_offline_without_cache_fails_clearly(tmp_path):
    with pytest.raises(RuntimeError, match="no HTTP client"):
        ensure_snapshot(tmp_path, DAY, None)


def test_fixture_snapshots_are_valid_and_dated():
    refs = list_snapshots(FIXTURE_SNAPSHOTS)
    assert [r.problem for r in refs] == [None, None, None]
    assert [r.snapshot_date.isoformat() for r in refs] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert {r.manifest["source"] for r in refs} == {"SYNTHETIC_fixture"}
    assert all(r.path.name.startswith("SYNTHETIC_") for r in refs)


def test_sha_mismatch_is_flagged_not_used(tmp_path):
    folder = ensure_snapshot(tmp_path, DAY, FakeClient(total=2))
    make_writable(folder)
    (folder / "records.json").write_text("[]\n", encoding="utf-8")
    refs = list_snapshots(tmp_path)
    assert refs[0].problem.startswith("sha256_mismatch")
    assert valid_snapshots(refs) == []


def test_local_today_late_night_utc_and_dst():
    """BUG-PE-03 / DESIGN T10: snapshot dates are America/New_York dates, across both DST edges."""
    from pipeline.snapshots import local_today

    assert local_today(dt.datetime(2026, 10, 30, 3, 30)) == dt.date(2026, 10, 29)      # 23:30 EDT
    assert local_today(dt.datetime(2026, 10, 30, 4, 0)) == dt.date(2026, 10, 30)       # 00:00 EDT
    assert local_today(dt.datetime(2026, 11, 1, 4, 30)) == dt.date(2026, 11, 1)        # 00:30 EDT, DST ends 06:00Z
    assert local_today(dt.datetime(2026, 11, 2, 4, 30)) == dt.date(2026, 11, 1)        # 23:30 EST (UTC-5)
    assert local_today(dt.datetime(2026, 11, 2, 5, 0)) == dt.date(2026, 11, 2)         # 00:00 EST
    assert local_today(dt.datetime(2026, 3, 8, 4, 30)) == dt.date(2026, 3, 7)          # 23:30 EST, before DST
    assert local_today(dt.datetime(2026, 3, 9, 4, 0)) == dt.date(2026, 3, 9)           # 00:00 EDT, after DST
    aware = dt.datetime(2026, 10, 30, 3, 30, tzinfo=dt.UTC)
    assert local_today(aware) == dt.date(2026, 10, 29)
