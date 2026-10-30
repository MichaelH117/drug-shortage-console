"""ML-08 live tests (AC-1.6). Skipped unless DSC_LIVE=1. They call api.fda.gov through pipeline.http only."""
import datetime as dt

import pytest

pytestmark = pytest.mark.live


def test_live_pull_count_equals_meta_total(tmp_path):
    from pipeline.http import default_client
    from pipeline.snapshots import fetch_all

    records, meta = fetch_all(default_client())
    total = meta["results"]["total"]
    print(f"live pull: {len(records)} records, meta.results.total={total}, last_updated={meta.get('last_updated')}")
    assert len(records) == total


def test_live_match_rate_reported(tmp_path):
    """Builds a live-mode DB in tmp and REPORTS (does not assert) how many formulary items match."""
    from pipeline.config import connect, paths_for
    from pipeline.http import default_client
    from pipeline.run import build

    paths = paths_for("live", tmp_path)
    build(paths, client=default_client(), refresh=False, seed=42)
    con = connect(paths.db, read_only=True)
    try:
        items = con.execute("SELECT count(*) FROM seed.formulary_item").fetchone()[0]
        matched = con.execute("SELECT count(DISTINCT item_id) FROM core.formulary_match").fetchone()[0]
        by_tier = con.execute("SELECT match_tier, count(*) FROM core.formulary_match GROUP BY 1 ORDER BY 1").fetchall()
        events = con.execute("SELECT count(*) FROM core.shortage_event").fetchone()[0]
    finally:
        con.close()
    print(f"live {dt.date.today()}: {events} events; {matched}/{items} formulary items matched "
          f"({matched / max(items, 1):.1%}); rows by tier {by_tier}")
