"""AE-03: headless Streamlit tests with streamlit.testing.v1.AppTest (no browser, no server, no network).

Each test points the app at a private copy of the session build (``DSC_DB_PATH``), with the SIMULATED script
applied, so the repo's data/ is never touched.
"""
from __future__ import annotations

import _socket
import shutil
import socket
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from console import simulate  # noqa: E402
from pipeline.config import connect  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"
KETOROLAC = "00409379301|2018-03-26"     # in today's queue; the simulation leaves its substitute 'proposed'


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def allow_loopback_only(monkeypatch):
    """AppTest starts an asyncio loop; on Windows that opens a loopback socketpair. Allow loopback only, so the
    offline guard (AC-1.4) still blocks every external host."""

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            raise RuntimeError(f"network access is blocked in tests (tried {address!r})")
        return _socket.socket.connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield


@pytest.fixture
def app_db(demo_build, tmp_path, monkeypatch):
    dst = tmp_path / "console.duckdb"
    shutil.copy(demo_build["paths"].db, dst)
    con = connect(dst)
    simulate.run_simulation(con)
    con.close()
    monkeypatch.setenv("DSC_DB_PATH", str(dst))
    return dst


def _run(page: str, **qp) -> AppTest:
    at = AppTest.from_file(str(APP / page), default_timeout=60)
    for k, v in qp.items():
        at.query_params[k] = v
    return at.run()


def _html(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_home_renders_queue_with_labels(app_db):
    at = _run("Home.py")
    assert not at.exception
    html = _html(at)
    assert "SYNTHETIC hospital data" in html
    assert "Since yesterday" in html
    assert any("demo identity, not authentication" in c.value.lower() for c in at.sidebar.caption)
    queue = at.dataframe[0].value
    assert len(queue) == 3 and set(queue["Change"]) <= {"NEW", "WORSENED"}
    assert queue.iloc[0]["Availability"] == "Unavailable" and queue["#"].tolist() == [1, 2, 3]
    assert any(e.label.startswith("Watch list: 3") for e in at.expander)


def test_home_baseline_day_shows_review_list(app_db):
    con = connect(app_db)
    base = con.execute("SELECT min(snapshot_date) FROM stage.snapshot").fetchone()[0]
    for t in ("stage.snapshot", "stage.shortage_record", "core.change_feed"):
        con.execute(f"DELETE FROM {t} WHERE snapshot_date > ?", [base])
    con.close()
    at = _run("Home.py")
    assert not at.exception
    assert "Baseline day" in _html(at) and "Since yesterday" not in _html(at)
    worklist = at.dataframe[0].value
    assert 0 < len(worklist) <= 40 and worklist["Formulary item"].notna().all()


@pytest.mark.parametrize("user,approve_disabled", [("lee.chen", True), ("jordan.blake", True), ("dana.ruiz", False)])
def test_approve_button_follows_role(app_db, user, approve_disabled):
    at = _run("pages/1_Drug.py", event=KETOROLAC, **{"as": user})
    assert not at.exception
    approve = next(b for b in at.button if b.label == "Approve substitute")
    assert approve.disabled is approve_disabled
    locked = [m.value for m in at.markdown if "Locked:" in m.value and "pharmacist" in m.value]
    assert bool(locked) is approve_disabled


def test_pharmacist_approves_and_views_update(app_db):
    at = _run("pages/1_Drug.py", event=KETOROLAC, **{"as": "dana.ruiz"})
    at.text_input(key="decide_reason").input("Strength confirmed with P&T; approved for all sites")
    next(b for b in at.button if b.label == "Approve substitute").click()
    at.run()
    assert not at.exception
    assert any("SUCCESS" in s.value for s in at.success)
    con = connect(app_db, read_only=True)
    try:
        row = con.execute("SELECT actor_id, outcome FROM audit.action_log ORDER BY seq DESC LIMIT 1").fetchone()
        approved = con.execute("SELECT substitute_approved FROM mart.triage_queue WHERE event_key = ?",
                               [KETOROLAC]).fetchone()[0]
    finally:
        con.close()
    assert row == ("dana.ruiz", "SUCCESS") and approved is True


def test_buyer_forced_approve_is_refused_server_side(app_db):
    at = _run("pages/1_Drug.py", event=KETOROLAC, **{"as": "lee.chen"})
    next(b for b in at.button if b.label == "Call approve_substitute as me").click()
    at.run()
    assert any("REFUSED" in e.value and "role" in e.value for e in at.error)
    con = connect(app_db, read_only=True)
    try:
        row = con.execute("SELECT actor_id, outcome, failed_criterion FROM audit.action_log "
                          "ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        con.close()
    assert row == ("lee.chen", "REFUSED", "role")


def test_audit_page_shows_chain_verify(app_db):
    at = _run("pages/2_Audit.py")
    assert not at.exception
    assert "Chain verify: OK" in _html(at)
    assert len(at.dataframe[0].value) == 14


@pytest.mark.parametrize("page", ["pages/3_Match_Quality.py", "pages/4_Metrics.py"])
def test_quality_and_metrics_pages_render(app_db, page):
    at = _run(page)
    assert not at.exception
    html = _html(at)
    if "Match" in page:
        assert "BELOW TARGET" in html or "meets both targets" in html
    else:
        assert "SIMULATED demo usage, not real users" in html


def test_wallpaper_is_off_by_default_and_home_only(app_db):
    """Design rule (BRAND.md audience rule): the moving background is OFF by default, lives only on the Home
    overview behind a toggle, and is never sent on a working screen (object view, audit, action forms)."""
    at = _run("Home.py")
    assert not at.exception
    assert at.toggle(key="wallpaper").value is False
    assert not at.get("html")
    at.toggle(key="wallpaper").set_value(True).run()
    assert not at.exception
    assert any("HansonWallpaper" in str(h.proto) for h in at.get("html"))
    assert "SYNTHETIC hospital data" in _html(at)
    for page in ("pages/1_Drug.py", "pages/2_Audit.py"):
        work = _run(page, bg="1")
        assert not work.exception
        assert not work.get("html") and not work.toggle
